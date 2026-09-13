"""Recovery reads distinguish retained results, live jobs and prepared commits."""

from pathlib import Path
import threading
from unittest.mock import patch

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, STUDIO_CANDIDATE_WORKFLOW
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.application.binding import ProjectBinding, bound_project
from archflow_studio_api.application.runtime import inspect_runtime
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.runtime import runtime_dto

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase
from .test_working_copies import register_model


class RuntimeTests(CandidateTestCase):
    def cold(self, *candidate_ids: str, limit: int = 50) -> dict:
        binding = ProjectBinding.open(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))
        return runtime_dto(inspect_runtime(binding, candidate_ids=candidate_ids, limit=limit)).model_dump(by_alias=True)

    def test_completed_result_is_reconstructed_without_jobs_or_writes(self) -> None:
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded")
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        with patch("archflow.project.repository.FilesystemProjectRepository.put_json", side_effect=AssertionError("read wrote")), \
             patch("archflow_studio_api.application.candidate.execute_candidate", side_effect=AssertionError("read ran")):
            snapshot = self.cold(accepted["candidateId"])
        row = next(row for row in snapshot["candidates"] if row["candidateId"] == accepted["candidateId"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["receiptRef"], candidate["receiptRef"])
        self.assertEqual(row["resultRecordDigest"], candidate["recordDigest"])
        delta = bound_project(self.app.state).candidate_delta(accepted["candidateId"])
        self.assertEqual(row["baseStateDigest"], delta["operator"]["base_state_digest"])
        self.assertIsNone(row["jobId"])
        self.assertEqual(snapshot["jobs"], [])
        self.assertEqual(snapshot["projectId"], PROJECT_ID)
        self.assertEqual(Path(snapshot["projectDir"]), self.root / PROJECT_ID)
        self.assertNotIn(REFERENCE_RUN_ID, [row["candidateId"] for row in snapshot["candidates"]])

    def test_unwritten_run_is_live_only_while_its_job_is_live(self) -> None:
        release = threading.Event()
        entered = threading.Event()

        def work():
            entered.set()
            release.wait(10)

        job = self.app.state.jobs.submit(candidate_id="tracked-before-create", proposal_id="tracked-proposal", work=work)
        try:
            self.assertTrue(entered.wait(5))
            response = self.client.get("/api/runtime", params={"candidateId": job.candidate_id})
            self.assertEqual(response.status_code, 200, response.text)
            row = response.json()["candidates"][0]
            self.assertEqual(row["status"], "running")
            self.assertEqual(row["jobId"], job.job_id)
            cold = self.cold(job.candidate_id)["candidates"][0]
            self.assertEqual(cold["status"], "needs_recovery")
            self.assertIsNone(cold["receiptRef"])
        finally:
            release.set()
            self.app.state.jobs.shutdown()

    def test_failure_after_retained_candidate_keeps_result_and_failed_job_distinct(self) -> None:
        with patch.object(self.app.state.episodes, "flush", side_effect=RuntimeError("after retained candidate")):
            accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "failed")
        response = self.client.get("/api/runtime", params={"candidateId": accepted["candidateId"]})
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()
        candidate = next(row for row in snapshot["candidates"] if row["candidateId"] == accepted["candidateId"])
        observed_job = next(row for row in snapshot["jobs"] if row["jobId"] == accepted["jobId"])
        self.assertEqual(candidate["status"], "completed")
        self.assertIsNotNone(candidate["receiptRef"])
        self.assertEqual(observed_job["status"], "failed")
        self.assertIn("after retained candidate", observed_job["error"])
        self.assertEqual(self.cold(accepted["candidateId"])["candidates"][0]["status"], "completed")

    def test_empty_run_and_retained_incomplete_receipt_cannot_report_completed(self) -> None:
        self.repository.create_run("empty-interrupted")
        self.assertEqual(self.cold("empty-interrupted")["candidates"][0]["status"], "needs_recovery")
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded")
        binding = bound_project(self.app.state)
        _, receipt = binding.newest_runner_receipt(accepted["candidateId"])
        self.repository.put_json(run=self.repository.load_run(accepted["candidateId"]),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=accepted["candidateId"]),
            record_kind=RUNNER_RUN_RECEIPT, payload={**receipt, "seat_execution_complete": False})
        row = self.cold(accepted["candidateId"])["candidates"][0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("incomplete", row["error"])

    def test_scan_is_bounded_and_explicit_candidate_survives_window(self) -> None:
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded")
        for index in range(4):
            self.repository.create_run(f"z-other-{index}")
        snapshot = self.cold(accepted["candidateId"], limit=1)
        self.assertTrue(snapshot["hasMore"])
        self.assertEqual(snapshot["runsScanned"], 2)
        self.assertEqual(snapshot["candidates"][0]["candidateId"], accepted["candidateId"])
        self.assertEqual(snapshot["candidates"][0]["status"], "completed")
        self.assertEqual(self.client.get("/api/runtime?limit=201").status_code, 422)
        self.assertEqual(self.client.get("/api/runtime?candidateId=../other").status_code, 422)

    def test_native_receipt_before_required_composition_is_not_a_completed_result(self) -> None:
        model_bytes = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        source = register_model(self.client, REFERENCE_RUN_ID, self.state_digest, model_bytes)
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded")
        candidate_id = accepted["candidateId"]
        binding = bound_project(self.app.state)
        _, receipt = binding.newest_runner_receipt(candidate_id)
        workflow = self.repository.load_json(record_ref_from_uri(receipt["workflow_ref"], PROJECT_ID))
        # Inject the persisted boundary after native seats completed but before
        # the required composition was retained. The read must remain cold.
        run = self.repository.load_run(candidate_id)
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=candidate_id)
        workflow_ref = self.repository.put_json(run=run, destination=destination,
            record_kind=STUDIO_CANDIDATE_WORKFLOW,
            payload={**workflow, "basis_refs": [*workflow["basis_refs"], source["receiptRef"]]})
        self.repository.put_json(run=run, destination=destination, record_kind=RUNNER_RUN_RECEIPT,
            payload={**receipt, "workflow_ref": workflow_ref.uri})
        row = self.cold(candidate_id)["candidates"][0]
        self.assertEqual(row["status"], "needs_recovery")
        self.assertIn("no completed composed model", row["error"])

    def test_prepared_stage_is_not_commit_and_post_commit_cold_read_is_exact(self) -> None:
        model_bytes = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        model = register_model(self.client, REFERENCE_RUN_ID, self.state_digest, model_bytes)["modelSource"]
        initial = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": model}).json()
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base",
            sourceStageRef=initial["stageRef"], sourceRunId=REFERENCE_RUN_ID)
        self.assertEqual(job["status"], "succeeded", job)
        candidate_id = accepted["candidateId"]
        state = self.client.get("/api/state", params={"run": candidate_id, "sourceStageRef": initial["stageRef"]}).json()
        register_model(self.client, candidate_id, state["stateDigest"], model_bytes)
        payload = {"projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": initial["stageRef"]}
        repository = bound_project(self.app.state).repository
        with patch.object(repository, "compare_and_swap_design_branch", side_effect=OSError("before commit")):
            try:
                self.client.post(f"/api/candidates/{candidate_id}/accept", json=payload)
            except OSError:
                pass
        snapshot = self.cold(candidate_id)
        self.assertEqual(snapshot["candidates"][0]["commitStageRefs"], [])
        self.assertEqual([row["stageRef"] for row in snapshot["stages"]], [initial["stageRef"]])
        result = self.client.post(f"/api/candidates/{candidate_id}/accept", json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        committed = result.json()
        snapshot = self.cold(candidate_id)
        self.assertEqual(snapshot["candidates"][0]["commitStageRefs"], [committed["stageRef"]])
        stage = next(row for row in snapshot["stages"] if row["stageRef"] == committed["stageRef"])
        self.assertEqual((stage["branchId"], stage["candidateId"], stage["parentStageRef"]),
                         ("main", candidate_id, initial["stageRef"]))
        self.assertEqual(self.client.post(f"/api/candidates/{candidate_id}/accept", json=payload).json(), committed)
        self.assertEqual(len(self.cold(candidate_id)["stages"]), 2)
