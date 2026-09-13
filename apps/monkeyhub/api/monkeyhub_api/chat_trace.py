"""Content-free observations of the existing CLI turn, never a second transcript."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from functools import wraps
from time import perf_counter

from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent
from archflow.contracts.canonical import canonical_digest

log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _optional(observe):
    @wraps(observe)
    def record(*args, **kwargs):
        try:
            return observe(*args, **kwargs)
        except Exception:
            log.warning("MonkeyMonitor could not interpret Hub activity; diagnostics are incomplete.")
    return record


class HubTurnObserver:
    """Measure CLI activity boundaries; these intervals are not pure inference.

    Native Codex JSONL remains its token source. Claude's message usage is
    provider metadata; streamed text, reasoning and transport bodies are never
    passed to the diagnostic store.
    """

    def __init__(self, store: UsageLog, turn_id: str, project_id: str, provider: str, model: str | None):
        self.store, self.turn_id, self.project_id = store, turn_id, project_id
        self.provider, self.model = provider, model or "unknown"
        self.session_id = None
        self.root_id = f"hub:turn:{turn_id}"
        self.spans = {}
        self.tools = set()
        self.round_id = None
        self.rounds = 0
        self.visible = False
        self.context_finished = False
        self.usage_ids = set()
        self.permissions = set()
        self._start(self.root_id, "hub_turn", parent=None, timing_scope="agent_turn")
        self._start(f"{self.root_id}:context", "context_build")

    def _emit(self, identifier, *, status="running", tokens=None, model_call=False, **extra):
        span = self.spans[identifier]
        row = {key: value for key, value in span.items() if key != "clock"}
        if status != "running":
            row.update(ended_at=_now(), duration_ms=round((perf_counter() - span["clock"]) * 1000))
        try:
            self.store.append(UsageEvent(
                event_id=identifier, source="hub", provider=self.provider, model=self.model,
                project_id=self.project_id, session_id=self.session_id, turn_id=self.turn_id,
                operation_id=self.turn_id, status=status, tokens=tokens or TokenUsage(),
                model_call=model_call, **row, **extra,
            ))
        except Exception:
            # A failed profiler cannot repeat a tool or a paid request.
            log.warning("MonkeyMonitor could not record Hub activity; diagnostics are incomplete.")

    def _start(self, identifier, phase, *, parent=True, timing_scope="service", details=None):
        self.spans[identifier] = dict(
            phase=phase, started_at=_now(), clock=perf_counter(), timing_scope=timing_scope,
            parent_event_id=self.root_id if parent is True else parent,
            details={"blocking": True, **(details or {})},
        )
        self._emit(identifier)

    def bind(self, session_id, model=None):
        self.session_id = session_id or self.session_id
        self.model = model or self.model
        self._emit(self.root_id)
        if self.round_id:
            self._emit(self.round_id)

    def ready(self):
        self._emit(f"{self.root_id}:context", status="succeeded")
        self.context_finished = True
        self.waiting()

    def waiting(self):
        if self.round_id is not None or self.tools or self.permissions:
            return
        self.rounds += 1
        self.round_id = f"{self.root_id}:round:{self.rounds}"
        self._start(self.round_id, "provider_round", details={"provider_timing_basis": "agent_activity_interval"})

    def _end_round(self, status="succeeded"):
        if self.round_id is not None:
            self._emit(self.round_id, status=status)
            self.round_id = None

    def first_response(self):
        if self.round_id and "first_token_ms" not in self.spans[self.round_id]["details"]:
            self.spans[self.round_id]["details"]["first_token_ms"] = round((perf_counter() - self.spans[self.round_id]["clock"]) * 1000)
            self._emit(self.round_id)
        if not self.visible:
            self.visible = True
            identifier = f"{self.root_id}:response"
            self._start(identifier, "first_response", details={"blocking": False})
            self._emit(identifier, status="succeeded")

    @_optional
    def tool(self, identifier, name, arguments, *, running, failed=False, result=None, candidate_id=None):
        identifier = f"hub:tool:{self.turn_id}:{identifier}"
        # Only documented names/codes are diagnostic labels. A CLI tool title
        # may contain a command or private text; it is not a safe tool name.
        safe_name = name if name in {"studio_schema", "studio_request", "fab_request"} else "agent_tool"
        arguments = arguments if isinstance(arguments, dict) else {}
        method, path = arguments.get("method", "GET"), arguments.get("path", "")
        request_kind = ("schema_read" if safe_name == "studio_schema" else
                        "state_read" if method == "GET" and path in {"/api/state", "/api/state/frame"} else
                        "readback" if method == "GET" and isinstance(path, str) and path.startswith("/api/candidates/") else
                        "mutation" if method in {"POST", "PUT", "DELETE"} else "tool")
        if identifier not in self.spans:
            details = {"tool_name": safe_name, "request_kind": request_kind, "input_bytes": _size(arguments),
                       "input_identity": {"context_digest": canonical_digest({"tool": safe_name, "arguments": arguments})}}
            self._end_round()
            self.tools.add(identifier)
            self._start(identifier, "tool_call", details=details)
        elif identifier not in self.tools:
            return  # Replayed completed/started notifications do not reopen it.
        if not running:
            details = self.spans[identifier]["details"]
            if result is not None:
                details["output_bytes"] = _size(result)
            if candidate_id and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", candidate_id):
                details["output_refs"] = [candidate_id]
            self._emit(identifier, status="failed" if failed else "succeeded")
            self.tools.discard(identifier)
            self.waiting()

    def permission(self, identifier, *, completed=False):
        identifier = f"{self.root_id}:permission:{identifier}"
        if completed:
            if identifier in self.permissions:
                self._emit(identifier, status="succeeded")
                self.permissions.discard(identifier)
                self.waiting()
        elif identifier not in self.permissions:
            self._end_round()
            self.permissions.add(identifier)
            self._start(identifier, "permission_wait", details={"wait_reason": "human_permission"})

    @_optional
    def claude_usage(self, message):
        """One finalized assistant message, deduplicated by native message id."""
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            return
        identifier = message.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[\w-]{1,160}", identifier) or identifier in self.usage_ids:
            return
        usage = message["usage"]
        def count(name):
            value = usage.get(name)
            return value if type(value) is int and value >= 0 else None
        ordinary, cached, write = (count(name) for name in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        # Claude reports ordinary input separately from cache buckets. Missing
        # buckets remain unknown, rather than silently completing a total.
        total = ordinary + cached + write if all(x is not None for x in (ordinary, cached, write)) else None
        cache = usage.get("cache_creation", {})
        hour = cache.get("ephemeral_1h_input_tokens") if isinstance(cache, dict) else None
        if write == 0:
            hour = 0
        try:
            tokens = TokenUsage(input_tokens=total, cached_input_tokens=cached, cache_write_input_tokens=write,
                                cache_write_1h_input_tokens=hour, output_tokens=count("output_tokens"))
        except (ValueError, TypeError):
            return
        self.usage_ids.add(identifier)
        event_id = f"{self.root_id}:usage:{identifier}"
        self.spans[event_id] = dict(phase="model_usage", started_at=_now(), clock=perf_counter(),
                                   timing_scope="unknown", parent_event_id=self.round_id or self.root_id, details={})
        if isinstance(message.get("model"), str):
            self.model = message["model"]
        self._emit(event_id, status="succeeded", tokens=tokens, model_call=True)

    def finish(self, status):
        if not self.context_finished:
            self._emit(f"{self.root_id}:context", status=status)
        self._end_round(status)
        for identifier in self.tools:
            self._emit(identifier, status="cancelled" if status == "cancelled" else "interrupted")
        self.tools.clear()
        for identifier in self.permissions:
            self._emit(identifier, status="cancelled")
        self.permissions.clear()
        identifier = f"{self.root_id}:final"
        self._start(identifier, "final_response")
        self._emit(identifier, status=status)
        self._emit(self.root_id, status=status)
