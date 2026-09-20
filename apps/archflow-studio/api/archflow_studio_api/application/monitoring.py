"""Host-side operation timing and compiler usage in one optional diagnostic log."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import logging
import inspect
from time import perf_counter
from uuid import uuid4

from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent

from .intent_agent import IntentCompiler, Compilation, Selection, invoke_structured
from .projection import StateProjection

log = logging.getLogger(__name__)


def projection_source_ref(projection: StateProjection) -> str | None:
    """An exact retained input, never an authored filesystem path."""

    if getattr(projection, "reference_state_exact", False):
        return projection.record_source
    stage = getattr(projection, "source_stage_ref", None)
    return None if stage is None else stage.uri


def candidate_event_id(project_id: str, run_id: str) -> str:
    return f"studio:candidate:{project_id}:{run_id}"


class StudioMonitor:
    """One host logger; diagnostic failures never change the operation it observes."""

    def __init__(self, store: UsageLog | None):
        self.store = store
        self._context = ContextVar("studio_operation", default={})

    def current(self):
        return dict(self._context.get())

    @contextmanager
    def scope(self, *, operation_id: str | None = None, parent_event_id: str | None = None,
              turn_id: str | None = None):
        token = self._context.set({"operation_id": operation_id, "event_id": parent_event_id, "turn_id": turn_id})
        try:
            yield
        finally:
            self._context.reset(token)

    def observer(self, *, project_id=None, run_id=None, source_ref=None, parent_event_id=None):
        """Freeze the host association before crossing a callback or worker boundary."""

        current = self.current()
        association = dict(project_id=project_id or current.get("project_id"), run_id=run_id or current.get("run_id"),
                           source_ref=source_ref or current.get("source_ref"),
                           operation_id=current.get("operation_id"), turn_id=current.get("turn_id"),
                           parent_event_id=parent_event_id or current.get("event_id"))

        def observed(row):
            try:
                self.record(**{**association, **dict(row)})
            except (Exception, asyncio.CancelledError):
                log.warning("MonkeyMonitor could not read this operation observation; diagnostics are incomplete.")
        return observed

    def record(
        self, *, phase: str, status: str, started_at: str,
        ended_at: str | None = None, duration_ms: int | None = None,
        timing_scope: str = "service", model_call: bool | None = False,
        provider: str = "none", model: str = "none", tokens: TokenUsage | None = None,
        event_id: str | None = None, project_id: str | None = None,
        run_id: str | None = None, source_ref: str | None = None,
        related_event_id: str | None = None, session_id: str | None = None,
        parent_session_id: str | None = None, turn_id: str | None = None,
        operation_id: str | None = None, parent_event_id: str | None = None, details=None,
        billing_mode: str = "unknown",
    ) -> str | None:
        if self.store is None:
            return None
        try:
            current = self.current()
            if parent_event_id is None:
                parent_event_id = current.get("parent_event_id") if event_id == current.get("event_id") else current.get("event_id")
            event = UsageEvent(
                event_id=event_id or str(uuid4()), source="studio",
                provider=provider, model=model, phase=phase, status=status,
                started_at=started_at, ended_at=ended_at, duration_ms=duration_ms,
                timing_scope=timing_scope, model_call=model_call,
                tokens=tokens if tokens is not None else TokenUsage(),
                project_id=project_id or current.get("project_id"), run_id=run_id or current.get("run_id"),
                source_ref=source_ref or current.get("source_ref"),
                related_event_id=related_event_id, session_id=session_id,
                parent_session_id=parent_session_id, turn_id=turn_id or current.get("turn_id"),
                billing_mode=billing_mode,
                operation_id=operation_id or current.get("operation_id"), parent_event_id=parent_event_id,
                details={} if details is None else details,
            )
            self.store.append(event)
            return event.event_id
        except (Exception, asyncio.CancelledError):
            log.warning("MonkeyMonitor could not record this operation; diagnostics are incomplete.")
            return None

    @contextmanager
    def measure(
        self, phase: str, *, project_id: str | None = None,
        run_id: str | None = None, source_ref: str | None = None,
        related_event_id: str | None = None, event_id: str | None = None,
        operation_id: str | None = None, parent_event_id: str | None = None,
        turn_id: str | None = None, details=None,
        timing_scope: str = "service",
    ):
        """Measure only the service call; a nested export is detail of this interval.

        The returned association may be completed with an input reference learned
        by the service itself. Both log rows retain the same operation identity.
        """

        current = self.current()
        event_id = event_id or str(uuid4())
        association = {
            "event_id": event_id, "project_id": project_id or current.get("project_id"),
            "run_id": run_id or current.get("run_id"), "source_ref": source_ref or current.get("source_ref"),
            "related_event_id": related_event_id,
            "operation_id": operation_id or current.get("operation_id") or event_id,
            "turn_id": turn_id or current.get("turn_id"),
            "parent_event_id": parent_event_id or current.get("event_id"),
            "details": {} if details is None else dict(details), "timing_scope": timing_scope,
        }
        started_at = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        self.record(phase=phase, status="running", started_at=started_at, **association)
        token = self._context.set(association)
        status = "succeeded"
        try:
            yield association
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)) else "failed"
            raise
        finally:
            self._context.reset(token)
            self.record(
                phase=phase, status=status, started_at=started_at,
                ended_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=round((perf_counter() - started) * 1000), **association,
            )


class MonitoredCompiler:
    def __init__(self, compiler: IntentCompiler, store: UsageLog | StudioMonitor):
        self.compiler = compiler
        self.monitor = store if isinstance(store, StudioMonitor) else StudioMonitor(store)
        self.store = self.monitor.store

    @property
    def provider(self):
        return getattr(self.compiler, "provider", "unknown")

    @property
    def model(self):
        return getattr(self.compiler, "model", None)

    def compile(self, *, message: str, selection: Selection, projection: StateProjection) -> Compilation:
        spans = []
        result, receipt = None, None
        with self.monitor.measure(
            "intent_compile", project_id=projection.project_id,
            run_id=getattr(getattr(projection, "run", None), "run_id", None),
            source_ref=projection_source_ref(projection),
        ):
            try:
                options = dict(message=message, selection=selection, projection=projection)
                # Older injected compilers may not implement the optional observer.
                if "operation_observer" in inspect.signature(self.compiler.compile).parameters:
                    options["operation_observer"] = spans.append
                result = self.compiler.compile(**options)
                receipt = result.receipt
                return result
            except BaseException as exc:
                receipt = getattr(exc, "receipt", None)
                raise
            finally:
                self._record_invocations(spans, receipt,
                    provider=result.provider if result is not None else self.provider,
                    model=result.model if result is not None else self.model,
                    success=result is not None and result.status == "compiled")

    def invoke_structured(self, *, request, prompt, schema, images=()):
        """Observe a second consumer through the same provider accounting path."""
        spans = []
        receipt, success = None, False
        try:
            output, receipt = invoke_structured(self.compiler, request=request, prompt=prompt,
                schema=schema, images=images, operation_observer=spans.append)
            success = True
            return output, receipt
        except BaseException as exc:
            receipt = getattr(exc, "receipt", None)
            raise
        finally:
            self._record_invocations(spans, receipt, provider=self.provider, model=self.model, success=success)

    def _record_invocations(self, spans, receipt, *, provider, model, success):
        try:
            model = getattr(receipt, "model_id", None) or model or "unknown"
            counts = {name: getattr(receipt, name, None) for name in (
                "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "cache_write_1h_input_tokens", "reasoning_output_tokens",
            )}
            receipt_id = getattr(receipt, "receipt_id", None)
            for index, span in enumerate(spans):
                # Only the observed provider boundary is model-request time.
                # Expanded context can make multiple calls; each span owns
                # its own reported usage. Legacy observers may only supply
                # the final receipt, which belongs to the final call alone.
                span = dict(span)
                reported_usage = span.pop("usage", None)
                reported_model = span.pop("reported_model", None)
                span_tokens = (TokenUsage(**reported_usage) if reported_usage is not None else
                               TokenUsage(**counts) if index == len(spans) - 1 else TokenUsage())
                details = dict(span.get("details", {}))
                details.setdefault("success", success
                                   if index == len(spans) - 1 else None)
                details.setdefault("validator_pass", None)
                details.setdefault("escalation", None)
                span["details"] = details
                self.monitor.record(
                    **span, event_id=f"studio:model:{receipt_id}" if receipt_id and len(spans) == 1 else None,
                    provider=provider, model=reported_model or model, timing_scope="model_call", model_call=True,
                    tokens=span_tokens,
                    billing_mode="api_estimate" if provider == "anthropic" else "unknown",
                )
            if not spans and receipt is not None and provider != "deterministic":
                # Compatibility for external compilers with a receipt but no
                # observed boundary: preserve usage without inventing duration.
                self.monitor.record(
                    phase="model_usage", status=getattr(receipt, "status", "unknown"),
                    event_id=f"studio:model:{receipt_id}" if receipt_id else None,
                    started_at=datetime.now(timezone.utc).isoformat(), timing_scope="unknown", model_call=True,
                    provider=provider, model=model, tokens=TokenUsage(**counts),
                    details={"success": success,
                             "validator_pass": None, "escalation": None},
                    billing_mode="api_estimate" if provider == "anthropic" else "unknown",
                )
        except Exception:
            # Monitoring is optional; losing diagnostics must never repeat a paid call.
            log.warning("MonkeyMonitor could not record this invocation; usage is incomplete.")
