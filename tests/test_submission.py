from __future__ import annotations

import unittest

from archflow.commit import (
    PromotionPackageError,
    build_promotion_decision_package,
)
from archflow.interaction import CandidateApprovalMode
from archflow.project import BranchRef, RunRef
from archflow.runtime.candidate_assembly import bind_mcp_execution
from archflow.runtime.player_control import (
    issue_disposable_automation_approval,
    issue_human_candidate_approval,
)
from archflow.state import ArtifactRef, OperationalMarkovState
from archflow.validation.commitments import monitor_commitments
from archflow.validation.model import Finding, ValidationReceipt
from tests.test_candidate_assembly import _assembly
from tests.test_player_control import (
    ISSUED,
    VALID,
    _approval_policy,
    _build_policy,
    _identity_receipt,
    _revised,
    _with_policies,
)


NOW = "2026-07-25T10:20:00Z"


def _execution(assembly):
    preview = ArtifactRef(
        artifact_id="preview-promotion",
        uri="project://portfolio-project/runs/run-001/candidates/preview",
        media_type="application/json",
        sha256="d" * 64,
    )
    executed = ArtifactRef(
        artifact_id="executed-promotion",
        uri="project://portfolio-project/runs/run-001/candidates/executed",
        media_type="application/json",
        sha256="e" * 64,
    )
    return bind_mcp_execution(
        assembly,
        preview_artifact=preview,
        executed_artifact=executed,
        preview_plan_digest=assembly.plan.plan_digest,
        execute_plan_digest=assembly.plan.plan_digest,
    )


def _monitor(
    assembly,
    *,
    submission=None,
    passed: bool = True,
    state_commitments=(),
):
    reviewed = assembly.submission if submission is None else submission
    state = OperationalMarkovState(
        branch=BranchRef(
            run=RunRef(
                project_id=assembly.plan.project_id,
                run_id=assembly.plan.run_id,
                base=assembly.plan.base,
            ),
            branch_id="promotion-review",
            epoch=0,
        ),
        compiler_version="promotion-test",
        phase="candidate",
        commitments=state_commitments,
    )
    if not passed:
        raise AssertionError(
            "failing commitment fixtures use dedicated monitor tests"
        )
    return monitor_commitments(
        state,
        candidate_id=reviewed.submission_id,
        observations=(),
        completion_boundary=True,
    )


def _hard_validation(
    assembly,
    *,
    submission=None,
    passed: bool = True,
):
    reviewed = assembly.submission if submission is None else submission
    findings = (
        ()
        if passed
        else (
            Finding(
                code="usable.failed",
                message="A hard usability relationship failed.",
            ),
        )
    )
    return ValidationReceipt(
        receipt_id=(
            "hard-validation-pass"
            if passed
            else "hard-validation-fail"
        ),
        submission_id=reviewed.submission_id,
        submission_digest=reviewed.content_digest(),
        checked_state=assembly.plan.base,
        passed=passed,
        findings=findings,
    )


def _human_package(*, hard_passed: bool = True, monitor_state_commitments=()):
    policy = _approval_policy()
    assembly = _with_policies(policy)
    approval = issue_human_candidate_approval(
        assembly,
        policy,
        identity_receipt=_identity_receipt(assembly),
        authority_id="authority-user",
        approval_event_ref="event://authority/promotion-approval",
        issued_at_utc=ISSUED,
        valid_until_utc=VALID,
    )
    execution = _execution(assembly)
    package = build_promotion_decision_package(
        assembly=assembly,
        execution=execution,
        approval_policy=policy,
        approval=approval,
        hard_validation=_hard_validation(
            assembly,
            submission=execution.submission,
            passed=hard_passed,
        ),
        commitment_monitor=_monitor(
            assembly,
            submission=execution.submission,
            state_commitments=monitor_state_commitments,
        ),
        now_utc=NOW,
    )
    return assembly, policy, approval, package


