"""Reported provider usage survives success, clarification and failed calls."""

import json
import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from archflow.ports.model import (
    ModelInvocationReceipt, ModelInvocationRequest, ModelInvocationStatus, ModelPhase,
)
from archflow_studio_api.application import intent_agent
from archflow_studio_api.application.intent_agent import (
    AnthropicCompiler, CodexCompiler, IntentAgentFailed, Selection,
)


ANSWER = {
    "status": "compiled", "targetComponentId": "portico",
    "elementId": "portico-base", "utterance": "set height to 0.8",
    "semanticEdit": None, "why": "Requested height.", "question": None,
}
CODEX_USAGE = {
    "input_tokens": 150, "cached_input_tokens": 100,
    "output_tokens": 40, "reasoning_output_tokens": 25,
}
ANTHROPIC_USAGE = {
    "input_tokens": 50, "cache_read_input_tokens": 100,
    "cache_creation_input_tokens": 30, "output_tokens": 40,
    "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 20},
}
COUNTERS = (
    "cached_input_tokens", "cache_write_input_tokens",
    "cache_write_1h_input_tokens", "reasoning_output_tokens",
)


class ModelUsageReceiptTests(unittest.TestCase):
    def receipt(self, **usage):
        request = ModelInvocationRequest.create(
            request_id="usage-request", phase=ModelPhase.INTENT_COMPILATION,
            checkpoint_digest="a" * 64, context_digest="b" * 64, payload={},
        )
        return ModelInvocationReceipt(
            receipt_id="usage-receipt", status=ModelInvocationStatus.TIMEOUT,
            request=request, provider_id="test", model_id="test-model",
            provider_version="test-1", provider_fingerprint="f" * 64,
            input_bytes=25, output_bytes=0, output_sha256=None,
            error_code="model.timeout", **usage,
        )

    def test_retained_v2_serializes_without_new_fields(self):
        receipt = self.receipt(input_tokens=150, output_tokens=40)
        wire = receipt.to_dict()
        self.assertEqual(wire["schema"], "ModelInvocationReceipt@2")
        self.assertFalse(set(COUNTERS).intersection(wire))
        self.assertEqual(ModelInvocationReceipt.from_dict(wire).to_dict(), wire)

    def test_extended_usage_round_trips_as_v3(self):
        receipt = self.receipt(
            input_tokens=180, output_tokens=40, cached_input_tokens=100,
            cache_write_input_tokens=30, cache_write_1h_input_tokens=10,
            reasoning_output_tokens=25,
        )
        wire = receipt.to_dict()
        self.assertEqual(wire["schema"], "ModelInvocationReceipt@3")
        self.assertEqual(ModelInvocationReceipt.from_dict(wire).to_dict(), wire)

    def test_v3_with_unknown_counters_preserves_its_serialized_identity(self):
        wire = self.receipt().to_dict()
        wire.update(schema="ModelInvocationReceipt@3", **dict.fromkeys(COUNTERS))
        self.assertEqual(ModelInvocationReceipt.from_dict(wire).to_dict(), wire)

    def test_v1_keeps_existing_upgrade_to_v2(self):
        wire = self.receipt().to_dict()
        wire["schema"] = "ModelInvocationReceipt@1"
        wire.pop("duration_ms")
        restored = ModelInvocationReceipt.from_dict(wire).to_dict()
        self.assertEqual(restored, {**wire, "schema": "ModelInvocationReceipt@2", "duration_ms": 0})

    def test_counter_types_and_wire_versions_are_checked(self):
        for name in COUNTERS:
            for value in (-1, 1.5, True, "5"):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    self.receipt(**{name: value})
        wire = self.receipt().to_dict()
        wire["cached_input_tokens"] = 1
        with self.assertRaises(ValueError):
            ModelInvocationReceipt.from_dict(wire)
        wire["schema"] = "ModelInvocationReceipt@3"
        with self.assertRaises(ValueError):
            ModelInvocationReceipt.from_dict(wire)


