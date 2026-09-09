"""Translate completed compiler outcomes into optional engineering telemetry."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from time import perf_counter
from uuid import uuid4

from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent

from .intent_agent import IntentCompiler, Compilation, Selection
from .projection import StateProjection

log = logging.getLogger(__name__)


class MonitoredCompiler:
    def __init__(self, compiler: IntentCompiler, store: UsageLog):
        self.compiler = compiler
        self.store = store

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
        except Exception as exc:
            receipt = getattr(exc, "receipt", None)
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
                    self.store.append(UsageEvent(
                        event_id=str(uuid4()), source="studio", provider=provider,
                        model=model, phase="intent", status=status, started_at=started_at,
                        duration_ms=duration_ms, tokens=TokenUsage(**counts),
                        billing_mode="api_estimate" if provider == "anthropic" else "unknown",
                        project_id=projection.project_id,
                    ))
                except Exception:
                    # Monitoring is optional; losing diagnostics must never repeat a paid call.
                    log.warning("MonkeyMonitor could not record this invocation; usage is incomplete.")