class SubmissionDecisionPackageTests(unittest.TestCase):
    def test_required_human_approval_builds_exact_nonwriting_package(
        self,
    ) -> None:
        assembly, _, approval, package = _human_package()
        payload = package.to_dict()

        self.assertEqual(
            package.readiness.source_submission_id,
            assembly.submission.submission_id,
        )
        self.assertEqual(
            package.readiness.submission_id,
            package.execution.submission.submission_id,
        )
        self.assertNotEqual(
            package.readiness.source_submission_id,
            package.readiness.submission_id,
        )
        self.assertEqual(payload["plan_digest"], assembly.plan.plan_digest)
        self.assertEqual(
            payload["approval_receipt"]["approval_id"],
            approval.approval_id,
        )
        self.assertTrue(package.readiness.ready)
        self.assertFalse(payload["hard_gate_waiver_authority"])
        self.assertFalse(payload["commitment_waiver_authority"])
        self.assertFalse(payload["aesthetic_winner_authority"])
        self.assertFalse(payload["canonical_write_authority"])

    def test_missing_required_approval_fails_before_commit(self) -> None:
        policy = _approval_policy()
        assembly = _with_policies(policy)

        with self.assertRaisesRegex(
            PromotionPackageError,
            "approval receipt is missing",
        ):
            execution = _execution(assembly)
            build_promotion_decision_package(
                assembly=assembly,
                execution=execution,
                approval_policy=policy,
                approval=None,
                hard_validation=_hard_validation(
                    assembly,
                    submission=execution.submission,
                ),
                commitment_monitor=_monitor(
                    assembly,
                    submission=execution.submission,
                ),
                now_utc=NOW,
            )

    def test_revision_and_plan_change_invalidate_old_approval(self) -> None:
        assembly, policy, approval, _ = _human_package()
        revised = _revised(assembly)
        execution = _execution(revised)

        with self.assertRaisesRegex(
            PromotionPackageError,
            "invalidated approval",
        ):
            build_promotion_decision_package(
                assembly=revised,
                execution=execution,
                approval_policy=policy,
                approval=approval,
                hard_validation=_hard_validation(
                    revised,
                    submission=execution.submission,
                ),
                commitment_monitor=_monitor(
                    revised,
                    submission=execution.submission,
                ),
                now_utc=NOW,
            )

    def test_approval_cannot_waive_hard_failure(self) -> None:
        policy = _approval_policy()
        assembly = _with_policies(policy)
        approval = issue_human_candidate_approval(
            assembly,
            policy,
            identity_receipt=_identity_receipt(assembly),
            authority_id="authority-user",
            approval_event_ref="event://authority/promotion-approval",
            issued_at_utc=ISSUED,
            valid_until_utc=VALID,
        )

        with self.assertRaisesRegex(
            PromotionPackageError,
            "not ready",
        ):
            execution = _execution(assembly)
            build_promotion_decision_package(
                assembly=assembly,
                execution=execution,
                approval_policy=policy,
                approval=approval,
                hard_validation=_hard_validation(
                    assembly,
                    submission=execution.submission,
                    passed=False,
                ),
                commitment_monitor=_monitor(
                    assembly,
                    submission=execution.submission,
                ),
                now_utc=NOW,
            )

    def test_preauthorized_disposable_policy_does_not_force_human_pause(
        self,
    ) -> None:
        base = _assembly()
        build_policy = _build_policy(base)
        policy = _approval_policy(
            mode=CandidateApprovalMode.PREAUTHORIZED_DISPOSABLE
        )
        assembly = _with_policies(
            policy,
            build_policy=build_policy,
        )
        approval = issue_disposable_automation_approval(
            assembly,
            policy,
            build_policy,
            build_policy_ref=next(
                item.policy_ref
                for item in assembly.policies
                if item.kind.value == "build"
            ),
            authority_id="authority-user",
            issued_at_utc=ISSUED,
            valid_until_utc=VALID,
        )
        execution = _execution(assembly)
        package = build_promotion_decision_package(
            assembly=assembly,
            execution=execution,
            approval_policy=policy,
            approval=approval,
            hard_validation=_hard_validation(
                assembly,
                submission=execution.submission,
            ),
            commitment_monitor=_monitor(
                assembly,
                submission=execution.submission,
            ),
            now_utc=NOW,
        )

        self.assertTrue(package.readiness.ready)
        self.assertEqual(
            package.approval.source.value,
            "preauthorized_policy",
        )
        self.assertIsNone(
            package.approval.authority_identity_receipt_ref
        )


if __name__ == "__main__":
    unittest.main()