class ProviderUsageTests(unittest.TestCase):
    def setUp(self):
        self.projection = SimpleNamespace(state_digest="a" * 64, record_digest="b" * 64)
        self.selection = Selection("portico", "portico-base")
        sheet = patch.object(intent_agent, "record_sheet", return_value={})
        sheet.start()
        self.addCleanup(sheet.stop)

    def invoke(self, compiler, operation_observer=None):
        return compiler.compile(
            message="set height to 0.8", selection=self.selection, projection=self.projection,
            operation_observer=operation_observer,
        )

    def codex(self, answer=ANSWER, *, events=None, returncode=0, stderr="", timeout=False, operation_observer=None):
        with patch.object(intent_agent, "_codex_version", return_value="test-codex-1"):
            compiler = CodexCompiler(model="configured-alias")
        if events is None:
            events = [{"type": "turn.completed", "usage": CODEX_USAGE, "model": "reported-exact-model"}]
        stdout = "\n".join(json.dumps(event) for event in events)

        def run(command, prompt, timeout_s):
            self.assertIn("--json", command)
            if timeout:
                raise subprocess.TimeoutExpired(command, timeout_s, output=stdout.encode(), stderr=stderr)
            raw = json.dumps(answer) if isinstance(answer, dict) else answer
            Path(command[command.index("-o") + 1]).write_text(raw, encoding="utf-8")
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        with patch.object(intent_agent, "_run_bounded", side_effect=run):
            return self.invoke(compiler, operation_observer)

    def anthropic(self, answer=ANSWER, *, usage=ANTHROPIC_USAGE, error=None, operation_observer=None):
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(answer) if isinstance(answer, dict) else answer)],
            model="reported-exact-model", usage=usage,
        )
        create = Mock(return_value=response, side_effect=error)
        sdk = SimpleNamespace(Anthropic=Mock(return_value=SimpleNamespace(messages=SimpleNamespace(create=create))))
        with patch.object(intent_agent, "_anthropic_sdk", return_value=(sdk, "test-sdk-1")):
            compiler = AnthropicCompiler(model="configured-alias")
        return self.invoke(compiler, operation_observer)

    def assert_codex_usage(self, receipt):
        for name, count in CODEX_USAGE.items():
            self.assertEqual(getattr(receipt, name), count)
        self.assertIsNone(receipt.cache_write_input_tokens)
        self.assertEqual(receipt.model_id, "reported-exact-model")

    def assert_anthropic_usage(self, receipt):
        self.assertEqual(receipt.input_tokens, 180)
        self.assertEqual(receipt.output_tokens, 40)
        self.assertEqual(receipt.cached_input_tokens, 100)
        self.assertEqual(receipt.cache_write_input_tokens, 30)
        self.assertEqual(receipt.cache_write_1h_input_tokens, 10)
        self.assertIsNone(receipt.reasoning_output_tokens)
        self.assertEqual(receipt.model_id, "reported-exact-model")

    def test_codex_success_uses_terminal_counters_and_exact_reported_model(self):
        compilation = self.codex()
        self.assert_codex_usage(compilation.receipt)
        self.assertEqual(compilation.model, "reported-exact-model")
        self.assertEqual(compilation.receipt.output_bytes, len(json.dumps(ANSWER).encode()))

    def test_codex_preserves_usage_on_clarification_and_unsupported(self):
        for status in ("question", "unsupported"):
            answer = {**ANSWER, "status": status, "utterance": None, "question": "Which height?" if status == "question" else None}
            with self.subTest(status=status):
                compilation = self.codex(answer)
                self.assertEqual(compilation.status, status)
                self.assert_codex_usage(compilation.receipt)

    def test_codex_preserves_usage_on_malformed_output(self):
        with self.assertRaises(IntentAgentFailed) as caught:
            self.codex("invalid output")
        self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
        self.assert_codex_usage(caught.exception.receipt)

    def test_codex_preserves_usage_on_nonzero_exit(self):
        with self.assertRaises(IntentAgentFailed) as caught:
            self.codex(returncode=1, events=[{"type": "turn.failed", "usage": CODEX_USAGE, "model": "reported-exact-model"}])
        self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.EXIT_ERROR)
        self.assert_codex_usage(caught.exception.receipt)

    def test_codex_preserves_reported_usage_when_process_later_times_out(self):
        with self.assertRaises(IntentAgentFailed) as caught:
            self.codex(timeout=True)
        self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.TIMEOUT)
        self.assert_codex_usage(caught.exception.receipt)

    def test_codex_excludes_message_text_stderr_and_progress_counters(self):
        noise = {"type": "turn.completed", "usage": CODEX_USAGE, "model": "invented-by-user"}
        compilation = self.codex(
            events=[
                {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(noise)}, "usage": CODEX_USAGE},
                {"type": "turn.started", "usage": CODEX_USAGE},
            ],
            stderr=json.dumps(noise),
        )
        receipt = compilation.receipt
        self.assertIsNone(receipt.input_tokens)
        self.assertIsNone(receipt.output_tokens)
        self.assertIsNone(receipt.cached_input_tokens)
        self.assertEqual(receipt.model_id, "configured-alias")
        self.assertEqual(receipt.to_dict()["schema"], "ModelInvocationReceipt@2")

    def test_codex_terminal_reports_are_not_added_twice(self):
        events = [{"type": "turn.completed", "usage": CODEX_USAGE, "model": "reported-exact-model"}] * 2
        self.assert_codex_usage(self.codex(events=events).receipt)

    def test_codex_partial_and_invalid_usage_stays_unknown(self):
        receipt = self.codex(events=[{"type": "turn.completed", "usage": {"input_tokens": True, "output_tokens": 8}}]).receipt
        self.assertIsNone(receipt.input_tokens)
        self.assertEqual(receipt.output_tokens, 8)
        self.assertIsNone(receipt.cached_input_tokens)
        self.assertIsNone(receipt.reasoning_output_tokens)

    def test_codex_preserves_reported_cache_writes(self):
        for written, one_hour in ((20, 5), (0, None), (20, None)):
            with self.subTest(written=written, one_hour=one_hour):
                usage = {**CODEX_USAGE, "cache_write_input_tokens": written}
                if one_hour is not None:
                    usage["cache_write_1h_input_tokens"] = one_hour
                receipt = self.codex(events=[{"type": "turn.completed", "usage": usage}]).receipt
                self.assertEqual(receipt.cache_write_input_tokens, written)
                self.assertEqual(receipt.cache_write_1h_input_tokens, 0 if written == 0 else one_hour)

    def test_anthropic_success_normalizes_input_and_uses_exact_model(self):
        compilation = self.anthropic()
        self.assert_anthropic_usage(compilation.receipt)
        self.assertEqual(compilation.model, "reported-exact-model")

    def test_anthropic_preserves_usage_on_clarification_and_malformed_output(self):
        answer = {**ANSWER, "status": "question", "utterance": None, "question": "Which height?"}
        self.assert_anthropic_usage(self.anthropic(answer).receipt)
        with self.assertRaises(IntentAgentFailed) as caught:
            self.anthropic("invalid output")
        self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.MALFORMED)
        self.assert_anthropic_usage(caught.exception.receipt)

    def test_anthropic_failure_preserves_explicit_usage_and_unknown_stays_unknown(self):
        error = RuntimeError("provider failure")
        error.body = {"usage": ANTHROPIC_USAGE, "model": "reported-exact-model"}
        with self.assertRaises(IntentAgentFailed) as caught:
            self.anthropic(error=error)
        self.assert_anthropic_usage(caught.exception.receipt)
        with self.assertRaises(IntentAgentFailed) as caught:
            self.anthropic(error=RuntimeError("no response"))
        self.assertIsNone(caught.exception.receipt.input_tokens)
        self.assertIsNone(caught.exception.receipt.output_tokens)

    def test_anthropic_old_no_cache_response_and_missing_usage(self):
        receipt = self.anthropic(usage=SimpleNamespace(input_tokens=50, output_tokens=40)).receipt
        self.assertEqual((receipt.input_tokens, receipt.cached_input_tokens, receipt.cache_write_input_tokens), (50, 0, 0))
        for usage in (None, {}, {"output_tokens": 40}):
            with self.subTest(usage=usage):
                receipt = self.anthropic(usage=usage).receipt
                self.assertIsNone(receipt.input_tokens)
                self.assertIsNone(receipt.cached_input_tokens)
                self.assertIsNone(receipt.cache_write_input_tokens)

    def test_anthropic_unreported_cache_duration_is_unknown(self):
        receipt = self.anthropic(usage={key: value for key, value in ANTHROPIC_USAGE.items() if key != "cache_creation"}).receipt
        self.assertEqual(receipt.input_tokens, 180)
        self.assertIsNone(receipt.cache_write_1h_input_tokens)

    def test_model_span_measures_request_and_retains_only_existing_input_identities(self):
        for provider in (self.codex, self.anthropic):
            with self.subTest(provider=provider.__name__):
                spans = []
                compilation = provider(operation_observer=spans.append)
                self.assertEqual(len(spans), 1)
                span = spans[0]
                self.assertEqual(span["phase"], "model_request")
                self.assertEqual(span["status"], "succeeded")
                self.assertEqual(span["duration_ms"], compilation.receipt.duration_ms)
                self.assertLessEqual(span["started_at"], span["ended_at"])
                self.assertIsNone(span["details"]["model_inference_ms"])
                self.assertEqual(span["details"]["request_kind"], provider.__name__ + ("_cli" if provider == self.codex else "_api"))
                self.assertEqual(span["details"]["input_identity"], {
                    "context_digest": compilation.receipt.request.context_digest,
                    "prompt_sha256": compilation.prompt_sha256,
                    "provider_fingerprint": compilation.receipt.provider_fingerprint,
                })
                self.assertEqual(span["details"]["comparison_refs"], [compilation.receipt.request.request_id])
                serialized = json.dumps(span)
                self.assertNotIn("set height", serialized)
                self.assertNotIn("Requested height", serialized)
                self.assertNotIn("output_json", serialized)

    def test_anthropic_request_duration_excludes_sdk_client_construction(self):
        spans = []
        with patch.object(intent_agent.time, "perf_counter", side_effect=[10.0, 12.0, 12.75]):
            compilation = self.anthropic(operation_observer=spans.append)
        self.assertEqual(compilation.receipt.duration_ms, 750)
        self.assertEqual(spans[0]["duration_ms"], 750)

    def test_model_span_reports_failed_request_and_survives_malformed_answer(self):
        spans = []
        with self.assertRaises(IntentAgentFailed):
            self.codex(timeout=True, operation_observer=spans.append)
        self.assertEqual(spans[0]["status"], "failed")
        spans.clear()
        with self.assertRaises(IntentAgentFailed):
            self.anthropic("not-json", operation_observer=spans.append)
        self.assertEqual(spans[0]["status"], "succeeded")

    def test_repeated_model_input_has_the_same_identity_but_distinct_request_refs(self):
        spans = []
        self.codex(operation_observer=spans.append)
        self.codex(operation_observer=spans.append)
        self.assertEqual(spans[0]["details"]["input_identity"], spans[1]["details"]["input_identity"])
        self.assertNotEqual(spans[0]["details"]["comparison_refs"], spans[1]["details"]["comparison_refs"])

    def test_failed_observer_cannot_change_or_repeat_a_model_request(self):
        for error in (RuntimeError("diagnostics unavailable"), asyncio.CancelledError()):
            def corrupt_then_fail(span):
                span["duration_ms"] = "invalid"
                span["details"]["input_identity"].clear()
                raise error

            with self.subTest(error=type(error).__name__):
                observer = Mock(side_effect=corrupt_then_fail)
                self.assertEqual(self.codex(operation_observer=observer).status, "compiled")
                observer.assert_called_once()
                observer.reset_mock()
                self.assertEqual(self.anthropic(operation_observer=observer).status, "compiled")
                observer.assert_called_once()

    def test_sdk_initialization_failure_does_not_invent_a_request_span(self):
        sdk = SimpleNamespace(Anthropic=Mock(side_effect=RuntimeError("client initialization failed")))
        with patch.object(intent_agent, "_anthropic_sdk", return_value=(sdk, "test-sdk-1")):
            compiler = AnthropicCompiler(model="configured-alias")
        spans = []
        with self.assertRaises(IntentAgentFailed) as caught:
            self.invoke(compiler, spans.append)
        self.assertEqual(caught.exception.receipt.status, ModelInvocationStatus.EXIT_ERROR)
        self.assertEqual(spans, [])

    def test_deterministic_compilation_has_no_model_request_span(self):
        spans = []
        compilation = self.invoke(intent_agent.DeterministicCompiler(), spans.append)
        self.assertIsNone(compilation.receipt)
        self.assertEqual(spans, [])


if __name__ == "__main__":
    unittest.main()
