"""Hub operation recovery uses real Studio runs and committed P036 history."""

import base64
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
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, RUNNER_RUN_RECEIPT
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


if __name__ == "__main__":
    unittest.main()
