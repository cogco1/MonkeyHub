"""Desktop update preparation, automatic checks and the owned host's activation handshake.

A patch reaches this owner in one of two ways: a local developer patch the
user uploads, or the unsigned prerelease channel, where the public GitHub
releases of this repository publish an update index naming a delta patch for
this exact installed commit. Both are staged by the same path. Checksums and
the release manifest detect a changed or foreign download; they do not
establish the publisher (issue #58). An automatic update never restarts the
application: it is switched in when the application quits normally, and the
new version finishes that transaction itself, or restores the previous entry,
when it starts. The caller supplies both filesystem roots; project data is
never an update input.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
import urllib.request

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import HubError, HubFailure

MAX_PATCH_BYTES = 256 * 1024 * 1024
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_INDEX_BYTES = 1024 * 1024
REPOSITORY = "cogco1/MonkeyHub"
CHANNEL = "unsigned-prerelease"
INDEX_SCHEMA = "MonkeyHubUpdateIndex@1"
FIRST_CHECK_SECONDS = 30.0
CHECK_INTERVAL_SECONDS = 6 * 60 * 60.0
# An automatic check that found an update transaction in progress looks again this soon.
BUSY_RETRY_SECONDS = 60.0
# The desktop's restart-now trial completes through its helper; a start that
# was not such a trial finishes itself after this long.
TRIAL_GRACE_SECONDS = 90.0
HEALTH_SECONDS = 60.0
PREFLIGHT_SECONDS = 180.0
MAX_LAUNCH_ATTEMPTS = 3
INSTALLER = "apps/monkeyhub/installer/install.ps1"
ENTRY = "apps/monkeyhub/run.py"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_log = logging.getLogger(__name__)


class PreparedUpdate(BaseModel):
    targetVersion: str = Field(pattern=r"^[0-9a-f]{12}-desktop$")
    targetRevision: str = Field(pattern=r"^[0-9a-f]{40}$")
    changedBytes: int = Field(ge=0)
    changedFiles: int = Field(ge=0)
    removedFiles: int = Field(ge=0)
    reusedFiles: int = Field(ge=0)
    releaseVersion: str | None = None


class UpdateCheck(BaseModel):
    """The last automatic or requested check of the unsigned prerelease channel."""
    state: Literal["never", "checking", "up-to-date", "downloading", "ready", "needs-full-update", "error"] = "never"
    checkedAt: datetime | None = None
    latestVersion: str | None = None
    detail: str | None = None
    releaseUrl: str | None = None


class UpdateStatus(BaseModel):
    currentVersion: str
    currentRevision: str | None
    releaseVersion: str | None = None
    mode: Literal["local", "unsupported"]
    state: Literal["idle", "preparing", "ready", "applying", "failed"]
    prepared: PreparedUpdate | None = None
    canApply: bool = False
    message: str | None = None
    error: HubError | None = None
    channel: Literal["unsigned-prerelease"] | None = None
    autoUpdate: bool = False
    nextLaunch: bool = False
    check: UpdateCheck = Field(default_factory=UpdateCheck)


class CompleteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fromCommit: str = Field(pattern=r"^[0-9a-f]{40}$")


class RollbackUpdate(CompleteUpdate):
    targetCommit: str = Field(pattern=r"^[0-9a-f]{40}$")


class UpdateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autoUpdate: bool = Field(strict=True)


class Preference(Protocol):
    def enabled(self) -> bool: ...
    def set(self, enabled: bool) -> None: ...


class UserPreference:
    """自动更新 is saved with the other user settings; absent means on."""

    def enabled(self) -> bool:
        from archflow_studio_api.settings import read_user_settings
        return read_user_settings().auto_update is not False

    def set(self, enabled: bool) -> None:
        from archflow_studio_api.settings import read_user_settings, save_user_settings
        # Only "off" is written: the default stays absent, which versions
        # older than this setting can still read.
        save_user_settings(read_user_settings().model_copy(update={"auto_update": None if enabled else False}))


class UpdateCheckError(Exception):
    """A check found nothing usable. Transient failures are retried next time."""

    def __init__(self, detail: str, *, transient: bool = False, version: str | None = None):
        super().__init__(detail)
        self.transient, self.version = transient, version


class UpdateCancelled(Exception):
    """The application is closing; a check stops without judging the release."""


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise HTTPError(newurl, code, "A release download may redirect only to HTTPS.", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_limited(response, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateCheckError(f"A release answer is larger than {limit} bytes.")
    return data


class ReleaseFeed:
    """Unauthenticated HTTPS reads of this repository's public GitHub releases."""

    API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"

    def __init__(self, user_agent: str, opener: urllib.request.OpenerDirector | None = None):
        self._user_agent = user_agent
        self._opener = opener or urllib.request.build_opener(_HttpsRedirects())

    def _open(self, url: str, accept: str, extra: dict[str, str] | None = None):
        request = urllib.request.Request(url, headers={"User-Agent": self._user_agent, "Accept": accept, **(extra or {})})
        return self._opener.open(request, timeout=30)

    def releases(self, etag: str | None) -> tuple[str | None, list | None]:
        """The release list and its ETag; None when the cached list is still current (HTTP 304)."""
        extra = {"X-GitHub-Api-Version": "2022-11-28", **({"If-None-Match": etag} if etag else {})}
        try:
            with self._open(self.API, "application/vnd.github+json", extra) as response:
                return response.headers.get("ETag"), json.loads(_read_limited(response, MAX_FEED_BYTES))
        except HTTPError as error:
            if error.code == 304:
                return etag, None
            if error.code in (403, 429):
                raise UpdateCheckError("GitHub's limit for unauthenticated checks is reached; the next check retries.",
                                       transient=True) from error
            raise UpdateCheckError(f"GitHub releases answered HTTP {error.code}.", transient=True) from error
        except (URLError, OSError, ValueError) as error:
            raise UpdateCheckError(f"GitHub releases could not be read: {error}", transient=True) from error

    @staticmethod
    def url(tag: str, name: str) -> str:
        return f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"

    def read(self, tag: str, name: str, size: int) -> bytes:
        """One small release asset of exactly the size its release lists."""
        try:
            with self._open(self.url(tag, name), "application/octet-stream") as response:
                data = _read_limited(response, size)
        except (URLError, OSError) as error:
            raise UpdateCheckError(f"{name} could not be downloaded: {error}", transient=True) from error
        if len(data) != size:
            raise UpdateCheckError(f"{name} ended after {len(data)} of {size} bytes.", transient=True)
        return data

    def download(self, tag: str, name: str, destination: Path, size: int, sha256: str,
                 cancelled: Callable[[], bool]) -> None:
        """Stream one release asset to a new file, refusing more bytes or other bytes than the index states."""
        digest, received = hashlib.sha256(), 0
        try:
            with self._open(self.url(tag, name), "application/octet-stream") as response, destination.open("xb") as output:
                while chunk := response.read(256 * 1024):
                    if cancelled():
                        raise UpdateCancelled()
                    received += len(chunk)
                    if received > size:
                        raise UpdateCheckError(f"{name} is larger than its update index states.")
                    digest.update(chunk)
                    output.write(chunk)
        except (URLError, OSError) as error:
            raise UpdateCheckError(f"{name} could not be downloaded: {error}", transient=True) from error
        if received != size:
            raise UpdateCheckError(f"{name} ended after {received} of {size} bytes.", transient=True)
        if digest.hexdigest() != sha256:
            raise UpdateCheckError(f"{name} does not match the SHA-256 in its update index; it was not used.",
                                   transient=True)


