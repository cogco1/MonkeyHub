"""One current working source for ordinary work; viewing history never moves it."""

from concurrent.futures import ThreadPoolExecutor
import threading
from unittest import mock

from fastapi.testclient import TestClient

from archflow_studio_api.application import publications
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.working_draft import resolve_working_source
from archflow_studio_api.protocol import BASE_CAPABILITIES

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_design_history import DesignHistoryFixture
from .test_working_copies import register_model


def adopt(client: TestClient, run_id: str, branch_id: str | None = None) -> dict:
    """The architect's Continue: explicitly make a shown result the editing base."""
    position = client.get("/api/working-draft").json()
    response = client.put("/api/working-draft", json={"projectId": PROJECT_ID, "runId": run_id, "branchId": branch_id,
                          "baseRevisionSha256": position["revisionSha256"]})
    assert response.status_code == 200, response.text
    return response.json()


class WorkingSourceFixture(DesignHistoryFixture):
    def adopt(self, run_id: str, branch_id: str | None = None) -> dict:
        return adopt(self.client, run_id, branch_id)

    def working_source(self, workspace: str = "modeling", client: TestClient | None = None, **params) -> dict:
        response = (client or self.client).get("/api/working-source", params={"workspace": workspace, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def state_of(self, run_id: str) -> dict:
        response = self.client.get("/api/state", params={"run": run_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def continue_from(self, run_id: str, *, element: str = "portico-base", height: float = 2.4) -> str:
        """Run one retained continuation of an exact candidate and register its complete model."""

        accepted, job = self.run_candidate(f"set height to {height}", elementId=element,
                                           stateDigest=self.state_of(run_id)["stateDigest"], sourceRunId=run_id)
        self.assertEqual(job["status"], "succeeded", job)
        candidate = accepted["candidateId"]
        register_model(self.client, candidate, self.state_of(candidate)["stateDigest"], self.model_bytes)
        return candidate


class WorkingSourceTests(WorkingSourceFixture):
    def test_the_reference_run_answers_before_any_stage_or_working_position(self):
        self.assertIn("working-source", BASE_CAPABILITIES)
        body = self.working_source()
        self.assertEqual((body["projectId"], body["workspace"]), (PROJECT_ID, "modeling"))
        self.assertIsNone(body["revisionSha256"])
        head = body["head"]
        self.assertEqual((head["origin"], head["runId"], head["stateDigest"]),
                         ("reference", REFERENCE_RUN_ID, self.state_digest))
        self.assertFalse(head["accepted"])
        self.assertEqual(head["lineage"], [REFERENCE_RUN_ID])
        self.assertTrue(body["compatible"])
        self.assertEqual(body["source"], self.model)
        self.assertIsNone(body["stageRef"])
        self.assertEqual(body["warnings"], [])

    def test_an_accepted_stage_is_the_head_until_work_continues_from_it(self):
        stage = self.initialize()
        body = self.working_source()
        head = body["head"]
        self.assertEqual((head["origin"], head["runId"], head["accepted"]), ("branch-head", stage["candidateId"], True))
        self.assertEqual((head["sourceStageRef"], head["branchId"], head["label"]), (stage["stageRef"], "main", stage["label"]))
        self.assertEqual(head["modelSource"], stage["modelSource"])
        self.assertEqual((body["source"], body["stageRef"]), (stage["modelSource"], stage["stageRef"]))

    def test_a_result_becomes_the_head_only_through_continue(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        # A generated result is recorded and listed, never adopted (GH-234 Q2).
        self.assertEqual(self.working_source()["head"]["runId"], stage["candidateId"])
        self.assertIn(first, [row["runId"] for row in self.client.get("/api/working-draft").json()["recovery"]])
        self.adopt(first)
        body = self.working_source()
        head = body["head"]
        self.assertEqual((head["origin"], head["runId"], head["accepted"]), ("working-position", first, False))
        self.assertEqual(head["sourceStageRef"], stage["stageRef"])
        self.assertEqual(head["lineage"], [first, stage["candidateId"]])
        self.assertIsNotNone(body["revisionSha256"])
        self.assertIsNone(body["stageRef"])
        self.assertEqual(body["source"]["runId"], first)

        second = self.continue_from(first)
        self.assertEqual(self.working_source()["head"]["runId"], first, "a continuation is shown, not adopted")
        self.adopt(second)
        head = self.working_source()["head"]
        self.assertEqual(head["runId"], second)
        self.assertEqual(head["lineage"], [second, first, stage["candidateId"]])

        # Separate work never moves the base.
        self.candidate_from(stage, height=2.6)
        self.assertEqual(self.working_source()["head"]["runId"], second)
        # Reading another run never selects it.
        self.state_of(first)
        self.assertEqual(self.working_source()["head"]["runId"], second)

    def test_an_explicit_position_is_followed_and_accepting_it_marks_it_accepted(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        accepted = self.accept(first, stage)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        head = self.working_source()["head"]
        self.assertEqual((head["runId"], head["accepted"], head["sourceStageRef"]), (first, True, accepted.json()["stageRef"]))

        current = self.client.get("/api/working-draft").json()
        moved = self.client.put("/api/working-draft", json={"projectId": PROJECT_ID, "runId": stage["candidateId"],
                                "baseRevisionSha256": current["revisionSha256"]})
        self.assertEqual(moved.status_code, 200, moved.text)
        head = self.working_source()["head"]
        self.assertEqual((head["origin"], head["runId"]), ("working-position", stage["candidateId"]))

    def test_drawing_needs_an_exact_step_and_says_why_it_cannot_follow(self):
        stage = self.initialize()
        body = self.working_source("drawing")
        self.assertEqual(body["head"]["runId"], stage["candidateId"])
        self.assertFalse(body["compatible"])
        self.assertIsNone(body["source"])
        self.assertIn("STEP", body["reason"])
        self.assertTrue(self.working_source("board")["compatible"])
        self.assertTrue(self.working_source("render")["compatible"])

    def test_an_unreadable_position_falls_back_to_the_branch_head_with_a_warning(self):
        stage = self.initialize()
        self.repository.create_run("bare-run")
        value, revision = self.repository.read_working_draft()
        value["runs"]["bare-run"] = {"updatedAt": "2026-09-24T00:00:00+00:00", "sourceStageRef": None,
                                     "branchId": None, "label": None, "automatic": False}
        value["current"] = "bare-run"
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=value)
        body = self.working_source()
        self.assertEqual((body["head"]["origin"], body["head"]["runId"]), ("branch-head", stage["candidateId"]))
        self.assertEqual(len(body["warnings"]), 1)
        self.assertIn("bare-run", body["warnings"][0])

    def test_frozen_policy_keeps_the_pin_and_reports_when_the_head_moved(self):
        stage = self.initialize()
        pin = {"runId": stage["modelSource"]["runId"], "stateDigest": stage["modelSource"]["stateDigest"],
               "assetSha256": stage["modelSource"]["assetSha256"]}
        frozen = self.working_source(policy="frozen", **pin)
        self.assertEqual((frozen["source"], frozen["compatible"], frozen["reason"]), (stage["modelSource"], True, None))
        self.adopt(self.candidate_from(stage))
        frozen = self.working_source(policy="frozen", **pin)
        self.assertEqual(frozen["source"], stage["modelSource"])
        self.assertIn("changed", frozen["reason"])

    def test_unknown_workspace_or_policy_is_refused(self):
        for params in ({"workspace": "timeline"}, {"workspace": "modeling", "policy": "latest"},
                       {"workspace": "modeling", "policy": "frozen"}):
            with self.subTest(params=params):
                response = self.client.get("/api/working-source", params=params)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn(response.json()["code"], {"WORKSPACE_INVALID", "SOURCE_POLICY_INVALID"})

    def test_resolution_writes_nothing(self):
        stage = self.initialize()
        self.candidate_from(stage)
        before = (self.repository.read_working_draft(), self.repository.read_design_branches(), self.repository.read_head())
        for workspace in ("modeling", "drawing", "render", "board"):
            self.working_source(workspace)
        self.assertEqual((self.repository.read_working_draft(), self.repository.read_design_branches(),
                          self.repository.read_head()), before)

    def test_work_continued_on_a_fork_stays_on_that_fork(self):
        stage = self.initialize()
        self.fork(stage, "facade-b")
        self.adopt(stage["candidateId"], "facade-b")
        # The fork shares main's Stage, but the position names the fork's line.
        self.assertEqual(self.working_source()["head"]["branchId"], "facade-b")
        first = self.candidate_from(stage)
        recorded = {row["runId"]: row for row in self.client.get("/api/working-draft").json()["recovery"]}
        self.assertEqual(recorded[first]["branchId"], "facade-b", "a fork's result is recorded on the fork")
        # Continue without naming a branch keeps the line the result was recorded on.
        self.assertEqual(self.adopt(first)["current"]["branchId"], "facade-b")
        head = self.working_source()["head"]
        self.assertEqual((head["runId"], head["branchId"]), (first, "facade-b"))

    def test_resolving_never_waits_for_a_project_writer(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        self.adopt(first)
        binding = bound_project(self.app.state)
        held, release = threading.Event(), threading.Event()

        def writer():
            with binding.repository.working_draft_guard():
                held.set()
                release.wait(10)

        thread = threading.Thread(target=writer)
        thread.start()
        pool = ThreadPoolExecutor(1)
        try:
            self.assertTrue(held.wait(5))
            future = pool.submit(resolve_working_source, binding)
            try:
                resolved = future.result(timeout=5)
            finally:
                release.set()
            self.assertEqual(resolved.head.run_id, first)
        finally:
            release.set()
            thread.join()
            pool.shutdown()

    def test_publication_sources_are_read_after_the_publication_lock_is_released(self):
        # A saver holds the project guard and then takes this lock; a reader that
        # kept it while reading sources behind that guard would deadlock both.
        free = []

        def attempt():
            got = publications._lock.acquire(timeout=1)
            if got:
                publications._lock.release()
            free.append(got)

        def statuses(binding, pages):
            probe = threading.Thread(target=attempt)
            probe.start()
            probe.join()
            return []

        with mock.patch.object(publications, "source_statuses", statuses):
            body = publications.read_publication(bound_project(self.app.state))
        self.assertEqual((free, body["sources"]), ([True], []))
