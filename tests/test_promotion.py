from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archive.archflow.adapters.fake_voxel import FakeVoxelAdapter
from archive.archflow.commit.committer import CommitRejected, Committer
from archive.archflow.commit.store import InMemoryStateStore
from archive.archflow.evaluation.engine import ClaimCoverageEvaluator, evaluate_submission
from archive.archflow.evaluation.model import ObservationStatus
from archive.archflow.runtime.fake_architect import FakeArchitect
from archive.archflow.runtime.walking_skeleton import initial_state
from archflow.state.model import CanonicalState, Fact
from archflow.state.commitments import Commitment, CommitmentKind, CommitmentStatus, CommitmentStrength, CriterionRef, RevisionPolicy
from archflow.validation.engine import ArtifactPresentValidator, ObligationDischargeValidator, RequiredClaimsValidator, validate_submission
from archive.archflow.workspace.manager import WorkspaceManager
from tests.test_submission import NOW, _human_package


VALIDATORS = (
    ArtifactPresentValidator(),
    RequiredClaimsValidator(),
    ObligationDischargeValidator(),
)


class BrokenEvaluator:
    name = "broken"

    def evaluate(self, state, submission):
        del state, submission
        raise RuntimeError("offline")


def _active_hard_commitment() -> Commitment:
    return Commitment(
        commitment_id="commitment-maintain-egress",
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.ACTIVE,
        authority_id="authority-user",
        authorized_by="authority-user",
        source_event_ref="event://brief/commitment",
        evidence_refs=("evidence://brief/egress",),
        scope_refs=("fact:brief:egress",),
        satisfaction_criterion=CriterionRef(
            criterion_id="criterion-egress",
            provider_id="validator-usability",
            subject_refs=("fact:brief:egress",),
        ),
        revision_policy=RevisionPolicy.OWNER_ONLY,
    )


class PromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.manager = WorkspaceManager(Path(self.temp_dir.name))
        self.initial = initial_state("A test building", run_id="run-promotion")
        self.store = InMemoryStateStore(self.initial)

    def _submission(self, *, omit: frozenset[str] = frozenset()):
        architect = FakeArchitect(FakeVoxelAdapter(), omit_claims=omit)
        workspace = self.manager.fork(self.store.read())
        return architect.propose(self.store.read(), workspace)

    def test_validation_and_evaluation_do_not_mutate_state(self) -> None:
        submission = self._submission()
        before = self.store.read()
        validation = validate_submission(before, submission, VALIDATORS)
        observations = evaluate_submission(
            before,
            submission,
            (ClaimCoverageEvaluator(),),
        )
        self.assertTrue(validation.passed)
        self.assertEqual(self.store.read(), before)
        self.assertEqual(observations[0].status, ObservationStatus.OK)

    def test_committer_advances_exactly_one_version(self) -> None:
        submission = self._submission()
        validation = validate_submission(self.initial, submission, VALIDATORS)
        evaluations = evaluate_submission(
            self.initial, submission, (ClaimCoverageEvaluator(),)
        )
        receipt = Committer(self.store).commit(
            submission, validation, evaluations
        )
        current = self.store.read()
        self.assertEqual(receipt.from_state.version, 0)
        self.assertEqual(receipt.to_state.version, 1)
        self.assertEqual(current.ref.version, 1)
        self.assertFalse(current.open_obligations)
        self.assertEqual(len(current.artifacts), 1)
        self.assertFalse(hasattr(self.store, "write"))

    def test_failed_validation_cannot_commit(self) -> None:
        submission = self._submission(omit=frozenset({"use.requested"}))
        validation = validate_submission(self.initial, submission, VALIDATORS)
        self.assertFalse(validation.passed)
        with self.assertRaises(CommitRejected):
            Committer(self.store).commit(submission, validation, ())
        self.assertEqual(self.store.read(), self.initial)

    def test_stale_submission_leaves_newer_state_unchanged(self) -> None:
        first = self._submission()
        second = self._submission()
        first_validation = validate_submission(self.initial, first, VALIDATORS)
        second_validation = validate_submission(self.initial, second, VALIDATORS)
        Committer(self.store).commit(first, first_validation, ())
        committed = self.store.read()
        with self.assertRaises(CommitRejected):
            Committer(self.store).commit(second, second_validation, ())
        self.assertEqual(self.store.read(), committed)

    def test_soft_evaluator_failure_is_a_readonly_error_observation(self) -> None:
        submission = self._submission()
        before = self.store.read()
        observations = evaluate_submission(before, submission, (BrokenEvaluator(),))
        self.assertEqual(observations[0].status, ObservationStatus.ERROR)
        self.assertEqual(observations[0].metrics, ())
        self.assertEqual(self.store.read(), before)

    def test_production_package_advances_only_through_single_writer(self) -> None:
        _, _, _, package = _human_package()
        initial = CanonicalState(ref=package.submission.base)
        store = InMemoryStateStore(initial)

        receipt = Committer(store).commit_decision_package(
            package,
            now_utc=NOW,
        )

        self.assertEqual(receipt.from_state, package.submission.base)
        self.assertEqual(receipt.to_state.version, 1)
        self.assertEqual(
            receipt.approval_receipt_id,
            package.approval.approval_id,
        )
        self.assertEqual(
            receipt.commitment_monitor_receipt_id,
            package.commitment_monitor.receipt_id,
        )
        self.assertEqual(receipt.decision_package_id, package.package_id)
        self.assertEqual(store.read().artifacts, package.submission.delta.artifacts_add)

    def test_substituted_delta_cannot_reuse_validation_receipt(self) -> None:
        submission = self._submission()
        validation = validate_submission(self.initial, submission, VALIDATORS)
        self.assertTrue(validation.passed)
        doctored = replace(
            submission,
            delta=replace(
                submission.delta,
                facts_add=(
                    Fact(
                        key="smuggled.fact",
                        value="never validated",
                        source_ref="nowhere",
                    ),
                ),
            ),
        )

        with self.assertRaisesRegex(
            CommitRejected,
            "does not bind this submission content",
        ):
            Committer(self.store).commit(doctored, validation, ())

        self.assertEqual(self.store.read(), self.initial)

    def test_receipt_failure_aborts_before_promotion(self) -> None:
        _, _, _, package = _human_package()
        initial = CanonicalState(ref=package.submission.base)
        store = InMemoryStateStore(initial)

        with patch.object(
            Committer,
            "_receipt",
            side_effect=RuntimeError("receipt construction failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "receipt construction failed",
            ):
                Committer(store).commit_decision_package(
                    package,
                    now_utc=NOW,
                )

        self.assertEqual(store.read(), initial)

    def test_unmonitored_canonical_commitment_blocks_promotion(self) -> None:
        _, _, _, package = _human_package()
        commitment = _active_hard_commitment()
        initial = CanonicalState(
            ref=package.submission.base,
            commitments=(commitment,),
        )
        store = InMemoryStateStore(initial)

        with self.assertRaisesRegex(
            CommitRejected,
            "never observed",
        ):
            Committer(store).commit_decision_package(
                package,
                now_utc=NOW,
            )

        self.assertEqual(store.read(), initial)

    def test_lookalike_commitment_content_cannot_cover_canonical(
        self,
    ) -> None:
        commitment = _active_hard_commitment()
        lookalike = replace(
            commitment,
            strength=CommitmentStrength.PREFERENCE,
        )
        _, _, _, package = _human_package(
            monitor_state_commitments=(lookalike,),
        )
        # The monitor itself passes: on the weakened lookalike every
        # missing-evidence finding is merely advisory.
        self.assertTrue(package.commitment_monitor.passed)
        self.assertEqual(
            [item.commitment_id for item in package.commitment_monitor.progress],
            [commitment.commitment_id],
        )

        initial = CanonicalState(
            ref=package.submission.base,
            commitments=(commitment,),
        )
        store = InMemoryStateStore(initial)

        with self.assertRaisesRegex(
            CommitRejected,
            "different commitment content|evidence_blocked",
        ):
            Committer(store).commit_decision_package(
                package,
                now_utc=NOW,
            )

        self.assertEqual(store.read(), initial)

    def test_expired_package_cannot_reach_canonical_state(self) -> None:
        _, _, _, package = _human_package()
        initial = CanonicalState(ref=package.submission.base)
        store = InMemoryStateStore(initial)

        with self.assertRaisesRegex(
            CommitRejected,
            "outside validity",
        ):
            Committer(store).commit_decision_package(
                package,
                now_utc="2026-07-25T10:30:01Z",
            )

        self.assertEqual(store.read(), initial)

    def test_stale_production_package_leaves_newer_state_unchanged(self) -> None:
        _, _, _, package = _human_package()
        initial = CanonicalState(ref=package.submission.base)
        store = InMemoryStateStore(initial)
        first = Committer(store).commit_decision_package(
            package,
            now_utc=NOW,
        )
        committed = store.read()

        with self.assertRaisesRegex(
            CommitRejected,
            "no longer canonical",
        ):
            Committer(store).commit_decision_package(
                package,
                now_utc=NOW,
            )

        self.assertEqual(first.to_state, committed.ref)
        self.assertEqual(store.read(), committed)


if __name__ == "__main__":
    unittest.main()
