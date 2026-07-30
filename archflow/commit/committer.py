"""The only normal API that promotes a Candidate Submission."""

from __future__ import annotations

import hashlib
import json

from archflow.commit.model import CommitReceipt, PromotionDecisionPackage
from archflow.commit.store import InMemoryStateStore
from archflow.evaluation import EvaluationObservation
from archflow.state import CanonicalState, StateRef
from archflow.submission import CandidateSubmission
from archflow.validation import ValidationReceipt


class CommitRejected(RuntimeError):
    """A candidate cannot be promoted."""


def _merge_unique_strings(current: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*current, *added)))


class Committer:
    def __init__(self, store: InMemoryStateStore) -> None:
        self._store = store

    def commit(
        self,
        submission: CandidateSubmission,
        validation: ValidationReceipt,
        evaluations: tuple[EvaluationObservation, ...],
    ) -> CommitReceipt:
        current = self._store.read()
        if current.goal is None:
            raise CommitRejected(
                "production canonical state requires a "
                "PromotionDecisionPackage"
            )
        self._check_decision_package(current, submission, validation, evaluations)
        replacement = self._apply(current, submission, evaluations)
        self._store._compare_and_swap(submission.base, replacement)
        return self._receipt(current, replacement, submission, validation, evaluations)

    def commit_decision_package(
        self,
        package: PromotionDecisionPackage,
        *,
        now_utc: str,
    ) -> CommitReceipt:
        """Promote one complete production package through the same CAS."""

        if not isinstance(package, PromotionDecisionPackage):
            raise TypeError(
                "package must be PromotionDecisionPackage"
            )
        current = self._store.read()
        submission = package.submission
        self._check_decision_package(
            current,
            submission,
            package.hard_validation,
            package.evaluations,
        )
        try:
            from archflow.runtime.player_control import (
                validate_candidate_approval,
            )

            validate_candidate_approval(
                package.assembly,
                package.approval_policy,
                package.approval,
                now_utc=now_utc,
            )
        except (TypeError, ValueError) as exc:
            raise CommitRejected(
                f"candidate approval is not valid at commit: {exc}"
            ) from exc
        if (
            not package.readiness.ready
            or not package.commitment_monitor.completion_boundary
            or not package.commitment_monitor.passed
        ):
            raise CommitRejected(
                "promotion package lacks passing completion-boundary "
                "commitment evidence"
            )
        replacement = self._apply(
            current,
            submission,
            package.evaluations,
        )
        self._store._compare_and_swap(submission.base, replacement)
        return self._receipt(
            current,
            replacement,
            submission,
            package.hard_validation,
            package.evaluations,
            package=package,
        )

    @staticmethod
    def _check_decision_package(
        current: CanonicalState,
        submission: CandidateSubmission,
        validation: ValidationReceipt,
        evaluations: tuple[EvaluationObservation, ...],
    ) -> None:
        if not validation.passed:
            raise CommitRejected("hard validation did not pass")
        if validation.submission_id != submission.submission_id:
            raise CommitRejected("validation belongs to another submission")
        if validation.checked_state != submission.base:
            raise CommitRejected("validation checked a different base state")
        if current.ref != submission.base:
            raise CommitRejected("submission base is no longer canonical")
        for observation in evaluations:
            if observation.submission_id != submission.submission_id:
                raise CommitRejected("evaluation belongs to another submission")
            if observation.checked_state != submission.base:
                raise CommitRejected("evaluation checked a different base state")

    @staticmethod
    def _apply(
        current: CanonicalState,
        submission: CandidateSubmission,
        evaluations: tuple[EvaluationObservation, ...],
    ) -> CanonicalState:
        delta = submission.delta

        facts = {fact.key: fact for fact in current.facts}
        for fact in delta.facts_add:
            previous = facts.get(fact.key)
            if previous is not None and previous != fact:
                raise CommitRejected(f"fact conflict: {fact.key}")
            facts[fact.key] = fact

        commitments = {
            item.commitment_id: item for item in current.commitments
        }
        for commitment in delta.commitments_add:
            previous = commitments.get(commitment.commitment_id)
            if previous is not None and previous != commitment:
                raise CommitRejected(
                    f"commitment conflict: {commitment.commitment_id}"
                )
            commitments[commitment.commitment_id] = commitment

        open_obligations = {
            item.obligation_id: item for item in current.open_obligations
        }
        unknown = set(delta.obligations_discharge) - set(open_obligations)
        if unknown:
            raise CommitRejected(f"unknown obligation discharge: {sorted(unknown)}")
        for obligation_id in delta.obligations_discharge:
            del open_obligations[obligation_id]
        for obligation in delta.obligations_add:
            if obligation.obligation_id in open_obligations:
                raise CommitRejected(
                    f"duplicate open obligation: {obligation.obligation_id}"
                )
            open_obligations[obligation.obligation_id] = obligation

        artifacts = {item.artifact_id: item for item in current.artifacts}
        for artifact in delta.artifacts_add:
            previous = artifacts.get(artifact.artifact_id)
            if previous is not None and previous != artifact:
                raise CommitRejected(f"artifact conflict: {artifact.artifact_id}")
            artifacts[artifact.artifact_id] = artifact

        return CanonicalState(
            ref=StateRef(
                project_id=current.ref.project_id,
                version=current.ref.version + 1,
            ),
            goal=current.goal,
            design_program_ref=current.design_program_ref,
            legacy_program_view=current.legacy_program_view,
            facts=tuple(sorted(facts.values(), key=lambda item: item.key)),
            commitments=tuple(
                sorted(
                    commitments.values(),
                    key=lambda item: item.commitment_id,
                )
            ),
            open_obligations=tuple(
                sorted(
                    open_obligations.values(),
                    key=lambda item: item.obligation_id,
                )
            ),
            artifacts=tuple(
                sorted(artifacts.values(), key=lambda item: item.artifact_id)
            ),
            evaluation_refs=_merge_unique_strings(
                current.evaluation_refs,
                tuple(item.observation_id for item in evaluations),
            ),
        )

    @staticmethod
    def _receipt(
        current: CanonicalState,
        replacement: CanonicalState,
        submission: CandidateSubmission,
        validation: ValidationReceipt,
        evaluations: tuple[EvaluationObservation, ...],
        package: PromotionDecisionPackage | None = None,
    ) -> CommitReceipt:
        payload = [
            current.ref.project_id,
            current.ref.version,
            replacement.ref.version,
            submission.submission_id,
            validation.receipt_id,
            *[item.observation_id for item in evaluations],
            *(
                (
                    package.approval.approval_id,
                    package.commitment_monitor.receipt_id,
                    package.package_id,
                )
                if package is not None
                else ()
            ),
        ]
        digest = hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return CommitReceipt(
            receipt_id=f"commit-{digest[:20]}",
            submission_id=submission.submission_id,
            validation_receipt_id=validation.receipt_id,
            from_state=current.ref,
            to_state=replacement.ref,
            evaluation_ids=tuple(item.observation_id for item in evaluations),
            artifact_ids=tuple(
                item.artifact_id for item in submission.delta.artifacts_add
            ),
            approval_receipt_id=(
                package.approval.approval_id
                if package is not None
                else None
            ),
            commitment_monitor_receipt_id=(
                package.commitment_monitor.receipt_id
                if package is not None
                else None
            ),
            decision_package_id=(
                package.package_id if package is not None else None
            ),
        )
