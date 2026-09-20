"""Observable provider boundary tests; no model calls."""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from .provider import ClaudeProvider


SCHEMA = {"type": "object", "properties": {"decision": {"type": "string"}},
          "required": ["decision"], "additionalProperties": False}


def result(**updates):
    value = {"type": "result", "subtype": "success", "is_error": False,
             "structured_output": {"decision": "continue"}, "num_turns": 2,
             "total_cost_usd": 0.03, "duration_ms": 100, "duration_api_ms": 90,
             # Deliberately not the all-model total.
             "usage": {"input_tokens": 1, "output_tokens": 1},
             "modelUsage": {
                 "claude-opus-5": {"inputTokens": 20, "outputTokens": 10,
                                   "cacheReadInputTokens": 30, "cacheCreationInputTokens": 5,
                                   "costUSD": 0.02, "maxOutputTokens": 4096},
                 "claude-haiku-4-5": {"inputTokens": 2, "outputTokens": 3,
                                      "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                      "costUSD": 0.01, "maxOutputTokens": 4096}}}
    value.update(updates)
    return value


class ProviderTests(unittest.TestCase):
    def run_call(self, payload=None, *, timeout=False, code=0, raw=None):
        provider = ClaudeProvider()
        provider._command, provider._version = ("private-machine/claude.exe",), "2.1.272"
        provider._stop = Mock()
        process = Mock()
        process.communicate.return_value = (raw if raw is not None else json.dumps(payload or result()), "private stderr")
        process.poll.return_value = code
        if timeout:
            process.wait.side_effect = subprocess.TimeoutExpired("private-command", 1)
        with patch("labs.checkpoint_critique.provider.subprocess.Popen", return_value=process) as launch:
            response = provider.call({"candidate": "public synthetic"}, SCHEMA,
                                     timeout_seconds=10, max_cost_usd=0.2)
        return response, provider, launch

    def test_accounts_for_all_models_cache_and_public_answer_only(self):
        payload = result(thinking="never-retain", result="never-retain", session_id="private-id")
        response, provider, launch = self.run_call(payload)
        self.assertEqual(response.outcome, "success")
        self.assertEqual(response.answer, {"decision": "continue"})
        self.assertEqual(response.actual_model, "claude-opus-5")
        self.assertEqual(response.usage["input_tokens"], 57)
        self.assertEqual(response.usage["output_tokens"], 13)
        self.assertEqual(response.usage["cached_input_tokens"], 30)
        self.assertEqual(response.usage["api_equivalent_cost_usd"], 0.03)
        self.assertIsNone(response.provider_metadata["provider_request_count"])
        serialized = json.dumps(response.__dict__)
        for private in ("private-machine", "private stderr", "never-retain", "private-id"):
            self.assertNotIn(private, serialized)
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", command)
        self.assertNotIn("--resume", command)
        self.assertNotIn("--verbose", command)
        environment = launch.call_args.kwargs["env"]
        self.assertEqual(environment["CLAUDE_CODE_MAX_RETRIES"], "0")
        self.assertEqual(environment["MAX_STRUCTURED_OUTPUT_RETRIES"], "1")
        self.assertEqual(environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "4096")
        self.assertEqual(provider.describe()["cli_version"], "2.1.272")

    def test_failure_preserves_paid_usage_without_answer_or_raw_error(self):
        response, _, _ = self.run_call(result(subtype="error_max_structured_output_retries",
                                              errors=["private prompt"], is_error=True), code=1)
        self.assertEqual(response.outcome, "provider_error")
        self.assertEqual(response.usage["input_tokens"], 57)
        self.assertIsNone(response.answer)
        self.assertNotIn("private prompt", str(response))

    def test_timeout_stops_only_owned_process_and_keeps_available_cost(self):
        response, provider, _ = self.run_call(timeout=True)
        self.assertEqual(response.outcome, "timeout")
        provider._stop.assert_called_once()
        self.assertEqual(response.usage["api_equivalent_cost_usd"], 0.03)
        self.assertIsNone(response.answer)

    def test_timeout_without_final_json_does_not_claim_zero_tokens(self):
        response, _, _ = self.run_call(timeout=True, raw="partial private output")
        self.assertEqual(response.outcome, "timeout")
        self.assertIsNone(response.usage["input_tokens"])
        self.assertIsNone(response.usage["api_equivalent_cost_usd"])

    def test_missing_auxiliary_counter_is_unknown_not_zero(self):
        payload = result()
        del payload["modelUsage"]["claude-haiku-4-5"]["cacheReadInputTokens"]
        response, _, _ = self.run_call(payload)
        self.assertEqual(response.outcome, "telemetry_missing")
        self.assertIsNone(response.usage["input_tokens"])
        self.assertEqual(response.usage["output_tokens"], 13)

    def test_root_usage_alone_is_not_all_model_telemetry(self):
        response, _, _ = self.run_call(result(modelUsage={}))
        self.assertEqual(response.outcome, "telemetry_missing")
        self.assertIsNone(response.usage["input_tokens"])

    def test_schema_mismatch_and_unrequested_primary_are_visible(self):
        response, _, _ = self.run_call(result(structured_output={"decision": 1}))
        self.assertEqual(response.outcome, "malformed")
        payload = result()
        payload["modelUsage"] = {"claude-other": payload["modelUsage"]["claude-opus-5"]}
        response, _, _ = self.run_call(payload)
        self.assertEqual(response.outcome, "model_mismatch")
        self.assertEqual(response.actual_model, "claude-other")

    def test_budget_denial_before_process_and_provider_budget_failure(self):
        provider = ClaudeProvider()
        provider._command, provider._version = ("private-path",), "2.1.272"
        with patch("labs.checkpoint_critique.provider.subprocess.Popen") as launch:
            response = provider.call({}, SCHEMA, timeout_seconds=1, max_cost_usd=0)
        launch.assert_not_called()
        self.assertEqual(response.outcome, "budget_exhausted")
        self.assertEqual(response.provider_metadata["process_calls"], 0)
        self.assertIsNone(response.usage["input_tokens"])
        response, _, _ = self.run_call(result(subtype="error_max_budget_usd"), code=1)
        self.assertEqual(response.outcome, "budget_exhausted")
        self.assertEqual(response.usage["api_equivalent_cost_usd"], 0.03)


if __name__ == "__main__":
    unittest.main()
