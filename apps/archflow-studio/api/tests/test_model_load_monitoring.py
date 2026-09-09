"""Browser wait is optional diagnostics bound to a retained project/run source."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from monkeymonitor.store import UsageLog

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project


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
