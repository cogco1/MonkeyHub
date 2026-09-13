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
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from .codex import bound_codex_sources, iter_codex_events
from .pricing import RateCard, quote
from .store import UsageLog
from .usage import TokenUsage

WEB = Path(__file__).parent / "web"
SHARED_WEB = Path(__file__).resolve().parents[1] / "apps/shared-web/src"
SHARED_ASSETS = {
    "/shared/appearance.js": ("appearance.js", "text/javascript"),
    "/shared/i18n.js": ("i18n.js", "text/javascript"),
    "/shared/browserTranslator.js": ("browserTranslator.js", "text/javascript"),
    "/shared/base.css": ("base.css", "text/css"),
}
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


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MonitorData:
    def __init__(self, data_dir: Path | None = None, codex_sessions: tuple[Path, ...] = (), *,
                 codex_bindings_url: str | None = None, codex_home: Path | None = None):
        self.store = UsageLog(data_dir) if data_dir is not None else None
        self.codex_sessions = codex_sessions
        if codex_bindings_url is not None:
            parsed = urlsplit(codex_bindings_url)
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path != "/api/chat/usage-sources" or parsed.query or parsed.fragment
                    or parsed.port == 0):
                raise ValueError("Codex bindings must use the loopback Hub usage-sources endpoint")
        self.codex_bindings_url = codex_bindings_url
        self.codex_home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()

    def _bound_sources(self, warnings: list[str]) -> tuple[dict[Path, str], dict[str, str]]:
        if self.codex_bindings_url is None:
            return {}, {}
        try:
            # This private Hub projection contains identity fields only. Never
            # follow a redirect or send it through a machine-configured proxy.
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            with opener.open(self.codex_bindings_url, timeout=2) as response:
                body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ValueError("Hub usage bindings response is too large")
            return bound_codex_sources(json.loads(body), self.codex_home, warnings)
        except (OSError, ValueError, UnicodeError):
            warnings.append("Hub 会话用量绑定暂不可读；已有诊断和手选来源仍保留。")
            return {}, {}

    def codex_sources(self) -> dict:
        return {"paths": [path.as_posix() for path in dict.fromkeys(
            Path(value).resolve() for value in self.codex_sessions
        )]}

    def select_codex_sources(self, paths: object) -> dict:
        if not isinstance(paths, list) or any(not isinstance(value, str) for value in paths):
            raise ValueError("paths must be a list of absolute Codex JSONL file paths")
        selected = []
        for value in paths:
            path = Path(value)
            if not path.is_absolute() or not path.is_file():
                raise ValueError("Each Codex source must be an existing absolute file path")
            selected.append(path.resolve())
        self.codex_sessions = tuple(dict.fromkeys(selected))
        return self.codex_sources()

    def snapshot(self) -> dict:
        events, warnings = [], []
        if self.store is not None:
            try:
                events, warnings = self.store.read()
            except (OSError, UnicodeError):
                warnings.append("Studio 用量文件暂时不可读。")
        paths, projects = self._bound_sources(warnings)
        try:
            events.extend(iter_codex_events((*self.codex_sessions, *paths), warnings=warnings,
                                           expected_sessions=paths, manual_sources=self.codex_sessions,
                                           project_ids=projects))
        except (OSError, ValueError, UnicodeError):
            warnings.append("指定的 Codex 会话暂时不可读或包含无效计数。")
        unique = {}
        for event in events:
            previous = unique.get(event.event_id)
            if previous is not None and previous.project_id is not None:
                if event.project_id is not None and event.project_id != previous.project_id:
                    warning = "同一用量记录的项目归属存在冲突；已保留原诊断归属。"
                    if warning not in warnings:
                        warnings.append(warning)
                event = replace(event, project_id=previous.project_id)
            unique[event.event_id] = event
        rows = sorted(unique.values(), key=lambda event: event.started_at, reverse=True)
        if any(event.status in {"counter_discontinuity", "partial_history", "counter_reset_unknown", "last_only"} for event in rows):
            warnings.append("Codex 累计计数存在断点，已保留可识别的单次用量；此汇总不是完整账单。")
        return {"events": _diagnose_operations([event.to_dict() for event in rows]), "warnings": warnings}


