"""Where the running Hub listens, written where any local process can read it (gap 44).

The desktop shell starts the Hub on a free port it picks again at every start, and
the runtime lease (``hub.lock``) is held exclusively, so a process the Hub did not
start, such as an external agent's MCP connection, could not find the Hub again
after a restart. Once it listens, the Hub writes ``runtime/hub.json`` under its
runtime root: its URL, process id, managed instance id and start time, replaced in
one step, and removes it when it quits normally. A reader checks the Hub's own
``/api/health`` against the file before using it, so a file a crashed Hub left
behind is never taken for a running Hub, and a port another program now holds is
never called as the Hub.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
import urllib.request

from ..models import HubFailure

LOCATOR = Path("runtime") / "hub.json"
SCHEMA = "MonkeyHubLocator@1"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def default_runtime_root() -> Path | None:
    """The runtime root a Hub uses when it is given none: ``%LOCALAPPDATA%\\MonkeyHub``."""

    local = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(local) / "MonkeyHub" if local and Path(local).is_absolute() else None


def locator_path(runtime_root: Path | str | None = None) -> Path | None:
    root = Path(runtime_root) if runtime_root is not None else default_runtime_root()
    return None if root is None else root / LOCATOR


def write(runtime_root: Path, *, url: str, managed_instance_id: str | None) -> Path:
    """Say where this Hub listens, replacing the file in one step so no reader sees half of it."""

    path = Path(runtime_root) / LOCATOR
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"schema": SCHEMA, "url": url, "processId": os.getpid(), "managedInstanceId": managed_instance_id,
              "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    temporary = path.with_name(f".hub.json.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(temporary, path)
    return path


def remove(runtime_root: Path) -> None:
    """Take back the file this process wrote; one a later Hub wrote stays."""

    path = Path(runtime_root) / LOCATOR
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(record, dict) and record.get("processId") == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


def locate(runtime_root: Path | str | None = None, *, timeout: float = 3.0) -> str:
    """The URL of the Hub running for this runtime root, checked against the Hub's own health answer.

    Refused with HUB_NOT_RUNNING when no Hub says where it is or the one named
    does not answer as itself, and with HUB_NOT_FOUND when the file cannot be
    read: each says what to do, so a caller never guesses a port.
    """

    path = locator_path(runtime_root)
    if path is None:
        raise HubFailure(503, "HUB_NOT_FOUND", "No runtime root to look in: LOCALAPPDATA is not set. Pass --hub-url "
                                               "or --runtime-root.")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HubFailure(503, "HUB_NOT_RUNNING", f"MonkeyHub is not running: {path} does not exist. Start MonkeyHub, "
                                                 "or pass --hub-url.") from None
    except (OSError, ValueError) as exc:
        raise HubFailure(503, "HUB_NOT_FOUND", f"{path} cannot be read ({exc}).") from None
    url = record.get("url") if isinstance(record, dict) else None
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme != "http" or parts.hostname not in _LOOPBACK or parts.port is None:
        raise HubFailure(503, "HUB_NOT_FOUND", f"{path} names no local Hub address.")
    stale = (f"MonkeyHub is not running at {url}, the address {path} names: the Hub that wrote it has stopped. "
             "Start MonkeyHub, or pass --hub-url.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{url}/api/health", timeout=timeout) as response:
            health = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        raise HubFailure(503, "HUB_NOT_RUNNING", stale) from None
    if (not isinstance(health, dict) or health.get("service") != "monkeyhub-api"
            or health.get("processId") != record.get("processId")
            or health.get("managedInstanceId") != record.get("managedInstanceId")):
        raise HubFailure(503, "HUB_NOT_RUNNING", stale)
    return f"http://{parts.netloc}"
