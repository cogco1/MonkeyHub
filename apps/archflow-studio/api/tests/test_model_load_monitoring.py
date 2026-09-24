"""Browser wait is optional diagnostics bound to a retained project/run source."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
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


def latest(events):
    """Merge revisions by event id, exactly as reading the journal does."""

    merged = {}
    for event in events:
        merged[event.event_id] = event
    return list(merged.values())


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

    def model_upload(self, data=None):
        import base64
        if data is None:
            data = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        return self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID,
            "stateDigest": runner_state_digest(self.repository, REFERENCE_RUN_ID),
            "fileName": "private-model.3dm", "contentBase64": base64.b64encode(data).decode(),
        })

    def test_ingestion_cold_then_retry_reuses_exact_bytes_without_inspection(self):
        from archflow_studio_api.application.artifacts import inspect_three_dm_contents
        head = self.repository.layout.head.read_bytes()
        captured = self.emissions()
        with patch("archflow_studio_api.application.artifacts.inspect_three_dm_contents",
                   wraps=inspect_three_dm_contents) as inspect:
            first = self.model_upload()
            second = self.model_upload()
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(inspect.call_count, 1)
        rows = latest(captured)
        operations = [row for row in rows if row.phase == "model_ingest"]
        self.assertEqual([row.details["cache_status"] for row in operations], ["miss", "hit"])
        for root in operations:
            self.assertEqual(root.status, "succeeded")
            self.assertEqual(root.details["input_identity"]["asset_sha256"], first.json()["sha256"])
            children = [row for row in rows if row.parent_event_id == root.event_id]
            self.assertTrue(children)
            self.assertTrue(all(row.operation_id == root.operation_id for row in children))
            self.assertTrue(all(row.duration_ms >= 0 for row in children))
            phases = {row.phase for row in children}
            self.assertEqual("model_ingest.inspect" in phases, root.details["cache_status"] == "miss")
            self.assertEqual("model_ingest.persist" in phases, root.details["cache_status"] == "miss")
        self.assertNotIn("private-model", repr([row.to_dict() for row in rows]))
        self.assertEqual(self.repository.layout.head.read_bytes(), head)

    def test_ingestion_new_revision_preserves_old_bytes_and_survives_restart(self):
        first = self.model_upload()
        changed = (Path(__file__).parent / "fixtures/model-source-b.3dm").read_bytes()
        second = self.model_upload(changed)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertNotEqual(first.json()["sha256"], second.json()["sha256"])
        with TestClient(create_app(self.settings)) as restarted:
            for response, expected in ((first, (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()),
                                       (second, changed)):
                download = restarted.get(f"/api/artifacts/{response.json()['sha256']}/bytes",
                                         params={"runId": REFERENCE_RUN_ID})
                self.assertEqual(download.status_code, 200, download.text if download.status_code != 200 else "")
                self.assertEqual(download.content, expected)
            with patch("archflow_studio_api.application.artifacts.inspect_three_dm_contents",
                       side_effect=AssertionError("unchanged registered source must not be re-inspected")):
                from .test_working_copies import register_model
                repeated = register_model(restarted, REFERENCE_RUN_ID,
                                          runner_state_digest(self.repository, REFERENCE_RUN_ID), changed)
            self.assertEqual(repeated["modelSource"], second.json()["modelSource"])

    def test_ingestion_failure_is_measured_and_diagnostic_failure_is_nonfatal(self):
        captured = self.emissions()
        response = self.model_upload(b"3D Geometry File Format invalid")
        self.assertEqual(response.status_code, 422, response.text)
        rows = latest(captured)
        failed = {row.phase for row in rows if row.status == "failed"}
        self.assertIn("model_ingest", failed)
        self.assertIn("model_ingest.inspect", failed)
        self.assertNotIn("model_ingest.persist", {row.phase for row in rows})
        with patch.object(self.app.state.monitor.store, "append", side_effect=OSError("offline")):
            with self.assertLogs("archflow_studio_api.application.monitoring", level="WARNING"):
                response = self.model_upload()
        self.assertEqual(response.status_code, 201, response.text)
        # A failed import released the registration lock and retained no bad source.
        self.assertEqual(len(self.client.get("/api/artifacts").json()["artifacts"]), 1)

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

    def emissions(self):
        """Every event the host emits, taken at the append boundary.

        Simultaneous requests still write the real journal, which may skip an
        observation while it is busy; association is a property of what was
        emitted, so it is asserted here rather than on what survived.
        """

        captured, guard = [], Lock()
        store = self.app.state.monitor.store
        original = store.append

        def capture(event):
            with guard:
                captured.append(event)
            original(event)

        self.enterContext(patch.object(store, "append", capture))
        return captured

    def test_concurrent_request_context_reaches_sync_compiler_without_cross_linking(self):
        operations = [str(uuid4()), str(uuid4())]
        digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)
        self.app.state.intent_compiler = MonitoredCompiler(
            scripted(utterance="set height to 2.2", component_id="portico", element_id="portico-base"), self.app.state.monitor)
        emitted = self.emissions()
        def submit(operation):
            return self.client.post("/api/intents", headers={"X-Monkey-Operation": operation, "X-Monkey-Parent": operation},
                json={"projectId": PROJECT_ID, "stateDigest": digest, "utterance": "make the portico base taller", "targetComponentId": "portico", "elementId": "portico-base"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, operations))
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(self.app.state.monitor.store.read()[1])
        rows = latest(emitted)
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
        emitted = self.emissions()
        def submit(turn):
            return self.client.post("/api/intents", headers={
                "x-monkey-turn-id": turn, "x-monkey-parent-span-id": f"hub:turn:{turn}"},
                json={"projectId": PROJECT_ID, "stateDigest": digest, "utterance": "make the portico base taller",
                      "targetComponentId": "portico", "elementId": "portico-base"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, turns))
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(self.app.state.monitor.store.read()[1])
        rows = latest(emitted)
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


class ExternalModelImportTests(unittest.TestCase):
    def test_empty_project_retains_external_revisions_without_design_state(self):
        import base64
        from .support import make_empty_project
        from archflow_studio_api.application.binding import bound_project
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = make_empty_project(root)
            settings = StudioSettings(project_dir=root / PROJECT_ID, cad_export="off")
            head = repository.layout.head.read_bytes()
            app = create_app(settings)
            with TestClient(app) as client:
                rows = []
                for name in ("a", "b"):
                    data = (Path(__file__).parent / f"fixtures/model-source-{name}.3dm").read_bytes()
                    response = client.post("/api/model-assets", json={"projectId": PROJECT_ID,
                        "fileName": "external.3dm", "contentBase64": base64.b64encode(data).decode()})
                    self.assertEqual(response.status_code, 201, response.text)
                    row = response.json()
                    self.assertEqual(row["representation"], "external")
                    self.assertIsNone(row["modelSource"])
                    self.assertIsNone(row["designStateDigest"])
                    self.assertIsNone(row["sourceStageRef"])
                    rows.append((row, data))
                self.assertNotEqual(rows[0][0]["sha256"], rows[1][0]["sha256"])
                self.assertEqual(len(client.get("/api/artifacts").json()["artifacts"]), 2)
                self.assertIsNone(bound_project(app.state).newest_runner_receipt(rows[0][0]["runId"]))
            with TestClient(create_app(settings)) as restarted:
                for row, data in rows:
                    downloaded = restarted.get(f"/api/artifacts/{row['sha256']}/bytes")
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.content, data)
                with patch("archflow_studio_api.application.artifacts.inspect_three_dm_contents",
                           side_effect=AssertionError("repeat import must reuse the registered source")):
                    repeated = restarted.post("/api/model-assets", json={"projectId": PROJECT_ID,
                        "fileName": "renamed.3dm", "contentBase64": base64.b64encode(rows[0][1]).decode()})
                self.assertEqual(repeated.status_code, 201, repeated.text)
                self.assertEqual(repeated.json()["receiptRef"], rows[0][0]["receiptRef"])
            self.assertEqual(repository.layout.head.read_bytes(), head)

    def test_invalid_external_model_and_partial_binding_create_no_source_run(self):
        import base64
        from .support import make_empty_project
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = make_empty_project(root)
            app = create_app(StudioSettings(project_dir=root / PROJECT_ID, cad_export="off"))
            with TestClient(app) as client:
                body = {"projectId": PROJECT_ID, "fileName": "invalid.3dm",
                        "contentBase64": base64.b64encode(b"3D Geometry File Format broken").decode()}
                for extra in ({}, {"runId": "missing"}, {"stateDigest": "a" * 64}):
                    response = client.post("/api/model-assets", json=body | extra)
                    self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(client.get("/api/artifacts").json()["artifacts"], [])
            self.assertEqual(list(repository.layout.runs.iterdir()), [])
