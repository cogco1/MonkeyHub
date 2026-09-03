"""The kernel's validation receipt, and the verdict the server issues beside it.

Nothing here rehearses validation. Every receipt these tests read came out of
``archflow.validation.engine.validate_submission`` over a submission built from
a real candidate run's own records, and the one thing the studio adds — the
advance verdict — is asserted as what it is: a conjunction of four named
clauses, each of which is shown blocking on its own.

The two negative cases are built the same way as the positive one. A stale base
is a real ``CandidateRun`` whose base is an explicit older
``ProjectVersionRef``, handed to the same application function, so the
``state.base_mismatch`` finding is the kernel's own; unchecked relations are a
real candidate with a synthetic three-state total, so ``advance`` flipping to
false is the verdict's own rule. Neither mocks the kernel, because a mocked
gate proves only that the mock was called.

The villa test at the bottom is the reality check, and it is a copy: it runs
candidates, and a test that ran them in the real project would be writing into
the thing it is measuring.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.candidate import (
    CandidateRun,
    RelationTotals,
    describe,
)
from archflow_studio_api.application.validation import (
    CANONICAL_FACTS,
    EFFECTIVE_CHECKS,
    VALIDATOR_NAMES,
    VALIDATOR_NOTE,
    submission_for,
    validate_candidate,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from archflow.project.refs import ProjectVersionRef
from archflow.project.repository import FilesystemProjectRepository

from .support import PROJECT_ID, RUNNER_RECORD_PATH
from .test_candidate import (
    JOB_DEADLINE,
    TERMINAL,
    VILLA_INPUTS_ENV,
    VILLA_PROJECT_ID,
    CandidateTestCase,
)

# The three validators the studio runs, in the order it passes them.
THREE = [
    "artifact-present",
    "obligation-discharge",
    "authorized-commitment-claims",
]


class ValidationTestCase(CandidateTestCase):
    """One real candidate run, and the ways of asking what it validated to."""

    def finished_candidate(
        self, utterance: str = "set height to 2.2", **body: object
    ) -> tuple[dict, dict]:
        body.setdefault("elementId", "portico-base")
        accepted, job = self.run_candidate(utterance, **body)
        self.assertEqual(job["status"], "succeeded", job)
        return accepted, job

    def validation_of(self, candidate_id: str) -> dict:
        response = self.client.get(f"/api/candidates/{candidate_id}/validation")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def candidate_run(self, accepted: dict, job: dict) -> CandidateRun:
        """The same ``CandidateRun`` the route validates, read the same way."""

        state = self.app.state
        return describe(
            bound_project(state),
            state.proposals.get(job["proposalId"]),
            candidate_id=accepted["candidateId"],
            job_id=job["jobId"],
            status=job["status"],
        )

    def validated(self, candidate: CandidateRun):
        """Run the application function over one candidate, events included."""

        state = self.app.state
        return validate_candidate(
            bound_project(state),
            candidate,
            state.proposals.get(candidate.proposal_id),
            events=state.events,
        )


class ValidationReceiptTests(ValidationTestCase):
    def test_a_finished_candidate_carries_the_kernels_receipt(self) -> None:
        accepted, _ = self.finished_candidate()

        validation = self.validation_of(accepted["candidateId"])
        print(
            f"\n[validation] {validation['candidateId']} "
            f"passed={validation['receipt']['passed']} "
            f"advance={validation['advance']} "
            f"blockedBy={validation['blockedBy']} "
            f"validators={validation['validators']} "
            f"effectiveChecks={validation['effectiveChecks']}"
        )

        self.assertEqual(validation["candidateId"], accepted["candidateId"])
        self.assertIs(validation["receipt"]["passed"], True)
        self.assertEqual(validation["receipt"]["findings"], [])
        # The three the studio runs, named in the order it runs them. The
        # compatibility-only ``required-claims`` gate is not among them.
        self.assertEqual(validation["validators"], THREE)
        self.assertNotIn("required-claims", validation["validators"])
        self.assertIs(validation["advance"], True)
        self.assertEqual(validation["blockedBy"], [])

    def test_the_receipt_names_the_submission_it_checked(self) -> None:
        accepted, _ = self.finished_candidate()

        receipt = self.validation_of(accepted["candidateId"])["receipt"]

        self.assertEqual(receipt["submissionId"], accepted["candidateId"])
        self.assertEqual(len(receipt["submissionDigest"]), 64)
        self.assertTrue(receipt["receiptId"].startswith("validation-"))
        # The state that was checked is the project's HEAD, exactly.
        head = self.repository.read_head()
        self.assertEqual(receipt["checkedState"]["version"], head.version)
        self.assertEqual(
            receipt["checkedState"]["stateSha256"], head.state_sha256
        )

    def test_what_could_not_be_checked_is_said_rather_than_greened(
        self,
    ) -> None:
        """A ref-only canonical state proves less, and the wire says which."""

        accepted, _ = self.finished_candidate()

        validation = self.validation_of(accepted["candidateId"])

        self.assertEqual(validation["canonicalFacts"], CANONICAL_FACTS)
        self.assertIn("P110", validation["canonicalFacts"])
        self.assertEqual(validation["effectiveChecks"], ["artifact-present"])
        self.assertEqual(validation["validatorNote"], VALIDATOR_NOTE)
        self.assertIn("P110", validation["validatorNote"])
        # Two of the three validators had nothing to check, and the wire does
        # not let a client read all three as evidence.
        self.assertNotEqual(
            validation["validators"], validation["effectiveChecks"]
        )

    def test_the_three_state_block_is_copied_from_the_candidate(self) -> None:
        accepted, _ = self.finished_candidate()

        candidate = self.client.get(
            f"/api/candidates/{accepted['candidateId']}"
        ).json()
        validation = self.validation_of(accepted["candidateId"])

        self.assertEqual(
            validation["relationChecks"], candidate["relationChecks"]
        )
        self.assertEqual(
            validation["seatExecutionComplete"],
            candidate["seatExecutionComplete"],
        )
        self.assertEqual(
            sorted(validation["relationChecks"]),
            ["fullyChecked", "held", "heldFlag", "unchecked", "violated"],
        )

    def test_the_submission_carries_the_runs_own_programs(self) -> None:
        """The artifacts are the seats' programs, evidenced by their own ids."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        submission = submission_for(
            candidate, self.app.state.proposals.get(candidate.proposal_id)
        )

        self.assertEqual(submission.submission_id, candidate.candidate_id)
        self.assertEqual(submission.workspace_id, candidate.candidate_id)
        self.assertEqual(submission.base, candidate.base)
        self.assertEqual(
            [artifact.artifact_id for artifact in submission.delta.artifacts_add],
            [seat.program_digest for seat in candidate.seat_results],
        )
        for artifact in submission.delta.artifacts_add:
            with self.subTest(artifact=artifact.artifact_id):
                self.assertEqual(artifact.media_type, "application/json")
                self.assertEqual(len(artifact.sha256), 64)
                self.assertTrue(artifact.uri.startswith("project://"))
                self.assertIn(artifact.sha256, artifact.uri)
                # ``ArtifactPresentValidator`` finds nothing missing only
                # because every added artifact is also evidence.
                self.assertIn(artifact.artifact_id, submission.evidence_refs)
        claim = submission.claims[0]
        self.assertEqual(claim.key, "studio.candidate.run_id")
        self.assertEqual(claim.value, candidate.candidate_id)
        self.assertEqual(claim.evidence_refs, (candidate.receipt_ref,))
        self.assertIn(candidate.receipt_ref, submission.evidence_refs)

    def test_the_candidates_receipt_record_is_named_not_null(self) -> None:
        """The evidence the claim stands on is a record the project still has.

        The runner writes its own ``receipt_ref`` into the mapping it returns
        and not into the record it retained, so a candidate read back off disk
        would name nothing. The studio names the retained record itself rather
        than submitting ``None`` as evidence.
        """

        accepted, job = self.finished_candidate()

        candidate = self.candidate_run(accepted, job)

        self.assertIsNotNone(candidate.receipt_ref)
        self.assertIn(
            f"runs/{accepted['candidateId']}/records/runner-run-receipt-",
            candidate.receipt_ref,
        )
        self.assertEqual(
            self.client.get(
                f"/api/candidates/{accepted['candidateId']}"
            ).json()["receiptRef"],
            candidate.receipt_ref,
        )


