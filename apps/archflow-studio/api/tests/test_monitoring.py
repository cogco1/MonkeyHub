import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import os
import time
import unittest
from unittest.mock import patch

from archflow_studio_api.application.intent_agent import DeterministicCompiler, Selection
from archflow_studio_api.application.jobs import JobRegistry
from archflow_studio_api.application.monitoring import MonitoredCompiler, StudioMonitor, candidate_event_id
from archflow.project.refs import record_ref_from_uri
from monkeyarch.runtime import project_runner
from monkeymonitor.store import UsageLog
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID
from .test_cad_export import NEEDS_OCCT, OcctCandidateTestCase, no_process
from .test_queue import Gate, Recorder, wait_until


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
                    self.assertIs(app.state.intent_compiler.monitor, app.state.monitor)
                    self.assertIs(app.state.intent_compiler.store, app.state.monitor.store)
                    self.assertEqual(app.state.intent_compiler.store.path, target / "usage.jsonl")
                    self.assertFalse(target.exists())
                finally:
                    app.state.jobs.shutdown()

    def run_call(self, compiler, directory):
        return MonitoredCompiler(compiler, UsageLog(directory)).compile(
            message="private message must never be logged", selection=Selection(None, None),
            projection=SimpleNamespace(project_id="example", run=SimpleNamespace(run_id="source-run"),
                                       reference_state_exact=True, record_source="retained-input"),
        )

    def test_success_question_and_exception_record_usage_without_content(self):
        for status in ("compiled", "question", "failed"):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                receipt = SimpleNamespace(receipt_id="model-receipt", model_id="actual-model", input_tokens=125, output_tokens=20,
                    cached_input_tokens=25, cache_write_input_tokens=0, cache_write_1h_input_tokens=0,
                    reasoning_output_tokens=5)
                class Compiler:
                    provider = "anthropic"
                    model = "requested-model"
                    def compile(self, *, operation_observer=None, **kwargs):
                        started = datetime.now(timezone.utc).isoformat()
                        clock = time.perf_counter()
                        time.sleep(0.01)
                        operation_observer(dict(phase="model_request", status="succeeded", started_at=started,
                            ended_at=datetime.now(timezone.utc).isoformat(), duration_ms=round((time.perf_counter() - clock) * 1000),
                            details={"model_inference_ms": None}))
                        time.sleep(0.02)  # Local answer parsing is outside the provider boundary.
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
                self.assertEqual(len(events), 2)
                parent = next(event for event in events if event.phase == "intent_compile")
                events = [event for event in events if event.phase == "model_request"]
                self.assertEqual(events[0].parent_event_id, parent.event_id)
                self.assertEqual(events[0].operation_id, parent.operation_id)
                self.assertGreater(parent.duration_ms, events[0].duration_ms)
                self.assertFalse(parent.model_call)
                self.assertTrue(all(count is None for count in parent.tokens.to_dict().values()))
                self.assertEqual(events[0].model, "actual-model")
                self.assertEqual(events[0].tokens.input_tokens, 125)
                self.assertEqual(events[0].status, "succeeded")
                self.assertEqual(parent.status, "failed" if status == "failed" else "succeeded")
                self.assertEqual(events[0].billing_mode, "api_estimate")
                self.assertEqual(events[0].event_id, "studio:model:model-receipt")
                self.assertEqual(events[0].timing_scope, "model_call")
                self.assertTrue(events[0].model_call)
                self.assertEqual(events[0].run_id, "source-run")
                self.assertEqual(events[0].source_ref, "retained-input")
                self.assertGreaterEqual(datetime.fromisoformat(events[0].ended_at), datetime.fromisoformat(events[0].started_at))
                self.assertFalse(warnings)
                self.assertNotIn("private", (Path(directory) / "usage.jsonl").read_text())

    def test_deterministic_does_not_invent_model_call(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "absent"
            self.run_call(DeterministicCompiler(), target)
            events, warnings = UsageLog(target).read()
            self.assertFalse(warnings)
            self.assertEqual([event.phase for event in events], ["intent_compile"])
            self.assertFalse(events[0].model_call)

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

    def test_running_failure_and_cancellation_keep_one_bounded_service_event(self):
        for error in (RuntimeError("operation failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__), TemporaryDirectory() as directory:
                store = UsageLog(Path(directory))
                monitor = StudioMonitor(store)
                with self.assertRaises(type(error)) as caught:
                    with monitor.measure("stage_save", project_id="example", run_id="candidate", source_ref="retained-input"):
                        running, warnings = store.read()
                        self.assertFalse(warnings)
                        self.assertEqual(len(running), 1)
                        self.assertEqual(running[0].status, "running")
                        self.assertIsNone(running[0].ended_at)
                        self.assertIsNone(running[0].duration_ms)
                        raise error
                self.assertIs(caught.exception, error)
                finished, warnings = store.read()
                self.assertFalse(warnings)
                self.assertEqual(len(finished), 1)
                event = finished[0]
                self.assertEqual(event.event_id, running[0].event_id)
                self.assertEqual(event.status, "cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
                self.assertGreaterEqual(datetime.fromisoformat(event.ended_at), datetime.fromisoformat(event.started_at))
                self.assertGreaterEqual(event.duration_ms, 0)
                self.assertEqual(event.timing_scope, "service")
                self.assertFalse(event.model_call)
                self.assertEqual((event.provider, event.model), ("none", "none"))
                self.assertTrue(all(value is None for value in event.tokens.to_dict().values()))

    def test_candidate_service_clock_starts_when_worker_enters_not_while_queued(self):
        with TemporaryDirectory() as directory:
            store = UsageLog(Path(directory))
            registry = JobRegistry(Recorder(), max_workers=1, monitor=StudioMonitor(store))
            first, second = Gate(), Gate()
            try:
                for run_id, gate in (("first", first), ("second", second)):
                    with registry._monitor.scope(operation_id=f"operation:{run_id}", parent_event_id=f"request:{run_id}"):
                        registry.submit(candidate_id=run_id, proposal_id=f"proposal-{run_id}", work=gate,
                                        project_id="example", source_ref="retained-input",
                                        related_event_id="studio:model:model-receipt")
                self.assertTrue(first.started.wait(2))
                events, _ = store.read()
                self.assertEqual({event.run_id for event in events}, {"first"})
                self.assertEqual(registry.for_candidate("second").status, "queued")
                first.release.set()
                self.assertTrue(second.started.wait(2))
                events, _ = store.read()
                by_run = {event.run_id: event for event in events if event.phase == "candidate"}
                self.assertGreaterEqual(datetime.fromisoformat(by_run["second"].started_at), datetime.fromisoformat(by_run["first"].ended_at))
                second.release.set()
                self.assertTrue(wait_until(lambda: registry.for_candidate("second").status == "succeeded"))
                events, warnings = store.read()
                self.assertFalse(warnings)
                self.assertEqual(len(events), 4)
                queues = [event for event in events if event.phase == "candidate_queue"]
                self.assertEqual(len(queues), 2)
                self.assertGreaterEqual(queues[1].duration_ms, queues[0].duration_ms)
                for event in (event for event in events if event.phase == "candidate"):
                    self.assertEqual(event.operation_id, f"operation:{event.run_id}")
                    self.assertEqual(event.parent_event_id, f"request:{event.run_id}")
                    self.assertEqual(event.event_id, candidate_event_id("example", event.run_id))
                    self.assertEqual(event.related_event_id, "studio:model:model-receipt")
                    self.assertEqual(event.source_ref, "retained-input")
                    self.assertEqual(event.status, "succeeded")
            finally:
                first.release.set()
                second.release.set()
                registry.shutdown()

    def test_failed_candidate_keeps_original_job_error_and_finishes_service_clock(self):
        with TemporaryDirectory() as directory:
            store = UsageLog(Path(directory))
            registry = JobRegistry(Recorder(), monitor=StudioMonitor(store))

            def fail():
                raise RuntimeError("candidate source was stale")

            try:
                registry.submit(candidate_id="failed", proposal_id="proposal", work=fail, project_id="example")
                self.assertTrue(wait_until(lambda: registry.for_candidate("failed").status == "failed"))
                self.assertEqual(registry.for_candidate("failed").error, "candidate source was stale")
                events, warnings = store.read()
                self.assertFalse(warnings)
                events = [event for event in events if event.phase == "candidate"]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].status, "failed")
                self.assertIsNotNone(events[0].ended_at)
                self.assertGreaterEqual(events[0].duration_ms, 0)
                self.assertFalse(events[0].model_call)
            finally:
                registry.shutdown()

    def test_export_observer_failure_preserves_geometry_result_and_original_error(self):
        result = {"status": "succeeded", "path": "incremental"}
        for outcome in (result, RuntimeError("geometry failed"), asyncio.CancelledError()):
            with self.subTest(outcome=type(outcome).__name__):
                observations = []

                def broken_observer(value):
                    observations.append(value)
                    raise OSError("diagnostic unavailable")

                executor = patch.object(project_runner, "_execute_cad",
                                        **({"side_effect": outcome} if isinstance(outcome, BaseException) else {"return_value": outcome}))
                with executor as export:
                    arguments = (None, None, None, None, SimpleNamespace(program_digest="a" * 64), "stage", SimpleNamespace(cad_backend="occt"), {"state_record_ref": "retained-state"})
                    if isinstance(outcome, BaseException):
                        with self.assertRaises(type(outcome)) as caught:
                            project_runner._export(*arguments, operation_observer=broken_observer)
                        self.assertIs(caught.exception, outcome)
                    else:
                        self.assertIs(project_runner._export(*arguments, operation_observer=broken_observer), result)
                self.assertEqual(export.call_count, 1)
                self.assertEqual(len(observations), 1)
                self.assertEqual(observations[0]["status"], "cancelled" if isinstance(outcome, asyncio.CancelledError) else "failed" if isinstance(outcome, Exception) else "succeeded")
                self.assertEqual(observations[0]["source_ref"], "retained-state")
                self.assertGreaterEqual(observations[0]["duration_ms"], 0)


@NEEDS_OCCT
class MonitoringOcctTests(OcctCandidateTestCase):
    """Real model generation, acceptance and restart with the shared usage log."""

    def setUp(self):
        super().setUp()
        self.settings = replace(self.settings, monitor_dir=self.root / "diagnostics")
        self.client = self.open_client(self.settings)
        self.store = self.client.app.state.monitor.store

    def initialize_from_generated_model(self):
        accepted, job = self.run_candidate(self.client, "set height to 2.1", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.candidate(self.client, accepted["candidateId"])
        _, preview = self.split(candidate["artifacts"])
        response = self.client.post("/api/design-stages/initialize", json={
            "projectId": PROJECT_ID, "modelSource": preview["modelSource"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def generate_from_stage(self, stage, height):
        accepted, job = self.run_candidate(
            self.client, f"set height to {height}", elementId="portico-base",
            stateDigest=stage["modelSource"]["stateDigest"], sourceStageRef=stage["stageRef"],
            sourceRunId=stage["candidateId"],
        )
        self.assertEqual(job["status"], "succeeded", job)
        return accepted["candidateId"]

    def accept_from_stage(self, run_id, stage):
        return self.client.post(f"/api/candidates/{run_id}/accept", json={
            "projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": stage["stageRef"],
        })

    def test_real_occt_candidate_and_save_have_links_and_failed_log_cannot_break_next_save(self):
        with no_process():
            initial = self.initialize_from_generated_model()
            candidate_id = self.generate_from_stage(initial, 2.8)
            response = self.accept_from_stage(candidate_id, initial)
            self.assertEqual(response.status_code, 200, response.text)
            accepted = response.json()
            self.assertEqual(accepted["parentStageRef"], initial["stageRef"])
            events, warnings = self.store.read()
            self.assertFalse(warnings)
            candidate = next(row for row in events if row.event_id == candidate_event_id(PROJECT_ID, candidate_id))
            exports = [row for row in events if row.run_id == candidate_id and row.phase.startswith("geometry_export.occt.")]
            self.assertTrue(exports)
            save = next(row for row in events if row.run_id == candidate_id and row.phase == "stage_save")
            self.assertEqual(save.source_ref, initial["stageRef"])
            self.assertEqual(save.related_event_id, candidate.event_id)
            self.assertEqual(candidate.phase, "candidate")
            for export in exports:
                self.assertEqual(export.related_event_id, candidate.event_id)
                self.assertGreaterEqual(datetime.fromisoformat(export.started_at), datetime.fromisoformat(candidate.started_at))
                self.assertLessEqual(datetime.fromisoformat(export.ended_at), datetime.fromisoformat(candidate.ended_at))
            for event in (candidate, save, *exports):
                self.assertEqual(event.project_id, PROJECT_ID)
                self.assertEqual(event.run_id, candidate_id)
                self.assertEqual(event.status, "succeeded")
                self.assertEqual(event.timing_scope, "service")
                self.assertFalse(event.model_call)
                self.assertEqual((event.provider, event.model), ("none", "none"))
                self.assertTrue(all(value is None for value in event.tokens.to_dict().values()))
                source = record_ref_from_uri(event.source_ref, PROJECT_ID)
                self.assertIsNotNone(source)
                self.assertGreaterEqual(event.duration_ms, 0)
            self.assertNotIn("set height", self.store.path.read_text())

            following_id = self.generate_from_stage(accepted, 3.1)
            with patch.object(self.store, "append", side_effect=OSError("diagnostic disk unavailable")), self.assertLogs("archflow_studio_api.application.monitoring", level="WARNING"):
                response = self.accept_from_stage(following_id, accepted)
            self.assertEqual(response.status_code, 200, response.text)
            following = response.json()
            self.assertEqual(following["label"], "S2")
            self.assertEqual(following["parentStageRef"], accepted["stageRef"])
            restarted = self.open_client(self.settings)
            history = restarted.get("/api/design-history", params={"branchId": "main"})
            self.assertEqual(history.status_code, 200, history.text)
            self.assertEqual(history.json()["stages"], [initial, accepted, following])


if __name__ == "__main__":
    unittest.main()
