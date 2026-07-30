from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.adapters import FakeVoxelAdapter
from archflow.commit import CommitRejected, Committer, InMemoryStateStore
from archflow.evaluation import (
    ClaimCoverageEvaluator,
    ObservationStatus,
    evaluate_submission,
)
from archflow.runtime import FakeArchitect, initial_state
from archflow.state import CanonicalState
from archflow.validation import (
    ArtifactPresentValidator,
    ObligationDischargeValidator,
    RequiredClaimsValidator,
    validate_submission,
)
from archflow.workspace import WorkspaceManager
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
