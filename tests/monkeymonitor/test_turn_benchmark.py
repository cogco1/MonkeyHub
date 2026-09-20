"""The manual benchmark must not promote missing evidence to success."""
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tests.monkeymonitor import run_turn_benchmark as benchmark


class BenchmarkTests(unittest.TestCase):
    def test_monitor_busy_does_not_abort_provider_but_other_errors_are_visible(self):
        failures = []
        with patch.object(benchmark, "request", side_effect=HTTPError("http://localhost", 503, "busy", {}, BytesIO(b'journal busy'))):
            self.assertIsNone(benchmark.monitor_snapshot("http://localhost", failures))
        self.assertEqual(failures, [{"status": 503, "detail": "journal busy"}])
        with patch.object(benchmark, "request", side_effect=HTTPError("http://localhost", 500, "error", {}, BytesIO(b'error'))):
            with self.assertRaises(HTTPError):
                benchmark.monitor_snapshot("http://localhost", failures)

    def test_actual_candidate_satisfies_geometry_and_authored_checks(self):
        from fastapi.testclient import TestClient
        from archflow_studio_api.main import create_app
        from archflow_studio_api.settings import StudioSettings

        with tempfile.TemporaryDirectory() as temporary:
            fixture = benchmark.project_fixture()
            repository, _ = fixture.make_project(Path(temporary))
            before = repository.layout.head.read_bytes()
            with TestClient(create_app(StudioSettings(project_dir=repository.layout.root, cad_export="occt"))) as client:
                for scenario in ("incremental-edit", "assembly-edit"):
                    fields = copy.deepcopy(next(row["fields"] for row in fixture.RECORD_PAYLOAD["entities"] if row["entity_id"] == "portico-cornice"))
                    fields["params"]["height"] = 0.5
                    entities = [{"entity_id": "portico-cornice", "fields": fields}]
                    if scenario == "assembly-edit":
                        base = copy.deepcopy(next(row["fields"] for row in fixture.RECORD_PAYLOAD["entities"] if row["entity_id"] == "portico-base"))
                        base["params"]["height"] = 0.8
                        entities.append({"entity_id": "portico-base", "fields": base})
                    response = client.post("/api/proposals", json={
                        "sourceRunId": fixture.REFERENCE_RUN_ID,
                        "stateDigest": client.get("/api/state", params={"run": fixture.REFERENCE_RUN_ID}).json()["stateDigest"],
                        "semanticEdit": {"summary": "Bounded benchmark preflight", "entities": entities}})
                    self.assertEqual(response.status_code, 201, response.text)
                    submitted = client.post(f"/api/proposals/{response.json()['proposalId']}/candidate")
                    self.assertEqual(submitted.status_code, 202, submitted.text)
                    job = submitted.json()
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        status = client.get(f"/api/jobs/{job['jobId']}").json()
                        if status["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.1)
                    self.assertEqual(status["status"], "succeeded", status)
                    candidate = client.get(f"/api/candidates/{job['candidateId']}").json()
                    self.assertTrue(benchmark.expected_geometry(candidate, scenario), candidate)
                    self.assertTrue(benchmark.expected_authored_fields(repository, job['candidateId'], scenario, fixture))
                    self.assertEqual(repository.layout.head.read_bytes(), before)

    def test_unknown_usage_and_model_rounds_stay_unknown(self):
        trace = {"summary": {"elapsed_ms": 100, "model_rounds": None, "provider_rounds": 2, "tool_rounds": 1},
                 "usage": {"tokens": {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None}},
                 "spans": [{"phase": "tool_call", "status": "failed", "details": {"retry_attempt": 1}}]}
        metrics = benchmark.measured_metrics(trace)
        self.assertIsNone(metrics["input_tokens"])
        self.assertIsNone(metrics["model_calls"])
        self.assertIsNone(metrics["provider_internal_retries"])
        self.assertEqual(metrics["observed_failed_tools"], 1)
        self.assertEqual(metrics["observed_retry_spans"], 1)

    def test_geometry_rejects_detached_support_nan_and_duplicate_objects(self):
        readback = {"objects": [
            {"producerOp": "portico-base", "lengthUnit": "meter", "upAxis": "Z-up", "bbox": {"min": [0, 0, 0], "max": [4, 2, 0.8]}},
            {"producerOp": "portico-cornice", "lengthUnit": "meter", "upAxis": "Z-up", "bbox": {"min": [0, 0, 0.8], "max": [4, 2, 1.3]}}]}
        self.assertTrue(benchmark.expected_geometry(readback, "assembly-edit"))
        wrong = copy.deepcopy(readback)
        wrong["objects"][1]["bbox"]["min"][2] = 0.6
        self.assertFalse(benchmark.expected_geometry(wrong, "assembly-edit"))
        wrong["objects"][1]["bbox"]["min"][2] = float("nan")
        self.assertFalse(benchmark.expected_geometry(wrong, "assembly-edit"))
        wrong = copy.deepcopy(readback)
        wrong["objects"].append(wrong["objects"][0])
        self.assertFalse(benchmark.expected_geometry(wrong, "assembly-edit"))

    def test_pair_preparation_uses_identical_retained_bytes_and_refuses_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(output=root, scenario="incremental-edit", model="explicit-test-model",
                                   order="project,continue", prepare_only=True)
            benchmark.paired_benchmark(args, {"provider": "codex"})
            before = json.loads((root / "prepared.json").read_text())
            self.assertEqual(before["order"], "project,continue")
            for condition in ("continue", "project"):
                repo = benchmark.FilesystemProjectRepository.open(root / condition / "projects/demo-project")
                self.assertEqual(benchmark.source_identity(repo), before["source"])
            # A completed or failed measurement cannot silently become a repeat.
            (root / "project/trace.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "already run"):
                benchmark.paired_benchmark(args, {"provider": "codex"})


if __name__ == "__main__":
    unittest.main()
