"""A proposal executed as a detached candidate, by the kernel's own runner.

Nothing in this file rehearses a run. ``POST /api/proposals/{id}/candidate``
creates a real P036 run in a real repository and hands the State Record to
``run_project``; what these tests read afterwards are the records the runner
retained — the ``runner-run-receipt``, the ``seat-geometry-program`` it names
and the ``seat-relation-check`` report with its three counts — not anything the
API remembered about its own work.

The failing case is as important as the passing one. A negative height is not
an API error and is never turned into one: the runner refuses it, and the job
carries the runner's own sentence to whoever asked. A candidate that quietly
became "no seats ran" would be the one bug this whole slice exists to prevent.

The optional villa test at the bottom runs the same code against a copy of the
real project's authored inputs. It is skipped unless ``ARCHFLOW_STUDIO_VILLA_INPUTS``
names a directory holding them, and it copies them into a temporary repository:
the real project is never written to by a test.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import bound_project, record_kind
from archflow_studio_api.application.candidate import execute_candidate
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from archflow.contracts.canonical import canonical_json
from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.repository import FilesystemProjectRepository

from archflow_studio_api.transport.errors import StudioError

from .support import (
    PROJECT_ID,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    RUNNER_SEATS_PATH,
    SEATS_PAYLOAD,
    add_unreadable_run,
    advance_head,
    make_project,
    runner_state_digest,
    write_runner_seats,
)

VILLA_INPUTS_ENV = "ARCHFLOW_STUDIO_VILLA_INPUTS"
VILLA_PROJECT_ID = "villa-rotonda-reconstruction"

# A run of the small fixture takes well under a second; the ceiling is here so
# a wedged worker fails the suite instead of hanging it.
JOB_DEADLINE = 120.0

TERMINAL = ("succeeded", "failed")

# ``studio-cand-<utc stamp>-<last 8 of the proposal>-<4 random hex>``, and the
# whole of it must be a P036 identifier.
CANDIDATE_ID = re.compile(
    r"^studio-cand-\d{8}-\d{6}-[0-9a-f]{8}-[0-9a-f]{4}$"
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class CandidateTestCase(unittest.TestCase):
    """One real project, one client, and the proposals it can be given."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.app = create_app(
            StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)
        )
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(
            self.repository, REFERENCE_RUN_ID
        )

    # ---- the three steps a candidate takes

    def propose(self, utterance: str, **body: object) -> dict:
        body.setdefault("stateDigest", self.state_digest)
        body.setdefault("targetComponentId", "portico")
        body["utterance"] = utterance
        response = self.client.post("/api/proposals", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def start(self, proposal_id: str) -> dict:
        response = self.client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def finished(self, job_id: str) -> dict:
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            if payload["status"] in TERMINAL:
                return payload
            time.sleep(0.02)
        raise AssertionError(f"job {job_id} never finished")

    def run_candidate(self, utterance: str, **body: object) -> tuple[dict, dict]:
        """Propose, execute, wait: the accepted body and the finished job."""

        accepted = self.start(self.propose(utterance, **body)["proposalId"])
        return accepted, self.finished(accepted["jobId"])

    def records_of(self, run_id: str) -> dict[str, int]:
        """How many retained records of each kind the run left behind."""

        kinds: dict[str, int] = {}
        for ref in self.repository.list_json(
            run=self.repository.load_run(run_id),
            destination=_run_records(run_id),
        ):
            kind = record_kind(ref)
            if kind is not None:
                kinds[kind] = kinds.get(kind, 0) + 1
        return kinds


class CandidateRunTests(CandidateTestCase):
    def test_accepted_candidate_names_the_job_and_the_run(self) -> None:
        accepted = self.start(
            self.propose("set height to 2.2", elementId="portico-base")[
                "proposalId"
            ]
        )

        self.assertEqual(accepted["status"], "queued")
        self.assertTrue(accepted["jobId"])
        # The candidate id is the run id: when it was made, which proposal it
        # came from, and which of that proposal's candidates it is.
        self.assertRegex(accepted["candidateId"], CANDIDATE_ID)
        self.assertRegex(accepted["candidateId"], IDENTIFIER)
        self.finished(accepted["jobId"])

    def test_a_semantic_proposal_runs_and_remains_parametrically_editable(self) -> None:
        from .test_intents import scripted, semantic_wall_edit

        edit = semantic_wall_edit()
        wall = edit["entities"][0]
        type_fields = {key: value for key, value in wall["fields"].items() if key != "component_id"}
        edit["entities"].insert(0, {
            "entity_id": "passage-wall-type", "schema": "Type@1",
            "fields": type_fields, "basis_refs": ["studio:intent"],
        })
        wall["fields"] = {"component_id": "portico", "producer": "wall", "type_ref": "passage-wall-type", "params": {}, "references": {}}
        self.app.state.intent_compiler = scripted(semantic_edit=edit, component_id="portico")
        before_head = self.repository.read_head()
        before_input = self.repository.layout.resolve_relative(RUNNER_RECORD_PATH).read_bytes()
        response = self.client.post("/api/intents", json={
            "stateDigest": self.state_digest, "sourceRunId": REFERENCE_RUN_ID,
            "targetComponentId": "portico", "elementId": "portico-base",
            "utterance": "Add a supporting wall with an arched passage, keeping the base unchanged.",
        })
        self.assertEqual(response.status_code, 201, response.text)
        proposal = response.json()["proposal"]
        self.assertEqual(proposal["sourceRunId"], REFERENCE_RUN_ID)
        accepted = self.start(proposal["proposalId"])
        job = self.finished(accepted["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        restored = _run_state_record(self.repository, accepted["candidateId"])
        wall = next(entity for entity in restored["entities"] if entity["entity_id"] == "passage-wall")
        self.assertEqual(wall["fields"]["producer"], "wall")
        self.assertEqual(wall["fields"]["type_ref"], "passage-wall-type")
        wall_type = next(entity for entity in restored["entities"] if entity["entity_id"] == "passage-wall-type")
        self.assertEqual(wall_type["fields"]["params"]["openings"][0]["width"], "@passage_width")
        state = self.client.get("/api/state", params={"run": accepted["candidateId"]}).json()
        edit = semantic_wall_edit()
        edit.update(
            summary="Narrow the same arched passage to 1.8 metres.", entities=[],
            parameters=[{"key": "passage_width", "value": 1.8, "unit": "m"}],
        )
        self.app.state.intent_compiler = scripted(semantic_edit=edit, component_id="portico")
        response = self.client.post("/api/intents", json={
            "stateDigest": state["stateDigest"], "sourceRunId": accepted["candidateId"],
            "targetComponentId": "portico", "elementId": "passage-wall",
            "utterance": "Make the same arch passage narrower, to 1.8 metres, keeping its semicircular relationship.",
        })
        self.assertEqual(response.status_code, 201, response.text)
        following = self.start(response.json()["proposal"]["proposalId"])
        self.assertEqual(self.finished(following["jobId"])["status"], "succeeded")
        successor = _run_state_record(self.repository, following["candidateId"])
        parameters = {item["key"]: item["value"] for item in successor["parameters"]}
        self.assertEqual(parameters["passage_width"], 1.8)
        self.assertEqual(parameters["passage_head"], 2.4)
        original_parameters = {item["key"]: item["value"] for item in restored["parameters"]}
        self.assertEqual(original_parameters["passage_width"], 2)
        self.assertEqual(self.repository.read_head(), before_head)
        self.assertEqual(self.repository.layout.resolve_relative(RUNNER_RECORD_PATH).read_bytes(), before_input)
        # Editing only the type must validate its instantiated opening before
        # another proposal or candidate can be offered.
        state = self.client.get("/api/state", params={"run": following["candidateId"]}).json()
        edit.update(entities=[{
            "entity_id": "passage-wall-type", "schema": "Type@1",
            "fields": {"params": {**type_fields["params"], "height": 1}},
        }], parameters=[])
        self.app.state.intent_compiler = scripted(semantic_edit=edit, component_id="portico")
        refused = self.client.post("/api/intents", json={
            "stateDigest": state["stateDigest"], "sourceRunId": following["candidateId"],
            "targetComponentId": "portico", "elementId": "passage-wall",
            "utterance": "Lower this wall type to one metre while keeping the same opening.",
        })
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertEqual(refused.json()["code"], "SEMANTIC_EDIT_INVALID")

    def test_two_candidates_of_one_proposal_never_share_a_run(self) -> None:
        """Started in the same second, they are still two different runs."""

        proposal_id = self.propose(
            "set height to 2.2", elementId="portico-base"
        )["proposalId"]

        first = self.start(proposal_id)
        second = self.start(proposal_id)

        self.assertNotEqual(first["candidateId"], second["candidateId"])
        self.assertNotEqual(first["jobId"], second["jobId"])
        # Both ids carry the same proposal; only the last group differs.
        self.assertEqual(
            first["candidateId"].rsplit("-", 1)[0].rsplit("-", 1)[1],
            second["candidateId"].rsplit("-", 1)[0].rsplit("-", 1)[1],
        )
        for accepted in (first, second):
            with self.subTest(candidate=accepted["candidateId"]):
                self.assertEqual(
                    self.finished(accepted["jobId"])["status"], "succeeded"
                )
                response = self.client.get(
                    f"/api/candidates/{accepted['candidateId']}"
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    response.json()["candidateId"], accepted["candidateId"]
                )

    def test_candidate_run_is_a_real_runner_run(self) -> None:
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        response = self.client.get(f"/api/candidates/{accepted['candidateId']}")
        self.assertEqual(response.status_code, 200, response.text)
        candidate = response.json()
        print(
            f"\n[candidate] {candidate['candidateId']} "
            f"wall={candidate['wallTimeS']}s job={job['wallTimeS']}s "
            f"seats={len(candidate['seatResults'])} "
            f"relations={candidate['relationChecks']}"
        )

        self.assertTrue(candidate["seatExecutionComplete"])
        self.assertEqual(candidate["proposalId"], job["proposalId"])
        self.assertEqual(candidate["jobId"], job["jobId"])
        self.assertEqual(candidate["status"], "succeeded")
        self.assertEqual(
            candidate["harness"],
            "studio-candidate-harness (not a project stage advance)",
        )
        # A run stands on a base, and the base travels: a candidate whose base
        # nobody could name would be a model of nothing in particular.
        self.assertEqual(candidate["base"]["version"], 0)
        self.assertEqual(len(candidate["stateDigest"]), 64)
        self.assertEqual(len(candidate["recordDigest"]), 64)
        # Export is off in the fixture, so no artifact is claimed.
        self.assertEqual(candidate["artifacts"], [])

        seat = candidate["seatResults"][0]
        self.assertEqual(seat["seatId"], "seat-portico")
        self.assertEqual(seat["status"], "proposal_accepted")
        self.assertEqual(seat["objects"], 2)
        self.assertEqual(len(seat["programDigest"]), 64)
        self.assertTrue(seat["programRef"].startswith("project://"))
        self.assertTrue(seat["relationCheckRef"].startswith("project://"))
        # Nothing was unreadable, and the list says so rather than being absent.
        self.assertEqual(candidate["skippedRuns"], [])

    def test_candidate_run_keeps_the_base_checked_before_head_moves(self) -> None:
        proposal = self.app.state.proposals.get(
            self.propose("set height to 2.2", elementId="portico-base")[
                "proposalId"
            ]
        )
        run_id = "studio-cand-head-race"
        binding = bound_project(self.app.state)
        candidate_repository = binding.repository
        original_create_run = candidate_repository.create_run
        moved = False

        def create_run_after_head_moves(
            requested_run_id: str, *, base=None
        ):
            nonlocal moved
            if requested_run_id == run_id and not moved:
                moved = True
                advance_head(
                    candidate_repository, run_id="promotion-during-candidate"
                )
            return original_create_run(requested_run_id, base=base)

        with mock.patch.object(
            candidate_repository,
            "create_run",
            side_effect=create_run_after_head_moves,
        ):
            execute_candidate(
                binding,
                self.app.state.settings,
                proposal,
                run_id,
            )

        self.assertEqual(self.repository.read_head().version, 1)
        self.assertEqual(self.repository.load_run(run_id).base.version, 0)

    def test_completed_candidate_is_recovered_from_p036_after_restart(self) -> None:
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        restarted = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(restarted.close)

        # A new process has no job or proposal memory, but the exact run and
        # its P036 runner receipt still describe the completed candidate.
        self.assertEqual(
            restarted.get(f"/api/jobs/{accepted['jobId']}").status_code, 404
        )
        response = restarted.get(
            f"/api/candidates/{accepted['candidateId']}"
        )

        self.assertEqual(response.status_code, 200, response.text)
        candidate = response.json()
        self.assertEqual(candidate["candidateId"], accepted["candidateId"])
        self.assertEqual(candidate["status"], "succeeded")
        self.assertIsNone(candidate["jobId"])
        self.assertIsNone(candidate["proposalId"])
        self.assertTrue(candidate["seatExecutionComplete"])
        self.assertTrue(candidate["receiptRef"].startswith("project://"))

    def test_restart_does_not_relabel_a_project_run_as_a_candidate(self) -> None:
        restarted = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(restarted.close)

        response = restarted.get(f"/api/candidates/{REFERENCE_RUN_ID}")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "CANDIDATE_NOT_FOUND")

    def test_restart_still_refuses_a_missing_run_and_a_run_without_a_receipt(
        self,
    ) -> None:
        empty_run = "studio-cand-empty"
        self.repository.create_run(empty_run)
        restarted = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(restarted.close)

        for candidate_id in ("studio-cand-missing", empty_run):
            with self.subTest(candidate=candidate_id):
                response = restarted.get(f"/api/candidates/{candidate_id}")
                self.assertEqual(response.status_code, 404, response.text)

    def test_candidate_differs_from_the_projection_it_started_from(
        self,
    ) -> None:
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        candidate = self.client.get(
            f"/api/candidates/{accepted['candidateId']}"
        ).json()
        projected = self.client.get("/api/state").json()

        self.assertTrue(candidate["changedVsProjection"])
        # Content identity, not binding identity: the candidate's record says
        # something different from the authored one.
        self.assertNotEqual(
            candidate["recordDigest"], projected["recordDigest"]
        )

    def test_a_candidate_that_changes_nothing_says_so(self) -> None:
        """The value the record already holds is still a candidate, and it
        reports that the content did not move."""

        accepted, job = self.run_candidate(
            "set height to 0.6", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        candidate = self.client.get(
            f"/api/candidates/{accepted['candidateId']}"
        ).json()
        projected = self.client.get("/api/state").json()

        self.assertIs(candidate["changedVsProjection"], False)
        self.assertEqual(
            candidate["recordDigest"], projected["recordDigest"]
        )
        # Content identity is invariant under binding; the binding identity is
        # a different number, and this candidate is bound to its own run.
        self.assertNotEqual(
            candidate["stateDigest"], projected["stateDigest"]
        )

    def test_a_candidate_says_what_it_recomputed_and_what_it_cannot_know(self) -> None:
        """What the declared chain did is stated with the values the run built from; what no edge covers is named as unknown."""

        accepted, job = self.run_candidate("set module to 1.5")
        self.assertEqual(job["status"], "succeeded", job)

        honesty = self.client.get(
            f"/api/candidates/{accepted['candidateId']}"
        ).json()["honesty"]

        self.assertEqual(
            honesty,
            [
                "this candidate applied the explicit edit at parameter:module "
                "and the kernel re-evaluated the declared expressions it "
                "reaches: parameter:bay = 3.0, parameter:span = 6.0",
                "2 components appear in no dependency edge (building, "
                "portico): what this edit does to them is unknown, not nothing",
            ],
        )

    def test_a_derived_parameter_is_refused_before_any_job_starts(self) -> None:
        """The kernel would refuse the scalar at run time; the proposal boundary refuses it first, naming the source to set."""

        response = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": self.state_digest,
                "targetComponentId": "portico",
                "utterance": "set bay to 3",
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertIn("parameter bay is derived by '2 * module'", body["question"])
        self.assertIn("set module (= 1.2 m) instead", body["question"])
        # nothing was queued: no candidate run directory exists
        self.assertEqual(
            [path.name for path in self.repository.layout.runs.iterdir() if path.name.startswith("studio-cand-")],
            [],
        )

    def test_unreadable_runs_are_named_on_the_candidate(self) -> None:
        """One corrupt run must not cost the candidate its readout."""

        broken = add_unreadable_run(self.repository)

        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        candidate = self.client.get(
            f"/api/candidates/{accepted['candidateId']}"
        ).json()

        self.assertEqual(candidate["skippedRuns"], [broken])
        self.assertIs(candidate["seatExecutionComplete"], True)

    def test_an_unfinished_candidate_says_it_is_unfinished(self) -> None:
        """A 404 for work still in flight must not read as a failure."""

        proposal_id = self.propose(
            "set height to 2.2", elementId="portico-base"
        )["proposalId"]
        # The registry has one worker; holding it lets the candidate be asked
        # for while it is provably not finished.
        release = threading.Event()
        self.addCleanup(release.set)
        held = self.app.state.jobs.submit(
            candidate_id="studio-cand-held",
            proposal_id=proposal_id,
            work=release.wait,
        )

        response = self.client.get("/api/candidates/studio-cand-held")

        self.assertEqual(response.status_code, 404, response.text)
        body = response.json()
        self.assertEqual(body["code"], "CANDIDATE_NOT_FOUND")
        self.assertRegex(body["detail"], r"still (queued|running)")
        self.assertIn(held.job_id, body["detail"])
        release.set()
        self.assertEqual(self.finished(held.job_id)["status"], "succeeded")

    def test_the_run_retains_the_kernel_records_the_readout_reads(self) -> None:
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        kinds = self.records_of(accepted["candidateId"])

        self.assertEqual(kinds.get("runner-run-receipt"), 1)
        self.assertGreaterEqual(kinds.get("seat-geometry-program", 0), 1)
        self.assertGreaterEqual(kinds.get("seat-relation-check", 0), 1)
        # The harness guard's own two records, retained before the run.
        self.assertEqual(kinds.get("studio-candidate-workflow"), 1)
        self.assertEqual(kinds.get("studio-candidate-envelope"), 1)
        # Running a candidate is a preview, not a judgement: the run retains
        # no deliberation episode of its own. The only link kept is the job
        # registry's, proposal -> candidate.
        self.assertNotIn("deliberation-episode", kinds)
        self.assertEqual(
            self.app.state.jobs.candidates_of(job["proposalId"]),
            (accepted["candidateId"],),
        )

    def test_relation_checks_are_reported_as_three_states(self) -> None:
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        checks = self.client.get(
            f"/api/candidates/{accepted['candidateId']}"
        ).json()["relationChecks"]

        # Three distinct counts, never two: unchecked is its own answer and is
        # never folded into either of the others.
        self.assertEqual(
            sorted(checks),
            ["fullyChecked", "held", "heldFlag", "unchecked", "violated"],
        )
        self.assertEqual(checks["held"], 2)
        self.assertEqual(checks["violated"], 0)
        self.assertEqual(checks["unchecked"], 0)
        self.assertIs(checks["heldFlag"], True)
        self.assertIs(checks["fullyChecked"], True)
        # Whatever the counts are, they account for every check the run made.
        report = _relation_report(self.repository, accepted["candidateId"])
        self.assertEqual(
            checks["held"] + checks["violated"] + checks["unchecked"],
            len(report["checks"]),
        )

    def test_a_candidate_run_never_becomes_the_projection_reference(
        self,
    ) -> None:
        """The harness is a harness: it must not move the project's answer."""

        before = self.client.get("/api/state").json()["referenceRun"]["runId"]
        _, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        self.assertEqual(
            self.client.get("/api/state").json()["referenceRun"]["runId"],
            before,
        )

    def test_head_and_the_authored_record_are_untouched(self) -> None:
        """A candidate writes a run and nothing else the project stands on."""

        record_path = self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        )
        before_head = self.repository.read_head()
        before_record = record_path.read_bytes()

        _, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        self.assertEqual(self.repository.read_head(), before_head)
        self.assertEqual(record_path.read_bytes(), before_record)

    def test_parameter_candidates_replace_the_source_and_the_kernel_recomputes_the_chain(self) -> None:
        accepted, job = self.run_candidate("set module to 1.5")
        self.assertEqual(job["status"], "succeeded", job)

        record = _run_state_record(self.repository, accepted["candidateId"])
        parameters = {parameter["key"]: parameter for parameter in record["parameters"]}
        values = {key: parameter["value"] for key, parameter in parameters.items()}
        self.assertEqual(values["module"], 1.5)
        # The studio edits the one authored value; what a changed input does
        # to the values downstream of it is the kernel's answer (kernel card
        # K1), and the kernel follows the declared expressions: bay = 2 *
        # module and span = 2 * bay are re-evaluated in the successor record.
        self.assertEqual(values["bay"], 3.0)
        self.assertEqual(values["span"], 6.0)
        # The declarations themselves did not move, and the unrelated locked
        # literal stands exactly as authored.
        self.assertEqual(parameters["bay"]["expr"], "2 * module")
        self.assertEqual(parameters["span"]["expr"], "2 * bay")
        self.assertEqual(values["plinth"], 0.6)
        self.assertEqual(parameters["plinth"]["lock_authority"], "client")
        elements = {
            entity["entity_id"]: entity["fields"]
            for entity in record["entities"]
            if entity.get("schema") == "Element@1"
        }
        self.assertEqual(elements["portico-base"]["params"]["height"], 0.6)
        self.assertEqual(elements["portico-cornice"]["params"]["height"], 0.3)

    def test_element_candidates_replace_only_the_named_param(self) -> None:
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)

        record = _run_state_record(self.repository, accepted["candidateId"])
        elements = {
            entity["entity_id"]: entity["fields"]
            for entity in record["entities"]
            if entity.get("schema") == "Element@1"
        }
        self.assertEqual(elements["portico-base"]["params"]["height"], 2.2)
        # The sibling row and the untouched param on the same row both stand.
        self.assertEqual(elements["portico-cornice"]["params"]["height"], 0.3)
        self.assertEqual(
            elements["portico-base"]["params"]["profile"],
            [[0, 0], [4, 0], [4, 2], [0, 2]],
        )


class CandidateContinuationTests(CandidateTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.before_head = self.repository.read_head()
        self.authored_inputs = {
            path: self.repository.layout.resolve_relative(path).read_bytes()
            for path in (RUNNER_RECORD_PATH, RUNNER_SEATS_PATH)
        }
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)
        self.source_run_id = accepted["candidateId"]
        response = self.client.get(
            "/api/state", params={"run": self.source_run_id}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.source_state = response.json()
        self.source_body = {
            "projectId": PROJECT_ID,
            "sourceRunId": self.source_run_id,
            "stateDigest": self.source_state["stateDigest"],
            "targetComponentId": "portico",
            "elementId": "portico-cornice",
            "utterance": "set height to 0.5",
        }

    def assert_continued_record(self, run_id: str, cornice_height: float) -> None:
        record = _run_state_record(self.repository, run_id)
        heights = {
            entity["entity_id"]: entity["fields"]["params"]["height"]
            for entity in record["entities"]
            if entity.get("schema") == "Element@1"
        }
        self.assertEqual(heights["portico-base"], 2.2)
        self.assertEqual(heights["portico-cornice"], cornice_height)

    def test_both_request_routes_continue_the_selected_candidate(self) -> None:
        self.assertEqual(
            self.source_state["referenceRun"]["runId"], self.source_run_id
        )
        self.assertTrue(self.source_state["matchesReferenceReceipt"])
        self.assertNotEqual(self.source_state["stateDigest"], self.state_digest)
        for route in ("/api/intents", "/api/proposals"):
            with self.subTest(route=route):
                response = self.client.post(route, json=self.source_body)
                self.assertEqual(response.status_code, 201, response.text)
                payload = response.json()
                proposal = payload.get("proposal", payload)
                self.assertEqual(proposal["sourceRunId"], self.source_run_id)
                self.assertEqual(
                    proposal["baseStateDigest"], self.source_state["stateDigest"]
                )
                self.assertEqual(
                    proposal["recordDigest"], self.source_state["recordDigest"]
                )
                accepted = self.start(proposal["proposalId"])
                job = self.finished(accepted["jobId"])
                self.assertEqual(job["status"], "succeeded", job)
                self.assert_continued_record(accepted["candidateId"], 0.5)

        self.assert_continued_record(self.source_run_id, 0.3)
        self.assertEqual(self.repository.read_head(), self.before_head)
        for path, content in self.authored_inputs.items():
            self.assertEqual(
                self.repository.layout.resolve_relative(path).read_bytes(), content
            )
        default = self.client.get("/api/state").json()
        self.assertEqual(default["referenceRun"]["runId"], REFERENCE_RUN_ID)
        self.assertEqual(default["stateDigest"], self.state_digest)
        default_proposal = self.propose("set height to 2.3", elementId="portico-base")
        self.assertEqual(default_proposal["change"]["old"], 0.6)

    def test_candidate_read_pick_and_closure_use_the_selected_run(self) -> None:
        for route in ("/api/state/frame", "/api/state/volumes"):
            with self.subTest(route=route):
                response = self.client.get(route, params={"run": self.source_run_id})
                self.assertEqual(response.status_code, 200, response.text)
                missing = self.client.get(route, params={"run": "missing-source"})
                self.assertEqual(missing.status_code, 404, missing.text)
                self.assertEqual(missing.json()["code"], "RUN_NOT_FOUND")
        selected = {
            "sourceRunId": self.source_run_id,
            "stateDigest": self.source_state["stateDigest"],
        }
        closure = self.client.post(
            "/api/state/closure",
            json={**selected, "changedRefs": ["entity:portico-base"]},
        )
        self.assertEqual(closure.status_code, 200, closure.text)
        self.assertIn("entity:portico-cornice", closure.json()["closure"])
        pick = self.client.post(
            "/api/pick/resolve",
            json={
                **selected,
                "userStrings": {
                    "archflow:component": "portico",
                    "archflow:object_ref": "cad-object:obj-portico-base",
                    "archflow:producer_op": "portico-base",
                },
                "documentUserStrings": {
                    "archflow:project_id": PROJECT_ID,
                    "archflow:run_id": self.source_run_id,
                    "archflow:design_state_digest": self.source_state["stateDigest"],
                },
            },
        )
        self.assertEqual(pick.status_code, 200, pick.text)
        self.assertEqual(pick.json()["elementId"], "portico-base")
        self.assertEqual(pick.json()["sourceState"], "current")

    def test_modifying_a_continuation_preserves_its_source(self) -> None:
        proposal = self.propose(**self.source_body)
        decision = self.client.post(
            f"/api/proposals/{proposal['proposalId']}/decision",
            json={
                "decision": "modified",
                "reason": "a smaller cornice increase",
                "modifiedTo": {"utterance": "set height to 0.4"},
            },
        )
        self.assertEqual(decision.status_code, 201, decision.text)
        replacement_id = decision.json()["proposals"][0]["modifiedTo"]["proposalId"]
        replacement = self.client.get(f"/api/proposals/{replacement_id}").json()
        self.assertEqual(replacement["sourceRunId"], self.source_run_id)
        self.assertEqual(
            replacement["baseStateDigest"], self.source_state["stateDigest"]
        )
        accepted = self.start(replacement_id)
        job = self.finished(accepted["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assert_continued_record(accepted["candidateId"], 0.4)

    def test_source_selection_does_not_fall_back_on_invalid_requests(self) -> None:
        for route in ("/api/intents", "/api/proposals"):
            for changed, status, code in (
                ({"sourceRunId": "missing-source"}, 404, "RUN_NOT_FOUND"),
                ({"projectId": "another-project"}, 403, "PROJECT_MISMATCH"),
                ({"stateDigest": self.state_digest}, 409, "STALE_BASE"),
            ):
                with self.subTest(route=route, code=code):
                    response = self.client.post(
                        route, json={**self.source_body, **changed}
                    )
                    self.assertEqual(response.status_code, status, response.text)
                    self.assertEqual(response.json()["code"], code)

        advance_head(self.repository, run_id="promotion-after-source")
        self.assertEqual(
            self.client.get("/api/state", params={"run": self.source_run_id}).status_code,
            200,
        )
        for route in ("/api/intents", "/api/proposals"):
            response = self.client.post(route, json=self.source_body)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "REFERENCE_BASE_STALE")

    def test_queued_continuation_rechecks_the_selected_record(self) -> None:
        proposal = self.propose(**self.source_body)
        release = threading.Event()
        self.addCleanup(release.set)
        held = self.app.state.jobs.submit(
            candidate_id="studio-cand-hold-continuation",
            proposal_id=proposal["proposalId"],
            work=release.wait,
            closure=("entity:portico-cornice",),
        )
        accepted = self.start(proposal["proposalId"])
        self.assertEqual(
            self.client.get(f"/api/jobs/{accepted['jobId']}").json()["status"],
            "queued",
        )
        receipt = _load_kind(self.repository, self.source_run_id, "runner-run-receipt")
        receipt["state_record_digest"] = "0" * 64
        ref = self.repository.put_json(
            run=self.repository.load_run(self.source_run_id),
            destination=_run_records(self.source_run_id),
            record_kind="runner-run-receipt",
            payload=receipt,
        )
        path = self.repository.layout.resolve_record(ref)
        newest = path.stat().st_mtime + 60.0
        os.utime(path, (newest, newest))
        for route in ("/api/intents", "/api/proposals"):
            response = self.client.post(route, json=self.source_body)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "REFERENCE_STATE_NOT_EXACT")
        release.set()
        self.assertEqual(self.finished(held.job_id)["status"], "succeeded")
        job = self.finished(accepted["jobId"])
        self.assertEqual(job["status"], "failed", job)
        self.assertIn("state_record_digest does not match", job["error"])
        self.assertFalse(
            (self.repository.layout.runs / accepted["candidateId"]).exists()
        )


class CandidateFailureTests(CandidateTestCase):
    def test_a_value_the_runner_refuses_fails_the_job_out_loud(self) -> None:
        accepted, job = self.run_candidate(
            "set height to -1", elementId="portico-base"
        )

        self.assertEqual(job["status"], "failed", job)
        # The runner's own sentence, not a summary of it.
        self.assertIn("height must be positive", job["error"])
        self.assertIsNotNone(job["finishedAt"])
        # A failed run produced no candidate to read.
        response = self.client.get(f"/api/candidates/{accepted['candidateId']}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "CANDIDATE_NOT_FOUND")

    def test_a_missing_seat_pack_fails_the_job_by_name(self) -> None:
        self.repository.layout.resolve_relative(RUNNER_SEATS_PATH).unlink()

        _, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )

        self.assertEqual(job["status"], "failed", job)
        self.assertIn("SEATS_NOT_FOUND", job["error"])
        self.assertIn(RUNNER_SEATS_PATH, job["error"])

    def test_a_candidate_whose_job_failed_early_is_named_by_its_job(
        self,
    ) -> None:
        """A job that failed before ``create_run`` left no run to look in.

        The seat pack, the base check and the authored record are all resolved
        before the run directory is made, so a candidate that failed there has
        no run at all — and the reason it failed is on the job. Reading it back
        must say that, rather than answer about a run id nobody created.
        """

        self.repository.layout.resolve_relative(RUNNER_SEATS_PATH).unlink()
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "failed", job)
        self.assertFalse(
            (self.repository.layout.runs / accepted["candidateId"]).exists()
        )

        response = self.client.get(
            f"/api/candidates/{accepted['candidateId']}"
        )

        self.assertEqual(response.status_code, 404, response.text)
        body = response.json()
        self.assertEqual(body["code"], "CANDIDATE_NOT_FOUND")
        self.assertIn(f"GET /api/jobs/{job['jobId']}", body["detail"])

    def test_a_conflicting_proposal_is_not_runnable(self) -> None:
        proposal = self.propose(
            "set height to 2.2 keep entity:portico-base",
            elementId="portico-base",
        )
        self.assertEqual(proposal["status"], "conflict")

        response = self.client.post(
            f"/api/proposals/{proposal['proposalId']}/candidate"
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "PROPOSAL_NOT_RUNNABLE")

    def test_a_proposal_against_another_base_is_stale(self) -> None:
        proposal = self.propose(
            "set height to 2.2", elementId="portico-base"
        )
        advance_head(self.repository, run_id="promotion-before-candidate")

        response = self.client.post(
            f"/api/proposals/{proposal['proposalId']}/candidate"
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "REFERENCE_BASE_STALE")

    def test_a_candidate_id_is_never_rebound_to_a_second_job(self) -> None:
        """Two jobs on one candidate id would orphan the first run's records."""

        registry = self.app.state.jobs
        first = registry.submit(
            candidate_id="studio-cand-clash",
            proposal_id="studio-first",
            work=lambda: None,
        )

        with self.assertRaises(StudioError) as caught:
            registry.submit(
                candidate_id="studio-cand-clash",
                proposal_id="studio-second",
                work=lambda: None,
            )

        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "CANDIDATE_ID_COLLISION")
        self.assertIn("studio-cand-clash", caught.exception.detail)
        self.assertIn(first.job_id, caught.exception.detail)
        # The first job still owns the id, and its own record is untouched.
        self.assertEqual(
            registry.for_candidate("studio-cand-clash").job_id, first.job_id
        )
        self.assertEqual(registry.get(first.job_id).proposal_id, "studio-first")

    def test_head_that_moved_after_preflight_fails_the_run(self) -> None:
        """The base is checked again where the record is actually read.

        The route checked it when the request arrived; this is the interval
        the route cannot see — after the job was accepted, before the record
        was read — and a candidate must not be built from a state nobody was
        shown. No run directory is created for a run that cannot happen.
        """

        proposal = self.app.state.proposals.get(
            self.propose("set height to 2.2", elementId="portico-base")[
                "proposalId"
            ]
        )
        advance_head(self.repository, run_id="promotion-before-worker")

        run_id = "studio-cand-stale-guard"
        binding = bound_project(self.app.state)
        job = self.app.state.jobs.submit(
            candidate_id=run_id,
            proposal_id=proposal.proposal_id,
            work=lambda: execute_candidate(
                binding, self.app.state.settings, proposal, run_id
            ),
        )
        finished = self.finished(job.job_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertIn("reference run", finished["error"])
        self.assertIn("HEAD is version 1", finished["error"])
        self.assertFalse((self.repository.layout.runs / run_id).exists())

    def test_what_compiled_the_words_is_retained_with_the_run_they_became(
        self,
    ) -> None:
        """A chat turn is work in progress; a run is shared, so the receipt lands.

        The receipt of the model call that compiled the utterance is retained
        in the candidate run and nowhere earlier: the proposal it belongs to
        may never become a run at all.
        """

        proposal = self.app.state.proposals.get(
            self.propose("set height to 2.2", elementId="portico-base")[
                "proposalId"
            ]
        )
        compiled = replace(
            proposal,
            compilation_receipt=_compilation_receipt(
                proposal.base_state_digest
            ),
        )
        run_id = "studio-cand-intent-receipt"

        execute_candidate(
            bound_project(self.app.state),
            self.app.state.settings,
            compiled,
            run_id,
        )

        self.assertEqual(self.records_of(run_id).get("intent-compilation"), 1)
        retained = _load_kind(self.repository, run_id, "intent-compilation")
        self.assertEqual(retained["schema"], "IntentCompilation@1")
        self.assertEqual(retained["proposal_id"], compiled.proposal_id)
        self.assertEqual(retained["utterance"], compiled.utterance)
        self.assertEqual(
            retained["base_state_digest"], compiled.base_state_digest
        )
        self.assertEqual(
            retained["receipt"]["schema"], "ModelInvocationReceipt@2"
        )
        self.assertEqual(
            retained["receipt"]["provider_id"], "intent-test-provider"
        )

    def test_a_sentence_no_model_read_retains_no_compilation(self) -> None:
        """The deterministic compiler calls nothing, so there is nothing to keep."""

        proposal = self.app.state.proposals.get(
            self.propose("set height to 2.2", elementId="portico-base")[
                "proposalId"
            ]
        )
        self.assertIsNone(proposal.compilation_receipt)
        run_id = "studio-cand-no-intent-receipt"

        execute_candidate(
            bound_project(self.app.state),
            self.app.state.settings,
            proposal,
            run_id,
        )

        self.assertNotIn("intent-compilation", self.records_of(run_id))
        # The run itself is a real one; only the compilation record is absent.
        self.assertIn("runner-run-receipt", self.records_of(run_id))

    def test_a_seat_pack_that_declares_no_provider_still_runs(self) -> None:
        """The runner records its own proposals; a declared live provider is optional."""

        write_runner_seats(
            self.repository,
            {
                key: value
                for key, value in SEATS_PAYLOAD.items()
                if key != "provider_identity"
            },
        )

        _, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )

        self.assertEqual(job["status"], "succeeded", job)

    def test_unknown_ids_are_named_not_guessed(self) -> None:
        for path, code in (
            ("/api/proposals/studio-nope/candidate", "PROPOSAL_NOT_FOUND"),
            ("/api/jobs/job-nope", "JOB_NOT_FOUND"),
            ("/api/candidates/studio-cand-nope", "CANDIDATE_NOT_FOUND"),
        ):
            with self.subTest(path=path):
                response = (
                    self.client.post(path)
                    if path.endswith("candidate")
                    else self.client.get(path)
                )
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["code"], code)


