"""Small loopback-only HTTP UI; no dependency on Studio or its API runtime."""
from __future__ import annotations

from functools import partial
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .codex import iter_codex_events
from .pricing import RateCard, quote
from .store import UsageLog
from .usage import TokenUsage

WEB = Path(__file__).parent / "web"
SERVER_VERSION = "0.1.0"


def _source_revision() -> str | None:
    root = Path(__file__).resolve().parent.parent
    try:
        revision = (root / "source-version.txt").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        try:
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True, timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    except (OSError, UnicodeError):
        return None
    return revision.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", revision) else None


class MonitorData:
    def __init__(self, data_dir: Path | None = None, codex_sessions: tuple[Path, ...] = ()):
        self.store = UsageLog(data_dir) if data_dir is not None else None
        self.codex_sessions = codex_sessions

    def snapshot(self) -> dict:
        events, warnings = [], []
        if self.store is not None:
            try:
                events, warnings = self.store.read()
            except (OSError, UnicodeError):
                warnings.append("Studio 用量文件暂时不可读。")
        for index, path in enumerate(dict.fromkeys(path.resolve() for path in self.codex_sessions)):
            try:
                for event in iter_codex_events((path,)):
                    if event.event_id.startswith("codex:file-0:"):
                        event = replace(event, event_id=event.event_id.replace("codex:file-0:", f"codex:file-{index}:", 1))
                    events.append(event)
            except (OSError, ValueError, UnicodeError):
                warnings.append("指定的 Codex 会话暂时不可读或包含无效计数。")
        unique = {event.event_id: event for event in events}
        rows = sorted(unique.values(), key=lambda event: event.started_at, reverse=True)
        if any(event.status in {"counter_discontinuity", "partial_history", "counter_reset_unknown", "last_only"} for event in rows):
            warnings.append("Codex 累计计数存在断点，已保留可识别的单次用量；此汇总不是完整账单。")
        return {"events": [event.to_dict() for event in rows], "warnings": warnings}


class MonitorHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, data: MonitorData, health: dict, **kwargs):
        self.data = data
        self.health = health
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        pass

    def _send(self, value, status=200, content_type="application/json; charset=utf-8"):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _local_request(self) -> bool:
        host = self.headers.get("Host", "")
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in expected:
            self._send({"error": "Loopback host required"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in expected}:
            self._send({"error": "Same-origin request required"}, 403)
            return False
        return True

    def do_GET(self):
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send(self.health)
        elif path == "/api/events":
            self._send(self.data.snapshot())
        elif path == "/api/rates":
            self._send(json.loads((Path(__file__).parent / "rates.json").read_text(encoding="utf-8")))
        elif path in {"/", "/index.html", "/style.css", "/app.js"}:
            filename = "index.html" if path == "/" else path[1:]
            mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
            self._send((WEB / filename).read_bytes(), content_type=mime[Path(filename).suffix] + "; charset=utf-8")
        else:
            self._send({"error": "Not found"}, 404)

    def do_POST(self):
        if not self._local_request():
            return
        if urlsplit(self.path).path != "/api/quote":
            self._send({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = quote(TokenUsage(**body["usage"]), RateCard(**body["rate"]))
            self._send(result)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            self._send({"error": str(exc)}, 400)


def make_server(
    data: MonitorData, port: int = 8788, *, managed_instance_id: str | None = None,
) -> ThreadingHTTPServer:
    health = {
        "status": "ok", "name": "MonkeyMonitor",
        "managedInstanceId": managed_instance_id, "processId": os.getpid(),
        "parentProcessId": os.getppid(),
        "sourceRevision": _source_revision(), "serverVersion": SERVER_VERSION,
    }
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(MonitorHandler, data=data, health=health))
    # Managed shutdown waits for accepted requests before the child exits.
    server.daemon_threads = managed_instance_id is None
    return server
