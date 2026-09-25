"""Hub operation recovery uses real Studio runs and committed P036 history."""

import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, RUNNER_RUN_RECEIPT
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.application.binding import ProjectBinding, bound_project
from archflow_studio_api.application.runtime import inspect_runtime
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.runtime import runtime_dto
from monkeyhub_api.models import HubFailure
from monkeyhub_api.runtime import HttpResult, OperationManager, ProjectRuntime, ProjectRuntimeManager

from test_monkeyhub_lifecycle import project_fixture


class OperationRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-operation-recovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = project_fixture()
        self.repository, _ = self.fixture.make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / self.fixture.PROJECT_ID, cad_export="off")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.jobs.shutdown)
        self.manager = OperationManager(self.fixture.PROJECT_ID)
        self.state_digest = self.fixture.runner_state_digest(self.repository, self.fixture.REFERENCE_RUN_ID)

    def snapshot(self, *, live=False):
        binding = bound_project(self.app.state) if live else ProjectBinding.open(self.settings)
        return runtime_dto(inspect_runtime(binding,
            jobs=self.app.state.jobs if live else None, candidate_ids=self.manager.candidate_ids())).model_dump(by_alias=True)

    def admission(self, path, payload=None, *, operation_id=None):
        body = b"" if payload is None else json.dumps(payload, sort_keys=True).encode()
        admission, fresh = self.manager.admit(operation_id or str(uuid4()), "POST", path, body,
            retained=self.snapshot(), source="studio", session_id=None)
        self.assertTrue(fresh)
        return admission, body

    def finished(self, job_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                self.assertEqual(job["status"], "succeeded", job)
                return job
            time.sleep(0.02)
        self.fail(f"Job {job_id} did not finish")

    def candidate(self, *, initial=None):
        payload = {"projectId": self.fixture.PROJECT_ID, "stateDigest": self.state_digest,
                   "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 2.2"}
        if initial:
            payload.update(sourceRunId=initial["candidateId"], sourceStageRef=initial["stageRef"],
                           stateDigest=initial["modelSource"]["stateDigest"])
        proposal = self.client.post("/api/proposals", json=payload)
        self.assertEqual(proposal.status_code, 201, proposal.text)
        path = f"/api/proposals/{proposal.json()['proposalId']}/candidate"
        admission, _ = self.admission(path)
        # Allocate the exact run before dispatch, then lose the HTTP reply.
        # Only id allocation is controlled; Studio executes and retains the run.
        with patch("archflow_studio_api.routes.candidates._run_id", return_value=admission.record.candidateId):
            accepted = self.client.post(path)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.manager.interrupted(admission, "injected lost candidate response")
        self.finished(accepted.json()["jobId"])
        return admission, accepted.json()

    def model(self, run_id, state_digest):
        data = (ROOT / "apps/archflow-studio/api/tests/fixtures/model-source-a.3dm").read_bytes()
        response = self.client.post("/api/model-assets", json={"projectId": self.fixture.PROJECT_ID,
            "runId": run_id, "stateDigest": state_digest, "fileName": "model.3dm",
            "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["modelSource"]

    def acceptance(self):
        model = self.model(self.fixture.REFERENCE_RUN_ID, self.state_digest)
        response = self.client.post("/api/design-stages/initialize", json={"projectId": self.fixture.PROJECT_ID, "modelSource": model})
        self.assertEqual(response.status_code, 201, response.text)
        initial = response.json()
        _, candidate = self.candidate(initial=initial)
        candidate_id = candidate["candidateId"]
        state = self.client.get("/api/state", params={"run": candidate_id, "sourceStageRef": initial["stageRef"]}).json()
        self.model(candidate_id, state["stateDigest"])
        payload = {"projectId": self.fixture.PROJECT_ID, "branchId": "main", "expectedHeadStageRef": initial["stageRef"]}
        path = f"/api/candidates/{candidate_id}/accept"
        admission, _ = self.admission(path, payload)
        return initial, candidate_id, path, payload, admission

    def record(self, admission):
        return next(row for row in self.manager.records() if row.operationId == admission.record.operationId)

    def durable_manager(self, *, project_id=None, project_dir=None):
        return OperationManager(project_id or self.fixture.PROJECT_ID,
            project_dir=str(project_dir or self.settings.project_dir),
            journal_path=self.root / "hub-runtime/runtime/operations/project.json")

    def test_cold_start_refuses_unretained_dispatched_request_and_changed_key(self):
        self.manager = self.durable_manager()
        coordinator = ProjectRuntimeManager(None, None)
        runtime = ProjectRuntime("journal-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        worker = SimpleNamespace(url="http://127.0.0.1:1", instance_id="test-worker")
        operation_id = str(uuid4())
        body = b'{"privateRequestBody":"must never be stored"}'
        with patch.object(coordinator, "service", return_value=worker), \
             patch("monkeyhub_api.runtime.request_http", side_effect=OSError("lost worker before retained output")) as forwarded:
            with self.assertRaises(HubFailure) as failure:
                coordinator.forward(runtime, "/api/program", "POST", body, {"idempotency-key": operation_id})
            self.assertEqual(failure.exception.error.code, "OPERATION_INTERRUPTED")
            self.assertEqual(forwarded.call_count, 1)
        self.assertNotIn("privateRequestBody", self.manager.journal_path.read_text(encoding="utf-8"))
        runtime.operations = self.durable_manager()  # No process-local state survives.
        with patch("monkeyhub_api.runtime.request_http", side_effect=AssertionError("cold replay")) as forwarded:
            for repeated_body, expected in ((body, "OPERATION_NEEDS_RECOVERY"), (b"{}", "OPERATION_ID_CONFLICT")):
                with self.subTest(expected=expected), self.assertRaises(HubFailure) as failure:
                    coordinator.forward(runtime, "/api/program", "POST", repeated_body, {"idempotency-key": operation_id})
                self.assertEqual(failure.exception.error.code, expected)
            forwarded.assert_not_called()
        restored = runtime.operations.records()[0]
        self.assertEqual(restored.status, "needs_recovery")
        self.assertFalse(restored.committed)
        self.assertEqual(bound_project(self.app.state).run_ids(), (self.fixture.REFERENCE_RUN_ID,))

    def test_operation_log_failure_prevents_dispatch_and_does_not_admit_request(self):
        self.manager = self.durable_manager()
        coordinator = ProjectRuntimeManager(None, None)
        runtime = ProjectRuntime("journal-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        with patch("monkeyhub_api.runtime.os.replace", side_effect=OSError("disk write failed")), \
             patch("monkeyhub_api.runtime.request_http", side_effect=AssertionError("dispatch without durable identity")) as forwarded:
            with self.assertRaises(HubFailure) as failure:
                coordinator.forward(runtime, "/api/program", "POST", b"{}", {"idempotency-key": str(uuid4())})
            self.assertEqual(failure.exception.error.code, "OPERATION_LOG_UNAVAILABLE")
            forwarded.assert_not_called()
        self.assertEqual(self.manager.records(), [])
        self.assertFalse(self.manager.journal_path.exists())

    def test_operation_journal_does_not_copy_response_details_or_request_content(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/program", {"private": "private input text"})
        self.manager.replied(admission, HttpResult(422,
            b'{"detail":"Invalid private input text"}', {"content-type": "application/json"}))
        self.assertIn("private input text", self.record(admission).reason)
        self.assertNotIn("private input text", self.manager.journal_path.read_text(encoding="utf-8"))
        self.assertEqual(self.durable_manager().records()[0].status, "failed")

    def test_new_operation_keeps_its_admission_time_and_older_rows_have_none(self):
        self.manager = self.durable_manager()
        before = datetime.now(timezone.utc)
        admission, _ = self.admission("/api/drawings/sheets", {})
        admitted = self.record(admission).createdAt
        self.assertTrue(before <= datetime.fromisoformat(admitted) <= datetime.now(timezone.utc))
        self.assertEqual(self.durable_manager().records()[0].createdAt, admitted, "the time is kept with the journal row")
        # A journal written before admission times were kept still restores, without one.
        saved = json.loads(self.manager.journal_path.read_text(encoding="utf-8"))
        del saved["operations"][0]["record"]["createdAt"]
        self.manager.journal_path.write_text(json.dumps(saved), encoding="utf-8")
        self.assertIsNone(self.durable_manager().records()[0].createdAt)

    def test_dismissed_failure_stays_dismissed_after_restart_and_is_otherwise_unchanged(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/drawings/sheets", {"private": "private sheet request"})
        self.manager.replied(admission, HttpResult(422, b'{"code":"SHEET_REFUSED","detail":"private refusal text"}', {}))
        before = self.record(admission)
        self.assertIsNone(before.acknowledgedAt)
        dismissed = self.manager.acknowledge(admission.record.operationId)
        self.assertTrue(dismissed.acknowledgedAt)
        self.assertEqual(dismissed.model_dump(exclude={"acknowledgedAt"}), before.model_dump(exclude={"acknowledgedAt"}),
                         "a dismissal changes no status, reason, order or result")
        self.assertEqual(self.record(admission).acknowledgedAt, dismissed.acknowledgedAt)
        self.assertEqual(self.manager.acknowledge(admission.record.operationId).acknowledgedAt, dismissed.acknowledgedAt,
                         "dismissing again keeps the first time")
        self.assertNotIn("private", self.manager.journal_path.read_text(encoding="utf-8"))
        restored = self.durable_manager().records()[0]
        self.assertEqual((restored.status, restored.acknowledgedAt), ("failed", dismissed.acknowledgedAt))

    def test_stale_operation_can_be_dismissed(self):
        admission, _ = self.admission("/api/proposals", {})
        self.manager.replied(admission, HttpResult(409, b'{"code":"PROPOSAL_BASE_STALE","detail":"The base moved."}', {}))
        self.assertEqual(self.record(admission).status, "stale")
        self.assertTrue(self.manager.acknowledge(admission.record.operationId).acknowledgedAt)
        self.assertTrue(self.record(admission).acknowledgedAt)

    def test_operation_that_needs_recovery_cannot_be_dismissed(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/proposals", {})
        self.manager.interrupted(admission, "lost reply")
        for operation_id, code in ((admission.record.operationId, "OPERATION_NOT_ACKNOWLEDGEABLE"),
                                   (str(uuid4()), "OPERATION_NOT_FOUND")):
            with self.subTest(code=code), self.assertRaises(HubFailure) as refusal:
                self.manager.acknowledge(operation_id)
            self.assertEqual(refusal.exception.error.code, code)
        self.assertNotIn("acknowledged", json.loads(self.manager.journal_path.read_text(encoding="utf-8")))
        restored = self.durable_manager().records()[0]
        self.assertEqual((restored.status, restored.acknowledgedAt), ("needs_recovery", None))

    def test_dismissal_that_cannot_be_saved_leaves_the_notice(self):
        self.manager = self.durable_manager()
        admission, _ = self.admission("/api/drawings/sheets", {})
        self.manager.replied(admission, HttpResult(422, b'{"detail":"refused"}', {}))
        with patch("monkeyhub_api.runtime.os.replace", side_effect=OSError("disk write failed")), \
             self.assertRaises(HubFailure) as failure:
            self.manager.acknowledge(admission.record.operationId)
        self.assertEqual(failure.exception.error.code, "OPERATION_LOG_UNAVAILABLE")
        self.assertIsNone(self.record(admission).acknowledgedAt)
        self.assertIsNone(self.durable_manager().records()[0].acknowledgedAt)

    def test_dismissed_retained_failure_is_kept_by_id_until_it_reads_otherwise(self):
        self.manager = self.durable_manager()
        retained = {"candidates": [{"candidateId": "old-run", "status": "failed", "error": "incomplete"}],
                    "jobs": [], "stages": [], "branches": []}
        self.manager.reconcile(retained, worker_alive=False)
        dismissed = self.manager.acknowledge("candidate:old-run")
        self.assertEqual((dismissed.source, dismissed.status), ("retained", "failed"))
        restarted = self.durable_manager()
        restarted.reconcile(retained, worker_alive=False)
        observed = lambda manager: next(row for row in manager.records() if row.operationId == "candidate:old-run")
        self.assertEqual(observed(restarted).acknowledgedAt, dismissed.acknowledgedAt)
        # The same run read later as needing recovery is reported again, undismissed.
        restarted.reconcile({**retained, "candidates": [{"candidateId": "old-run", "status": "needs_recovery"}]}, worker_alive=False)
        self.assertEqual((observed(restarted).status, observed(restarted).acknowledgedAt), ("needs_recovery", None))

    def test_dismiss_route_is_bound_to_its_runtime_and_project(self):
        from monkeyhub_api.chat import _project
        from monkeyhub_api.main import HubSettings, create_app as create_hub
        hub = create_hub(HubSettings(runtime_root=self.root / "hub-app-runtime"))
        client = TestClient(hub, base_url="http://127.0.0.1:8790")  # Without its lifespan, nothing is started.
        self.addCleanup(client.close)
        project_id, project_dir = _project(str(self.settings.project_dir))
        self.manager = self.durable_manager()
        runtime = ProjectRuntime("route-runtime", project_id, project_dir, self.manager, ProjectBinding.open(self.settings))
        hub.state.runtimes._projects[runtime.runtime_id] = runtime
        failed, _ = self.admission("/api/drawings/sheets", {})
        self.manager.replied(failed, HttpResult(422, b'{"detail":"refused"}', {}))
        waiting, _ = self.admission("/api/proposals", {})
        self.manager.interrupted(waiting, "lost reply")

        def dismiss(operation_id, **body):
            return client.post(f"/api/runtime/operations/{operation_id}/acknowledge",
                               json={"runtimeId": runtime.runtime_id, "projectId": project_id, **body})

        answered = dismiss(failed.record.operationId)
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual((answered.json()["operationId"], answered.json()["status"]), (failed.record.operationId, "failed"))
        self.assertTrue(answered.json()["acknowledgedAt"])
        listed = {row["operationId"]: row for row in client.get(f"/api/runtime/projects/{runtime.runtime_id}").json()["operations"]}
        self.assertEqual(listed[failed.record.operationId]["acknowledgedAt"], answered.json()["acknowledgedAt"])
        self.assertIsNone(listed[waiting.record.operationId]["acknowledgedAt"])
        for operation_id, body, status, code in (
                (waiting.record.operationId, {}, 409, "OPERATION_NOT_ACKNOWLEDGEABLE"),
                (str(uuid4()), {}, 404, "OPERATION_NOT_FOUND"),
                (failed.record.operationId, {"projectId": "another-project"}, 409, "PROJECT_MISMATCH"),
                (failed.record.operationId, {"runtimeId": "another-runtime"}, 404, "RUNTIME_NOT_FOUND")):
            with self.subTest(code=code):
                refused = dismiss(operation_id, **body)
                self.assertEqual((refused.status_code, refused.json()["code"]), (status, code))
        self.assertEqual(dismiss(failed.record.operationId, note="extra").status_code, 422)

    def test_cold_operation_binding_refuses_other_project_or_same_id_at_other_path(self):
        self.manager = self.durable_manager()
        self.admission("/api/program", {})
        for binding in ({"project_id": "another-project"}, {"project_dir": self.root / "another-copy"}):
            with self.subTest(binding=binding), self.assertRaises(HubFailure) as failure:
                self.durable_manager(**binding)
            self.assertEqual(failure.exception.error.code, "OPERATION_LOG_INVALID")

    def test_cold_start_after_commit_reconciles_from_p036_without_reexecution(self):
        self.manager = self.durable_manager()
        _, _, path, payload, admission = self.acceptance()
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.manager.interrupted(admission, "lost committed response")
        stages_before = self.snapshot()["stages"]
        self.manager = self.durable_manager()
        self.assertFalse(self.record(admission).committed)
        self.assertIsNone(self.record(admission).resultDigest)
        with patch("archflow_studio_api.application.candidate.execute_candidate", side_effect=AssertionError("cold replay")):
            self.manager.reconcile(self.snapshot(), worker_alive=False)
        record = self.record(admission)
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.committed)
        self.assertEqual(record.resultDigest, response.json()["recordDigest"])
        self.assertEqual(self.snapshot()["stages"], stages_before)

    def test_cold_candidate_order_uses_admissions_and_verified_receipts_without_jobs(self):
        self.manager = self.durable_manager()
        older, _ = self.candidate()
        newer, _ = self.candidate()
        unfinished, _ = self.admission("/api/proposals/unexecuted/candidate")
        refused, _ = self.admission("/api/program", {})
        self.manager.replied(refused, HttpResult(422, b'{"detail":"refused"}', {}))
        journal_before = self.manager.journal_path.read_bytes()
        runs_before = bound_project(self.app.state).run_ids()
        head_before = self.repository.read_head()

        cold_app = create_app(self.settings)
        self.addCleanup(cold_app.state.jobs.shutdown)
        cold = self.durable_manager()
        self.assertTrue(all(row.resultDigest is None for row in cold.records()))
        with TestClient(cold_app) as client, patch(
            "archflow_studio_api.application.candidate.execute_candidate", side_effect=AssertionError("cold replay"),
        ):
            response = client.get("/api/runtime", params=[("candidateId", row.record.candidateId)
                for row in (older, newer, unfinished)])
            self.assertEqual(response.status_code, 200, response.text)
            retained = response.json()
            self.assertEqual(retained["jobs"], [], "a fresh Studio has no process jobs or job timestamps")
            cold.reconcile(retained, worker_alive=False)
        records = {row.operationId: row for row in cold.records()}
        successful = [row for row in records.values() if row.status == "completed" and row.resultDigest]
        self.assertEqual([row.candidateId for row in sorted(successful, key=lambda row: row.admissionSequence)],
                         [older.record.candidateId, newer.record.candidateId])
        self.assertEqual(records[unfinished.record.operationId].status, "needs_recovery")
        self.assertEqual(records[refused.record.operationId].status, "failed")
        self.assertEqual([records[row.record.operationId].admissionSequence for row in (older, newer, unfinished, refused)],
                         [1, 2, 3, 4])
        for row in successful:
            candidate = next(item for item in retained["candidates"] if item["candidateId"] == row.candidateId)
            self.assertTrue(candidate["receiptRef"])
            self.assertEqual(row.resultDigest, candidate["resultStateDigest"])
        self.assertEqual(self.manager.journal_path.read_bytes(), journal_before, "reading order does not rewrite the journal")
        self.assertNotIn(b"admissionSequence", journal_before, "order is projected from the existing array, not newly persisted")
        self.assertEqual(bound_project(self.app.state).run_ids(), runs_before)
        self.assertEqual(self.repository.read_head(), head_before)

    def test_lost_candidate_reply_recovers_exact_retained_result_without_resubmission(self):
        admission, accepted = self.candidate()
        before_runs = bound_project(self.app.state).run_ids()
        retained = self.snapshot()
        candidate = next(row for row in retained["candidates"] if row["candidateId"] == accepted["candidateId"])
        with patch("archflow_studio_api.application.candidate.execute_candidate", side_effect=AssertionError("replayed")):
            self.manager.reconcile(retained, worker_alive=False)
            repeated, fresh = self.manager.admit(admission.record.operationId, *admission.signature[:2], b"",
                retained=retained, source="studio", session_id=None)
        self.assertFalse(fresh)
        self.assertIs(repeated, admission)
        record = self.record(admission)
        self.assertEqual(record.status, "completed")
        self.assertFalse(record.committed)
        self.assertEqual(record.baseDigest, candidate["baseStateDigest"])
        self.assertEqual(record.resultDigest, candidate["resultStateDigest"])
        delta = bound_project(self.app.state).candidate_delta(record.candidateId)
        self.assertEqual(record.baseDigest, delta["operator"]["base_state_digest"])
        self.assertEqual(candidate["resultRecordDigest"], delta["result_record_digest"])
        self.assertEqual(bound_project(self.app.state).run_ids(), before_runs)
        restarted = OperationManager(self.fixture.PROJECT_ID)
        restarted.reconcile(retained, worker_alive=False)
        observed = next(row for row in restarted.records() if row.candidateId == record.candidateId)
        self.assertEqual(observed.status, "completed")
        self.assertEqual(observed.source, "retained")
        self.assertIsNone(observed.admissionSequence, "a receipt without an admission journal cannot invent request order")

    def test_runtime_pages_revalidate_completed_and_new_candidates_without_a_full_history_probe(self):
        first, first_result = self.candidate()
        second, second_result = self.candidate()
        binding = bound_project(self.app.state)
        snapshot = self.snapshot(live=True)
        self.manager.reconcile(snapshot, worker_alive=True)
        runtime = ProjectRuntime("bounded-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, binding, retained=snapshot)
        manager = ProjectRuntimeManager(None, None)
        worker = SimpleNamespace(url="http://owned-worker/")
        queried = []

        def bounded_read(_url, path, **_kwargs):
            query = parse_qs(urlsplit(path).query)
            # Model the reported failure: a broad history response exceeds
            # the transport budget, while the existing exact API can finish.
            if query.get("limit") != ["1"] or len(query.get("candidateId", [])) > 1:
                raise TimeoutError("a full project history cannot finish in one probe")
            response = self.client.get(path)
            queried.extend(row["candidateId"] for row in response.json()["candidates"])
            return HttpResult(response.status_code, response.content, dict(response.headers))

        with patch("monkeyhub_api.runtime.request_http", side_effect=bounded_read):
            runtime.retained = manager._read_retained(runtime, worker=worker)
            self.assertIn(first_result["candidateId"], queried)
            self.assertIn(second_result["candidateId"], queried)
            self.assertEqual({row["candidateId"] for row in runtime.retained["candidates"]},
                             {first_result["candidateId"], second_result["candidateId"]})
            self.assertTrue(all(row["status"] == "completed" for row in runtime.retained["candidates"]))

            # A new run must be found even if its operation is not in this
            # Hub's journal; the project remains the source of its identity.
            third, third_result = self.candidate()
            self.manager._operations.pop(third.record.operationId)
            queried.clear()
            runtime.retained = manager._read_retained(runtime, worker=worker)
            self.assertIn(third_result["candidateId"], queried)
            self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                  if row["candidateId"] == third_result["candidateId"]), "completed")

            # Every completed run is still checked. Losing its retained
            # evidence must replace the old completed observation, and the
            # same run can complete again when that evidence becomes readable.
            row = next(row for row in runtime.retained["candidates"] if row["candidateId"] == second_result["candidateId"])
            receipt_path = binding.repository.layout.resolve_record(record_ref_from_uri(row["receiptRef"], self.fixture.PROJECT_ID))
            receipt_bytes = receipt_path.read_bytes()
            receipt_path.unlink()
            try:
                runtime.retained = manager._read_retained(runtime, worker=worker)
                self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                      if row["candidateId"] == second_result["candidateId"]), "needs_recovery")
            finally:
                receipt_path.write_bytes(receipt_bytes)
            runtime.retained = manager._read_retained(runtime, worker=worker)
            self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                  if row["candidateId"] == second_result["candidateId"]), "completed")

            # Cold/recovery reads do not reuse even a formerly completed row.
            row = next(row for row in runtime.retained["candidates"] if row["candidateId"] == first_result["candidateId"])
            receipt_path = binding.repository.layout.resolve_record(record_ref_from_uri(row["receiptRef"], self.fixture.PROJECT_ID))
            receipt_bytes = receipt_path.read_bytes()
            receipt_path.unlink()
            try:
                cold = manager._read_retained(runtime)
                self.assertEqual(next(row["status"] for row in cold["candidates"]
                                      if row["candidateId"] == first_result["candidateId"]), "needs_recovery")
            finally:
                receipt_path.write_bytes(receipt_bytes)

    def test_slow_projection_preserves_verified_candidate_and_rebuilds_for_a_new_worker(self):
        admission, accepted = self.candidate()
        binding = bound_project(self.app.state)
        runtime = ProjectRuntime("projection-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, binding)
        worker = SimpleNamespace(url="http://owned-worker/", instance_id="worker-a", state="ready", healthy=True)
        applications = SimpleNamespace(worker_snapshots=lambda **_kwargs: (worker,), set_busy=lambda **_kwargs: None)
        manager = ProjectRuntimeManager(applications, None)
        state_reads = []

        def read(_url, path, **_kwargs):
            if path == "/api/state":
                state_reads.append(worker.instance_id)
                if len(state_reads) == 1:
                    raise TimeoutError("projection still rebuilding")
            response = self.client.get(path)
            return HttpResult(response.status_code, response.content, dict(response.headers))

        with patch("monkeyhub_api.runtime.request_http", side_effect=read):
            with self.assertRaises(TimeoutError):
                manager.refresh(runtime)
            self.assertNotEqual(runtime.projection, "ready")
            self.assertEqual(self.record(admission).status, "completed")
            self.assertEqual(next(row["status"] for row in runtime.retained["candidates"]
                                  if row["candidateId"] == accepted["candidateId"]), "completed")
            manager.refresh(runtime)
            self.assertEqual(runtime.projection, "ready")
            manager.refresh(runtime)
            self.assertEqual(state_reads, ["worker-a", "worker-a"], "same worker and base reuse the ready projection")
            worker.instance_id = "worker-b"
            manager.refresh(runtime)
            self.assertEqual(runtime.projection, "ready")
            self.assertEqual(state_reads[-1], "worker-b", "a new process must rebuild its projection")

    def test_http_acceptance_and_live_process_do_not_prove_a_candidate_finished(self):
        admission, _ = self.admission("/api/proposals/unexecuted/candidate")
        self.manager.replied(admission, HttpResult(202, json.dumps({"jobId": "no-such-job",
            "candidateId": admission.record.candidateId, "status": "queued"}).encode(), {}))
        self.manager.reconcile(self.snapshot(), worker_alive=True)
        self.assertNotEqual(self.record(admission).status, "completed")
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual(self.record(admission).status, "needs_recovery")
        self.assertEqual(bound_project(self.app.state).run_ids(), (self.fixture.REFERENCE_RUN_ID,))

    def test_truncated_http_response_is_interrupted_and_duplicate_is_not_dispatched(self):
        self.manager = self.durable_manager()
        received = []

        class TruncatedResponse(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                self.send_response(202)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"{")
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), TruncatedResponse)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        manager = ProjectRuntimeManager(None, None)
        runtime = ProjectRuntime("truncated-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
                                 self.manager, ProjectBinding.open(self.settings), retained=self.snapshot())
        worker = SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", instance_id="truncated-worker")
        operation_id = str(uuid4())
        try:
            with patch.object(manager, "service", return_value=worker):
                for expected in ("OPERATION_INTERRUPTED", "OPERATION_NEEDS_RECOVERY"):
                    if expected == "OPERATION_NEEDS_RECOVERY":
                        self.manager = self.durable_manager()
                        runtime.operations = self.manager
                    with self.assertRaises(HubFailure) as failure:
                        manager.forward(runtime, "/api/program", "POST", b"{}", {"idempotency-key": operation_id})
                    self.assertEqual(failure.exception.error.code, expected)
            self.manager.reconcile(self.snapshot(), worker_alive=False)
            record = next(row for row in self.manager.records() if row.operationId == operation_id)
            self.assertEqual(record.status, "needs_recovery")
            self.assertFalse(record.committed)
            self.assertEqual(received, [b"{}"])
            self.assertEqual(bound_project(self.app.state).run_ids(), (self.fixture.REFERENCE_RUN_ID,))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_same_operation_id_refuses_changed_payload_method_or_path(self):
        admission, body = self.admission("/api/proposals", {"stateDigest": self.state_digest})
        for method, path, changed in (("POST", "/api/proposals", b'{}'),
                                     ("PUT", "/api/proposals", body),
                                     ("POST", "/api/program", body)):
            with self.subTest(method=method, path=path), self.assertRaises(HubFailure) as failure:
                self.manager.admit(admission.record.operationId, method, path, changed,
                    retained=self.snapshot(), source="studio", session_id=None)
            self.assertEqual(failure.exception.error.code, "OPERATION_ID_CONFLICT")

    def test_lost_reply_after_commit_is_reconciled_from_reachable_stage(self):
        initial, candidate_id, path, payload, admission = self.acceptance()
        before_head = self.repository.read_head()
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        committed = response.json()
        self.manager.interrupted(admission, "injected response loss after branch CAS")
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        record = self.record(admission)
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.committed)
        self.assertEqual(record.resultDigest, committed["recordDigest"])
        self.assertIsNone(record.resultRevision)
        self.assertEqual(self.repository.read_head(), before_head)
        self.assertEqual(self.client.post(path, json=payload).json(), committed)
        self.assertEqual(len(self.snapshot()["stages"]), 2)
        cold = OperationManager(self.fixture.PROJECT_ID)
        retained = self.snapshot()
        cold.reconcile(retained, worker_alive=False)
        restored = next(row for row in cold.records() if row.candidateId == candidate_id)
        candidate = next(row for row in retained["candidates"] if row["candidateId"] == candidate_id)
        self.assertTrue(restored.committed)
        self.assertEqual(restored.baseRevision, candidate["base"]["version"])
        self.assertEqual(restored.baseRecordDigest, candidate["baseRecordDigest"])
        self.assertEqual(restored.resultDigest, candidate["resultStateDigest"])
        for wrong in ({**payload, "branchId": "different-branch"},
                      {**payload, "expectedHeadStageRef": committed["stageRef"]}):
            other, _ = self.admission(path, wrong)
            self.manager.interrupted(other, "injected unknown response")
            self.manager.reconcile(self.snapshot(), worker_alive=False)
            self.assertEqual(self.record(other).status, "needs_recovery")
            self.assertFalse(self.record(other).committed)

    def test_failure_before_commit_excludes_prepared_stage_even_with_complete_candidate(self):
        initial, candidate_id, path, payload, admission = self.acceptance()
        with patch.object(bound_project(self.app.state).repository, "compare_and_swap_design_branch",
                          side_effect=OSError("injected interruption before branch CAS")):
            with self.assertRaises(OSError):
                self.client.post(path, json=payload)
        self.manager.interrupted(admission, "worker exited before commit")
        review = self.repository.list_json(run=self.repository.load_run(candidate_id),
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=candidate_id))
        self.assertTrue(any(ref.record_kind == DESIGN_STAGE for ref in review))
        retained = self.snapshot()
        self.assertEqual([stage["stageRef"] for stage in retained["stages"]], [initial["stageRef"]])
        self.manager.reconcile(retained, worker_alive=False)
        self.assertEqual(self.record(admission).status, "needs_recovery")
        self.assertFalse(self.record(admission).committed)

    def test_retained_seat_failure_is_not_relabelled_as_unknown_worker_failure(self):
        admission, accepted = self.candidate()
        candidate_id = accepted["candidateId"]
        _, receipt = bound_project(self.app.state).newest_runner_receipt(candidate_id)
        self.repository.put_json(run=self.repository.load_run(candidate_id),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=candidate_id),
            record_kind=RUNNER_RUN_RECEIPT, payload={**receipt, "seat_execution_complete": False})
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual(self.record(admission).status, "failed")
        self.assertIn("incomplete", self.record(admission).reason)

    def test_invalid_accept_cannot_borrow_the_initial_stage_after_lost_refusal(self):
        model = self.model(self.fixture.REFERENCE_RUN_ID, self.state_digest)
        initialized = self.client.post("/api/design-stages/initialize", json={
            "projectId": self.fixture.PROJECT_ID, "modelSource": model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        initial = initialized.json()
        self.assertIsNone(initial["parentStageRef"])
        path = f"/api/candidates/{initial['candidateId']}/accept"
        payload = {"projectId": self.fixture.PROJECT_ID}
        admission, _ = self.admission(path, payload)
        refused = self.client.post(path, json=payload)
        self.assertEqual(refused.status_code, 422, refused.text)
        self.manager.interrupted(admission, "lost invalid acceptance response")
        self.manager.reconcile(self.snapshot(), worker_alive=False)
        self.assertEqual(self.record(admission).status, "needs_recovery")
        self.assertFalse(self.record(admission).committed)

    def test_refused_redispatch_after_hub_restart_cannot_claim_old_candidate_result(self):
        original, _ = self.candidate()
        retained = self.snapshot()
        restarted = OperationManager(self.fixture.PROJECT_ID)
        runtime = ProjectRuntime("fixture-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            restarted, ProjectBinding.open(self.settings), retained=retained)
        manager = ProjectRuntimeManager(None, None)
        ready = SimpleNamespace(url="http://127.0.0.1:1", instance_id="fixture-instance")
        with patch.object(manager, "service", return_value=ready), \
             patch("monkeyhub_api.runtime.request_http", side_effect=AssertionError("refused operation dispatched")) as forwarded:
            with self.assertRaises(HubFailure) as refusal:
                manager.forward(runtime, "/api/proposals/a-different-proposal/candidate", "POST", b"",
                    {"idempotency-key": original.record.operationId})
        self.assertEqual(refusal.exception.error.code, "OPERATION_RETAINED")
        forwarded.assert_not_called()
        restarted.reconcile(retained, worker_alive=False)
        record = next(row for row in restarted.records() if row.operationId == original.record.operationId)
        self.assertEqual(record.status, "failed")
        self.assertFalse(record.committed)
        self.assertIsNone(record.resultDigest)

    def test_acceptance_is_dispatched_while_a_refresh_names_its_candidate_proposal(self):
        # A retained refresh names the candidate's job and proposal on every
        # operation bound to that candidate, an acceptance included. Its own
        # path is not a proposal, so the acceptance is still dispatched once
        # and nothing else is read from the worker on its behalf.
        initial, candidate_id, path, payload, _ = self.acceptance()
        accepting = OperationManager(self.fixture.PROJECT_ID)
        runtime = ProjectRuntime("accepting-runtime", self.fixture.PROJECT_ID, str(self.settings.project_dir),
            accepting, ProjectBinding.open(self.settings), retained=self.snapshot(live=True))
        manager = ProjectRuntimeManager(None, None)
        ready = SimpleNamespace(url="http://127.0.0.1:1", instance_id="fixture-instance")
        requests = []

        def studio(base, target, method="GET", body=None, headers=None, *, timeout=10):
            requests.append((method, target))
            answer = self.client.request(method, target, content=body, headers=headers or {})
            return HttpResult(answer.status_code, answer.content,
                              {"content-type": answer.headers.get("content-type", "application/json")})

        def refreshed(_runtime):
            # The watcher's own reconcile, at the window it really occupies:
            # after this request is admitted and before it is dispatched.
            accepting.reconcile(self.snapshot(live=True), worker_alive=True)
            return ready

        with patch.object(manager, "service", side_effect=refreshed), \
             patch("monkeyhub_api.runtime.request_http", side_effect=studio):
            result = manager.forward(runtime, path, "POST", json.dumps(payload, sort_keys=True).encode(),
                {"idempotency-key": str(uuid4()), "content-type": "application/json"})
        self.assertEqual(requests, [("POST", path)])
        self.assertEqual(result.status, 200, result.body)
        self.assertEqual(result.json()["parentStageRef"], initial["stageRef"])
        accepting.reconcile(self.snapshot(live=True), worker_alive=True)
        record = next(row for row in accepting.records() if row.candidateId == candidate_id and row.source == "studio")
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.committed)


if __name__ == "__main__":
    unittest.main()