class CandidateEventTests(CandidateTestCase):
    def test_the_lifecycle_is_published_in_order(self) -> None:
        accepted, job = self.run_candidate(
            "set height to 2.2", elementId="portico-base"
        )
        self.assertEqual(job["status"], "succeeded", job)
        _, failed = self.run_candidate(
            "set height to -1", elementId="portico-base"
        )
        self.assertEqual(failed["status"], "failed", failed)

        published = self.app.state.events.replay()
        for_job = [
            event
            for event in published
            if event.get("job_id") == accepted["jobId"]
        ]

        self.assertEqual(
            [event["type"] for event in for_job],
            ["candidate.queued", "candidate.running", "candidate.succeeded"],
        )
        self.assertEqual(
            [event["seq"] for event in for_job],
            sorted(event["seq"] for event in for_job),
        )
        # All four lifecycle types are reachable, failure included.
        self.assertEqual(
            {event["type"] for event in published},
            {
                "candidate.queued",
                "candidate.running",
                "candidate.succeeded",
                "candidate.failed",
            },
        )
        succeeded = for_job[-1]
        self.assertEqual(succeeded["run_id"], accepted["candidateId"])
        self.assertIsInstance(succeeded["wall_time_s"], float)
        failure = next(
            event
            for event in published
            if event["type"] == "candidate.failed"
        )
        self.assertIn("height must be positive", failure["error"])


