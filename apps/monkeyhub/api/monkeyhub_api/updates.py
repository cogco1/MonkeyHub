"""Local desktop patch preparation and the owned host's activation handshake.

The caller supplies both filesystem roots. Project data is never an update
input. Download channels/signing are not inferred from a developer patch.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import HubError, HubFailure

MAX_PATCH_BYTES = 256 * 1024 * 1024
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class PreparedUpdate(BaseModel):
    targetVersion: str = Field(pattern=r"^[0-9a-f]{12}-desktop$")
    targetRevision: str = Field(pattern=r"^[0-9a-f]{40}$")
    changedBytes: int = Field(ge=0)
    changedFiles: int = Field(ge=0)
    removedFiles: int = Field(ge=0)
    reusedFiles: int = Field(ge=0)


class UpdateStatus(BaseModel):
    currentVersion: str
    currentRevision: str | None
    mode: Literal["local", "unsupported"]
    state: Literal["idle", "preparing", "ready", "applying", "failed"]
    prepared: PreparedUpdate | None = None
    canApply: bool = False
    message: str | None = None
    error: HubError | None = None


class CompleteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fromCommit: str = Field(pattern=r"^[0-9a-f]{40}$")


class RollbackUpdate(CompleteUpdate):
    targetCommit: str = Field(pattern=r"^[0-9a-f]{40}$")


class DesktopUpdates:
    def __init__(self, source_root: Path, directory: Path, *, managed: bool,
                 busy: Callable[[], str | None]):
        self.source_root, self.directory = source_root, directory
        self._busy = busy
        self._lock = threading.RLock()
        self._activation_lock = threading.Lock()
        self._writes = 0
        self._restart_commit: str | None = None
        self._worker: threading.Thread | None = None
        self._preparing = False
        self._verifying = False
        self._state_path = directory / "state.json"
        self._record: dict = {}
        self._error: HubError | None = None
        try:
            commit = (source_root / "source-version.txt").read_text(encoding="utf-8-sig").strip()
            build = json.loads((source_root / "build-info.json").read_text(encoding="utf-8"))
            self.revision = commit if _COMMIT.fullmatch(commit) else None
            self.supported = bool(managed and self.revision and build.get("sourceCommit") == commit
                                  and build.get("desktop", {}).get("sourceCommit") == commit
                                  and source_root.name == commit[:12] + "-desktop"
                                  and source_root.parent.name == "versions"
                                  and (source_root / "MonkeyHub.exe").is_file())
        except (OSError, ValueError, AttributeError):
            self.revision, self.supported = None, False
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
            if (isinstance(value, dict) and _COMMIT.fullmatch(str(value.get("baseCommit", "")))
                    and _COMMIT.fullmatch(str(value.get("targetCommit", "")))):
                if value.get("state") not in {"ready", "applying", "applied", "failed"} or value["baseCommit"] == value["targetCommit"]:
                    raise ValueError("Invalid update transaction")
                prepared = PreparedUpdate.model_validate(value["prepared"])
                if (prepared.targetRevision != value["targetCommit"]
                        or prepared.targetVersion != value["targetCommit"][:12] + "-desktop"
                        or not re.fullmatch(r"incoming-[A-Za-z0-9_-]+", str(value.get("patch", "")))):
                    raise ValueError("Invalid update state")
                self._record = value
                if value.get("error"):
                    self._error = HubError.model_validate(value["error"])
                if value.get("state") == "applying" and value["baseCommit"] == self.revision:
                    # A restarted old host must not automatically retry a failed update.
                    self._error = HubError(code="UPDATE_INCOMPLETE", detail="The update did not finish. The previous application is available.")
            else:
                raise ValueError("Invalid update state")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError):
            self._record = {}
            self._error = HubError(code="UPDATE_STATE_UNREADABLE", detail="The previous update state could not be read.")

    def _save(self, record: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self._state_path)
        self._record = record

    def _activation_pending(self) -> bool:
        return self._record.get("state") == "applying" and self._record.get("targetCommit") == self.revision

    @contextmanager
    def mutation(self):
        with self._lock:
            if self._verifying or self._restart_commit or self._activation_pending():
                raise HubFailure(409, "UPDATE_RESTARTING", "The application is restarting for an update. No new operation was started.")
            self._writes += 1
        try:
            yield
        finally:
            with self._lock:
                self._writes -= 1

    def status(self) -> UpdateStatus:
        with self._lock:
            record = self._record
            prepared = None
            if record.get("baseCommit") == self.revision and record.get("prepared"):
                prepared = PreparedUpdate.model_validate(record["prepared"])
            applying = bool(self._verifying or self._restart_commit or self._activation_pending())
            state = "applying" if applying else "preparing" if self._preparing else "failed" if self._error else "ready" if prepared else "idle"
            busy = self._busy() if self.supported and prepared and not applying and not self._preparing else None
            return UpdateStatus(
                currentRevision=self.revision, currentVersion=self.revision[:12] if self.revision else "development",
                mode="local" if self.supported else "unsupported", state=state, prepared=prepared,
                canApply=bool(self.supported and prepared and not applying and not self._preparing and not busy and not self._writes),
                message=busy or ("Local developer patches only. No signed remote update channel is configured." if self.supported else "Use the packaged desktop application to apply patches."),
                error=self._error,
            )

    def begin_upload(self) -> Path:
        with self._lock:
            if not self.supported:
                raise HubFailure(409, "UPDATE_UNSUPPORTED", "Patches require the packaged desktop application.")
            if self._preparing or self._verifying or self._restart_commit or self._activation_pending():
                raise HubFailure(409, "UPDATE_BUSY", "An update is already being prepared or applied.")
            self.directory.mkdir(parents=True, exist_ok=True)
            path = Path(tempfile.mkdtemp(prefix="incoming-", dir=self.directory)) / "patch.zip"
            self._preparing, self._error = True, None
            return path

    def fail_upload(self, path: Path, detail: str) -> None:
        with self._lock:
            self._preparing = False
            self._error = HubError(code="UPDATE_PATCH_INVALID", detail=detail)
        folder = path.parent
        # Delete only this service's individual incoming patch directory, never
        # a version, project, linked directory or arbitrary caller-supplied path.
        if (folder.resolve().parent != self.directory.resolve()
                or not re.fullmatch(r"incoming-[A-Za-z0-9_-]+", folder.name)
                or folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction())):
            return
        shutil.rmtree(folder, ignore_errors=True)

    def prepare(self, path: Path) -> None:
        def work():
            try:
                from apps.monkeyhub.installer.patch import inspect_patch, stage_patch
                metadata = inspect_patch(path, self.source_root)
                target = stage_patch(path, self.source_root, self.source_root.parent)
                expected = self.source_root.parent / (metadata["targetCommit"][:12] + "-desktop")
                if target != expected:
                    raise ValueError("The prepared patch has an unexpected installation location.")
                prepared = PreparedUpdate(
                    targetVersion=metadata["targetVersion"], targetRevision=metadata["targetCommit"],
                    changedBytes=metadata["payloadBytes"], changedFiles=metadata["changedFiles"],
                    removedFiles=metadata["removedFiles"], reusedFiles=metadata["reusedFiles"],
                )
                # Keep the exact patch for a last readback before activation.
                with self._lock:
                    self._save({"baseCommit": self.revision, "targetCommit": metadata["targetCommit"],
                                "state": "ready", "prepared": prepared.model_dump(), "patch": path.parent.name})
            except (OSError, ValueError, KeyError) as error:
                self.fail_upload(path, str(error))
            finally:
                with self._lock:
                    self._preparing = False
        self._worker = threading.Thread(target=work, name="hub-prepare-update", daemon=True)
        self._worker.start()

    def apply(self) -> UpdateStatus:
        with self._lock:
            if not self.supported or not self._record.get("prepared") or self._record.get("baseCommit") != self.revision:
                raise HubFailure(409, "UPDATE_NOT_READY", "Choose and verify a patch for this application first.")
            if self._preparing or self._verifying or self._restart_commit:
                raise HubFailure(409, "UPDATE_BUSY", "An update is already being prepared or applied.")
            reason = self._busy()
            if self._writes or reason:
                raise HubFailure(409, "UPDATE_WORK_RUNNING", reason or "Wait for the current request to finish before restarting.")
            target = self.source_root.parent / (self._record["targetCommit"][:12] + "-desktop")
            if not (target / "MonkeyHub.exe").is_file():
                raise HubFailure(409, "UPDATE_TARGET_MISSING", "The prepared application is no longer available. Prepare the patch again.")
            self._verifying = True
            patch = self.directory / self._record["patch"] / "patch.zip"
        try:
            from apps.monkeyhub.installer.patch import inspect_patch, verify_target
            inspect_patch(patch, self.source_root)
            verify_target(patch, target)
            with self._lock:
                self._save({**self._record, "state": "applying", "error": None})
                self._restart_commit = self._record["targetCommit"]
                self._error = None
        except (OSError, ValueError) as error:
            with self._lock:
                self._error = HubError(code="UPDATE_REVALIDATION_FAILED", detail=str(error))
            raise HubFailure(409, "UPDATE_REVALIDATION_FAILED", str(error)) from error
        finally:
            with self._lock:
                self._verifying = False
        return self.status()

    def restart(self) -> dict:
        with self._lock:
            return {"targetCommit": self._restart_commit}

    def _activate(self) -> None:
        script = self.source_root / "apps/monkeyhub/installer/install.ps1"
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                 "-ActivateInstalled", "-CreateDesktopShortcut"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HubFailure(503, "UPDATE_ACTIVATION_FAILED", "Could not switch the desktop entry.") from error
        if result.returncode:
            raise HubFailure(503, "UPDATE_ACTIVATION_FAILED", "Could not switch the desktop entry. The previous version can be restored.")

    def complete(self, from_commit: str) -> UpdateStatus:
        with self._activation_lock:
            with self._lock:
                if (not self.supported or self._record.get("baseCommit") != from_commit
                        or self._record.get("targetCommit") != self.revision
                        or self._record.get("state") not in {"applying", "applied"}):
                    raise HubFailure(409, "UPDATE_TRANSACTION_MISMATCH", "This is not the prepared desktop update.")
                applied = self._record["state"] == "applied"
            if not applied:
                from apps.monkeyhub.installer.patch import verify_target
                try:
                    verify_target(self.directory / self._record["patch"] / "patch.zip", self.source_root)
                except (OSError, ValueError) as error:
                    raise HubFailure(409, "UPDATE_REVALIDATION_FAILED", str(error)) from error
                self._activate()
                with self._lock:
                    self._save({**self._record, "state": "applied", "error": None})
            with self._lock:
                self._error = None
        return self.status()

    def rollback(self, from_commit: str, target_commit: str) -> UpdateStatus:
        with self._activation_lock:
            with self._lock:
                if (not self.supported or from_commit != self.revision
                        or self._record.get("baseCommit") != from_commit or self._record.get("targetCommit") != target_commit):
                    raise HubFailure(409, "UPDATE_TRANSACTION_MISMATCH", "This application does not own that failed update.")
                self._verifying = True
            try:
                self._activate()
                with self._lock:
                    self._restart_commit = None
                    self._error = HubError(code="UPDATE_ROLLED_BACK", detail="The new application could not start. The previous desktop version has been restored.")
                    self._save({**self._record, "state": "failed", "error": self._error.model_dump()})
            finally:
                with self._lock:
                    self._verifying = False
        return self.status()

    def shutdown(self) -> None:
        if self._worker and self._worker.is_alive():
            self._worker.join()
