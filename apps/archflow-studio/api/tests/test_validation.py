"""The kernel's validation receipt and the server's review-readiness result.

Nothing here rehearses validation. Every receipt these tests read came out of
``archflow.validation.engine.validate_submission`` over a submission built from
a real candidate run's own records, and the one thing the studio adds — the
review-readiness result — is asserted as what it is: a conjunction of five
named clauses, each of which is shown blocking on its own.

The two negative cases are built the same way as the positive one. A stale base
is a real ``CandidateRun`` whose base is an explicit older
``ProjectVersionRef``, handed to the same application function, so the
``state.base_mismatch`` finding is the kernel's own; unchecked relations are a
real candidate with a synthetic three-state total, so ``review_ready`` flipping
to false is the readiness rule. Neither mocks the kernel, because a mocked
gate proves only that the mock was called.

The villa test at the bottom is the reality check, and it is a copy: it runs
candidates, and a test that ran them in the real project would be writing into
the thing it is measuring.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow_studio_api.application.artifacts import ArtifactRecord, ModelSource
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.candidate import (
    CandidateRun,
    RelationTotals,
    describe,
)
from archflow_studio_api.application.validation import (
    EFFECTIVE_CHECKS,
    EXPORTS_CLAUSE,
    VALIDATOR_NAMES,
    VALIDATOR_NOTE,
    validate_candidate,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from archflow.project.refs import ProjectVersionRef
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import PROMOTION_DECISION, STATE_RECORD
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.operational_state import DesignObligation, ObligationStatus
from archflow.state.state_record import StateRecord

from .support import (
    PROJECT_ID, RUNNER_RECORD_PATH, advance_head, retain_runner_receipt,
    runner_state_digest,
)
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


def _export_artifact(
    *,
    status: str | None,
    available: bool,
    unavailable_reason: str | None = None,
    stage_id: str | None = "cad-rhino-execution",
    file_name: str = "model.3dm",
    receipt_ref: str = "receipt-ref-test",
) -> ArtifactRecord:
    """A real, minimal ``ArtifactRecord`` for exercising the exports clause.

    The clause and its honesty line read only ``stage_id``, ``file_name``,
    ``status``, ``available`` and ``unavailable_reason``; every other field is
    an inert placeholder, filled in rather than mocked because
    ``ArtifactRecord`` is a real dataclass and this is a real instance of it.
    """

    return ArtifactRecord(
        artifact_id="artifact-test",
        run_id="studio-cand-test",
        stage_id=stage_id,
        file_name=file_name,
        relative_path=None,
        path=None,
        sha256=None,
        size_bytes=None,
        object_count=None,
        status=status,
        readback_verified=None,
        available=available,
        unavailable_reason=unavailable_reason,
        unavailable_error=None,
        base_version=None,
        base_state_sha256=None,
        branch_id=None,
        branch_epoch=None,
        program_ref=None,
        program_digest=None,
        design_state_digest=None,
        length_unit=None,
        up_axis=None,
        receipt_ref=receipt_ref,
        format="3dm",
        representation="exact",
    )


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
            bound_project(state).head(),
            candidate,
            binding=bound_project(state),
            events=state.events,
        )


class ValidationReceiptTests(ValidationTestCase):
    def test_a_finished_candidate_carries_the_kernels_receipt(self) -> None:
        accepted, _ = self.finished_candidate()

        validation = self.validation_of(accepted["candidateId"])
        print(
            f"\n[validation] {validation['candidateId']} "
            f"passed={validation['receipt']['passed']} "
            f"reviewReady={validation['reviewReady']} "
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
        self.assertIs(validation["reviewReady"], True)
        self.assertEqual(validation["blockedBy"], [])
        # Every seat's program reached the submission, so there is nothing to
        # confess. Empty is the answer, not a missing field.
        self.assertEqual(validation["honesty"], [])

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

        self.assertIn("Published version 0", validation["canonicalFacts"])
        self.assertIn("does not declare authoritative_record_refs", validation["canonicalFacts"])
        self.assertIn(f"Candidate {accepted['candidateId']}", validation["canonicalFacts"])
        self.assertIn("declares 0 obligation(s)", validation["canonicalFacts"])
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
        """The artifacts are the seats' programs, and the kernel says so.

        ``ArtifactPresentValidator`` answers ``artifact.missing`` for a
        submission carrying no artifact and ``artifact.evidence_missing`` for
        an added artifact that is not also evidence. A receipt with neither
        finding is the kernel stating that the seats' compiled programs
        arrived, each standing as its own evidence — which is the thing worth
        proving, and it is proved by the validator rather than by reading the
        submission back out of the module that built it.
        """

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        # Only the seats that compiled something have a program to submit. A
        # seat that produced nothing is not a dropped artifact, and asserting
        # a digest for it would break the moment one is ``empty``.
        submitted = [
            seat
            for seat in candidate.seat_results
            if seat.program_ref is not None
        ]
        self.assertTrue(submitted, candidate.seat_results)

        validated = self.validated(candidate)

        for seat in submitted:
            with self.subTest(seat=seat.seat_id):
                self.assertEqual(len(seat.program_digest), 64)
                self.assertTrue(seat.program_ref.startswith("project://"))
        self.assertEqual(
            validated.receipt.submission_id, candidate.candidate_id
        )
        self.assertEqual(validated.receipt.findings, ())
        self.assertIs(validated.receipt.passed, True)
        # Nothing was left out of the submission, so nothing is confessed.
        self.assertEqual(validated.honesty, ())

    def test_a_seat_whose_program_cannot_be_named_is_confessed(self) -> None:
        """A dropped artifact is said out loud, and never quietly greened.

        The studio will not invent a digest for a record whose name the P036
        rule does not recognize. What it does instead is submit one artifact
        fewer, say which seat that was, and let the kernel answer — which it
        does, with ``artifact.missing``.
        """

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        seat = candidate.seat_results[0]

        mangled = self.validated(
            replace(
                candidate,
                seat_results=(
                    replace(
                        seat,
                        program_ref=(
                            f"project://{PROJECT_ID}/runs/"
                            f"{candidate.candidate_id}/records/"
                            "seat-geometry-program.json"
                        ),
                    ),
                ),
            )
        )

        self.assertEqual(
            mangled.honesty,
            (
                f"seat {seat.seat_id}: program record name could not be "
                "parsed; its program was not submitted for validation",
            ),
        )
        # The drop is not a pass. With no artifact left the kernel refuses,
        # and the verdict names the clause that refused.
        self.assertIs(mangled.receipt.passed, False)
        self.assertEqual(
            [finding.code for finding in mangled.receipt.findings],
            ["artifact.missing"],
        )
        self.assertEqual(mangled.blocked_by, ("validation.receipt",))

    def test_a_seat_with_no_program_digest_says_that_instead(self) -> None:
        """The confession names the actual cause, not a nearby one."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        seat = candidate.seat_results[0]

        undigested = self.validated(
            replace(
                candidate,
                seat_results=(replace(seat, program_digest=None),),
            )
        )

        self.assertEqual(
            undigested.honesty,
            (
                f"seat {seat.seat_id}: seat carries no program digest; its "
                "program was not submitted for validation",
            ),
        )

    def test_a_seat_that_compiled_nothing_is_not_a_confession(self) -> None:
        """An empty seat named no program; there is nothing to have dropped."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        seat = candidate.seat_results[0]

        empty = self.validated(
            replace(
                candidate,
                seat_results=(
                    replace(seat, program_ref=None, program_digest=None),
                ),
            )
        )

        self.assertEqual(empty.honesty, ())
        self.assertEqual(
            [finding.code for finding in empty.receipt.findings],
            ["artifact.missing"],
        )

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


class ConditionSourceCoverageTests(ValidationTestCase):
    """Synthetic duties exercise source reporting, not real project compliance."""

    def _source_payload(self, obligation_id: str, status=ObligationStatus.OPEN):
        payload = json.loads(
            self.repository.layout.resolve_relative(RUNNER_RECORD_PATH).read_text(
                encoding="utf-8"
            )
        )
        payload["obligations"] = [
            DesignObligation(
                obligation_id=obligation_id,
                statement="Synthetic review duty; no architectural criterion is supplied.",
                source_ref="test:declared-duty",
                status=status,
            ).to_dict()
        ]
        return payload

    def _candidate_with_duty(self):
        payload = self._source_payload("candidate-duty", ObligationStatus.SATISFIED)
        run = self.repository.create_run("condition-source")
        retain_runner_receipt(
            self.repository,
            run,
            record_payload=payload,
            design_state_digest=runner_state_digest(self.repository, run.run_id, payload),
        )
        binding = bound_project(self.app.state)
        binding.settings = replace(binding.settings, reference_run=run.run_id)
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]
        return self.finished_candidate()

    def _publish(self, *, payload=None, references=None):
        """Retain a source and issue a test snapshot through the existing P036 seam."""

        run = self.repository.create_run("published-source")
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
        if payload is not None:
            record = StateRecord.from_dict(payload).bound_to(run)
            ref = self.repository.put_json(
                run=run, destination=destination, record_kind=STATE_RECORD,
                payload=record.to_dict(),
            )
            references = [ref.uri]
        decision = self.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run.run_id),
            record_kind=PROMOTION_DECISION,
            payload={
                "schema": "PromotionDecision@1", "status": "accepted",
                "project_id": PROJECT_ID, "run_id": run.run_id,
                "checked_state": run.base.to_dict(), "candidate_ref": "test:snapshot",
            },
        )
        prepared = self.repository.prepare_transition(
            run=run, expected=run.base, decision_receipt=decision,
            replacement_state={
                "schema": "CanonicalProjectState@1",
                "authoritative_record_refs": references,
                "derived_record_refs": [], "phase": "test",
            },
        )
        self.repository.compare_and_swap(
            expected=prepared.expected, event=prepared.event, replacement=prepared.replacement,
        )

    def test_a_retained_satisfied_duty_is_reported_but_remains_unchecked(self):
        accepted, _ = self._candidate_with_duty()
        before = self.repository.read_head()

        validation = self.validation_of(accepted["candidateId"])

        sources = validation["canonicalFacts"]
        self.assertIn("candidate-duty [satisfied]", sources)
        self.assertIn("source test:declared-duty; validator not declared", sources)
        self.assertIn("Recorded obligation statuses are declarations", sources)
        self.assertIn("project conditions remain unchecked", sources)
        self.assertIn("No source-backed authorized commitment", validation["validatorNote"])
        self.assertEqual(validation["effectiveChecks"], ["artifact-present"])
        self.assertTrue(validation["receipt"]["passed"])
        self.assertEqual(validation["receipt"]["findings"], [])
        self.assertEqual(self.repository.read_head(), before)

    def test_published_and_candidate_duties_keep_their_own_sources(self):
        accepted, _ = self._candidate_with_duty()
        self._publish(payload=self._source_payload("published-duty"))

        validation = self.validation_of(accepted["candidateId"])

        published, candidate = validation["canonicalFacts"].split("Candidate ", 1)
        self.assertIn("Published version 1", published)
        self.assertIn("published-duty [open]", published)
        self.assertIn("runs/published-source/records/state-record-", published)
        self.assertNotIn("candidate-duty", published)
        self.assertIn("candidate-duty [satisfied]", candidate)
        self.assertIn(f"runs/{accepted['candidateId']}/records/state-record-", candidate)
        self.assertIn("based on version 0", candidate)
        self.assertNotIn("published-duty", candidate)
        self.assertEqual(validation["effectiveChecks"], ["artifact-present"])
        self.assertEqual(
            [item["code"] for item in validation["receipt"]["findings"]],
            ["state.base_mismatch"],
        )

    def test_empty_published_references_do_not_mean_conditions_passed(self):
        accepted, _ = self.finished_candidate()
        self._publish(references=[])

        validation = self.validation_of(accepted["candidateId"])

        self.assertIn("No authoritative record references are published", validation["canonicalFacts"])
        self.assertIn("remain unchecked", validation["canonicalFacts"])
        self.assertEqual(validation["effectiveChecks"], ["artifact-present"])

    def test_unreadable_published_source_is_named_without_claiming_coverage(self):
        accepted, _ = self.finished_candidate()
        missing = f"project://{PROJECT_ID}/runs/missing/records/state-record-{'a' * 64}.json"
        self._publish(references=[missing])

        validation = self.validation_of(accepted["candidateId"])

        self.assertIn(f"Published source {missing} could not be verified", validation["canonicalFacts"])
        self.assertIn("remain unchecked", validation["canonicalFacts"])
        self.assertEqual(validation["effectiveChecks"], ["artifact-present"])

    def test_another_runs_receipt_cannot_describe_the_candidate_duties(self):
        accepted, job = self._candidate_with_duty()
        candidate = self.candidate_run(accepted, job)
        binding = bound_project(self.app.state)
        other_ref, _ = binding.newest_runner_receipt("condition-source")

        validation = self.validated(replace(candidate, receipt_ref=other_ref.uri))

        self.assertIn("could not be verified", validation.canonical_facts)
        self.assertIn("different project or run", validation.canonical_facts)
        self.assertNotIn("candidate-duty [satisfied]", validation.canonical_facts)

    def test_a_head_move_during_source_read_is_not_cached_under_the_old_head(self):
        accepted, _ = self.finished_candidate()
        binding = bound_project(self.app.state)
        load = binding.repository.load_current_state

        def read_then_move():
            value = load()
            advance_head(self.repository)
            return value

        with patch.object(binding.repository, "load_current_state", side_effect=read_then_move):
            response = self.client.get(f"/api/candidates/{accepted['candidateId']}/validation")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "VALIDATION_HEAD_CHANGED")
        self.assertEqual(self.app.state.validations.receipt_ids(accepted["candidateId"]), ())
        validation = self.validation_of(accepted["candidateId"])
        self.assertIn("Published version 1", validation["canonicalFacts"])


class ReviewReadinessTests(ValidationTestCase):
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
        self.assertIs(drifted.review_ready, False)
        # The rest of the candidate is untouched: one failing clause is one
        # failing clause, not a blanket refusal.
        self.assertEqual(drifted.blocked_by, ("validation.receipt",))

    def test_unchecked_relations_alone_block_review_readiness(self) -> None:
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
        self.assertIs(partial.review_ready, False)
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

        self.assertIs(violated.review_ready, False)
        self.assertEqual(violated.blocked_by, ("relations.held",))

    def test_seats_that_did_not_finish_block_by_their_own_name(self) -> None:
        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        incomplete = self.validated(
            replace(candidate, seat_execution_complete=False)
        )

        self.assertIs(incomplete.review_ready, False)
        self.assertEqual(
            incomplete.blocked_by, ("runner.seat_execution_complete",)
        )

    def test_a_failed_export_blocks_review_readiness_and_is_confessed(self) -> None:
        """A candidate whose requested export failed is not review-ready.

        Its artifacts already say so, and review readiness must agree.
        """

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        failed = self.validated(
            replace(
                candidate,
                artifacts=(
                    _export_artifact(
                        status="failed",
                        available=False,
                        unavailable_reason="no inspection digest",
                        stage_id="cad-rhino-execution",
                        file_name="model.3dm",
                    ),
                ),
            )
        )

        self.assertIs(failed.review_ready, False)
        self.assertIn(EXPORTS_CLAUSE, failed.blocked_by)
        self.assertIn(
            "export of cad-rhino-execution (model.3dm) is not available: "
            "status failed, reason no inspection digest",
            failed.honesty,
        )

    def test_no_export_requested_satisfies_the_clause_by_construction(
        self,
    ) -> None:
        """An unexported candidate — the default — is not asked about exports."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        self.assertEqual(candidate.artifacts, ())
        # Vacuous by what the run's own receipt says, not by an empty list:
        # no seat row carries a ``cad`` block, so no seat attempted one.
        self.assertEqual(
            [seat.cad for seat in candidate.seat_results],
            [None for _ in candidate.seat_results],
        )

        empty = self.validated(replace(candidate, artifacts=()))

        self.assertNotIn(EXPORTS_CLAUSE, empty.blocked_by)
        self.assertEqual(empty.honesty, ())

    def test_registered_complete_model_is_ready_only_for_its_exact_candidate(self) -> None:
        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        source = ModelSource(candidate.candidate_id, candidate.state_digest, "a" * 64)
        artifact = replace(_export_artifact(status="registered", available=True),
                           representation="composed", sha256=source.asset_sha256,
                           run_id=candidate.candidate_id, design_state_digest=candidate.state_digest,
                           model_source=source)
        ready = self.validated(replace(candidate, artifacts=(artifact,)))
        self.assertTrue(ready.review_ready)
        self.assertEqual(ready.honesty, ())
        for changed in (replace(source, run_id="another-run"), replace(source, state_digest="b" * 64),
                        replace(source, asset_sha256="c" * 64), None):
            with self.subTest(source=changed):
                refused = self.validated(replace(candidate, artifacts=(replace(artifact, model_source=changed),)))
                self.assertEqual(refused.blocked_by, (EXPORTS_CLAUSE,))
                self.assertEqual(len(refused.honesty), 1)

    def test_a_seat_that_exported_with_no_artifact_record_blocks(self) -> None:
        """The receipt says an export happened; nothing says it can be had.

        This is the case an empty ``artifacts`` tuple could not tell from "no
        export was asked for": the seat row carries a ``cad`` block, so the
        run did export, and the record that would let anyone open the result
        never reached this run's record area. That candidate is not ready for
        review.
        """

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        seat = candidate.seat_results[0]

        exported = self.validated(
            replace(
                candidate,
                seat_results=(
                    replace(
                        seat,
                        cad={
                            "status": "succeeded",
                            "execution_ref": "project://demo-project/runs/x/"
                            "records/seat-rhino-execution-"
                            f"{'f' * 64}.json",
                        },
                    ),
                ),
                artifacts=(),
            )
        )

        self.assertIs(exported.review_ready, False)
        self.assertIn(EXPORTS_CLAUSE, exported.blocked_by)
        self.assertEqual(
            exported.honesty,
            (
                f"export of {seat.seat_id} was attempted (succeeded) but no "
                "artifact record is available for it",
            ),
        )

    def test_a_seat_whose_export_has_its_artifact_does_not_block(self) -> None:
        """Matched by the very ref the runner wrote into the seat row."""

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        seat = candidate.seat_results[0]
        execution_ref = (
            f"project://demo-project/runs/{candidate.candidate_id}/records/"
            f"seat-rhino-execution-{'f' * 64}.json"
        )

        exported = self.validated(
            replace(
                candidate,
                seat_results=(
                    replace(
                        seat,
                        cad={
                            "status": "succeeded",
                            "execution_ref": execution_ref,
                        },
                    ),
                ),
                artifacts=(
                    _export_artifact(
                        status="succeeded",
                        available=True,
                        receipt_ref=execution_ref,
                    ),
                ),
            )
        )

        self.assertNotIn(EXPORTS_CLAUSE, exported.blocked_by)
        self.assertEqual(exported.honesty, ())

    def test_a_seat_whose_export_failed_blocks_and_says_its_status(
        self,
    ) -> None:
        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)
        seat = candidate.seat_results[0]

        exported = self.validated(
            replace(
                candidate,
                seat_results=(replace(seat, cad={"status": "failed"}),),
                artifacts=(),
            )
        )

        self.assertIn(EXPORTS_CLAUSE, exported.blocked_by)
        self.assertEqual(
            exported.honesty,
            (
                f"export of {seat.seat_id} was attempted (failed) but no "
                "artifact record is available for it",
            ),
        )

    def test_a_succeeded_export_does_not_block_review_readiness(self) -> None:
        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        succeeded = self.validated(
            replace(
                candidate,
                artifacts=(
                    _export_artifact(status="succeeded", available=True),
                ),
            )
        )

        self.assertNotIn(EXPORTS_CLAUSE, succeeded.blocked_by)
        self.assertEqual(succeeded.honesty, ())

    def test_two_failed_exports_are_each_confessed_once(self) -> None:
        """Every failing artifact gets its own line; the clause names once.

        The honesty lines are one per artifact, not one per clause — two
        failed exports must not collapse into a single confession, and the
        clause that blocks review readiness must not be repeated per failure.
        """

        accepted, job = self.finished_candidate()
        candidate = self.candidate_run(accepted, job)

        failed = self.validated(
            replace(
                candidate,
                artifacts=(
                    _export_artifact(
                        status="failed",
                        available=False,
                        unavailable_reason="no inspection digest",
                        stage_id="cad-rhino-execution",
                        file_name="model.3dm",
                    ),
                    _export_artifact(
                        status="failed",
                        available=False,
                        unavailable_reason="file missing",
                        stage_id="cad-rhino-review",
                        file_name="review.3dm",
                    ),
                ),
            )
        )

        self.assertEqual(
            failed.honesty,
            (
                "export of cad-rhino-execution (model.3dm) is not "
                "available: status failed, reason no inspection digest",
                "export of cad-rhino-review (review.3dm) is not available: "
                "status failed, reason file missing",
            ),
        )
        self.assertEqual(failed.blocked_by.count(EXPORTS_CLAUSE), 1)

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

        self.assertIs(blocked.review_ready, False)
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
    def test_review_readiness_is_published(self) -> None:
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
        self.assertIs(event["review_ready"], validation["reviewReady"])
        self.assertEqual(event["blocked_by"], validation["blockedBy"])

    def test_review_readiness_reaches_the_stream(self) -> None:
        accepted, _ = self.finished_candidate()
        self.validation_of(accepted["candidateId"])

        response = self.client.get("/api/events", params={"limit": 50})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("event: validation.computed", response.text)
        self.assertIn('"reviewReady":true', response.text)
        self.assertIn('"blockedBy":[]', response.text)

    def test_one_validation_is_computed_and_published_per_candidate(
        self,
    ) -> None:
        """Reading a verdict twice is one verdict, not two events.

        A candidate's records do not change after the run, so the second read
        is the same answer as the first. Publishing it again would let a
        client's polling look like the server deciding repeatedly.
        """

        accepted, _ = self.finished_candidate()

        first = self.validation_of(accepted["candidateId"])
        second = self.validation_of(accepted["candidateId"])

        self.assertEqual(first, second)
        published = [
            event
            for event in self.app.state.events.replay()
            if event["type"] == "validation.computed"
        ]
        self.assertEqual(len(published), 1, published)
        stream = self.client.get("/api/events", params={"limit": 50}).text
        self.assertEqual(stream.count("event: validation.computed"), 1)

    def test_a_moved_head_is_a_new_verdict_and_a_new_event(self) -> None:
        """A verdict names the state it checked, and cannot outlive it.

        The memo exists so polling does not look like deciding. It must not
        become a way for stale ``reviewReady: true`` to survive the version it
        was true about: once HEAD moves, the candidate's base is no longer
        current, and the kernel says so.
        """

        accepted, _ = self.finished_candidate()
        first = self.validation_of(accepted["candidateId"])
        self.assertIs(first["reviewReady"], True)
        before = self.repository.read_head()

        # Promotion through P036's own path; the API never does this.
        promoted = advance_head(self.repository)
        self.assertNotEqual(promoted, before)

        second = self.validation_of(accepted["candidateId"])

        self.assertEqual(
            second["receipt"]["checkedState"]["version"], promoted.version
        )
        self.assertEqual(
            second["receipt"]["checkedState"]["stateSha256"],
            promoted.state_sha256,
        )
        self.assertIs(second["receipt"]["passed"], False)
        self.assertEqual(
            [
                finding["code"]
                for finding in second["receipt"]["findings"]
            ],
            ["state.base_mismatch"],
        )
        self.assertEqual(second["blockedBy"], ["validation.receipt"])
        # A new decision about a new state is published, not swallowed.
        published = [
            event
            for event in self.app.state.events.replay()
            if event["type"] == "validation.computed"
        ]
        self.assertEqual(
            [event["review_ready"] for event in published], [True, False]
        )

    def test_two_candidates_are_two_verdicts(self) -> None:
        """The memo is per candidate; it must not answer for another run."""

        first, _ = self.finished_candidate()
        second, _ = self.finished_candidate("set height to 3.3")

        first_validation = self.validation_of(first["candidateId"])
        second_validation = self.validation_of(second["candidateId"])

        self.assertNotEqual(
            first_validation["candidateId"], second_validation["candidateId"]
        )
        self.assertNotEqual(
            first_validation["receipt"]["submissionDigest"],
            second_validation["receipt"]["submissionDigest"],
        )
        published = [
            event
            for event in self.app.state.events.replay()
            if event["type"] == "validation.computed"
        ]
        self.assertEqual(
            [event["candidate_id"] for event in published],
            [first["candidateId"], second["candidateId"]],
        )


