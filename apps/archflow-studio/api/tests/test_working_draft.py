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

    def test_candidate_is_listed_without_moving_current_and_manual_save_is_not_stage_or_issue(self):
        initial = self.read()
        self.assertIsNone(initial["revisionSha256"])
        from archflow_studio_api.protocol import BASE_CAPABILITIES
        self.assertIn("working-draft", BASE_CAPABILITIES)
        head = self.repository.read_head()
        # GH-234 Q2: a generated candidate is retained and listed for recovery,
        # but it never becomes the saved working position by itself.
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        listed = self.read()
        self.assertIsNone(listed["current"])
        self.assertIn(accepted["candidateId"], listed["managedRunIds"])
        self.assertEqual([row["runId"] for row in listed["recovery"]], [accepted["candidateId"]])
        # Only the explicit select route moves it: Continue, Return to default,
        # a Versions choice or an adopted Sync.
        selected = self.client.put("/api/working-draft", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": listed["revisionSha256"], "runId": accepted["candidateId"]})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(selected.json()["current"]["runId"], accepted["candidateId"])
        # A candidate generated from that very base is listed beside it and
        # leaves the architect's base where it was.
        digest = self.client.get("/api/state", params={"run": accepted["candidateId"]}).json()["stateDigest"]
        generated, job = self.run_candidate("set height to 0.5", elementId="portico-cornice",
                                            sourceRunId=accepted["candidateId"], stateDigest=digest)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertTrue(self.repository.layout.run(generated["candidateId"]).root.is_dir())
        current = self.read()
        self.assertEqual(current["current"]["runId"], accepted["candidateId"])
        self.assertIn(generated["candidateId"], current["managedRunIds"])
        self.assertEqual({row["runId"] for row in current["recovery"]}, {accepted["candidateId"], generated["candidateId"]})
        saved = self.client.post("/api/working-draft/save", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": current["revisionSha256"], "runId": accepted["candidateId"], "label": "Study A"})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["saved"][0]["label"], "Study A")
        self.assertEqual([row["runId"] for row in saved.json()["recovery"]], [generated["candidateId"]])
        self.assertEqual(saved.json()["current"]["runId"], accepted["candidateId"])
        self.assertEqual(self.repository.read_head(), head)
        self.assertEqual(self.repository.read_design_branches(), {})
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as cold:
            reopened = self.read(cold)
            self.assertEqual(reopened, saved.json())
            self.assertEqual(reopened["current"]["runId"], accepted["candidateId"])

    def test_an_automatic_candidate_older_than_24_hours_stays_listed_for_recovery(self):
        # GH-234 Q3: a generated candidate stays an alternative until the architect
        # explicitly rejects or archives it; age alone never drops it from the list.
        older, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        newer, job = self.run_candidate("set height to 2.4", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        value, revision = self.repository.read_working_draft()
        value["runs"][older["candidateId"]]["updatedAt"] = "2020-01-01T00:00:00+00:00"
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
        newest_first = [newer["candidateId"], older["candidateId"]]
        self.assertEqual([row["runId"] for row in self.read()["recovery"]], newest_first)
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as cold:
            reopened = self.read(cold)
            self.assertEqual([row["runId"] for row in reopened["recovery"]], newest_first)
            self.assertEqual(reopened["recovery"][-1]["updatedAt"], "2020-01-01T00:00:00+00:00")
            self.assertEqual(reopened["managedRunIds"], sorted(newest_first))
            self.assertIsNone(reopened["current"])

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
            # The finished batch is retained and listed; it does not become the
            # saved position until the client adopts it explicitly (GH-234 Q2).
            self.assertIsNone(recovered.json()["current"])
            self.assertIn(accepted["candidateId"], recovered.json()["managedRunIds"])
            restored_source = cold.get("/api/state")
            self.assertEqual(restored_source.status_code, 200, restored_source.text)
            self.assertEqual(restored_source.json()["stateDigest"], self.state_digest)
            again = cold.put("/api/working-draft/local", json={"projectId": PROJECT_ID,
                "baseRevisionSha256": recovered.json()["revisionSha256"], "draft": draft})
            self.assertEqual(again.status_code, 200, again.text)
            adopted = cold.put("/api/working-draft", json={"projectId": PROJECT_ID,
                "baseRevisionSha256": again.json()["revisionSha256"], "runId": accepted["candidateId"]})
            self.assertEqual(adopted.status_code, 200, adopted.text)
            self.assertEqual(adopted.json()["current"]["runId"], accepted["candidateId"])
        with TestClient(create_app(StudioSettings(project_dir=self.repository.layout.root, cad_export="off"))) as reopened:
            kept = reopened.get("/api/working-draft")
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertEqual(kept.json()["current"]["runId"], accepted["candidateId"])
            self.assertEqual(kept.json()["localDraft"]["source"], source)


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
