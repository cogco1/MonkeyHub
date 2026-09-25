"""A Candidate is a closed-loop result plus admission (#294, slices S1 and S2).

Every run stays retained and recoverable; only a ``CandidateAdmission@1``,
written through the preflight Stage acceptance also uses, makes one a
Candidate. These tests drive real candidate runs through the HTTP boundary,
then read the pool back from a new binding, from retained records only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    CANDIDATE_ADMISSION,
    DELIBERATION_EPISODE,
    RUNNER_RUN_RECEIPT,
    SEAT_RELATION_CHECK,
    STUDIO_WORKING_COPY,
)
from archflow_studio_api.application.authentication import request_action
from archflow_studio_api.application.binding import ProjectBinding, bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.protocol import BASE_CAPABILITIES
from monkeyarch.capabilities.relation_checks import RelationCheck, RelationCheckReport

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_working_copies import register_model
from .test_working_source import WorkingSourceFixture


def run_records(run_id: str) -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)


class AdmissionFixture(WorkingSourceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.models: dict[str, dict] = {}

    # ---- producing results

    def result(self, stage: dict | None = None, height: float = 2.2, *, source: str | None = None,
               model: bool = True) -> str:
        """One finished run from a Stage, from another run, or from the reference run."""

        body: dict[str, object] = {"elementId": "portico-base"}
        if source is not None:
            body.update(sourceRunId=source, stateDigest=self.state_of(source)["stateDigest"])
        elif stage is not None:
            body.update(sourceRunId=stage["candidateId"], sourceStageRef=stage["stageRef"],
                        stateDigest=stage["modelSource"]["stateDigest"])
        accepted, job = self.run_candidate(f"set height to {height}", **body)
        self.assertEqual(job["status"], "succeeded", job)
        run_id = accepted["candidateId"]
        if model:
            self.models[run_id] = register_model(self.client, run_id, self.state_of(run_id)["stateDigest"],
                                                 self.model_bytes)["modelSource"]
        return run_id

    def stage(self, label: str = "S0") -> dict:
        response = self.client.post("/api/design-stages/initialize",
                                    json={"projectId": PROJECT_ID, "modelSource": self.model, "label": label})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def violate(self, run_id: str) -> None:
        """Retain the report a seat's relation check writes when a relation is violated."""

        report = RelationCheckReport(self.state_of(run_id)["recordDigest"], (RelationCheck(
            "rel-cornice-on-base", "support", "support_contact", "violated", 0.001, {"gap": 0.05},
            "the cornice no longer bears on the base"),))
        self.repository.put_json(run=self.repository.load_run(run_id), destination=run_records(run_id),
                                 record_kind=SEAT_RELATION_CHECK,
                                 payload={**report.to_dict(), "seat_id": "seat-portico"})

    def managed(self) -> TestClient:
        """A second Runtime on the same project, started by a Hub."""

        app = create_app(self.settings)
        app.state.managed_instance_id = "hub-1"
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    # ---- the admission boundary

    def admit(self, *results: dict, task: str = "ui", client: TestClient | None = None, expect: int = 201,
              **body: object) -> dict:
        response = (client or self.client).post("/api/admissions", json={
            "projectId": PROJECT_ID, "task": {"kind": task}, "results": list(results), **body})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def refused(self, *results: dict, code: str, status: int = 409, **body: object) -> dict:
        answer = self.admit(*results, expect=status, **body)
        self.assertEqual(answer["code"], code, answer)
        return answer

    def failures(self, answer: dict) -> set[tuple[str, str, str]]:
        self.assertEqual(answer["code"], "ADMISSION_GATE_REFUSED", answer)
        return {(row["runId"], row["clause"], row["code"]) for row in answer["failures"]}

    def pool(self, client: TestClient | None = None, **params: str) -> dict:
        response = (client or self.client).get("/api/design-history", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def admitted(self, history: dict) -> dict[str, dict]:
        """Candidates an admission record made, by run; Stage-derived ones aside."""

        return {row["candidateId"]: row for row in history["candidates"] if row["legacy"] is None}

    def listed(self, client: TestClient | None = None, **params: str) -> dict:
        response = (client or self.client).get("/api/admissions", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def result_lines(self) -> dict[str, dict]:
        response = self.client.get("/api/worktrees")
        self.assertEqual(response.status_code, 200, response.text)
        return {line["runId"]: line for line in response.json()["lines"] if line["kind"] == "result"}

    def no_admission_run(self) -> None:
        self.assertFalse(self.repository.layout.run("studio-admissions").root.exists())


class CandidateAdmissionTests(AdmissionFixture):
    def test_a_result_that_superseded_its_attempt_is_one_candidate_after_restart(self) -> None:
        stage = self.stage()
        a1 = self.result(stage, 2.2)
        a2 = self.result(source=a1, height=2.4)
        self.assertEqual(self.pool()["candidates"][0]["legacy"], "stage")
        self.assertEqual(self.admitted(self.pool()), {}, "a finished run alone is not a Candidate")

        record = self.admit({"runId": a2, "outcome": "admitted", "supersedes": [a1], "label": "A"})
        self.assertTrue(record["admissionId"].startswith("adm-"))
        self.assertIsNone(record["previousRevisionRef"])
        self.assertEqual(record["actor"], {"actorId": "studio:explicit-user-action", "authenticated": False,
                                           "origin": "studio"})
        self.assertEqual(record["task"], {"kind": "ui", "ids": []})
        [row] = record["results"]
        self.assertEqual((row["runId"], row["outcome"], row["supersedes"]), (a2, "admitted", [a1]))
        self.assertEqual(row["modelSource"], self.models[a2])
        self.assertEqual((row["baseStageRef"], row["blockedBy"]), (stage["stageRef"], []))

        history = self.pool()
        admitted = self.admitted(history)
        self.assertEqual(list(admitted), [a2])
        self.assertNotIn(a1, [row["candidateId"] for row in history["candidates"]])
        candidate = admitted[a2]
        self.assertEqual((candidate["label"], candidate["baseStageRef"], candidate["studyId"]),
                         ("A", stage["stageRef"], record["admissionId"]))
        self.assertEqual((candidate["admissionRef"], candidate["admittedAt"]), (record["admissionRef"], record["occurredAt"]))
        self.assertEqual((candidate["continuedFrom"], candidate["acceptedStageRef"]), (REFERENCE_RUN_ID, None))
        self.assertEqual(candidate["supersedes"], [a1])
        self.assertFalse(candidate["inWorkingHeadLineage"])
        self.assertEqual(history["studies"], [{
            "id": record["admissionId"], "label": None, "baseRunId": REFERENCE_RUN_ID,
            "baseStageRef": stage["stageRef"], "candidateIds": [a2], "source": "admission"}])
        self.assertEqual(history["warnings"], [])
        # Every run stays retained and recoverable; admission deletes and moves nothing.
        position = self.client.get("/api/working-draft").json()
        self.assertTrue({a1, a2} <= set(position["managedRunIds"]))
        self.assertIsNone(position["current"])

        with TestClient(create_app(self.settings)) as restarted:
            again = self.pool(restarted)
            self.assertEqual((again["candidates"], again["studies"]), (history["candidates"], history["studies"]))
            self.assertEqual(self.listed(restarted)["admissions"], [record])

    def test_a_rejected_result_is_retained_and_listed_only_on_request(self) -> None:
        stage = self.stage()
        chosen = self.result(stage, 2.4)
        tried = self.result(stage, 2.8)
        record = self.admit({"runId": chosen, "outcome": "admitted"},
                            {"runId": tried, "outcome": "rejected", "reason": "too tall for the street"})
        rejected = next(row for row in record["results"] if row["runId"] == tried)
        self.assertEqual((rejected["modelSource"], rejected["blockedBy"]), (None, None))
        self.assertEqual(rejected["reason"], "too tall for the street")
        self.assertTrue(rejected["receiptRef"].startswith(f"project://{PROJECT_ID}/runs/{tried}/"))

        self.assertEqual(list(self.admitted(self.pool())), [chosen])
        everything = self.admitted(self.pool(include="rejected"))
        self.assertEqual(list(everything), [chosen, tried])
        self.assertEqual((everything[tried]["outcome"], everything[tried]["studyId"]), ("rejected", record["admissionId"]))
        self.assertEqual([row["runId"] for row in self.listed()["admissions"][0]["results"]], [chosen])
        # Admissions are read from their one fixed run, never by enumerating the project's runs.
        with mock.patch.object(ProjectBinding, "run_ids", side_effect=AssertionError("an admission read scanned runs")):
            self.assertEqual(self.listed(include="rejected")["admissions"], [record])
        lines = self.result_lines()
        self.assertEqual((lines[tried]["admission"], lines[tried]["studyId"]), ("rejected", record["admissionId"]))
        self.assertEqual(lines[chosen]["admission"], "admitted")

        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(list(self.admitted(self.pool(restarted))), [chosen])
            self.assertEqual(self.listed(restarted, include="rejected")["admissions"], [record])

    def test_each_clause_of_the_gate_refuses_and_retains_nothing(self) -> None:
        stage = self.stage()
        a1 = self.result(stage, 2.2)
        a2 = self.result(source=a1, height=2.4)
        other = self.result(stage, 2.6)
        # A run from a composed Stage model that composed none of its own is not a completed result.
        bare = self.result(stage, 2.8, model=False)
        # Two complete models of one state are not one exact model to admit.
        twice = self.result(stage, 3.0)
        register_model(self.client, twice, self.state_of(twice)["stateDigest"], self.model_bytes + b"\n")

        with self.subTest(clause="C2 not a finished run"):
            answer = self.refused({"runId": "studio-cand-absent", "outcome": "admitted"}, code="ADMISSION_GATE_REFUSED")
            self.assertEqual(self.failures(answer), {("studio-cand-absent", "C2", "RUN_NOT_FOUND")})
            answer = self.refused({"runId": bare, "outcome": "rejected"}, code="ADMISSION_GATE_REFUSED")
            self.assertEqual(self.failures(answer), {(bare, "C2", "CANDIDATE_NOT_FOUND")})
        with self.subTest(clause="C1 no replayable change"):
            answer = self.refused({"runId": REFERENCE_RUN_ID, "outcome": "admitted"}, code="ADMISSION_GATE_REFUSED")
            self.assertEqual(self.failures(answer), {(REFERENCE_RUN_ID, "C1", "CANDIDATE_DELTA_MISSING")})
        with self.subTest(clause="C1 not built from the Study base"):
            answer = self.refused({"runId": other, "outcome": "admitted"}, code="ADMISSION_GATE_REFUSED",
                                  study={"id": "entrance", "label": "Entrance", "baseRunId": a1})
            self.assertEqual(self.failures(answer), {(other, "C1", "STUDY_BASE_MISMATCH")})
        with self.subTest(clause="C5 and C7 are named together"):
            answer = self.refused({"runId": twice, "outcome": "admitted"},
                                  {"runId": a2, "outcome": "admitted", "supersedes": [other]},
                                  code="ADMISSION_GATE_REFUSED")
            self.assertEqual(self.failures(answer), {(twice, "C5", "CANDIDATE_MODEL_AMBIGUOUS"),
                                                     (a2, "C7", "SUPERSESSION_INVALID")})
            self.assertIn(f"{twice} fails C5", answer["detail"])

        with self.subTest(clause="C3 an Agent admits only a review-ready result"):
            self.violate(other)
            answer = self.admit({"runId": other, "outcome": "admitted"}, task="hub-chat", client=self.managed(),
                                expect=409, messageSource={"sessionId": "chat-1", "messageId": "message-1"})
            self.assertEqual(self.failures(answer), {(other, "C3", "CANDIDATE_NOT_READY")})
            self.assertIn("relations.held", answer["failures"][0]["detail"])

        with self.subTest(clause="C4 every exporting seat reads back"):
            # The receipt the runner leaves when every seat exported and none was inspected.
            binding = bound_project(self.app.state)
            _, receipt = binding.newest_runner_receipt(a1)
            exported = [{**row, "cad": {"status": "succeeded", "execution_ref": None}} for row in receipt["seat_results"]]
            self.repository.put_json(run=self.repository.load_run(a1), destination=run_records(a1),
                                     record_kind=RUNNER_RUN_RECEIPT, payload={**receipt, "seat_results": exported})
            answer = self.refused({"runId": a1, "outcome": "admitted"}, code="ADMISSION_GATE_REFUSED")
            self.assertEqual(self.failures(answer), {(a1, "C4", "OBJECT_READBACK_MISSING")})

        with self.subTest(clause="C2 an interrupted execution is not judged"):
            self.repository.protect_working_run(a2, a1)
            answer = self.refused({"runId": a2, "outcome": "rejected"}, code="ADMISSION_GATE_REFUSED")
            self.assertEqual(self.failures(answer), {(a2, "C2", "CANDIDATE_RUNNING")})

        self.assertEqual(self.listed(include="rejected")["admissions"], [])
        self.no_admission_run()

    def test_a_person_may_admit_a_recorded_violation_and_it_carries_the_marker(self) -> None:
        stage = self.stage()
        option = self.result(stage, 2.6)
        self.violate(option)
        record = self.admit({"runId": option, "outcome": "admitted", "label": "Comparison only"})
        self.assertEqual(record["results"][0]["blockedBy"], ["relations.held"])
        candidate = self.admitted(self.pool())[option]
        self.assertEqual(candidate["blockedBy"], ["relations.held"])
        # Accept-as-Stage still requires review readiness (owner decision Q2).
        refused = self.accept(option, stage)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "CANDIDATE_NOT_READY")
        self.assertEqual(len(self.pool()["stages"]), 1)

    def test_an_identical_retry_returns_the_record_and_a_conflicting_one_is_409(self) -> None:
        stage = self.stage()
        first, second, third = (self.result(stage, height) for height in (2.2, 2.6, 3.0))
        entrance = {"id": "entrance", "label": "Entrance", "baseRunId": REFERENCE_RUN_ID}
        results = ({"runId": first, "outcome": "admitted", "label": "A"},
                   {"runId": second, "outcome": "admitted", "label": "B"})
        record = self.admit(*results, study=entrance)
        self.assertEqual(record["study"], {**entrance, "baseStageRef": stage["stageRef"]})
        self.assertEqual(self.admit(*results, study=entrance, expect=200), record)
        self.assertEqual(self.admit(*reversed(results), study=entrance, expect=200), record)
        self.refused({"runId": first, "outcome": "rejected"}, code="ADMISSION_CONFLICT", study=entrance)
        conflict = self.refused({"runId": first, "outcome": "admitted", "label": "A again"}, code="ADMISSION_CONFLICT")
        self.assertIn(f"{first} is already admitted in {record['admissionId']}", conflict["detail"])
        self.refused({"runId": third, "outcome": "admitted", "supersedes": [second]}, code="ADMISSION_CONFLICT")
        self.refused({"runId": third, "outcome": "admitted"}, code="ADMISSION_CONFLICT",
                     study={**entrance, "label": "Another name"})

        later = self.admit({"runId": third, "outcome": "admitted", "label": "C"}, study=entrance)
        self.assertNotEqual(later["admissionId"], record["admissionId"])
        self.assertEqual([row["admissionId"] for row in self.listed()["admissions"]],
                         [record["admissionId"], later["admissionId"]])
        history = self.pool()
        self.assertEqual(history["studies"], [{**entrance, "baseStageRef": stage["stageRef"],
                                               "candidateIds": [first, second, third], "source": "declared"}])
        self.assertEqual(history["warnings"], [])

    def test_accept_refuses_a_rejected_result_and_a_rejected_head_only_warns(self) -> None:
        stage = self.stage()
        tried = self.result(stage, 2.8)
        self.adopt(tried)
        record = self.admit({"runId": tried, "outcome": "rejected"})
        refused = self.accept(tried, stage)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "CANDIDATE_REJECTED")
        self.assertIn(record["admissionId"], refused.json()["detail"])
        self.assertEqual(len(self.pool()["stages"]), 1)
        # The head stays where the architect put it; only Continue moves it (Q2).
        source = self.working_source()
        self.assertEqual(source["head"]["runId"], tried)
        self.assertTrue(any(record["admissionId"] in warning and "rejected" in warning
                            for warning in source["warnings"]), source["warnings"])
        # A committed Stage's run stays admitted: it is not rejected or superseded afterwards.
        self.refused({"runId": stage["candidateId"], "outcome": "rejected"}, code="ADMISSION_CONFLICT")

    def test_the_tree_is_one_study_under_its_stage_and_the_stage_it_led_to(self) -> None:
        s2 = self.stage("S2")
        a1 = self.result(s2, 2.2)
        a2 = self.result(source=a1, height=2.4)
        b = self.result(s2, 2.6)
        c = self.result(s2, 2.8)
        entrance = {"id": "entrance", "label": "Entrance", "baseRunId": s2["candidateId"]}
        self.admit({"runId": a2, "outcome": "admitted", "supersedes": [a1], "label": "A"},
                   {"runId": b, "outcome": "admitted", "label": "B"},
                   {"runId": c, "outcome": "rejected", "reason": "blocks the court"}, study=entrance)
        study = {**entrance, "baseStageRef": s2["stageRef"], "source": "declared"}

        history = self.pool()
        self.assertEqual(list(self.admitted(history)), [a2, b])
        self.assertEqual({row["studyId"] for row in self.admitted(history).values()}, {"entrance"})
        self.assertEqual({row["baseStageRef"] for row in self.admitted(history).values()}, {s2["stageRef"]})
        self.assertEqual(history["studies"], [{**study, "candidateIds": [a2, b]}])
        self.assertEqual(self.pool(include="rejected")["studies"], [{**study, "candidateIds": [a2, b, c]}])
        lines = self.result_lines()
        self.assertEqual({run: (lines[run]["admission"], lines[run]["studyId"]) for run in (a1, a2, b, c)},
                         {a1: ("superseded", "entrance"), a2: ("admitted", "entrance"),
                          b: ("admitted", "entrance"), c: ("rejected", "entrance")})
        with TestClient(create_app(self.settings)) as restarted:
            again = self.pool(restarted)
            self.assertEqual((again["candidates"], again["studies"]), (history["candidates"], history["studies"]))

        # B's worktree continues, and its tip is accepted: S3 is "from B".
        tip = self.result(source=b, height=3.0)
        accepted = self.client.post(f"/api/candidates/{tip}/accept", json={
            "projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": s2["stageRef"], "label": "S3"})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        s3 = accepted.json()
        history = self.pool()
        candidates = {row["candidateId"]: row for row in history["candidates"]}
        self.assertEqual(candidates[b]["acceptedStageRef"], s3["stageRef"])
        self.assertIsNone(candidates[a2]["acceptedStageRef"])
        self.assertEqual((candidates[tip]["legacy"], candidates[tip]["acceptedStageRef"], candidates[tip]["continuedFrom"]),
                         ("stage", s3["stageRef"], b))
        self.assertEqual((candidates[b]["inWorkingHeadLineage"], candidates[a2]["inWorkingHeadLineage"]), (True, False))
        self.assertEqual(history["studies"], [{**study, "candidateIds": [a2, b]}])
        self.assertEqual([stage["label"] for stage in history["stages"]], ["S2", "S3"])
        with TestClient(create_app(self.settings)) as restarted:
            again = self.pool(restarted)
            self.assertEqual((again["candidates"], again["studies"]), (history["candidates"], history["studies"]))

    def test_a_legacy_stage_without_a_record_derives_as_admitted(self) -> None:
        s0 = self.stage()
        first = self.result(s0, 2.2)
        accepted = self.accept(first, s0)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        s1 = accepted.json()
        with TestClient(create_app(self.settings)) as restarted:
            history = self.pool(restarted)
        self.assertEqual([(row["candidateId"], row["legacy"], row["acceptedStageRef"], row["baseStageRef"])
                          for row in history["candidates"]],
                         [(REFERENCE_RUN_ID, "stage", s0["stageRef"], None), (first, "stage", s1["stageRef"], s0["stageRef"])])
        legacy = history["candidates"][1]
        self.assertEqual(legacy["admittedBy"], {"actorId": "studio:explicit-user-action", "authenticated": False,
                                                "origin": "studio"})
        self.assertEqual((legacy["admissionRef"], legacy["modelSource"], legacy["continuedFrom"]),
                         (s1["stageRef"], s1["modelSource"], REFERENCE_RUN_ID))
        self.assertEqual(history["candidates"][0]["admittedBy"],
                         {"actorId": "studio:explicit-user-action", "authenticated": None, "origin": None})
        self.assertEqual(history["studies"], [])
        self.no_admission_run()

    def test_a_testmodel_shaped_project_has_no_candidate_until_one_is_admitted(self) -> None:
        # No Stage and many runs: a chain, redone attempts and a sibling from the same base.
        first = self.result(height=2.2)
        second = self.result(source=first, height=2.4)
        third = self.result(source=second, height=2.6)
        sibling = self.result(height=2.8)
        position = self.client.get("/api/working-draft").json()
        self.assertTrue({first, second, third, sibling} <= {row["runId"] for row in position["recovery"]})
        history = self.pool()
        self.assertEqual((history["stages"], history["candidates"], history["studies"], history["warnings"]),
                         ([], [], [], []))
        self.assertEqual({line["admission"] for line in self.result_lines().values()}, {"none"})

        record = self.admit({"runId": third, "outcome": "admitted", "supersedes": [second], "label": "Final"},
                            task="retroactive")
        self.assertEqual(record["actor"]["origin"], "retroactive")
        history = self.pool()
        [candidate] = history["candidates"]
        self.assertEqual((candidate["candidateId"], candidate["baseStageRef"], candidate["continuedFrom"]),
                         (third, None, None))
        self.assertEqual(history["studies"], [{"id": record["admissionId"], "label": None, "baseRunId": first,
                                               "baseStageRef": None, "candidateIds": [third], "source": "admission"}])

    def test_retained_explorations_and_accepted_episodes_derive_without_a_write(self) -> None:
        stage = self.stage()
        option = self.result(stage, 2.6)
        chosen = self.result(stage, 2.8)
        base = self.model
        self.repository.put_json(run=self.repository.load_run(REFERENCE_RUN_ID), destination=run_records(REFERENCE_RUN_ID),
                                 record_kind=STUDIO_WORKING_COPY, payload={
            "schema": "StudioWorkingCopy@1", "projectId": PROJECT_ID, "groupId": "local-cabinets",
            "label": "Local cabinets", "stageId": "stage02", "commonBase": base, "baseStageRef": stage["stageRef"],
            "scope": ["element:portico-base"], "options": [
                {"id": "A", "label": "Original", "modelSource": base},
                {"id": "B", "label": "Taller", "modelSource": self.models[option]}],
            "selectedOptionId": None, "previousRevisionSha256": None})
        self.repository.put_json(run=self.repository.load_run(chosen), destination=run_records(chosen),
                                 record_kind=DELIBERATION_EPISODE, payload={
            "schema": "DeliberationEpisode@1", "episodeId": "ep-000000000001", "projectId": PROJECT_ID,
            "stateDigest": stage["modelSource"]["stateDigest"],
            "intent": {"utterance": "set height to 2.8", "targetComponentId": "portico", "elementId": "portico-base",
                       "requestedProperty": "height", "knownSlots": {}, "requestId": None},
            "proposals": [{"proposalId": "proposal-1", "target": "entity:portico-base",
                           "change": {"key": "height", "old": 0.6, "new": 2.8}, "closure": [],
                           "decision": "accepted", "reason": None, "modifiedTo": None}],
            "protected": [], "evidenceRefs": [], "validationRefs": [], "producedRun": chosen, "chosenScope": None,
            "createdAt": datetime.now(timezone.utc).isoformat()})

        with TestClient(create_app(self.settings)) as restarted:
            history = self.pool(restarted)
        candidates = {row["candidateId"]: row for row in history["candidates"]}
        self.assertEqual((candidates[option]["legacy"], candidates[option]["studyId"], candidates[option]["label"]),
                         ("working-copy", "working-copy:local-cabinets", "Taller"))
        self.assertEqual((candidates[option]["modelSource"], candidates[option]["baseStageRef"]),
                         (self.models[option], stage["stageRef"]))
        self.assertEqual((candidates[chosen]["legacy"], candidates[chosen]["studyId"]), ("episode", None))
        self.assertEqual(history["studies"], [{
            "id": "working-copy:local-cabinets", "label": "Local cabinets", "baseRunId": REFERENCE_RUN_ID,
            "baseStageRef": stage["stageRef"], "candidateIds": [option], "source": "working-copy"}])
        self.no_admission_run()

        # An explicit, attributed verdict is stronger than any legacy reading.
        with TestClient(create_app(self.settings)) as restarted:
            self.admit({"runId": option, "outcome": "rejected"}, client=restarted)
            history = self.pool(restarted)
        self.assertNotIn(option, [row["candidateId"] for row in history["candidates"]])
        self.assertEqual(history["studies"], [])

    def test_an_agent_admits_through_the_hub_and_rejects_only_on_bound_words(self) -> None:
        stage = self.stage()
        kept = self.result(stage, 2.4)
        tried = self.result(stage, 2.8)
        message = {"sessionId": "chat-1", "messageId": "message-7"}
        self.refused({"runId": kept, "outcome": "admitted"}, code="ADMISSION_INVALID", status=422,
                     task="hub-chat", messageSource=message)
        hub = self.managed()
        answer = self.admit({"runId": kept, "outcome": "admitted"}, task="hub-chat", client=hub, expect=422)
        self.assertEqual(answer["code"], "ADMISSION_INVALID")
        answer = self.admit({"runId": tried, "outcome": "rejected"}, task="hub-chat", client=hub, expect=422,
                            messageSource=message)
        self.assertIn("rawLanguage", answer["detail"])
        record = self.admit({"runId": kept, "outcome": "admitted"}, {"runId": tried, "outcome": "rejected"},
                            task="hub-chat", client=hub, messageSource=message, rawLanguage="keep the lower one")
        self.assertEqual(record["actor"]["origin"], "hub-agent")
        self.assertEqual((record["messageSource"], record["rawLanguage"]), (message, "keep the lower one"))
        self.assertEqual(list(self.admitted(self.pool())), [kept])

    def test_competing_and_unreadable_records_are_named_left_out_and_block_writes(self) -> None:
        stage = self.stage()
        kept = self.result(stage, 2.4)
        record = self.admit({"runId": kept, "outcome": "admitted"})
        run = self.repository.load_run("studio-admissions")
        review = PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id="studio-admissions")
        [ref] = self.repository.list_json(run=run, destination=review, record_kind=CANDIDATE_ADMISSION)
        retained = self.repository.load_json(ref)
        self.assertEqual((retained["admissionId"], retained["results"][0]["runId"]), (record["admissionId"], kept))
        # A second live record naming the same run, as a writer outside this gate could leave.
        self.repository.put_json(run=run, destination=review, record_kind=CANDIDATE_ADMISSION,
                                 payload={**retained, "admissionId": "adm-000000000002"})
        history = self.pool()
        self.assertNotIn(kept, self.admitted(history))
        competing = [warning for warning in history["warnings"] if kept in warning]
        self.assertEqual(len(competing), 1, history["warnings"])
        self.assertIn("adm-000000000002", competing[0])
        self.assertEqual(self.result_lines()[kept]["admission"], "none")
        self.assertEqual(len(self.listed()["admissions"]), 2)

        self.repository.put_json(run=run, destination=review, record_kind=CANDIDATE_ADMISSION,
                                 payload={"schema": "CandidateAdmission@1", "projectId": PROJECT_ID})
        listed = self.listed()
        self.assertEqual(len(listed["admissions"]), 2)
        self.assertTrue(any("left out" in warning for warning in listed["warnings"]), listed["warnings"])
        other = self.result(stage, 2.8)
        self.refused({"runId": other, "outcome": "admitted"}, code="ADMISSION_RECORD_INVALID")

    def test_admission_is_advertised_and_uses_the_decision_grant(self) -> None:
        self.assertIn("candidate-admission", BASE_CAPABILITIES)
        self.assertIn("candidate-admission", self.client.get("/api/protocol").json()["capabilities"])
        self.assertEqual(request_action("POST", "/api/admissions", shared_project=False), "accept")
        self.assertEqual(request_action("GET", "/api/admissions", shared_project=False), "read")
        # A shared project service does not take admissions until they synchronize.
        self.assertIsNone(request_action("POST", "/api/admissions", shared_project=True))
        malformed = self.client.post("/api/admissions", json={"projectId": PROJECT_ID, "task": {"kind": "ui"},
                                                             "results": [{"runId": "a", "outcome": "admitted"},
                                                                         {"runId": "a", "outcome": "rejected"}]})
        self.assertEqual((malformed.status_code, malformed.json()["code"]), (422, "ADMISSION_INVALID"))
        self.no_admission_run()