@unittest.skipUnless(
    os.environ.get(VILLA_INPUTS_ENV),
    f"set {VILLA_INPUTS_ENV} to a directory holding the villa's "
    "state-record.json and seats.json to run this",
)
class VillaCopyTests(unittest.TestCase):
    """The same code path on a copy of the real project's authored inputs.

    A copy, never the project: this test creates runs, and a test that created
    them in the villa would be writing into the thing it is measuring.
    """

    def setUp(self) -> None:
        source = Path(os.environ[VILLA_INPUTS_ENV])
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        repository = FilesystemProjectRepository.initialize(
            self.root / VILLA_PROJECT_ID,
            project_id=VILLA_PROJECT_ID,
            initial_state={"project_id": VILLA_PROJECT_ID, "version": 0},
        )
        for name in ("state-record.json", "seats.json"):
            destination = repository.layout.resolve_relative(
                f"input/runner/{name}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        self.repository = repository
        self.client = TestClient(
            create_app(
                StudioSettings(cad_export="off", project_dir=self.root / VILLA_PROJECT_ID)
            )
        )
        self.addCleanup(self.client.close)

    def test_the_villa_copy_runs_its_seats(self) -> None:
        state = self.client.get("/api/state").json()
        proposal = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": state["stateDigest"],
                "targetComponentId": "portico-roof-abutments",
                "elementId": "portico-roof-abutment-west",
                "utterance": "set height to 2.2",
            },
        )
        self.assertEqual(proposal.status_code, 201, proposal.text)
        accepted = self.client.post(
            f"/api/proposals/{proposal.json()['proposalId']}/candidate"
        )
        self.assertEqual(accepted.status_code, 202, accepted.text)
        job_id = accepted.json()["jobId"]
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in TERMINAL:
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "succeeded", job)

        candidate = self.client.get(
            f"/api/candidates/{accepted.json()['candidateId']}"
        ).json()
        print(
            f"\n[villa candidate] wall={candidate['wallTimeS']}s "
            f"seats={[(s['seatId'], s['status'], s['objects']) for s in candidate['seatResults']]} "
            f"relations={candidate['relationChecks']} "
            f"honesty={candidate['honesty']}"
        )
        self.assertTrue(candidate["seatExecutionComplete"])
        self.assertTrue(candidate["changedVsProjection"])
        # The villa's record declares no dependency edges at all, so nothing
        # was propagated *and* nothing could have been. The candidate says the
        # second, not just the first.
        self.assertEqual(
            candidate["honesty"],
            [
                "0 dependency edges: nothing downstream could be recomputed "
                "or checked"
            ],
        )
        self.assertEqual(len(candidate["seatResults"]), 2)
        self.assertTrue(
            all(
                seat["status"] == "proposal_accepted"
                for seat in candidate["seatResults"]
            ),
            candidate["seatResults"],
        )


