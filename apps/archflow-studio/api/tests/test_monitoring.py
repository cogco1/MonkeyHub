from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from archflow_studio_api.application.intent_agent import DeterministicCompiler, Selection
from archflow_studio_api.application.monitoring import MonitoredCompiler
from monkeymonitor.store import UsageLog
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings


class MonitoringTests(unittest.TestCase):
    def test_environment_wires_optional_logger_without_creating_files(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "diagnostics"
            with patch.dict(os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR":str(Path(directory) / "project"),
                                         "MONKEYMONITOR_DATA_DIR":str(target)}, clear=True):
                settings = StudioSettings.from_env()
                app = create_app(settings)
                try:
                    self.assertIsInstance(app.state.intent_compiler, MonitoredCompiler)
                    self.assertEqual(app.state.intent_compiler.store.path, target / "usage.jsonl")
                    self.assertFalse(target.exists())
                finally:
                    app.state.jobs.shutdown()

    def run_call(self, compiler, directory):
        return MonitoredCompiler(compiler, UsageLog(directory)).compile(
            message="private message must never be logged", selection=Selection(None, None),
            projection=SimpleNamespace(project_id="example"),
        )

    def test_success_question_and_exception_record_usage_without_content(self):
        for status in ("compiled", "question", "failed"):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                receipt = SimpleNamespace(model_id="actual-model", input_tokens=125, output_tokens=20,
                    cached_input_tokens=25, cache_write_input_tokens=0, cache_write_1h_input_tokens=0,
                    reasoning_output_tokens=5)
                class Compiler:
                    provider = "anthropic"
                    model = "requested-model"
                    def compile(self, **kwargs):
                        if status == "failed":
                            error = RuntimeError("private provider text")
                            error.receipt = receipt
                            raise error
                        return SimpleNamespace(receipt=receipt, status=status, provider=self.provider, model=self.model)
                if status == "failed":
                    with self.assertRaisesRegex(RuntimeError, "private provider text"):
                        self.run_call(Compiler(), Path(directory))
                else:
                    self.assertEqual(self.run_call(Compiler(), Path(directory)).status, status)
                events, warnings = UsageLog(Path(directory)).read()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].model, "actual-model")
                self.assertEqual(events[0].tokens.input_tokens, 125)
                self.assertEqual(events[0].status, status)
                self.assertEqual(events[0].billing_mode, "api_estimate")
                self.assertFalse(warnings)
                self.assertNotIn("private", (Path(directory) / "usage.jsonl").read_text())

    def test_deterministic_does_not_invent_model_call(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "absent"
            self.run_call(DeterministicCompiler(), target)
            self.assertFalse(target.exists())

    def test_diagnostic_failure_does_not_repeat_or_fail_compilation(self):
        class BrokenStore:
            def append(self, event):
                raise OSError("unavailable")
        class Compiler:
            provider = "codex"
            model = "model"
            calls = 0
            def compile(self, **kwargs):
                self.calls += 1
                return SimpleNamespace(receipt=None, status="compiled", provider=self.provider, model=self.model)
        compiler = Compiler()
        wrapped = MonitoredCompiler(compiler, BrokenStore())
        with self.assertLogs("archflow_studio_api.application.monitoring", level="WARNING"):
            self.assertEqual(wrapped.compile(message="x", selection=Selection(None, None), projection=SimpleNamespace(project_id="example")).status, "compiled")
        self.assertEqual(compiler.calls, 1)


if __name__ == "__main__":
    unittest.main()