def _version(value: object) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _json_object(data: bytes, label: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result
    try:
        document = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        raise UpdateCheckError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise UpdateCheckError(f"{label} is not a JSON object.")
    return document


def _desktop_releases(raw: object) -> list[dict]:
    """Published desktop prereleases: vMAJOR.MINOR.PATCH tags and their asset sizes."""
    if not isinstance(raw, list):
        raise UpdateCheckError("GitHub returned an unexpected release list.", transient=True)
    rows = []
    for item in raw:
        if not isinstance(item, dict) or item.get("draft") is not False or item.get("prerelease") is not True:
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v") or _version(tag[1:]) is None:
            continue
        assets = {asset["name"]: asset["size"] for asset in item.get("assets") or ()
                  if isinstance(asset, dict) and isinstance(asset.get("name"), str) and type(asset.get("size")) is int}
        rows.append({"tag": tag, "version": tag[1:], "assets": assets})
    return rows


def _entry(value: object, name: str, label: str) -> dict:
    if (not isinstance(value, dict) or value.get("name") != name or type(value.get("size")) is not int
            or value["size"] < 0 or not isinstance(value.get("sha256"), str) or not _SHA256.fullmatch(value["sha256"])):
        raise UpdateCheckError(f"The update index names no valid {label}.")
    return value


def _update_index(data: bytes, version: str) -> dict:
    document = _json_object(data, "The update index")
    commit = document.get("releaseCommit")
    if (document.get("schema") != INDEX_SCHEMA or document.get("version") != version
            or not isinstance(commit, str) or not _COMMIT.fullmatch(commit)):
        raise UpdateCheckError(f"The update index does not describe release {version}.")
    prefix = f"MonkeyHub-{version}-windows-x64-candidate.zip"
    _entry(document.get("releaseManifest"), prefix + ".release-manifest.json", "release manifest")
    patches = document.get("patches")
    if not isinstance(patches, list):
        raise UpdateCheckError("The update index lists no patches.")
    for row in patches:
        base = row.get("baseVersion") if isinstance(row, dict) else None
        if _version(base) is None or not isinstance(row.get("baseCommit"), str) or not _COMMIT.fullmatch(row["baseCommit"]):
            raise UpdateCheckError("The update index lists a patch without a base release.")
        _entry(row, f"MonkeyHub-{version}-from-{base}.patch.zip", "patch")
    return document


def _release_manifest(data: bytes, version: str, commit: str) -> dict:
    document = _json_object(data, "The release manifest")
    release = document.get("release") if isinstance(document.get("release"), dict) else {}
    build_info = document.get("buildInfo") if isinstance(document.get("buildInfo"), dict) else {}
    if (document.get("schema") != "ReleaseManifest@1" or release.get("version") != version
            or release.get("sourceCommit") != commit or release.get("target") != "windows-x64"
            or not isinstance(build_info.get("sha256"), str) or not _SHA256.fullmatch(build_info["sha256"])):
        raise UpdateCheckError(f"The release manifest does not describe release {version} at {commit[:12]}.")
    return document


def _command_path(path: Path) -> str:
    # Windows PowerShell's FileSystem provider cannot use the desktop's
    # verbatim source path. Adapt only a command argument, as for Node.
    text = str(path)
    if os.name == "nt":
        if text.lower().startswith("\\\\?\\unc\\"):
            return "\\\\" + text[8:]
        if re.match(r"^\\\\\?\\[a-zA-Z]:\\", text):
            return text[4:]
    return text


def _commit_of(root: Path) -> str | None:
    try:
        commit = (root / "source-version.txt").read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        return None
    return commit if _COMMIT.fullmatch(commit) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DesktopUpdates:
    def __init__(self, source_root: Path, directory: Path, *, managed: bool,
                 busy: Callable[[], str | None], feed: ReleaseFeed | None = None,
                 preference: Preference | None = None):
        self.source_root, self.directory = source_root, directory
        self._busy = busy
        self._lock = threading.RLock()
        self._activation_lock = threading.Lock()
        self._writes = 0
        self._restart_commit: str | None = None
        self._worker: threading.Thread | None = None
        self._threads: list[threading.Thread] = []
        self._preparing = False
        self._verifying = False
        # This version is finishing, or undoing, a next-launch activation.
        self._finishing = False
        self._started = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._explicit = False
        self._progress: tuple[str, str | None] | None = None
        self._feed = feed
        self._preference = preference or UserPreference()
        self._state_path = directory / "state.json"
        self._check_path = directory / "check.json"
        self._record: dict = {}
        self._error: HubError | None = None
        self.release_version: str | None = None
        try:
            commit = (source_root / "source-version.txt").read_text(encoding="utf-8-sig").strip()
            build = json.loads((source_root / "build-info.json").read_text(encoding="utf-8"))
            self.revision = commit if _COMMIT.fullmatch(commit) else None
            self.supported = bool(managed and self.revision and build.get("sourceCommit") == commit
                                  and build.get("desktop", {}).get("sourceCommit") == commit
                                  and source_root.name == commit[:12] + "-desktop"
                                  and source_root.parent.name == "versions"
                                  and (source_root / "MonkeyHub.exe").is_file())
            release = build.get("releaseVersion") or (build.get("desktop") or {}).get("version")
            self.release_version = release if _version(release) else None
        except (OSError, ValueError, AttributeError):
            self.revision, self.supported = None, False
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
            if (isinstance(value, dict) and _COMMIT.fullmatch(str(value.get("baseCommit", "")))
                    and _COMMIT.fullmatch(str(value.get("targetCommit", "")))):
                if (value.get("state") not in {"ready", "applying", "activated", "applied", "failed"}
                        or value["baseCommit"] == value["targetCommit"]):
                    raise ValueError("Invalid update transaction")
                prepared = PreparedUpdate.model_validate(value["prepared"])
                if (prepared.targetRevision != value["targetCommit"]
                        or prepared.targetVersion != value["targetCommit"][:12] + "-desktop"
                        or not re.fullmatch(r"incoming-[A-Za-z0-9_-]+", str(value.get("patch", "")))):
                    raise ValueError("Invalid update state")
                if type(value.get("launchAttempts", 0)) is not int or value.get("launchAttempts", 0) < 0:
                    raise ValueError("Invalid update launch count")
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
        if self.supported and self._record.get("state") == "activated" and self._record.get("targetCommit") == self.revision:
            # Counted before anything else can fail, so a version that never
            # finishes starting is recognised by the next start of either one.
            try:
                self._save({**self._record, "launchAttempts": self._record.get("launchAttempts", 0) + 1})
            except OSError:
                _log.warning("Could not count this start of a new desktop version.")
        try:
            check = json.loads(self._check_path.read_text(encoding="utf-8"))
            self._check: dict = check if isinstance(check, dict) else {}
        except (OSError, ValueError):
            self._check = {}

    def _save(self, record: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        for attempt in range(1, 11):
            try:
                temporary.replace(self._state_path)
                break
            except PermissionError:
                # A scanner or indexer can hold the file open for a moment on
                # Windows, which refuses to replace it until it is released.
                if attempt == 10:
                    raise
                time.sleep(0.05 * attempt)
        self._record = record

    def _write_check(self, check: dict) -> None:
        self._check = check
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self._check_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(check, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(self._check_path)
        except OSError as error:
            _log.warning("Could not record the update check: %s", error)

    def _activation_pending(self) -> bool:
        return self._record.get("state") == "applying" and self._record.get("targetCommit") == self.revision

    def _owns_pending(self, record: dict) -> bool:
        """This version still has to finish (or undo) the transaction that installed it."""
        return (record.get("targetCommit") == self.revision and record.get("baseCommit") != self.revision
                and record.get("state") in {"ready", "applying", "activated"})

    def _update_busy(self) -> bool:
        # A version that is finishing its own activation, or whose activation
        # was just undone, does not prepare another one in the same session.
        undone = self._record.get("targetCommit") == self.revision and self._record.get("state") == "failed"
        return bool(self._preparing or self._verifying or self._restart_commit or self._finishing
                    or self._activation_pending() or self._owns_pending(self._record) or undone)

    def _auto_enabled(self) -> bool:
        try:
            return bool(self._preference.enabled())
        except (OSError, ValueError):
            # Unreadable settings keep the documented default.
            return True

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

    def _check_view(self) -> UpdateCheck:
        last = self._check.get("lastCheck") if self._check.get("revision") == self.revision else None
        try:
            view = UpdateCheck.model_validate(last) if isinstance(last, dict) else UpdateCheck()
        except ValidationError:
            view = UpdateCheck()
        if self._progress is not None:
            state, version = self._progress
            view = view.model_copy(update={"state": state, "latestVersion": version or view.latestVersion, "detail": None})
        return view

    def status(self) -> UpdateStatus:
        with self._lock:
            record = self._record
            prepared = None
            if record.get("baseCommit") == self.revision and record.get("prepared"):
                prepared = PreparedUpdate.model_validate(record["prepared"])
            applying = bool(self._verifying or self._restart_commit or self._activation_pending())
            state = "applying" if applying else "preparing" if self._preparing else "failed" if self._error else "ready" if prepared else "idle"
            busy = self._busy() if self.supported and prepared and not applying and not self._preparing else None
            auto = self._auto_enabled() if self.supported else False
            message = busy
            if not self.supported:
                message = "Use the packaged desktop application to apply patches."
            elif self._finishing:
                message = "Finishing the update this start installed."
            elif state == "ready" and record.get("activationError"):
                message = f"The update could not be switched in when the application last closed: {record['activationError']}"
            return UpdateStatus(
                currentRevision=self.revision, currentVersion=self.revision[:12] if self.revision else "development",
                releaseVersion=self.release_version,
                mode="local" if self.supported else "unsupported", state=state, prepared=prepared,
                canApply=bool(self.supported and prepared and not applying and not self._preparing and not busy
                              and not self._writes and not self._finishing),
                message=message, error=self._error,
                channel=CHANNEL if self.supported else None, autoUpdate=auto,
                # Switched in at quit when on; already switched once activated.
                nextLaunch=bool(state == "ready" and (auto or record.get("state") in {"activated", "applied"})),
                check=self._check_view(),
            )

    def begin_upload(self) -> Path:
        with self._lock:
            if not self.supported:
                raise HubFailure(409, "UPDATE_UNSUPPORTED", "Patches require the packaged desktop application.")
            if self._update_busy():
                raise HubFailure(409, "UPDATE_BUSY", "An update is already being prepared or applied.")
            self.directory.mkdir(parents=True, exist_ok=True)
            path = Path(tempfile.mkdtemp(prefix="incoming-", dir=self.directory)) / "patch.zip"
            self._preparing, self._error = True, None
            return path

    def _discard_upload(self, path: Path) -> None:
        folder = path.parent
        # Delete only this service's individual incoming patch directory, never
        # a version, project, linked directory or arbitrary caller-supplied path.
        if (folder.resolve().parent != self.directory.resolve()
                or not re.fullmatch(r"incoming-[A-Za-z0-9_-]+", folder.name)
                or folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction())):
            return
        shutil.rmtree(folder, ignore_errors=True)

    def fail_upload(self, path: Path, detail: str, code: str = "UPDATE_PATCH_INVALID") -> None:
        with self._lock:
            self._preparing = False
            self._error = HubError(code=code, detail=detail)
        self._discard_upload(path)

    def _prepare_patch(self, path: Path, extra: dict | None = None,
                       cancelled: Callable[[], bool] | None = None) -> Exception | None:
        """Stage one received patch beside this version and record it as ready; return what stopped it."""
        from apps.monkeyhub.installer.patch import PatchCancelled, describe_patch, stage_patch
        try:
            metadata = describe_patch(path)
            if metadata["baseCommit"] != self.revision:
                raise ValueError("This patch starts from another installed version.")
            # stage_patch verifies the complete base and every payload byte
            # before it writes anything, then verifies the reconstructed copy.
            target = stage_patch(path, self.source_root, self.source_root.parent, cancelled=cancelled)
            expected = self.source_root.parent / (metadata["targetCommit"][:12] + "-desktop")
            if target != expected:
                raise ValueError("The prepared patch has an unexpected installation location.")
            prepared = PreparedUpdate(
                targetVersion=metadata["targetVersion"], targetRevision=metadata["targetCommit"],
                changedBytes=metadata["payloadBytes"], changedFiles=metadata["changedFiles"],
                removedFiles=metadata["removedFiles"], reusedFiles=metadata["reusedFiles"],
                releaseVersion=(extra or {}).get("releaseVersion"),
            )
            # Keep the exact patch for a last readback before activation.
            with self._lock:
                self._save({"baseCommit": self.revision, "targetCommit": metadata["targetCommit"],
                            "state": "ready", "prepared": prepared.model_dump(), "patch": path.parent.name,
                            **(extra or {})})
            return None
        except PatchCancelled as error:
            self._discard_upload(path)
            return error
        except (OSError, ValueError, KeyError) as error:
            self.fail_upload(path, str(error))
            return error
        finally:
            with self._lock:
                self._preparing = False

    def prepare(self, path: Path) -> None:
        # A patch the user chose is allowed to finish even when the application closes.
        self._worker = threading.Thread(target=self._prepare_patch, args=(path,), name="hub-prepare-update", daemon=True)
        self._worker.start()

    def apply(self) -> UpdateStatus:
        with self._lock:
            if not self.supported or not self._record.get("prepared") or self._record.get("baseCommit") != self.revision:
                raise HubFailure(409, "UPDATE_NOT_READY", "Choose and verify a patch for this application first.")
            if self._preparing or self._verifying or self._restart_commit or self._finishing:
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

    def _activate(self, root: Path | None = None) -> None:
        """Point the desktop entry at ``root`` (default: this version) through its own installer."""
        script = _command_path((root or self.source_root) / INSTALLER)
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
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
                        or self._record.get("state") not in {"applying", "activated", "applied"}):
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
                    self._save({**self._record, "state": "applied", "error": None, "launchAttempts": 0})
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

    # -- Automatic updates over the unsigned prerelease channel --------------

    def _spawn(self, target: Callable, *args, name: str) -> None:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _ensure_checker(self) -> None:
        if not any(thread.name == "hub-update-check" and thread.is_alive() for thread in self._threads):
            self._spawn(self._check_loop, name="hub-update-check")

    def start(self, port: int, instance: str | None) -> None:
        """At startup: finish or undo this version's own activation, and begin automatic checks."""
        with self._lock:
            if not self.supported or self._started:
                return
            self._started = True
            record = dict(self._record)
            finish = self._owns_pending(record)
            abandoned = (record.get("state") == "activated" and record.get("baseCommit") == self.revision
                         and record.get("launchAttempts", 0) > 0)
            self._finishing = finish or abandoned
            if finish:
                self._spawn(self._finish_activation, port, instance, name="hub-finish-update")
            elif abandoned:
                self._spawn(self._restore_abandoned, name="hub-restore-update")
            self._ensure_checker()

    def _own_health(self, port: int, instance: str | None) -> bool | None:
        """True once this process answers its own health route; None when closing."""
        deadline = time.monotonic() + HEALTH_SECONDS
        while not self._stop.is_set():
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                connection.request("GET", "/api/health", headers={"Accept": "application/json"})
                response = connection.getresponse()
                body = response.read(65536)
                if response.status == 200:
                    health = json.loads(body)
                    return bool(health.get("status") == "ok" and health.get("service") == "monkeyhub-api"
                                and health.get("processId") == os.getpid()
                                and health.get("sourceRevision") == self.revision
                                and health.get("managedInstanceId") == instance)
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            if time.monotonic() >= deadline:
                return False
            self._stop.wait(0.5)
        return None

    def _restore_previous(self, record: dict, detail: str) -> None:
        """Point the entry back at the version the transaction started from and record the failure."""
        base = self.source_root.parent / (record["baseCommit"][:12] + "-desktop")
        try:
            if _commit_of(base) != record["baseCommit"] or not (base / "MonkeyHub.exe").is_file():
                raise ValueError("its folder is no longer complete")
            self._activate(base)
        except (HubFailure, OSError, ValueError) as error:
            reason = error.error.detail if isinstance(error, HubFailure) else str(error)
            detail += f" The previous desktop entry could not be restored ({reason}); start it from its version folder."
        error = HubError(code="UPDATE_ROLLED_BACK", detail=detail)
        with self._lock:
            self._restart_commit = None
            self._error = error
            if (self._record.get("baseCommit"), self._record.get("targetCommit")) == (record["baseCommit"], record["targetCommit"]):
                try:
                    self._save({**self._record, "state": "failed", "error": error.model_dump(), "launchAttempts": 0})
                except OSError:
                    _log.warning("Could not record the restored desktop entry.")

    def _finish_activation(self, port: int, instance: str | None) -> None:
        """The started version completes its transaction: health, full verification, entry."""
        from apps.monkeyhub.installer.patch import PatchCancelled, verify_target
        try:
            if self._record.get("state") == "applying":
                # A restart-now trial is completed by its desktop helper.
                deadline = time.monotonic() + TRIAL_GRACE_SECONDS
                while time.monotonic() < deadline and self._record.get("state") == "applying":
                    if self._stop.wait(0.5):
                        return
            with self._activation_lock:
                with self._lock:
                    record = dict(self._record)
                    if not self._owns_pending(record):
                        return
                    if record["state"] == "applying":
                        # Nobody completed this start: reopen writes before checking it.
                        self._save({**record, "state": "activated"})
                        record = dict(self._record)
                label = (record.get("prepared") or {}).get("releaseVersion") or record["targetCommit"][:12]
                if record.get("launchAttempts", 0) > MAX_LAUNCH_ATTEMPTS:
                    self._restore_previous(record, f"MonkeyHub {label} did not finish starting after "
                                                   f"{record['launchAttempts']} attempts. The previous version opens next time.")
                    return
                healthy = self._own_health(port, instance)
                if healthy is None:
                    return
                if not healthy:
                    self._restore_previous(record, f"MonkeyHub {label} did not pass its health check. "
                                                   "The previous version opens next time.")
                    return
                try:
                    verify_target(self.directory / record["patch"] / "patch.zip", self.source_root,
                                  cancelled=self._stop.is_set)
                    self._activate()
                except PatchCancelled:
                    return
                except (HubFailure, OSError, ValueError) as error:
                    reason = error.error.detail if isinstance(error, HubFailure) else str(error)
                    self._restore_previous(record, f"MonkeyHub {label} could not be confirmed ({reason}). "
                                                   "The previous version opens next time.")
                    return
                with self._lock:
                    if self._owns_pending(self._record) and self._record.get("patch") == record["patch"]:
                        self._save({**self._record, "state": "applied", "error": None, "launchAttempts": 0})
                        self._error = None
        except OSError as error:
            _log.warning("The update this start installed could not be recorded: %s", error)
        finally:
            with self._lock:
                self._finishing = False

    def _restore_abandoned(self) -> None:
        """The previous version runs again after its successor started without finishing."""
        try:
            with self._activation_lock:
                with self._lock:
                    record = dict(self._record)
                if record.get("state") == "activated" and record.get("baseCommit") == self.revision:
                    label = (record.get("prepared") or {}).get("releaseVersion") or record["targetCommit"][:12]
                    self._restore_previous(record, f"MonkeyHub {label} started but did not finish its startup check. "
                                                   "This version opens again.")
        finally:
            with self._lock:
                self._finishing = False

    def startup_failed(self, reason: str) -> None:
        """This version could not start: undo an unfinished next-launch activation of it."""
        with self._lock:
            record = dict(self._record)
            pending = self.supported and self._owns_pending(record) and record["state"] in {"ready", "activated"}
        if pending:
            label = (record.get("prepared") or {}).get("releaseVersion") or record["targetCommit"][:12]
            self._restore_previous(record, f"MonkeyHub {label} could not start ({reason}). The previous version opens next time.")

    def activate_on_quit(self) -> None:
        """After a normal quit, switch the desktop entry to a ready update; never restart anything."""
        from apps.monkeyhub.installer.patch import verify_target_layout
        with self._lock:
            record = dict(self._record)
            if (not self.supported or self._restart_commit or self._verifying or self._preparing or self._finishing
                    or record.get("state") != "ready" or record.get("baseCommit") != self.revision):
                return
        if not self._auto_enabled():
            return
        target = self.source_root.parent / (record["targetCommit"][:12] + "-desktop")
        try:
            # Staging hashed every file; recheck the layout and the two files run below.
            verify_target_layout(self.directory / record["patch"] / "patch.zip", target, exact=(INSTALLER, ENTRY))
            if record.get("preflight") != "passed":
                self._preflight(target)
            self._activate(target)
        except (HubFailure, OSError, ValueError) as error:
            reason = error.error.detail if isinstance(error, HubFailure) else str(error)
            _log.warning("The prepared update was not switched in: %s", reason)
            with self._lock:
                if self._record.get("patch") == record["patch"]:
                    try:
                        self._save({**self._record, "activationError": reason[:500]})
                    except OSError:
                        pass
            return
        with self._lock:
            try:
                self._save({**record, "state": "activated", "preflight": "passed", "launchAttempts": 0,
                            "activatedAt": _now(), "activationError": None})
            except OSError as error:
                # The entry already points at the new version, which finishes a
                # still-ready transaction for itself when it starts.
                _log.warning("The switched-in update could not be recorded: %s", error)

    def _preflight(self, target: Path) -> None:
        """The new version's own runtime loads its Hub and prints its options; nothing is started."""
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
        try:
            result = subprocess.run(
                [_command_path(target / "_runtime/python/python.exe"), "-B", _command_path(target / ENTRY), "--help"],
                cwd=_command_path(target), env=environment, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=PREFLIGHT_SECONDS,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"The new version's runtime could not be started: {error}") from error
        if result.returncode or "--runtime-root" not in result.stdout:
            tail = " / ".join((result.stderr or result.stdout).strip().splitlines()[-3:])
            raise ValueError(f"The new version's runtime could not load MonkeyHub: {tail or result.returncode}")

    def set_auto_update(self, enabled: bool) -> UpdateStatus:
        if not self.supported:
            raise HubFailure(409, "UPDATE_UNSUPPORTED", "Automatic updates require the packaged desktop application.")
        with self.mutation():
            self._preference.set(enabled)
        if enabled:
            self._wake.set()
        return self.status()

    def check_now(self) -> UpdateStatus:
        with self._lock:
            if not self.supported:
                raise HubFailure(409, "UPDATE_UNSUPPORTED", "Automatic updates require the packaged desktop application.")
            if self._update_busy():
                raise HubFailure(409, "UPDATE_BUSY", "An update is being prepared, applied or finished. Check again afterwards.")
            self._explicit = True
            self._progress = self._progress or ("checking", None)
            self._ensure_checker()
        self._wake.set()
        return self.status()

    def _check_loop(self) -> None:
        delay = FIRST_CHECK_SECONDS
        while True:
            self._wake.wait(delay)
            if self._stop.is_set():
                return
            self._wake.clear()
            with self._lock:
                explicit, self._explicit = self._explicit, False
            delay = CHECK_INTERVAL_SECONDS
            if (explicit or self._auto_enabled()) and not self._run_check(explicit=explicit):
                # A new version finishing its own start checks again shortly after.
                delay = BUSY_RETRY_SECONDS

    def _run_check(self, *, explicit: bool) -> bool:
        """One check; False when it did not run because an update transaction is busy."""
        with self._lock:
            if self._stop.is_set() or self._update_busy():
                # Never while an update is being prepared, applied or finished.
                self._progress = None
                return False
            self._progress = ("checking", None)
        try:
            outcome = self._check_once(explicit)
        except UpdateCancelled:
            outcome = None
        except UpdateCheckError as error:
            outcome = {"state": "error", "detail": str(error), "latestVersion": error.version,
                       "sticky": not error.transient}
        except Exception as error:  # A background check must never end the checker.
            _log.exception("Automatic update check failed")
            outcome = {"state": "error", "detail": f"The update check failed unexpectedly: {error}"}
        with self._lock:
            self._progress = None
            if outcome is not None:
                self._write_check({**self._check, "revision": self.revision,
                                   "lastCheck": {**outcome, "checkedAt": _now()}})
        return True

    def _feed_or_default(self) -> ReleaseFeed:
        if self._feed is None:
            self._feed = ReleaseFeed(f"MonkeyHub/{self.release_version or 'development'} (+https://github.com/{REPOSITORY})")
        return self._feed

    def _check_once(self, explicit: bool) -> dict | None:
        from apps.monkeyhub.installer.patch import describe_patch
        feed = self._feed_or_default()
        cached = self._check.get("releases") if isinstance(self._check.get("releases"), list) else None
        etag, listed = feed.releases(self._check.get("etag") if cached is not None else None)
        if listed is None:
            releases = cached or []
        else:
            releases = _desktop_releases(listed)
            with self._lock:
                self._write_check({**self._check, "etag": etag, "releases": releases})
        current = _version(self.release_version) or (0, 0, 0)
        known = [row for row in releases if _version(row.get("version")) and isinstance(row.get("assets"), dict)]
        newest = max(known, key=lambda row: _version(row["version"]), default=None)
        newer = [row for row in known if _version(row["version"]) > current]
        if not newer:
            return {"state": "up-to-date", "latestVersion": newest["version"] if newest else None}
        latest = max(newer, key=lambda row: _version(row["version"]))
        version, tag, assets = latest["version"], latest["tag"], latest["assets"]
        page = f"https://github.com/{REPOSITORY}/releases/tag/{quote(tag, safe='')}"
        full = {"state": "needs-full-update", "latestVersion": version, "releaseUrl": page}
        index_name = f"MonkeyHub-{version}-update-index.json"
        if type(assets.get(index_name)) is not int or assets[index_name] > MAX_INDEX_BYTES:
            return {**full, "detail": f"Release {version} has no automatic update data. Install its complete package."}
        index = _update_index(feed.read(tag, index_name, assets[index_name]), version)
        target = index["releaseCommit"]
        with self._lock:
            record = dict(self._record)
            last = self._check.get("lastCheck") if self._check.get("revision") == self.revision else None
        if record.get("baseCommit") == self.revision and record.get("targetCommit") == target:
            if record.get("state") in {"ready", "activated", "applied"}:
                return {"state": "ready", "latestVersion": version, "releaseUrl": page}
            if record.get("state") == "failed" and not explicit:
                return {"state": "error", "latestVersion": version, "releaseUrl": page, "sticky": True,
                        "detail": f"Release {version} did not start correctly here before; choose Check now to try it again."}
        if (not explicit and isinstance(last, dict) and last.get("sticky") and last.get("latestVersion") == version
                and last.get("state") == "error"):
            return {key: value for key, value in last.items() if key != "checkedAt"}
        entry = next((row for row in index["patches"] if row["baseCommit"] == self.revision), None)
        if entry is None:
            return {**full, "detail": f"No update patch for {version} starts from this installed version. "
                                      "Install its complete package."}
        if entry["size"] > MAX_PATCH_BYTES or assets.get(entry["name"]) != entry["size"]:
            return {**full, "detail": f"The update patch for {version} is larger than {MAX_PATCH_BYTES} bytes "
                                      "or differs from its release. Install the complete package."}
        described = index["releaseManifest"]
        if assets.get(described["name"]) != described["size"] or described["size"] > MAX_INDEX_BYTES:
            raise UpdateCheckError(f"Release {version} does not carry the release manifest its update index names.",
                                   version=version)
        manifest_bytes = feed.read(tag, described["name"], described["size"])
        if hashlib.sha256(manifest_bytes).hexdigest() != described["sha256"]:
            raise UpdateCheckError(f"The release manifest of {version} does not match its update index.",
                                   transient=True, version=version)
        manifest = _release_manifest(manifest_bytes, version, target)
        try:
            path = self.begin_upload()
        except HubFailure:
            return None  # A patch the user chose started meanwhile.
        with self._lock:
            self._progress = ("downloading", version)
        try:
            feed.download(tag, entry["name"], path, entry["size"], entry["sha256"], self._stop.is_set)
            summary = describe_patch(path)
            if (summary["baseCommit"], summary["targetCommit"], summary["targetBuildInfoSha256"]) != (
                    self.revision, target, manifest["buildInfo"]["sha256"]):
                raise UpdateCheckError(f"The patch for {version} does not rebuild the release its manifest describes.")
        except UpdateCancelled:
            with self._lock:
                self._preparing = False
            self._discard_upload(path)
            raise
        except (UpdateCheckError, OSError, ValueError) as error:
            refusal = error if isinstance(error, UpdateCheckError) else UpdateCheckError(str(error))
            self.fail_upload(path, str(refusal), code="UPDATE_DOWNLOAD_REFUSED")
            refusal.version = version
            raise refusal from error
        failure = self._prepare_patch(path, {"source": "auto", "releaseVersion": version}, cancelled=self._stop.is_set)
        if failure is not None:
            if self._stop.is_set():
                return None
            # A refused file operation (disk space, a lock that outlasted the
            # wait) is retried by the next check; a patch that does not match
            # this installation is not downloaded again automatically.
            raise UpdateCheckError(str(failure), version=version,
                                   transient=isinstance(failure, OSError) or isinstance(failure.__cause__, OSError))
        prepared_root = self.source_root.parent / (target[:12] + "-desktop")
        try:
            self._preflight(prepared_root)
            passed = True
        except ValueError as error:
            passed, failure = False, HubError(code="UPDATE_PREFLIGHT_FAILED", detail=str(error))
        with self._lock:
            # A restart-now that started meanwhile keeps its own transaction.
            if self._record.get("targetCommit") == target and self._record.get("state") == "ready":
                if passed:
                    self._save({**self._record, "preflight": "passed"})
                else:
                    self._save({**self._record, "state": "failed", "error": failure.model_dump()})
                    self._error = failure
        if not passed:
            raise UpdateCheckError(failure.detail, version=version)
        return {"state": "ready", "latestVersion": version, "releaseUrl": page}

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in list(self._threads):
            if thread.is_alive():
                thread.join()
        if self._worker and self._worker.is_alive():
            self._worker.join()
        with self._lock:
            record = self._record
            # A normal quit is not a failed start of this version.
            if (self.supported and record.get("state") == "activated" and record.get("targetCommit") == self.revision
                    and record.get("launchAttempts")):
                try:
                    self._save({**record, "launchAttempts": 0})
                except OSError:
                    pass


def recover_failed_start(source_root: Path, runtime_root: Path, *, managed: bool, reason: str) -> None:
    """Before the Hub exists: undo an unfinished next-launch activation of this version."""
    try:
        DesktopUpdates(source_root, runtime_root / "updates", managed=managed, busy=lambda: None).startup_failed(reason)
    except Exception:  # Never hide the original startup failure.
        _log.exception("Could not restore the previous desktop entry after a failed start")