def _compilation_receipt(base_state_digest: str) -> dict:
    """One ``ModelInvocationReceipt@2``, shaped as the intent compilers write it."""

    request = ModelInvocationRequest.create(
        request_id="intent-test-request",
        phase=ModelPhase.INTENT_COMPILATION,
        checkpoint_digest=base_state_digest,
        context_digest=hashlib.sha256(b"intent-test-context").hexdigest(),
        payload={"utterance": "set height to 2.2"},
    )
    answer = canonical_json(
        {"utterance": "set height of portico-base to 2.2"}, ascii=False
    )
    return ModelInvocationReceipt(
        receipt_id="intent-test-receipt",
        status=ModelInvocationStatus.SUCCESS,
        request=request,
        provider_id="intent-test-provider",
        model_id="intent-test-model",
        provider_version="1",
        provider_fingerprint=hashlib.sha256(b"intent-test-provider").hexdigest(),
        input_bytes=len(request.payload_json.encode("utf-8")),
        output_bytes=len(answer.encode("utf-8")),
        output_sha256=hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        duration_ms=7,
        output_json=answer,
    ).to_dict()


def _run_records(run_id: str) -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)


def _load_kind(
    repository: FilesystemProjectRepository, run_id: str, kind: str
) -> dict:
    """The one retained record of that kind in the run, loaded by the kernel."""

    for ref in repository.list_json(
        run=repository.load_run(run_id), destination=_run_records(run_id)
    ):
        if record_kind(ref) == kind:
            return dict(repository.load_json(ref))
    raise AssertionError(f"run {run_id} retained no {kind} record")


def _relation_report(
    repository: FilesystemProjectRepository, run_id: str
) -> dict:
    return _load_kind(repository, run_id, "seat-relation-check")


def _run_state_record(
    repository: FilesystemProjectRepository, run_id: str
) -> dict:
    return _load_kind(repository, run_id, "state-record")


if __name__ == "__main__":
    unittest.main()
