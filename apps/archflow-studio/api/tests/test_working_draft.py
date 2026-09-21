"""Autosave keeps frozen local commands separate from execution and acceptance."""
from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase, CandidateArchiveTests
from .test_design_history import DesignHistoryFixture


class WorkingDraftTests(CandidateTestCase):
    def read(self, client=None):
        response = (client or self.client).get("/api/working-draft")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def source(self):
        return {"projectId": PROJECT_ID, "sourceRunId": REFERENCE_RUN_ID,
                "sourceStageRef": None, "stateDigest": self.state_digest}

    def local(self, revision, draft, **extra):
        return self.client.put("/api/working-draft/local", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": revision, "draft": draft, **extra})

    def test_candidate_becomes_current_and_manual_save_is_not_stage_or_issue(self):
        initial = self.read()
        self.assertIsNone(initial["revisionSha256"])
        from archflow_studio_api.protocol import BASE_CAPABILITIES
        self.assertIn("working-draft", BASE_CAPABILITIES)
        head = self.repository.read_head()
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        current = self.read()
        self.assertEqual(current["current"]["runId"], accepted["candidateId"])
        self.assertIn(accepted["candidateId"], current["managedRunIds"])
        self.assertEqual(len(current["recovery"]), 1)
        saved = self.client.post("/api/working-draft/save", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": current["revisionSha256"], "runId": accepted["candidateId"], "label": "Study A"})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["saved"][0]["label"], "Study A")
        self.assertEqual(saved.json()["recovery"], [])
        self.assertEqual(self.repository.read_head(), head)
        self.assertEqual(self.repository.read_design_branches(), {})
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as cold:
            self.assertEqual(self.read(cold), saved.json())

    def test_select_clear_and_stale_window_never_overwrite_current(self):
        body = {"projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "baseRevisionSha256": None}
        first = self.client.put("/api/working-draft", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.client.put("/api/working-draft", json=body | {"runId": None})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "WORKING_DRAFT_STALE")
        cleared = self.client.put("/api/working-draft", json=body | {"runId": None,
            "baseRevisionSha256": first.json()["revisionSha256"]})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertIsNone(cleared.json()["current"])
        other = self.client.put("/api/working-draft", json=body | {"projectId": "other"})
        self.assertEqual(other.status_code, 403)

    def test_frozen_commands_and_pending_ids_reopen_without_executing_and_clear_is_source_bound(self):
        runs = set(self.repository.layout.runs.iterdir())
        draft = {"source": self.source(), "commands": [{"kind": "translate", "offset": [1, 0, 0]}],
            "attempt": {"syncedCommands": [], "pending": {"commands": [{"id": "frozen"}], "attempt": {
                "requestId": "stable-request", "nextCommand": 1, "sourceProposalId": "p1", "finalProposalId": "p2",
                "accepted": {"jobId": "job1", "candidateId": "candidate1"}}}}}
        first = self.local(None, draft)
        self.assertEqual(first.status_code, 200, first.text)
        value = first.json()
        self.assertEqual(value["localDraft"]["attempt"], draft["attempt"])
        self.assertEqual(value["managedRunIds"], [])
        self.assertEqual(set(self.repository.layout.runs.iterdir()) - runs,
                         {self.repository.layout.run("studio-working-draft").root})
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as cold:
            self.assertEqual(self.read(cold)["localDraft"], value["localDraft"])
        second = self.local(value["revisionSha256"], draft | {"commands": [{"kind": "translate", "offset": [2, 0, 0]}]})
        self.assertEqual(second.status_code, 200, second.text)
        stale = self.local(value["revisionSha256"], None)
        self.assertEqual(stale.status_code, 409)
        revision = second.json()["revisionSha256"]
        mismatch = self.local(revision, None, expectedSource=self.source() | {"stateDigest": "f" * 64})
        self.assertEqual(mismatch.status_code, 409)
        self.assertIsNotNone(self.read()["localDraft"])
        cleared = self.local(revision, None, expectedSource=self.source())
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertIsNone(cleared.json()["localDraft"])

    def test_wrong_exact_source_refuses_before_retaining_commands(self):
        response = self.local(None, {"source": self.source() | {"stateDigest": "b" * 64}, "commands": [], "attempt": None})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.read()["localDraft"])
        self.assertFalse(self.repository.layout.run("studio-working-draft").root.exists())

    def test_uploaded_model_is_permanent_but_generated_composition_can_expire(self):
        import base64
        from pathlib import Path
        from archflow_studio_api.application.artifacts import register_model_asset
        from archflow_studio_api.application.binding import bound_project
        content = base64.b64encode((Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()).decode()
        for generated in (True, False):
            accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
            self.assertEqual(job["status"], "succeeded", job)
            run_id = accepted["candidateId"]
            digest = self.client.get("/api/state", params={"run": run_id}).json()["stateDigest"]
            register_model_asset(bound_project(self.app.state), run_id, digest, "model.3dm", content, generated=generated)
            value, revision = self.repository.read_working_draft()
            value["current"] = None
            value["runs"][run_id]["updatedAt"] = "2020-01-01T00:00:00+00:00"
            self.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
            removed = self.repository.prune_working_draft(now="2026-01-03T00:00:00+00:00")
            self.assertEqual(removed, (run_id,) if generated else ())


class InitialProjectionRecoveryTests(CandidateArchiveTests):
    def test_initial_authored_source_recovers_after_first_candidate_completed_before_local_clear(self):
        source = {"projectId": PROJECT_ID, "sourceRunId": None, "sourceStageRef": None, "stateDigest": self.state_digest}
        draft = {"source": source, "commands": [{"id": "edit"}],
                 "attempt": {"syncedCommands": [], "pending": {"commands": [{"id": "edit"}],
                    "attempt": {"requestId": "stable", "nextCommand": 1, "sourceProposalId": "p1", "finalProposalId": "p2"}}}}
        saved = self.client.put("/api/working-draft/local", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": None, "draft": draft})
        self.assertEqual(saved.status_code, 200, saved.text)
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as cold:
            recovered = cold.get("/api/working-draft")
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assertEqual(recovered.json()["localDraft"]["source"], source)
            self.assertEqual(recovered.json()["current"]["runId"], accepted["candidateId"])
            restored_source = cold.get("/api/state")
            self.assertEqual(restored_source.status_code, 200, restored_source.text)
            self.assertEqual(restored_source.json()["stateDigest"], self.state_digest)
            again = cold.put("/api/working-draft/local", json={"projectId": PROJECT_ID,
                "baseRevisionSha256": recovered.json()["revisionSha256"], "draft": draft})
            self.assertEqual(again.status_code, 200, again.text)


class BranchWorkingDraftTests(DesignHistoryFixture):
    def test_current_retains_non_main_branch_and_confirmed_stage_survives_expiry(self):
        s0 = self.initialize()
        self.fork(s0, branch_id="alternative")
        candidate = self.candidate_from(s0)
        current = self.client.get("/api/working-draft").json()
        selected = self.client.put("/api/working-draft", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": current["revisionSha256"], "runId": candidate, "branchId": "alternative"})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(selected.json()["current"]["branchId"], "alternative")
        accepted = self.accept(candidate, s0, branch_id="alternative")
        self.assertEqual(accepted.status_code, 200, accepted.text)
        value, revision = self.repository.read_working_draft()
        value["current"] = None
        value["runs"][candidate]["updatedAt"] = "2020-01-01T00:00:00+00:00"
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
        self.assertEqual(self.repository.prune_working_draft(now="2026-01-03T00:00:00+00:00"), ())
        self.assertEqual(self.history("alternative")["stages"][-1]["stageRef"], accepted.json()["stageRef"])
