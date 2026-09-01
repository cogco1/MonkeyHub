"""Start the built Studio gateway as a durable, local background process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


APP_ROOT = Path(__file__).resolve().parent
HEALTH_URL = "http://127.0.0.1:8765/api/health"
STUDIO_URL = "http://127.0.0.1:8765/"
RUNTIME_ROOT = APP_ROOT / ".generated" / "runtime"


def studio_is_ready() -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=2) as response:  # noqa: S310 - fixed localhost URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False
    return (
        payload.get("schema") == "StudioHealth@1"
        and payload.get("service") == "archflow-studio-gateway"
    )


def ensure_build() -> None:
    if (APP_ROOT / "dist" / "index.html").is_file():
        return
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required to build ArchFlow Studio")
    subprocess.run([npm, "run", "build"], cwd=APP_ROOT, check=True)


def launch() -> int:
    if studio_is_ready():
        print(f"ArchFlow Studio is already running: {STUDIO_URL}")
        return 0

    ensure_build()
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    stdout_path = RUNTIME_ROOT / "gateway.stdout.log"
    stderr_path = RUNTIME_ROOT / "gateway.stderr.log"
    command = [
        sys.executable,
        str(APP_ROOT / "run_server.py"),
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ]

    creation_flags = 0
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_kwargs["start_new_session"] = True

    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=APP_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creation_flags,
            **popen_kwargs,
        )

    for _ in range(40):
        if studio_is_ready():
            (RUNTIME_ROOT / "gateway.pid").write_text(
                str(process.pid),
                encoding="ascii",
            )
            print(f"ArchFlow Studio started: {STUDIO_URL}")
            print(f"Gateway PID: {process.pid}")
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.25)

    if process.poll() is None:
        process.terminate()
    detail = ""
    if stderr_path.is_file():
        detail = "\n".join(
            stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        )
    raise RuntimeError(f"ArchFlow Studio failed to start.\n{detail}".rstrip())


if __name__ == "__main__":
    raise SystemExit(launch())