class AdvanceVerdictTests(ValidationTestCase):
    def test_a_stale_base_is_the_kernels_finding_not_a_studio_fix(self) -> None:
        """The studio submits the base the candidate stood on, and is told."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        drifted = self.validated(
            replace(
                candidate,
                base=ProjectVersionRef(PROJECT_ID, 0, "e" * 64),
            )
        )

        self.assertIs(drifted.receipt.passed, False)
        self.assertEqual(
            [finding.code for finding in drifted.receipt.findings],
            ["state.base_mismatch"],
        )
        self.assertIn("validation.receipt", drifted.blocked_by)
        self.assertIs(drifted.advance, False)
        # The rest of the candidate is untouched: one failing clause is one
        # failing clause, not a blanket refusal.
        self.assertEqual(drifted.blocked_by, ("validation.receipt",))

    def test_unchecked_relations_alone_block_the_advance(self) -> None:
        """Unchecked is never green, even when nothing was violated."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        partial = self.validated(
            replace(
                candidate,
                relation_checks=RelationTotals(2, 0, 1, True, False),
            )
        )

        self.assertIs(partial.receipt.passed, True)
        self.assertIs(partial.advance, False)
        self.assertEqual(partial.blocked_by, ("relations.fully_checked",))

    def test_a_violated_relation_blocks_by_its_own_name(self) -> None:
        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        violated = self.validated(
            replace(
                candidate,
                relation_checks=RelationTotals(1, 1, 0, False, True),
            )
        )

        self.assertIs(violated.advance, False)
        self.assertEqual(violated.blocked_by, ("relations.held",))

    def test_seats_that_did_not_finish_block_by_their_own_name(self) -> None:
        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        incomplete = self.validated(
            replace(candidate, seat_execution_complete=False)
        )

        self.assertIs(incomplete.advance, False)
        self.assertEqual(
            incomplete.blocked_by, ("runner.seat_execution_complete",)
        )

    def test_every_failing_clause_is_named_together(self) -> None:
        """Four clauses, four names, in the order the verdict states them."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        blocked = self.validated(
            replace(
                candidate,
                base=ProjectVersionRef(PROJECT_ID, 0, "e" * 64),
                seat_execution_complete=False,
                relation_checks=RelationTotals(0, 1, 2, False, False),
            )
        )

        self.assertIs(blocked.advance, False)
        self.assertEqual(
            blocked.blocked_by,
            (
                "validation.receipt",
                "runner.seat_execution_complete",
                "relations.held",
                "relations.fully_checked",
            ),
        )

    def test_the_validator_names_are_the_ones_the_kernel_declares(self) -> None:
        """The list on the wire is read off the validators, never retyped."""

        self.assertEqual(list(VALIDATOR_NAMES), THREE)
        self.assertEqual(list(EFFECTIVE_CHECKS), ["artifact-present"])


class ValidationEventTests(ValidationTestCase):
    def test_the_verdict_is_published(self) -> None:
        accepted, _ = self.finished_candidate()

        validation = self.validation_of(accepted["candidateId"])

        published = [
            event
            for event in self.app.state.events.replay()
            if event["type"] == "validation.computed"
        ]
        self.assertEqual(len(published), 1, published)
        event = published[0]
        self.assertEqual(event["candidate_id"], accepted["candidateId"])
        self.assertIs(event["advance"], validation["advance"])
        self.assertEqual(event["blocked_by"], validation["blockedBy"])

    def test_the_verdict_reaches_the_stream(self) -> None:
        accepted, _ = self.finished_candidate()
        self.validation_of(accepted["candidateId"])

        response = self.client.get("/api/events", params={"limit": 50})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("event: validation.computed", response.text)
        self.assertIn('"advance":true', response.text)
        self.assertIn('"blockedBy":[]', response.text)


class ValidationRefusalTests(ValidationTestCase):
    def test_an_unfinished_candidate_is_refused_by_name(self) -> None:
        """Work in flight is a 409, never a receipt nobody could stand on."""

        proposal_id = self.propose(
            "set height to 2.2", elementId="portico-base"
        )["proposalId"]
        release = threading.Event()
        self.addCleanup(release.set)
        held = self.app.state.jobs.submit(
            candidate_id="studio-cand-held",
            proposal_id=proposal_id,
            work=release.wait,
        )

        response = self.client.get(
            "/api/candidates/studio-cand-held/validation"
        )

        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["code"], "CANDIDATE_NOT_FINISHED")
        self.assertIn(held.job_id, body["detail"])
        release.set()

    def test_an_unknown_candidate_is_named_not_guessed(self) -> None:
        response = self.client.get(
            "/api/candidates/studio-cand-nope/validation"
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "CANDIDATE_NOT_FOUND")

    def test_a_failed_run_has_no_receipt_to_read(self) -> None:
        accepted, job = self.run_candidate(
            "set height to -1", elementId="portico-base"
        )
        self.assertEqual(job["status"], "failed", job)

        response = self.client.get(
            f"/api/candidates/{accepted['candidateId']}/validation"
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "CANDIDATE_NOT_FOUND")


class ValidationWritesNothingTests(ValidationTestCase):
    def test_validating_commits_nothing(self) -> None:
        """A receipt is a reading. HEAD, the record and the runs all stand."""

        accepted, _ = self.finished_candidate()
        head = self.repository.read_head()
        record = self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).read_bytes()
        runs = sorted(item.name for item in self.repository.layout.runs.iterdir())
        before = sorted(
            path.name
            for path in (
                self.repository.layout.run(accepted["candidateId"]).root
            ).rglob("*.json")
        )

        self.validation_of(accepted["candidateId"])
        self.validation_of(accepted["candidateId"])

        self.assertEqual(self.repository.read_head(), head)
        self.assertEqual(
            self.repository.layout.resolve_relative(
                RUNNER_RECORD_PATH
            ).read_bytes(),
            record,
        )
        self.assertEqual(
            sorted(item.name for item in self.repository.layout.runs.iterdir()),
            runs,
        )
        self.assertEqual(
            sorted(
                path.name
                for path in (
                    self.repository.layout.run(accepted["candidateId"]).root
                ).rglob("*.json")
            ),
            before,
        )


@unittest.skipUnless(
    os.environ.get(VILLA_INPUTS_ENV),
    f"set {VILLA_INPUTS_ENV} to a directory holding the villa's "
    "state-record.json and seats.json to run this",
)
class VillaValidationTests(unittest.TestCase):
    """The reality check: the villa's own inputs, in a temporary copy."""

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
                StudioSettings(project_dir=self.root / VILLA_PROJECT_ID)
            )
        )
        self.addCleanup(self.client.close)

    def test_the_villa_candidate_validates_and_may_advance(self) -> None:
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

        response = self.client.get(
            f"/api/candidates/{accepted.json()['candidateId']}/validation"
        )
        self.assertEqual(response.status_code, 200, response.text)
        validation = response.json()
        print(
            f"\n[villa validation] {validation['candidateId']}\n"
            f"  receipt={validation['receipt']['receiptId']} "
            f"passed={validation['receipt']['passed']} "
            f"findings={validation['receipt']['findings']}\n"
            f"  validators={validation['validators']} "
            f"effectiveChecks={validation['effectiveChecks']}\n"
            f"  relations={validation['relationChecks']} "
            f"seats={validation['seatExecutionComplete']}\n"
            f"  advance={validation['advance']} "
            f"blockedBy={validation['blockedBy']}\n"
            f"  canonicalFacts={validation['canonicalFacts']}\n"
            f"  validatorNote={validation['validatorNote']}"
        )

        self.assertIs(validation["receipt"]["passed"], True)
        self.assertEqual(validation["validators"], THREE)
        self.assertEqual(validation["effectiveChecks"], ["artifact-present"])
        self.assertIs(validation["advance"], True)
        self.assertEqual(validation["blockedBy"], [])
        self.assertIn("P110", validation["canonicalFacts"])
        self.assertIn("P110", validation["validatorNote"])


if __name__ == "__main__":
    unittest.main()
