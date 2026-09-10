"""Host-side operation timing and compiler usage in one optional diagnostic log."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from time import perf_counter
from uuid import uuid4

from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent

from .intent_agent import IntentCompiler, Compilation, Selection
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

    def record(
        self, *, phase: str, status: str, started_at: str,
        ended_at: str | None = None, duration_ms: int | None = None,
        timing_scope: str = "service", model_call: bool | None = False,
        provider: str = "none", model: str = "none", tokens: TokenUsage | None = None,
        event_id: str | None = None, project_id: str | None = None,
        run_id: str | None = None, source_ref: str | None = None,
        related_event_id: str | None = None, session_id: str | None = None,
        parent_session_id: str | None = None, turn_id: str | None = None,
        billing_mode: str = "unknown",
    ) -> str | None:
        if self.store is None:
            return None
        try:
            event = UsageEvent(
                event_id=event_id or str(uuid4()), source="studio",
                provider=provider, model=model, phase=phase, status=status,
                started_at=started_at, ended_at=ended_at, duration_ms=duration_ms,
                timing_scope=timing_scope, model_call=model_call,
                tokens=tokens if tokens is not None else TokenUsage(),
                project_id=project_id, run_id=run_id, source_ref=source_ref,
                related_event_id=related_event_id, session_id=session_id,
                parent_session_id=parent_session_id, turn_id=turn_id,
                billing_mode=billing_mode,
            )
            self.store.append(event)
            return event.event_id
        except Exception:
            log.warning("MonkeyMonitor could not record this operation; diagnostics are incomplete.")
            return None

    @contextmanager
    def measure(
        self, phase: str, *, project_id: str | None = None,
        run_id: str | None = None, source_ref: str | None = None,
        related_event_id: str | None = None, event_id: str | None = None,
    ):
        """Measure only the service call; a nested export is detail of this interval.

        The returned association may be completed with an input reference learned
        by the service itself. Both log rows retain the same operation identity.
        """

        association = {
            "event_id": event_id or str(uuid4()), "project_id": project_id,
            "run_id": run_id, "source_ref": source_ref,
            "related_event_id": related_event_id,
        }
        started_at = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        self.record(phase=phase, status="running", started_at=started_at, **association)
        status = "succeeded"
        try:
            yield association
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)) else "failed"
            raise
        finally:
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
        started_at = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        result, receipt, status = None, None, "failed"
        try:
            result = self.compiler.compile(message=message, selection=selection, projection=projection)
            receipt = result.receipt
            status = result.status
            return result
        except BaseException as exc:
            receipt = getattr(exc, "receipt", None)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                status = "cancelled"
            raise
        finally:
            duration_ms = round((perf_counter() - started) * 1000)
            # A deterministic pass calls no model and cannot acquire billed usage.
            if receipt is not None or self.provider != "deterministic":
                try:
                    provider = result.provider if result is not None else self.provider
                    model = getattr(receipt, "model_id", None) or (result.model if result is not None else self.model) or "unknown"
                    counts = {name: getattr(receipt, name, None) for name in (
                        "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
                        "cache_write_1h_input_tokens", "reasoning_output_tokens",
                    )}
                    receipt_id = getattr(receipt, "receipt_id", None)
                    self.monitor.record(
                        event_id=f"studio:model:{receipt_id}" if receipt_id else None, provider=provider,
                        model=model, phase="intent", status=status, started_at=started_at,
                        ended_at=datetime.now(timezone.utc).isoformat(),
                        timing_scope="model_call", model_call=True,
                        duration_ms=duration_ms, tokens=TokenUsage(**counts),
                        billing_mode="api_estimate" if provider == "anthropic" else "unknown",
                        project_id=projection.project_id,
                        run_id=getattr(getattr(projection, "run", None), "run_id", None),
                        source_ref=projection_source_ref(projection),
                    )
                except Exception:
                    # Monitoring is optional; losing diagnostics must never repeat a paid call.
                    log.warning("MonkeyMonitor could not record this invocation; usage is incomplete.")