class ValidationRefusalTests(ValidationTestCase):
    def test_a_completed_candidate_validates_from_p036_after_restart(self) -> None:
        accepted, job = self.finished_candidate()
        self.assertEqual(job["status"], "succeeded", job)
        before = self.validation_of(accepted["candidateId"])
        restarted = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(restarted.close)

        # The new process has neither the job nor its proposal.  The exact
        # retained Studio harness receipt is still sufficient to validate it.
        self.assertEqual(
            restarted.get(f"/api/jobs/{accepted['jobId']}").status_code,
            404,
        )
        response = restarted.get(
            f"/api/candidates/{accepted['candidateId']}/validation"
        )

        self.assertEqual(response.status_code, 200, response.text)
        after = response.json()
        self.assertEqual(after["candidateId"], accepted["candidateId"])
        self.assertEqual(
            after["receipt"]["submissionDigest"],
            before["receipt"]["submissionDigest"],
        )

    def test_a_finished_non_proposal_job_validates_its_retained_run(self) -> None:
        accepted, _ = self.finished_candidate()
        restarted = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(restarted.close)
        state = restarted.app.state
        job = state.jobs.submit(
            candidate_id=accepted["candidateId"],
            proposal_id="program-sheet:fixture",
            work=lambda: None,
        )
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            if state.jobs.get(job.job_id).status in TERMINAL:
                break
            time.sleep(0.01)
        self.assertEqual(state.jobs.get(job.job_id).status, "succeeded")

        response = restarted.get(
            f"/api/candidates/{accepted['candidateId']}/validation"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidateId"], accepted["candidateId"])

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
                StudioSettings(cad_export="off", project_dir=self.root / VILLA_PROJECT_ID)
            )
        )
        self.addCleanup(self.client.close)

    def test_the_villa_candidate_becomes_ready_for_review(self) -> None:
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
            f"  reviewReady={validation['reviewReady']} "
            f"blockedBy={validation['blockedBy']} "
            f"honesty={validation['honesty']}\n"
            f"  canonicalFacts={validation['canonicalFacts']}\n"
            f"  validatorNote={validation['validatorNote']}"
        )

        self.assertIs(validation["receipt"]["passed"], True)
        self.assertEqual(validation["validators"], THREE)
        self.assertEqual(validation["effectiveChecks"], ["artifact-present"])
        self.assertIs(validation["reviewReady"], True)
        self.assertEqual(validation["blockedBy"], [])
        # Both of the villa's seats compiled a program the studio could name.
        self.assertEqual(validation["honesty"], [])
        self.assertIn("P110", validation["canonicalFacts"])
        self.assertIn("P110", validation["validatorNote"])


if __name__ == "__main__":
    unittest.main()
