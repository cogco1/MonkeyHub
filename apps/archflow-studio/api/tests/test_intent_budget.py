from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from archflow_studio_api.application.intent_agent import Selection
from archflow_studio_api.application.intent_budget import build_context_budget
from archflow_studio_api.application.monitoring import MonitoredCompiler
from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent, diagnostic_details
from archflow_studio_api.settings import StudioSettings, SettingsError, CONTEXT_BUDGET_ENV, PROJECT_DIR_ENV


class ContextBudgetTests(unittest.TestCase):
    def test_process_budget_override_reaches_the_provider_without_changing_model(self):
        from archflow_studio_api.application.intent_agent import compiler_from_settings
        with patch.dict(os.environ, {PROJECT_DIR_ENV: str(Path.cwd()), CONTEXT_BUDGET_ENV: "4096",
                                    "ARCHFLOW_STUDIO_INTENT_PROVIDER": "codex",
                                    "ARCHFLOW_STUDIO_INTENT_MODEL": "configured-model"}, clear=True):
            settings = StudioSettings.from_env()
        with patch("archflow_studio_api.application.intent_agent._codex_version", return_value="fixture-cli"):
            compiler = compiler_from_settings(settings)
        self.assertEqual(compiler.context_budget_tokens, 4096)
        self.assertEqual(compiler.model, "configured-model")

    def test_invalid_process_budget_is_rejected_before_a_model_can_run(self):
        for value in ("0", "-1", "1.5", "many"):
            with self.subTest(value=value), patch.dict(os.environ, {
                PROJECT_DIR_ENV: str(Path.cwd()), CONTEXT_BUDGET_ENV: value,
            }, clear=True), self.assertRaises(SettingsError):
                StudioSettings.from_env()

    def test_request_sections_are_counted_once_and_contributors_are_only_diagnostics(self):
        report = build_context_budget(
            {"intent": "width 1200", "system": "rules", "schema": "schema", "state": "state text"},
            model="test-model", task_type="scalar", count_tokens=lambda text: len(text.split()),
            estimator="fixture_words", contributors={"all_components": "state text"},
            budget_tokens=6, expected_max_output_tokens=800,
        )
        self.assertEqual(report.estimated_input_tokens, 6)
        self.assertFalse(report.exceeded)
        self.assertEqual(dict(report.section_tokens)["dependencies"], 0)
        details = diagnostic_details(report.to_details())
        self.assertEqual(details["context_budget"]["largest_contributors"],
                         [{"name": "all_components", "estimated_tokens": 2}])
        self.assertEqual(details["context_budget"]["expected_max_output_tokens"], 800)

    def test_over_budget_warns_with_counts_and_never_retains_prompt_or_claims_actual_usage(self):
        sections = {"intent": "秘密私有请求", "schema": "private schema contents", "state": "private state" * 20}
        original = dict(sections)
        report = build_context_budget(sections, model="test-model", task_type="component",
                                      budget_tokens=2, image_count=1)
        self.assertEqual(sections, original)
        self.assertTrue(report.exceeded)
        with self.assertLogs("budget-test", level="WARNING") as captured:
            report.log_preflight(logging.getLogger("budget-test"))
        self.assertIn("context budget exceeded", captured.output[0])
        self.assertIn("state=", captured.output[0])
        serialized = json.dumps(report.to_details()) + captured.output[0]
        for source in sections.values():
            self.assertNotIn(source, serialized)
        self.assertNotIn("private", serialized)
        budget = report.to_details()["context_budget"]
        self.assertEqual(budget["basis"], "text_estimate")
        self.assertEqual(budget["estimator"], "heuristic_utf8_bytes_div4")
        self.assertFalse(budget["provider_overhead_included"])
        self.assertIsNone(budget["image_tokens"])
        event = UsageEvent("event", "studio", "test", "test-model", "model_request", "succeeded",
                           "2026-09-10T12:00:00Z", TokenUsage(), model_call=True, details=report.to_details())
        self.assertIsNone(event.tokens.input_tokens)
        self.assertEqual(UsageEvent.from_dict(event.to_dict()), event)

    def test_diagnostic_handler_failure_does_not_prevent_the_call(self):
        class BrokenLogger:
            def log(self, *args, **kwargs):
                raise OSError("unavailable diagnostic destination")
        build_context_budget({"intent": "edit"}, model="test", task_type="scalar").log_preflight(BrokenLogger())

    def test_budget_metadata_refuses_free_text_or_fabricated_usage(self):
        base = build_context_budget({"intent": "edit"}, model="test", task_type="scalar").to_details()
        for key, value in (
            ("prompt", "private"), ("basis", "provider_actual"),
            ("provider_overhead_included", True), ("image_tokens", 2),
            ("estimator", "private prompt with spaces"), ("estimated_input_tokens", 999),
            ("largest_contributors", [{"name": "private prompt", "estimated_tokens": 1}]),
            ("largest_contributors", [{"name": "state", "estimated_tokens": 1}] * 6),
        ):
            with self.subTest(field=key, value=value):
                details = deepcopy(base)
                details["context_budget"][key] = value
                with self.assertRaises(ValueError):
                    diagnostic_details(details)
        for details in ({"success": "private"}, {"validator_pass": 1}, {"escalation": 0},
                        {"validator_scope": "architectural_correctness"}, {"task_type": "private prompt"}):
            with self.subTest(details=details), self.assertRaises(ValueError):
                diagnostic_details(details)

    def test_injected_counter_must_return_nonnegative_integer_estimates(self):
        for invalid in (-1, True, 1.5, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_context_budget({"intent": "edit"}, model="test", task_type="scalar",
                                     count_tokens=lambda text: invalid)

    def test_enforced_budget_boundary_is_inclusive_and_does_not_claim_provider_tokens(self):
        report = build_context_budget({"intent": "1234"}, model="test", task_type="design", budget_tokens=1)
        self.assertIsNone(report.limitation())
        report = build_context_budget({"intent": "12345"}, model="test", task_type="design", budget_tokens=1)
        self.assertIn("estimated at 2", report.limitation())
        self.assertIn("Required design constraints have been retained", report.limitation())
        self.assertIn("excludes CLI/provider overhead and image token costs", report.limitation())


class PerAttemptUsageTests(unittest.TestCase):
    def test_expansion_preserves_each_call_usage_model_and_validation_without_double_counting(self):
        budget = build_context_budget({"intent": "private request"}, model="requested", task_type="scalar").to_details()
        class Compiler:
            provider = "anthropic"
            model = "requested"
            def compile(self, *, operation_observer=None, **kwargs):
                for index in range(2):
                    operation_observer(dict(
                        phase="model_request", status="succeeded", started_at="2026-09-10T12:00:00Z",
                        ended_at="2026-09-10T12:00:00Z", duration_ms=1,
                        usage={"input_tokens": 100 + index * 20, "output_tokens": 10,
                               "cached_input_tokens": 5 + index * 5},
                        reported_model=f"actual-{index}",
                        details={**budget, "success": bool(index), "validator_pass": True,
                                 "validator_scope": "request_output", "escalation": not bool(index)},
                    ))
                # The final receipt repeats the final attempt's usage; it must
                # not be charged a second time or overwrite the first attempt.
                receipt = SimpleNamespace(receipt_id="final", model_id="actual-1", input_tokens=120,
                                          output_tokens=10, cached_input_tokens=10)
                return SimpleNamespace(provider=self.provider, model=self.model, status="compiled", receipt=receipt)

        with TemporaryDirectory() as directory:
            store = UsageLog(Path(directory))
            MonitoredCompiler(Compiler(), store).compile(
                message="private request", selection=Selection(None, None), projection=SimpleNamespace(project_id="example"))
            events, warnings = store.read()
            self.assertFalse(warnings)
            attempts = [event for event in events if event.phase == "model_request"]
            self.assertEqual(len(attempts), 2)
            self.assertEqual(sum(event.tokens.input_tokens for event in attempts), 220)
            self.assertEqual([event.model for event in attempts], ["actual-0", "actual-1"])
            self.assertEqual([event.details["success"] for event in attempts], [False, True])
            self.assertEqual([event.details["escalation"] for event in attempts], [True, False])
            self.assertTrue(all(event.details["validator_pass"] for event in attempts))
            self.assertIsNone(attempts[0].tokens.cache_write_input_tokens)
            self.assertNotIn("private", store.path.read_text())
            parent = next(event for event in events if event.phase == "intent_compile")
            self.assertFalse(parent.model_call)
            self.assertIsNone(parent.tokens.input_tokens)

    def test_legacy_observer_keeps_validation_and_missing_attempt_usage_unknown(self):
        class Compiler:
            provider = "codex"
            model = "requested"
            def compile(self, *, operation_observer=None, **kwargs):
                for _ in range(2):
                    operation_observer(dict(phase="model_request", status="succeeded",
                                            started_at="2026-09-10T12:00:00Z"))
                return SimpleNamespace(provider=self.provider, model=self.model, status="compiled",
                    receipt=SimpleNamespace(receipt_id="legacy", model_id="actual", input_tokens=20))
        with TemporaryDirectory() as directory:
            store = UsageLog(Path(directory))
            MonitoredCompiler(Compiler(), store).compile(
                message="edit", selection=Selection(None, None), projection=SimpleNamespace(project_id="example"))
            events, warnings = store.read()
            self.assertFalse(warnings)
            attempts = [event for event in events if event.phase == "model_request"]
            self.assertEqual([event.tokens.input_tokens for event in attempts], [None, 20])
            self.assertTrue(all(event.details["validator_pass"] is None for event in attempts))
            self.assertTrue(all(event.details["escalation"] is None for event in attempts))
