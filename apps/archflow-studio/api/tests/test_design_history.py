"""Explicit model acceptance, forked history and cold reads through P036."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.adapters import occt_backend
from archflow.project.record_kinds import DESIGN_STAGE, STUDIO_CANDIDATE_DELTA
from archflow.project.refs import record_ref_from_uri
from archflow.state.state_record import StateRecordEditKind, StateRecordOperator
from archflow_studio_api.application.artifacts import list_artifacts
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.candidate import replay_candidate, run_operator
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    PROJECT_ID, REFERENCE_RUN_ID, RECORD_PAYLOAD, SEATS_PAYLOAD, advance_head,
    retain_runner_receipt, runner_state_digest, write_runner_seats,
)
from .test_candidate import CandidateTestCase
from .test_working_copies import register_model


class DesignHistoryTests(CandidateTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.model_bytes = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        self.model = register_model(self.client, REFERENCE_RUN_ID, self.state_digest, self.model_bytes)["modelSource"]
        self.initial_head = self.repository.read_head()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off")

    def initialize(self) -> dict:
        response = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": self.model})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def history(self, branch_id: str = "main", client: TestClient | None = None) -> dict:
        response = (client or self.client).get("/api/design-history", params={"branchId": branch_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def candidate_from(self, stage: dict, height: float = 2.2) -> str:
        accepted, job = self.run_candidate(
            f"set height to {height}", elementId="portico-base",
            stateDigest=stage["modelSource"]["stateDigest"], sourceStageRef=stage["stageRef"],
            sourceRunId=stage["candidateId"],
        )
        self.assertEqual(job["status"], "succeeded", job)
        candidate_id = accepted["candidateId"]
        response = self.client.get("/api/state", params={"run": candidate_id, "sourceStageRef": stage["stageRef"]})
        self.assertEqual(response.status_code, 200, response.text)
        # A complete model is explicitly registered against the real runner
        # state. These tests exercise acceptance; they do not run a CAD app.
        register_model(self.client, candidate_id, response.json()["stateDigest"], self.model_bytes)
        return candidate_id

    def accept(self, candidate_id: str, stage: dict, branch_id: str = "main", client: TestClient | None = None):
        return (client or self.client).post(f"/api/candidates/{candidate_id}/accept", json={
            "projectId": PROJECT_ID, "branchId": branch_id, "expectedHeadStageRef": stage["stageRef"],
        })

    def fork(self, stage: dict, branch_id: str = "alternative", parent: str = "main") -> dict:
        response = self.client.post("/api/design-branches", json={"projectId": PROJECT_ID,
                                    "branchId": branch_id, "parentBranch": parent, "stageRef": stage["stageRef"]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_legacy_model_does_not_become_history_without_explicit_acceptance(self) -> None:
        self.assertEqual(self.history()["stages"], [])
        self.assertEqual(self.history()["branches"], [])
        stage = self.initialize()
        self.assertEqual(stage["label"], "S0")
        self.assertIsNone(stage["parentStageRef"])
        self.assertEqual(stage["modelSource"], self.model)
        self.assertEqual(self.initialize(), stage)
        self.assertEqual(self.history()["stages"], [stage])
        self.assertEqual(self.repository.read_head(), self.initial_head)

    def test_acceptance_replays_and_survives_restart_without_proposal_memory(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        self.assertEqual(self.history()["stages"], [initial])
        with TestClient(create_app(self.settings)) as restarted:
            accepted = self.accept(candidate, initial, client=restarted)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            stage = accepted.json()
            self.assertEqual(stage["label"], "S1")
            self.assertEqual(stage["parentStageRef"], initial["stageRef"])
            self.assertEqual(self.history(client=restarted)["stages"], [initial, stage])
            replayed = replay_candidate(bound_project(restarted.app.state), candidate)
            self.assertEqual(replayed.digest, stage["recordDigest"])
            self.assertEqual(self.accept(candidate, initial, client=restarted).json(), stage)
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(self.history(client=restarted)["stages"], [initial, stage])
        self.assertEqual(self.repository.read_head(), self.initial_head)

    def test_artifact_candidate_source_survives_cold_read_without_projection(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        state = self.client.get("/api/state", params={"run": candidate}).json()
        second_model = register_model(self.client, candidate, state["stateDigest"], self.model_bytes + b"\n")
        with TestClient(create_app(self.settings)) as restarted:
            binding = bound_project(restarted.app.state)
            with (
                patch("archflow_studio_api.application.artifacts.project_state", side_effect=AssertionError("listing projected a state")),
                patch("archflow_studio_api.application.projection.project_state", side_effect=AssertionError("listing projected a state")),
                patch("archflow_studio_api.application.catalog.build_catalog", side_effect=AssertionError("listing built a catalog")),
                patch("archflow_studio_api.application.design_history.read_stage", side_effect=AssertionError("listing read a full Stage view")),
                patch.object(binding, "_survey", side_effect=AssertionError("candidate source surveyed every run")),
                patch.object(binding, "candidate_delta", wraps=binding.candidate_delta) as read_delta,
            ):
                response = restarted.get("/api/artifacts")
            self.assertEqual(response.status_code, 200, response.text)
            rows = response.json()["artifacts"]
            candidates = [row for row in rows if row["runId"] == candidate]
            self.assertEqual(len(candidates), 2)
            self.assertTrue(all(row["sourceStageRef"] == initial["stageRef"] for row in candidates))
            self.assertTrue(all(row["modelSource"]["stateDigest"] == state["stateDigest"] for row in candidates))
            self.assertTrue(all(row["sourceStageRef"] is None for row in rows if row["runId"] == REFERENCE_RUN_ID))
            self.assertEqual(sum(call.args == (candidate,) for call in read_delta.call_args_list), 1)
            # Internal artifact reads and downloads keep their original cost.
            with patch.object(binding, "candidate_delta", side_effect=AssertionError("internal artifact read asked for candidate metadata")):
                self.assertTrue(all(row.source_stage_ref is None for row in list_artifacts(binding).artifacts))
                downloaded = restarted.get(f"/api/artifacts/{second_model['modelSource']['assetSha256']}/bytes")
                self.assertEqual(downloaded.status_code, 200, downloaded.text)
                self.assertEqual(downloaded.content, self.model_bytes + b"\n")
        self.assertEqual(self.repository.read_head(), self.initial_head)

    def test_invalid_candidate_source_does_not_hide_artifacts(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        binding = bound_project(self.app.state)
        run = binding.load_run(candidate)
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=candidate)
        delta_ref = next(ref for ref in binding.record_refs(candidate) if ref.record_kind == STUDIO_CANDIDATE_DELTA)
        delta = self.repository.load_json(delta_ref)
        runner_ref, runner = binding.newest_runner_receipt(candidate)
        initial_ref = record_ref_from_uri(initial["stageRef"], PROJECT_ID)
        initial_stage = binding.design_stage(initial_ref)
        uncommitted = self.repository.put_json(
            run=self.repository.load_run(initial_stage.candidate_id),
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=initial_stage.candidate_id),
            record_kind=DESIGN_STAGE,
            payload={**self.repository.load_json(initial_ref), "label": "Uncommitted"},
        )
        cases = (
            (delta_ref, delta, "source_stage_ref", uncommitted.to_dict()),
            (delta_ref, delta, "source_stage_ref", {**initial_ref.to_dict(), "project_id": "another-project"}),
            (delta_ref, delta, "result_record_digest", "0" * 64),
            (delta_ref, delta, "run_id", "another-run"),
            (delta_ref, delta, "project_id", "another-project"),
            (runner_ref, runner, "state_record_ref", initial_stage.record_ref.uri),
            (runner_ref, runner, "state_record_digest", "0" * 64),
            (runner_ref, runner, "design_state_digest", "0" * 64),
        )
        for original_ref, original, field, value in cases:
            with self.subTest(record_kind=original_ref.record_kind, field=field, value=value):
                changed = self.repository.put_json(
                    run=run, destination=destination, record_kind=original_ref.record_kind,
                    payload={**original, field: value},
                )
                # Replace one fixture record through P036 so its stored hash is
                # valid and the cold reader must detect the semantic mismatch.
                self.repository.layout.resolve_record(original_ref).unlink()
                try:
                    with TestClient(create_app(self.settings)) as restarted:
                        response = restarted.get("/api/artifacts")
                        self.assertEqual(response.status_code, 200, response.text)
                        rows = [row for row in response.json()["artifacts"] if row["runId"] == candidate]
                        self.assertEqual(len(rows), 1)
                        self.assertTrue(rows[0]["available"])
                        self.assertIsNone(rows[0]["sourceStageRef"])
                finally:
                    self.repository.put_json(run=run, destination=destination,
                                             record_kind=original_ref.record_kind, payload=original)
                    self.repository.layout.resolve_record(changed).unlink()

    def test_competing_candidate_stays_available_after_branch_head_advances(self) -> None:
        initial = self.initialize()
        first = self.candidate_from(initial, 2.2)
        second = self.candidate_from(initial, 2.8)
        accepted = self.accept(first, initial)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        refused = self.accept(second, initial)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "DESIGN_BRANCH_STALE")
        self.assertEqual(self.client.get(f"/api/candidates/{second}").status_code, 200)
        self.assertEqual(self.history()["stages"], [initial, accepted.json()])
        following = self.candidate_from(accepted.json(), 3.1)
        self.assertEqual(self.accept(following, accepted.json()).status_code, 200)
        self.assertEqual(self.accept(first, initial).json(), accepted.json())
        self.assertEqual(len(self.history()["stages"]), 3)

    def test_two_adjustments_and_independent_candidate_combine_after_restart(self) -> None:
        initial = self.initialize()
        first = self.candidate_from(initial, 2.2)
        current = self.client.get("/api/state", params={"run": first}).json()
        self.assertEqual(current["sourceStageRef"], initial["stageRef"])
        started, job = self.run_candidate("set height to 2.4", elementId="portico-base",
                                          sourceRunId=first, sourceStageRef=initial["stageRef"], stateDigest=current["stateDigest"])
        self.assertEqual(job["status"], "succeeded", job)
        revised = started["candidateId"]
        binding = bound_project(self.app.state)
        projection = project_state(binding, source_stage_ref=initial["stageRef"])
        operator = StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR,
                                       base_record_digest=projection.record.digest, base_state_digest=projection.record.state_digest,
                                       target_ref="parameter:module", key="module", value=1.5)
        run_operator(binding, self.settings, operator, "studio-cand-independent",
                     source_stage_ref=record_ref_from_uri(initial["stageRef"], PROJECT_ID))
        self.assertEqual(self.history()["stages"], [initial])
        with TestClient(create_app(self.settings)) as restarted:
            response = restarted.post("/api/candidates/combine", json={"projectId": PROJECT_ID,
                                       "candidateIds": [revised, "studio-cand-independent"]})
            self.assertEqual(response.status_code, 202, response.text)
            accepted = response.json()
            restarted.app.state.jobs.shutdown()
            self.assertEqual(restarted.get(f"/api/jobs/{accepted['jobId']}").json()["status"], "succeeded")
            binding = bound_project(restarted.app.state)
            result = replay_candidate(binding, accepted["candidateId"])
            self.assertEqual(result.entity("portico-base").fields["params"]["height"], 2.4)
            self.assertEqual(result.parameter("module").value, 1.5)
            self.assertEqual(result.parameter("span").value, 6.0)
            register_model(restarted, accepted["candidateId"], project_state(binding, accepted["candidateId"]).state_digest, self.model_bytes)
            chosen = self.accept(accepted["candidateId"], initial, client=restarted)
            self.assertEqual(chosen.status_code, 200, chosen.text)
            self.assertEqual(len(self.history(client=restarted)["stages"]), 2)

    def test_combining_alternative_edits_refuses_before_creating_a_run(self) -> None:
        initial = self.initialize()
        first, second = self.candidate_from(initial, 2.2), self.candidate_from(initial, 2.8)
        before = set(bound_project(self.app.state).run_ids())
        response = self.client.post("/api/candidates/combine", json={"projectId": PROJECT_ID, "candidateIds": [first, second]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "CANDIDATE_COMBINE_CONFLICT")
        self.assertEqual(set(bound_project(self.app.state).run_ids()), before)

    def test_fork_from_old_stage_continues_after_formal_head_changes(self) -> None:
        initial = self.initialize()
        first = self.candidate_from(initial)
        accepted = self.accept(first, initial)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        branch = self.fork(initial)
        self.assertEqual(branch["forkStageRef"], initial["stageRef"])
        self.assertEqual(self.fork(initial), branch)
        advance_head(self.repository)
        published = self.repository.read_head()
        alternate = self.candidate_from(initial, 3.5)
        shown_validation = self.client.get(f"/api/candidates/{alternate}/validation")
        self.assertEqual(shown_validation.status_code, 200, shown_validation.text)
        self.assertTrue(shown_validation.json()["reviewReady"], shown_validation.text)
        self.assertEqual(shown_validation.json()["receipt"]["checkedState"]["version"], self.initial_head.version)
        chosen = self.accept(alternate, initial, "alternative")
        self.assertEqual(chosen.status_code, 200, chosen.text)
        self.assertEqual(chosen.json()["branchId"], "alternative")
        self.assertEqual(self.history("main")["stages"], [initial, accepted.json()])
        self.assertEqual(self.history("alternative")["stages"], [initial, chosen.json()])
        self.assertEqual(self.repository.load_run(alternate).base, self.initial_head)
        self.assertEqual(self.repository.read_head(), published)

    def test_failure_before_pointer_update_leaves_no_committed_stage(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        repository = bound_project(self.app.state).repository
        with TestClient(self.app, raise_server_exceptions=False) as client:
            with patch.object(repository, "compare_and_swap_design_branch", side_effect=OSError("interrupted before pointer write")):
                response = self.accept(candidate, initial, client=client)
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(self.history()["stages"], [initial])
        review = self.repository.list_json(run=self.repository.load_run(candidate), destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=candidate))
        uncommitted = next(ref for ref in review if ref.record_kind == DESIGN_STAGE)
        with TestClient(create_app(self.settings)) as restarted:
            refused = restarted.post("/api/design-branches", json={"projectId": PROJECT_ID, "branchId": "orphan",
                                      "parentBranch": "main", "stageRef": uncommitted.uri})
            self.assertEqual(refused.status_code, 409, refused.text)
            accepted = self.accept(candidate, initial, client=restarted)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(len(self.history(client=restarted)["stages"]), 2)

    def test_pinned_stage_ignores_later_runner_receipt_in_same_run(self) -> None:
        stage = self.initialize()
        retain_runner_receipt(self.repository, self.repository.load_run(REFERENCE_RUN_ID), design_state_digest=None)
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(self.history(client=restarted)["stages"], [stage])
            state = restarted.get("/api/state", params={"sourceStageRef": stage["stageRef"]})
            self.assertEqual(state.status_code, 200, state.text)
            self.assertEqual(state.json()["stateDigest"], self.model["stateDigest"])

    def test_missing_model_and_non_candidate_legacy_run_cannot_advance(self) -> None:
        invalid = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID,
                                  "modelSource": {**self.model, "assetSha256": "0" * 64}})
        self.assertEqual(invalid.status_code, 409, invalid.text)
        self.assertEqual(self.history()["stages"], [])
        stage = self.initialize()
        refused = self.accept(REFERENCE_RUN_ID, stage)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "CANDIDATE_DELTA_MISSING")
        self.assertEqual(self.history()["stages"], [stage])

    @unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
    def test_native_part_cannot_bootstrap_a_complete_multiseat_stage(self) -> None:
        record = deepcopy(RECORD_PAYLOAD)
        wing = deepcopy(record["entities"][1])
        wing["entity_id"] = "wing"
        element = deepcopy(record["entities"][4])
        element.update(entity_id="wing-base", parent_id="wing")
        element["fields"]["component_id"] = "wing"
        element["fields"]["params"]["profile"] = [[10, 0], [14, 0], [14, 2], [10, 2]]
        record["entities"] += [wing, element]
        seats = deepcopy(SEATS_PAYLOAD)
        seat = deepcopy(seats["seats"][0])
        seat.update(seat_id="seat-wing", owned_component_ids=["wing"])
        seats["seats"].append(seat)
        write_runner_seats(self.repository, seats)
        source = self.repository.create_run("whole-building-source")
        digest = runner_state_digest(self.repository, source.run_id, record)
        retain_runner_receipt(self.repository, source, design_state_digest=digest, record_payload=record)
        self.client.close()
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt"))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        accepted, job = self.run_candidate("set height to 2.8", elementId="portico-base", sourceRunId=source.run_id, stateDigest=digest)
        self.assertEqual(job["status"], "succeeded", job)
        result = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        previews = [row for row in result["artifacts"] if row["format"] == "3dm"]
        self.assertEqual(len(previews), 2)
        first, second = previews
        partial_request = {"projectId": PROJECT_ID, "modelSource": first["modelSource"]}
        refused = self.client.post("/api/design-stages/initialize", json=partial_request)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "MODEL_SOURCE_INCOMPLETE")
        self.assertEqual(self.history()["stages"], [])
        # Losing the other seat's preview does not make this part a whole model.
        (self.repository.layout.root / second["relativePath"]).write_bytes(b"unavailable preview")
        refused = self.client.post("/api/design-stages/initialize", json=partial_request)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "MODEL_SOURCE_INCOMPLETE")
        self.assertEqual(self.history()["stages"], [])
        self.assertEqual(self.repository.read_head(), self.initial_head)

    @unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
    def test_composed_candidate_uses_the_stage_runner_after_newer_receipt_is_retained(self) -> None:
        self.client.close()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        started, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        result = self.client.get(f"/api/candidates/{started['candidateId']}").json()
        native = next(row for row in result["artifacts"] if row["format"] == "3dm")
        data = self.client.get(f"/api/artifacts/{native['sha256']}/bytes").content
        # Register the complete real native model as an explicit source. A
        # trailing newline keeps the parsed geometry while separating its file
        # identity from the native receipt; the API's inspector verifies it.
        complete = register_model(self.client, result["candidateId"], result["stateDigest"], data + b"\n")
        response = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": complete["modelSource"]})
        self.assertEqual(response.status_code, 201, response.text)
        stage = response.json()
        binding = bound_project(self.app.state)
        pinned = binding.design_stage(record_ref_from_uri(stage["stageRef"], PROJECT_ID))
        before, job = self.run_candidate("set height to 2.8", elementId="portico-base",
                                         sourceRunId=stage["candidateId"], sourceStageRef=stage["stageRef"],
                                         stateDigest=stage["modelSource"]["stateDigest"])
        self.assertEqual(job["status"], "succeeded", job)
        newer = retain_runner_receipt(self.repository, self.repository.load_run(stage["candidateId"]), design_state_digest=None)
        self.assertNotEqual(newer, pinned.runner_ref)
        projected = self.client.get("/api/state", params={"sourceStageRef": stage["stageRef"]})
        self.assertEqual(projected.json()["stateDigest"], stage["modelSource"]["stateDigest"])
        after, job = self.run_candidate("set height to 3.1", elementId="portico-base",
                                        sourceRunId=stage["candidateId"], sourceStageRef=stage["stageRef"],
                                        stateDigest=stage["modelSource"]["stateDigest"])
        self.assertEqual(job["status"], "succeeded", job)
        actual = self.client.get(f"/api/candidates/{after['candidateId']}")
        self.assertEqual(actual.status_code, 200, actual.text)
        self.assertTrue(any(row["representation"] == "composed" and row["available"] for row in actual.json()["artifacts"]))
        self.assertEqual(binding.candidate_delta(after["candidateId"])["source_runner_ref"], pinned.runner_ref.to_dict())
        self.assertEqual(replay_candidate(binding, after["candidateId"]).entity("portico-base").fields["params"]["height"], 3.1)
        self.assertEqual(self.history()["stages"], [stage])
        self.assertEqual(self.repository.read_head(), self.initial_head)
