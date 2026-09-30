"""The intent compiler, observed: each model call it makes becomes MonkeyMonitor rows.

Optional - the host wraps its compiler only when ``MONKEYMONITOR_DATA_DIR`` is set - and
metadata only: it never changes a result or repeats a call, and a diagnostic it cannot
write is logged and dropped. It sits beside the compiler it wraps rather than in
``project_runtime.monitoring``, which is infrastructure and does not import the agent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import inspect
import logging

from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage

from ..monitoring import StudioMonitor, projection_source_ref
from .intent_agent import IntentCompiler, Compilation, Selection, invoke_structured
from .projection import StateProjection

log = logging.getLogger(__name__)


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