def _diagnose_operations(rows: list[dict]) -> list[dict]:
    """Compare observed inputs in this selection, without another history store.

    Matching metadata establishes repeated inputs, not that a retry or a retained
    result was safe to omit. Actual execution and reuse paths remain separate.
    """
    seen, contexts = {}, {}
    for row in sorted(rows, key=lambda event: event["started_at"]):
        if row["source"] != "studio" or row["status"] == "running":
            continue
        phase, details = row["phase"], row["details"]
        if not (phase in {"model_request", "drawing_generate", "drawing.hlr", "geometry_build", "element_production", "model_download", "document_load"}
                or phase.startswith("geometry_export.")):
            continue
        identity = details.get("input_identity")
        if not identity:
            details["duplicate_status"] = "insufficient_input_identity"
            continue
        category = ".".join(phase.split(".")[:2]) if phase.startswith("geometry_export.") else phase
        key = (row.get("project_id"), category, row["provider"], row["model"], json.dumps(identity, sort_keys=True, separators=(",", ":")))
        previous = seen.get(key)
        cache_hit = details.get("cache_status") == "hit" or details.get("execution_path") in {"reused", "retained_drawing"}
        if cache_hit:
            details["duplicate_status"] = "reused_result"
        elif previous is None:
            details["duplicate_status"] = "first_observed_input"
        else:
            # input_equivalent belongs to the producer's selected-source check;
            # this comparison may refer to another historical execution.
            details["comparison_event_id"] = previous["event_id"]
            if phase == "model_request":
                details.update(duplicate_status="same_input_request", duplicate_reason="same_observed_model_inputs",
                               reuse_opportunity="provider_cache_policy_requires_verification")
            elif phase in {"model_download", "document_load"}:
                details.update(duplicate_status="same_asset_request", duplicate_reason="same_asset_identity",
                               reuse_opportunity="browser_cache_transfer_not_observed")
            else:
                details.update(duplicate_status="repeated_execution", duplicate_reason="same_observed_execution_inputs",
                               reuse_opportunity="retained_result_binding_requires_verification")
            if previous["status"] not in {"succeeded", "completed", "compiled"}:
                details["reuse_opportunity"] = "previous_attempt_has_no_verified_result"
        if phase == "model_request" and identity.get("context_digest") and identity.get("provider_fingerprint"):
            context_key = (row.get("project_id"), row["provider"], row["model"], identity["context_digest"], identity["provider_fingerprint"])
            context = contexts.get(context_key)
            if context is not None and previous is None:
                details.update(stable_input_parts=["context_digest", "provider_fingerprint"],
                               opportunity_refs=[context["event_id"]],
                               reuse_opportunity="shared_context_prefix_eligibility_unknown")
            contexts[context_key] = row
        # Failed/cancelled attempts still happened. Their inputs can recur even
        # though they establish no successful result to reuse.
        seen[key] = row
    return rows


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
        elif path == "/api/sources/codex":
            self._send(self.data.codex_sources())
        elif path == "/api/rates":
            self._send(json.loads((Path(__file__).parent / "rates.json").read_text(encoding="utf-8")))
        elif path in SHARED_ASSETS:
            filename, mime = SHARED_ASSETS[path]
            try:
                body = (SHARED_WEB / filename).read_bytes()
            except FileNotFoundError:
                self._send({"error": "Not found"}, 404)
            else:
                self._send(body, content_type=mime + "; charset=utf-8")
        elif path in {"/", "/index.html", "/style.css", "/app.js"}:
            filename = "index.html" if path == "/" else path[1:]
            mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
            self._send((WEB / filename).read_bytes(), content_type=mime[Path(filename).suffix] + "; charset=utf-8")
        else:
            self._send({"error": "Not found"}, 404)

    def do_PUT(self):
        if not self._local_request():
            return
        if urlsplit(self.path).path != "/api/sources/codex":
            self._send({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or set(body) != {"paths"}:
                raise ValueError("Expected an object containing paths")
            self._send(self.data.select_codex_sources(body["paths"]))
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            self._send({"error": str(exc)}, 400)

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
