"""Request-scoped access to this Hub snapshot's Fab CLI; it owns all model output."""

import json
import os
from pathlib import Path
import subprocess
import sys

from pydantic import TypeAdapter

from .models import (
    FabPrepareRequest, FabPrepareResult, FabProfile, FabSendRequest, FabSendResult,
    HubFailure,
)

ACCESS_CODE_ENV = "MONKEYFAB_HUB_ACCESS_CODE"


def available(source_root: Path) -> bool:
    return (source_root / "apps/monkeyfab/src/monkeyfab/__main__.py").is_file()


class Fabrication:
    def __init__(self, source_root: Path):
        self.source_root = source_root

    def _run(self, arguments: list[str], *, access_code: str | None = None) -> str:
        if not available(self.source_root):
            raise HubFailure(503, "FAB_UNAVAILABLE", "MonkeyFab is not included in this Python environment. Use the integrated application package.")
        environment = os.environ.copy()
        environment.pop("BAMBU_ACCESS_CODE", None)
        environment.pop(ACCESS_CODE_ENV, None)
        environment["PYTHONUTF8"] = "1"
        # Source launchers' sys.path changes do not reach a fresh interpreter.
        # Always prefer the Fab code in this Hub snapshot over another install.
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(self.source_root / "apps/monkeyfab/src"), environment.get("PYTHONPATH"),
        )))
        if access_code is not None:
            environment[ACCESS_CODE_ENV] = access_code
        try:
            result = subprocess.run(
                [sys.executable, "-m", "monkeyfab", *arguments],
                cwd=self.source_root, env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                shell=False, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except ValueError:
            raise HubFailure(422, "FAB_REQUEST_INVALID", "The fabrication arguments contain an invalid value.") from None
        except OSError:
            raise HubFailure(503, "FAB_START_FAILED", "The installed MonkeyFab CLI could not start.") from None
        # The CLI already omits credentials. Keep its captured output request-local
        # and redact the supplied secret even if a dependency unexpectedly echoes it.
        stdout, stderr = result.stdout, result.stderr
        if access_code:
            stdout = stdout.replace(access_code, "[redacted]")
            stderr = stderr.replace(access_code, "[redacted]")
        if result.returncode:
            if result.returncode == 2:
                detail = stderr.strip() or "MonkeyFab rejected the request. Check the input file and options."
                raise HubFailure(422, "FAB_COMMAND_FAILED", detail)
            raise HubFailure(502, "FAB_COMMAND_FAILED", "MonkeyFab could not complete the operation. Check its installed dependencies and the selected input.")
        return stdout

    @staticmethod
    def _json_result(stdout: str, result_type):
        try:
            return TypeAdapter(result_type).validate_python(json.loads(stdout), strict=True)
        except ValueError:
            raise HubFailure(502, "FAB_OUTPUT_INVALID", "MonkeyFab returned an unreadable result.") from None

    def profiles(self) -> dict[str, FabProfile]:
        return self._json_result(self._run(["profiles", "--json"]), dict[str, FabProfile])

    def prepare(self, body: FabPrepareRequest) -> FabPrepareResult:
        stdout = self._run([
            "prepare", body.source, "--output", body.outputDir,
            f"--printer={body.printer}", f"--input-unit={body.inputUnit}",
            f"--scale={body.scale}", f"--xy-margin={body.xyMarginMm}",
            f"--z-clearance={body.zClearanceMm}",
        ])
        return FabPrepareResult(stdout=stdout, outputDir=body.outputDir)

    def send(self, body: FabSendRequest) -> FabSendResult:
        access_code = body.accessCode.get_secret_value() if body.accessCode is not None else None
        if not body.dryRun and (not access_code or any(character.isspace() for character in access_code)):
            raise HubFailure(422, "FAB_ACCESS_CODE_REQUIRED", "Enter the printer access code for this upload, or use dry run to validate the local file.")
        arguments = [
            "send", body.source, f"--host={body.host}", f"--timeout={body.timeout}",
            "--access-code-env", ACCESS_CODE_ENV, "--json",
        ]
        if body.remoteName is not None:
            arguments.append(f"--remote-name={body.remoteName}")
        if body.dryRun:
            arguments.append("--dry-run")
        return self._json_result(
            self._run(arguments, access_code=None if body.dryRun else access_code), FabSendResult,
        )
