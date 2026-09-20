"""Offline transport failures and accounting; these are not live Jev evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from labs.event_gating.jev import JevConsumer, MODEL, _http_transport


def response():
    return {"model": MODEL, "answers": {"decision": {
        "type": "choice", "choice": "ignore", "confidence": 0.94,
        "probabilities": {"ignore": 0.97, "review": 0.02, "uncertain": 0.01},
    }}, "usage": {"input_tokens": 140, "output_tokens": 24}}


class JevConsumerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.environment = patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-secret"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def invoke(self, value=None, *, status=200, exception=None):
        raw = json.dumps(response() if value is None else value).encode("utf-8")
        transport = Mock(return_value=(status, raw), side_effect=exception)
        consumer = JevConsumer(self.path, timeout_seconds=0.5, transport=transport)
        result = consumer.call({"update": "把入口标签改成 Entry，不改变功能"}, call_id="public-1")
        return result, consumer, transport

    def test_documented_wire_contract_preserves_chinese_and_metered_output(self):
        result, consumer, transport = self.invoke()
        payload, key, timeout = transport.call_args.args
        wire = json.loads(payload)
        self.assertEqual(wire["model"], MODEL)
        self.assertEqual(wire["questions"]["decision"]["type"], "choice")
        self.assertEqual(set(wire["questions"]["decision"]["criteria"]), {"ignore", "review", "uncertain"})
        self.assertEqual(wire["state"]["update"], "把入口标签改成 Entry，不改变功能")
        self.assertEqual(key, "test-secret")
        self.assertEqual(timeout, 0.5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["usage"]["output_tokens"], 24)
        self.assertIsNone(result["usage"]["cached_input_tokens"])
        rows, warnings = consumer.log.read()
        self.assertFalse(warnings)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tokens.input_tokens, 140)
        self.assertEqual(rows[0].model, MODEL)
        self.assertEqual(rows[0].timing_scope, "client_wait")
        diagnostic = consumer.log.path.read_text(encoding="utf-8")
        self.assertNotIn("test-secret", diagnostic)
        self.assertNotIn("Entry", diagnostic)
        self.assertNotIn("probabilities", diagnostic)

    def test_missing_key_has_no_network_attempt_or_invented_usage(self):
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}):
            result, consumer, transport = self.invoke()
        transport.assert_not_called()
        self.assertEqual(result["status"], "missing_credentials")
        self.assertFalse(result["attempted"])
        self.assertIsNone(result["parsed"])
        self.assertIsNone(result["usage"]["input_tokens"])
        self.assertIsNone(result["actual_charge_usd"])
        self.assertFalse(consumer.log.read()[0][0].model_call)

    def test_network_timeout_and_429_fail_once_without_leaking_details(self):
        for code, exception, expected in [(200, TimeoutError("test-secret"), "timeout"),
                                          (200, OSError("test-secret"), "transport_error"),
                                          (429, None, "http_error")]:
            with self.subTest(status=expected):
                result, consumer, transport = self.invoke({"error": "test-secret"}, status=code, exception=exception)
                transport.assert_called_once()
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["parsed"])
                self.assertIsNone(result["usage"]["input_tokens"])
                self.assertNotIn("test-secret", json.dumps(result))
                self.assertNotIn("test-secret", consumer.log.path.read_text())

    def test_low_confidence_and_uncertain_remain_for_runner_fallback(self):
        value = response()
        value["answers"]["decision"].update(choice="uncertain", confidence=0.01,
            probabilities={"ignore": 0.32, "review": 0.33, "uncertain": 0.35})
        result, _, _ = self.invoke(value)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["parsed"]["choice"], "uncertain")
        self.assertEqual(result["parsed"]["confidence"], 0.01)

    def test_unknown_choice_type_probability_or_confidence_cannot_suppress_review(self):
        for update in [{"choice": "accept"}, {"type": "noul"}, {"choice": []},
                       {"confidence": True}, {"confidence": float("nan")},
                       {"probabilities": {"ignore": 1}},
                       {"probabilities": {"ignore": 0.2, "review": 0.4, "uncertain": 0.4}},
                       {"probabilities": {"ignore": 0.9, "review": 0.9, "uncertain": 0.9}}]:
            with self.subTest(update=update):
                value = deepcopy(response())
                value["answers"]["decision"].update(update)
                result, _, _ = self.invoke(value)
                self.assertEqual(result["status"], "invalid_response")
                self.assertIsNone(result["parsed"])
                # Failure costs must survive answer rejection.
                self.assertEqual(result["usage"]["input_tokens"], 140)

    def test_version_mismatch_retains_actual_model_and_usage_but_no_decision(self):
        value = response()
        value["model"] = "jev-1.14.0"
        result, consumer, _ = self.invoke(value)
        self.assertEqual(result["status"], "model_mismatch")
        self.assertEqual(result["actual_model"], "jev-1.14.0")
        self.assertIsNone(result["parsed"])
        self.assertEqual(consumer.log.read()[0][0].model, "jev-1.14.0")
        self.assertEqual(result["usage"]["input_tokens"], 140)

    def test_missing_and_bad_token_fields_stay_unknown(self):
        value = response()
        value["usage"] = {"input_tokens": True, "output_tokens": 7}
        result, _, _ = self.invoke(value)
        self.assertIsNone(result["usage"]["input_tokens"])
        self.assertEqual(result["usage"]["output_tokens"], 7)

    def test_malformed_json_fails_without_echoing_body(self):
        transport = Mock(return_value=(200, b"test-secret malformed response"))
        result = JevConsumer(self.path, transport=transport).call({}, call_id="invalid-json")
        self.assertEqual(result["status"], "invalid_response")
        self.assertIsNone(result["parsed"])
        self.assertNotIn("test-secret", json.dumps(result))
        transport.assert_called_once()

    def test_default_transport_does_not_read_error_body_or_follow_redirect(self):
        for status in (302, 429):
            with self.subTest(status=status), patch("labs.event_gating.jev.http.client.HTTPSConnection") as factory:
                connection = factory.return_value
                connection.getresponse.return_value.status = status
                result = _http_transport(b"{}", "test-secret", 0.5)
                self.assertEqual(result, (status, b""))
                factory.assert_called_once_with("api.typesafe.ai", timeout=0.5)
                connection.request.assert_called_once()
                connection.getresponse.return_value.read.assert_not_called()
                connection.close.assert_called_once()

    def test_non_json_state_is_not_sent(self):
        transport = Mock()
        result = JevConsumer(self.path, transport=transport).call({"number": float("nan")}, call_id="bad-state")
        transport.assert_not_called()
        self.assertEqual(result["status"], "invalid_state")
        self.assertFalse(result["attempted"])


if __name__ == "__main__":
    unittest.main()
