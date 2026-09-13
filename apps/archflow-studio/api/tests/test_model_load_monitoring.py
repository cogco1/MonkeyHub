"""Browser wait is optional diagnostics bound to a retained project/run source."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.application.monitoring import MonitoredCompiler
from archflow_studio_api.settings import StudioSettings
from monkeymonitor.store import UsageLog

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest
from .test_intents import scripted


class ModelLoadMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository, self.ref = make_project(self.root)
        self.settings = StudioSettings(
            project_dir=self.root / PROJECT_ID, cad_export="off", monitor_dir=self.root / "monitor",
        )
        self.app = create_app(self.settings)
        self.client = self.enterContext(TestClient(self.app))
        self.payload = {
            "eventId": str(uuid4()), "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID,
            "sourceRef": self.ref.uri, "startedAt": "2026-09-09T10:00:00+00:00",
            "endedAt": "2026-09-09T10:00:01+00:00", "durationMs": 998, "status": "succeeded",
        }

    def test_statuses_are_client_wait_with_no_tokens_and_duplicate_id_counts_once(self):
        head = self.repository.layout.head.read_bytes()
        for status in ("succeeded", "failed", "cancelled"):
            body = dict(self.payload, eventId=str(uuid4()), status=status)
            for _ in range(2):
                response = self.client.post("/api/events/model-load", json=body)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"recorded": True})
        events, warnings = UsageLog(self.settings.monitor_dir).read()
        self.assertFalse(warnings)
        self.assertEqual(len(events), 3)
        self.assertEqual({event.status for event in events}, {"succeeded", "failed", "cancelled"})
        for event in events:
            self.assertEqual(event.timing_scope, "client_wait")
            self.assertFalse(event.model_call)
            self.assertTrue(all(value is None for value in event.tokens.to_dict().values()))
            self.assertEqual(event.duration_ms, 998)
            self.assertEqual(event.source_ref, self.ref.uri)
            self.assertEqual(event.related_event_id, f"studio:candidate:{PROJECT_ID}:{REFERENCE_RUN_ID}")
        self.assertEqual(self.repository.layout.head.read_bytes(), head)

    def test_invalid_binding_and_content_never_enter_the_log(self):
        cases = (
            ({"projectId": "another-project"}, 409),
            ({"runId": "missing-run"}, 404),
            ({"sourceRef": self.ref.uri.replace(self.ref.sha256, "0" * 64)}, 409),
            ({"durationMs": -1}, 422),
            ({"endedAt": "2026-09-09T09:59:59+00:00"}, 422),
            ({"prompt": "private text"}, 422),
            ({"tokens": {"input_tokens": 10}}, 422),
        )
        for changes, expected in cases:
            with self.subTest(changes=changes):
                response = self.client.post("/api/events/model-load", json=dict(self.payload, **changes))
                self.assertEqual(response.status_code, expected, response.text)
        self.assertFalse((self.settings.monitor_dir / "usage.jsonl").exists())

    def test_disabled_or_broken_logger_does_not_change_the_operation(self):
        with patch.object(self.app.state.monitor.store, "append", side_effect=OSError("offline")) as append:
            with self.assertLogs("archflow_studio_api.application.monitoring", level="WARNING"):
                result = self.client.post("/api/events/model-load", json=self.payload)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json(), {"recorded": False})
            self.assertEqual(append.call_count, 1)
        with TestClient(create_app(replace(self.settings, monitor_dir=None))) as client:
            self.assertNotIn("operation-timing", client.get("/api/protocol").json()["capabilities"])
            self.assertEqual(client.post("/api/events/model-load", json=self.payload).json(), {"recorded": False})
        self.assertFalse((self.settings.monitor_dir / "usage.jsonl").exists())

    def test_interaction_and_nested_browser_spans_keep_client_duration_and_closed_metadata(self):
        operation, child = str(uuid4()), str(uuid4())
        root = dict(self.payload, eventId=operation, operationId=operation, phase="design_edit",
                    details={"active_wait_ms": 600, "between_actions_ms": 398})
        running = {**root, "status": "running", "endedAt": None, "durationMs": None}
        for body in (running, root, dict(root, eventId=child, parentEventId=operation, phase="model_download",
                                       details={"asset_sha256": "a" * 64, "input_bytes": 1000})):
            response = self.client.post("/api/events/timing", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["recorded"])
        rows, warnings = self.app.state.monitor.store.read()
        self.assertFalse(warnings)
        self.assertEqual(len(rows), 2)
        root_event = next(row for row in rows if row.phase == "design_edit")
        child_event = next(row for row in rows if row.phase == "model_download")
        self.assertEqual(root_event.duration_ms, 998)
        self.assertEqual(root_event.timing_scope, "interaction")
        self.assertEqual(child_event.operation_id, root_event.event_id)
        self.assertEqual(child_event.parent_event_id, root_event.event_id)
        self.assertEqual(child_event.related_event_id, f"studio:candidate:{PROJECT_ID}:{REFERENCE_RUN_ID}")
        self.assertEqual(child_event.details["input_identity"], {"asset_sha256": "a" * 64})
        for invalid in (dict(root, details={"prompt": "private"}), dict(root, tokens={"input_tokens": 20}),
                        dict(root, status="failed", endedAt=None), dict(root, operationId="not-an-id")):
            self.assertEqual(self.client.post("/api/events/timing", json=invalid).status_code, 422)

    def test_concurrent_request_context_reaches_sync_compiler_without_cross_linking(self):
        operations = [str(uuid4()), str(uuid4())]
        digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)
        self.app.state.intent_compiler = MonitoredCompiler(
            scripted(utterance="set height to 2.2", component_id="portico", element_id="portico-base"), self.app.state.monitor)
        def submit(operation):
            return self.client.post("/api/intents", headers={"X-Monkey-Operation": operation, "X-Monkey-Parent": operation},
                json={"projectId": PROJECT_ID, "stateDigest": digest, "utterance": "make the portico base taller", "targetComponentId": "portico", "elementId": "portico-base"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, operations))
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
        rows, warnings = self.app.state.monitor.store.read()
        self.assertFalse(warnings)
        self.assertEqual({row.operation_id for row in rows}, {f"studio:client:{value}" for value in operations})
        for operation in operations:
            group = [row for row in rows if row.operation_id == f"studio:client:{operation}"]
            request = next(row for row in group if row.phase == "api_request")
            compiler = next(row for row in group if row.phase == "intent_compile")
            self.assertEqual(compiler.parent_event_id, request.event_id)
            self.assertEqual(request.parent_event_id, f"studio:client:{operation}")
            self.assertEqual(request.project_id, PROJECT_ID)
        self.assertEqual(self.app.state.monitor.current(), {})
        # Unsupported diagnostic headers do not break a valid user action.
        self.assertEqual(submit("malformed").status_code, 201)

    def test_concurrent_hub_turns_keep_request_and_observer_association(self):
        turns = [str(uuid4()), str(uuid4())]
        digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)
        self.app.state.intent_compiler = MonitoredCompiler(
            scripted(utterance="set height to 2.2", component_id="portico", element_id="portico-base"), self.app.state.monitor)
        def submit(turn):
            return self.client.post("/api/intents", headers={
                "x-monkey-turn-id": turn, "x-monkey-parent-span-id": f"hub:turn:{turn}"},
                json={"projectId": PROJECT_ID, "stateDigest": digest, "utterance": "make the portico base taller",
                      "targetComponentId": "portico", "elementId": "portico-base"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, turns))
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
        rows, warnings = self.app.state.monitor.store.read()
        self.assertFalse(warnings)
        self.assertEqual({row.turn_id for row in rows}, set(turns))
        for turn in turns:
            group = [row for row in rows if row.turn_id == turn]
            request = next(row for row in group if row.phase == "api_request")
            compiler = next(row for row in group if row.phase == "intent_compile")
            self.assertEqual(request.parent_event_id, f"hub:turn:{turn}")
            self.assertEqual(compiler.parent_event_id, request.event_id)
        self.assertEqual(self.app.state.monitor.current(), {})
        self.assertEqual(submit("malformed").status_code, 201)

    def test_first_project_action_does_not_need_an_invented_retained_run(self):
        body = dict(self.payload, phase="design_edit", operationId=str(uuid4()), runId=None, sourceRef=None)
        response = self.client.post("/api/events/timing", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["recorded"])
        rows, warnings = self.app.state.monitor.store.read()
        self.assertFalse(warnings)
        self.assertIsNone(rows[0].run_id)
        self.assertEqual(rows[0].project_id, PROJECT_ID)
        for changes in ({"runId": "studio-projection"}, {"projectId": "missing-project"},
                        {"sourceRef": "a" * 64}):
            answer = self.client.post("/api/events/timing", json={**body, **changes})
            self.assertIn(answer.status_code, (404, 409), answer.text)
