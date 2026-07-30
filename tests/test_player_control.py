from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.interaction import (
    CandidateApprovalMode,
    CandidateApprovalPolicy,
    CandidateApprovalReceipt,
    ClarificationDisposition,
    CommitmentClarificationAction,
    PlayerAuthorityError,
)
from archflow.project import BranchRef, RunRef
from archflow.runtime.candidate_assembly import (
    CandidateAssembly,
    CandidateExecutablePlan,
    CandidatePolicyBinding,
    CandidatePolicyKind,
)
from archflow.runtime.clarification import (
    ClarificationUnauthorizedError,
    create_clarification_request,
    issue_authority_decision,
)
from archflow.runtime.player_control import (
    CandidateControlStatus,
    CandidateImpactWarning,
    CandidateRevisionKind,
    MaterialImpact,
    PlayerControlError,
    VoxelBounds,
    WorldTarget,
    apply_player_clarification,
    assess_candidate_promotion_readiness,
    cancel_candidate_control,
    create_candidate_preview,
    create_revision_proposal,
    inspect_candidate_program,
    issue_disposable_automation_approval,
    issue_human_candidate_approval,
    open_candidate_control,
    pause_candidate_control,
    record_candidate_preference,
    validate_candidate_approval,
)
from archflow.state import (
    BuildPolicy,
    BuildStagingMode,
    DesignObligation,
    OperationalMarkovState,
    PolicyProvenance,
    ResourcePolicyMode,
)
from archflow.validation.commitments import monitor_commitments
from archflow.validation.model import Finding, ValidationReceipt
from tests.test_candidate_assembly import (
    APPROVAL_POLICY,
    BUILD_POLICY,
    EVIDENCE,
    _assembly,
)
from tests.test_clarification_authority import (
    _commitment,
    _receipt,
    _request,
    _state,
)


CREATED = "2026-07-25T10:00:00Z"
ISSUED = "2026-07-25T10:10:00Z"
VALID = "2026-07-25T10:30:00Z"
EXPIRES = "2026-07-25T11:00:00Z"


def _approval_policy(
    *,
    mode: CandidateApprovalMode = CandidateApprovalMode.HUMAN_REQUIRED,
) -> CandidateApprovalPolicy:
    return CandidateApprovalPolicy(
        policy_ref=APPROVAL_POLICY,
        authority_ids=("authority-user",),
        mode=mode,
        max_validity_seconds=3_600,
        allows_disposable_automation=(
            mode is CandidateApprovalMode.PREAUTHORIZED_DISPOSABLE
        ),
        authorization_event_ref="event://authority/approval-policy",
        evidence_refs=(EVIDENCE,),
    )


def _with_policies(
    policy: CandidateApprovalPolicy,
    *,
    build_policy: BuildPolicy | None = None,
) -> CandidateAssembly:
    assembly = _assembly()
    build_binding = next(
        item
        for item in assembly.policies
        if item.kind is CandidatePolicyKind.BUILD
    )
    if build_policy is not None:
        build_binding = CandidatePolicyBinding(
            kind=CandidatePolicyKind.BUILD,
            policy_ref=BUILD_POLICY,
            policy_digest=build_policy.policy_digest,
            evidence_refs=(EVIDENCE,),
        )
    approval_binding = CandidatePolicyBinding(
        kind=CandidatePolicyKind.APPROVAL,
        policy_ref=policy.policy_ref,
        policy_digest=policy.policy_digest,
        evidence_refs=(EVIDENCE,),
    )
    return replace(
        assembly,
        policies=tuple(
            sorted(
                (build_binding, approval_binding),
                key=lambda item: item.kind.value,
            )
        ),
    )


def _identity_receipt(assembly: CandidateAssembly):
    branch = BranchRef(
        run=RunRef(
            project_id=assembly.plan.project_id,
            run_id=assembly.plan.run_id,
            base=assembly.plan.base,
        ),
        branch_id="authority-context",
        epoch=0,
    )
    obligation = DesignObligation(
        obligation_id="confirm-authority-context",
        statement="Confirm the named candidate-stage authority.",
        source_ref=EVIDENCE,
        subject_refs=("fact:brief:authority-context",),
    )
    state = OperationalMarkovState(
        branch=branch,
        compiler_version="player-control-test",
        phase="candidate",
        obligations=(obligation,),
        evidence_refs=(EVIDENCE,),
    )
    request = create_clarification_request(
        state,
        obligation_id=obligation.obligation_id,
        requesting_agent_id="agent-primary",
        authority_ids=("authority-user",),
        question="Who holds the candidate-stage decision authority?",
        blocked_reason="A named identity receipt is required.",
        created_at_utc=CREATED,
        expires_at_utc=EXPIRES,
    )
    return issue_authority_decision(
        request,
        authority_id="authority-user",
        disposition=ClarificationDisposition.UNRESOLVED,
        authority_event_ref="event://authority/identity",
        issued_at_utc="2026-07-25T10:05:00Z",
        valid_until_utc=EXPIRES,
    )


def _build_policy(assembly: CandidateAssembly) -> BuildPolicy:
    source = "project://portfolio-project/input/build-policy.json"
    provenance = PolicyProvenance(
        authority_id="authority-user",
        source_refs=(source,),
        assumption_refs=(),
        compiler_id="player-control-test",
        base_state_sha256=assembly.plan.base.require_digest(),
    )
    return BuildPolicy(
        project_id=assembly.plan.project_id,
        run_id=assembly.plan.run_id,
        base=assembly.plan.base,
        brief_digest="1" * 64,
        program_digest="2" * 64,
        site_context_digest="3" * 64,
        compiler_id="player-control-test",
        compiler_version="1",
        resource_mode=ResourcePolicyMode.CREATIVE,
        staging_mode=BuildStagingMode.SINGLE_PASS,
        disposable_sandbox=True,
        unbounded_resources=True,
        policy_provenance=provenance,
        assumptions=(),
        availability=(),
        demands=(),
        protected_rules=(),
        budget_limits=(),
        staging_assumptions=(),
        constraints=(),
        obligations=(),
        evidence_refs=(source,),
    )


def _revised(assembly: CandidateAssembly) -> CandidateAssembly:
    payload = assembly.plan.payload
    payload["operations"][0]["origin"][0] = 1
    plan = CandidateExecutablePlan.create(
        plan_id="candidate-plan-revised",
        projection=assembly.projection,
        payload=payload,
        bindings=assembly.plan.bindings,
    )
    submission = replace(
        assembly.submission,
        submission_id="candidate-revised",
        workspace_id="candidate-workspace-revised",
    )
    return replace(assembly, plan=plan, submission=submission)


def _preview(assembly: CandidateAssembly):
    return create_candidate_preview(
        assembly,
        world=WorldTarget(
            server_sha256="9" * 64,
            world_id="disposable-world",
            dimension_id="minecraft:overworld",
        ),
        bounds=VoxelBounds((0, 0, 0), (8, 6, 8)),
        additions=120,
        removals=4,
        collisions=(
            CandidateImpactWarning(
                subject_ref="world:collision/001",
                code="collision.detected",
                message="The candidate overlaps an observed object.",
                evidence_refs=(EVIDENCE,),
            ),
        ),
        materials=(
            MaterialImpact(
                resource_ref="resource:project-surface",
                required=120,
                available=80,
                unit="blocks",
                evidence_refs=(EVIDENCE,),
            ),
        ),
        protected_objects=(
            CandidateImpactWarning(
                subject_ref="world:protected/001",
                code="protected.replacement",
                message="A protected object would be replaced.",
                evidence_refs=(EVIDENCE,),
            ),
        ),
        unresolved_obligation_refs=("obligation:project-check",),
        evidence_refs=(EVIDENCE,),
    )


class PlayerControlTests(unittest.TestCase):
    def test_preview_reports_impact_before_any_world_write(self) -> None:
        assembly = _assembly()
        preview = _preview(assembly)
        inspection = inspect_candidate_program(assembly)
        payload = preview.to_dict()

        self.assertEqual(
            {item.facet.value for item in inspection.values},
            {"area", "function"},
        )
        self.assertFalse(
            inspection.to_dict()["generation_authority"]
        )
        self.assertTrue(preview.has_blocking_warning)
        self.assertEqual(preview.materials[0].shortage, 40)
        self.assertEqual(payload["additions"], 120)
        self.assertEqual(payload["removals"], 4)
        self.assertFalse(payload["world_write_performed"])
        self.assertFalse(payload["hard_usability_evaluated"])
        self.assertFalse(payload["canonical_write_authority"])

    def test_human_approval_is_distinct_exact_and_revision_invalidates(
        self,
    ) -> None:
        policy = _approval_policy()
        assembly = _with_policies(policy)
        identity = _identity_receipt(assembly)
        approval = issue_human_candidate_approval(
            assembly,
            policy,
            identity_receipt=identity,
            authority_id="authority-user",
            approval_event_ref="event://authority/candidate-approval",
            issued_at_utc=ISSUED,
            valid_until_utc=VALID,
        )

        self.assertFalse(
            identity.to_dict()["candidate_approval_authority"]
        )
        self.assertNotEqual(identity.ref, approval.ref)
        self.assertEqual(
            CandidateApprovalReceipt.from_dict(approval.to_dict()),
            approval,
        )
        validate_candidate_approval(
            assembly,
            policy,
            approval,
            now_utc="2026-07-25T10:20:00Z",
        )

        revised = _revised(assembly)
        proposal = create_revision_proposal(
            assembly,
            revised,
            kind=CandidateRevisionKind.MOVE,
            authority_id="authority-user",
            rationale="Move the project-authored candidate.",
            evidence_refs=(EVIDENCE,),
            prior_approval=approval,
        )
        self.assertEqual(proposal.invalidated_approval_ref, approval.ref)
        self.assertFalse(proposal.to_dict()["world_write_authority"])
        with self.assertRaisesRegex(
            PlayerAuthorityError,
            "approval policy|invalidated",
        ):
            validate_candidate_approval(
                revised,
                policy,
                approval,
                now_utc="2026-07-25T10:20:00Z",
            )

    def test_disposable_automation_requires_exact_dual_policy(self) -> None:
        base_assembly = _assembly()
        build_policy = _build_policy(base_assembly)
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
            build_policy_ref=BUILD_POLICY,
            authority_id="authority-user",
            issued_at_utc=ISSUED,
            valid_until_utc=VALID,
        )

        self.assertEqual(
            approval.source.value,
            "preauthorized_policy",
        )
        self.assertIsNone(approval.authority_identity_receipt_ref)
        with self.assertRaisesRegex(
            PlayerControlError,
            "exact disposable build policy",
        ):
            issue_disposable_automation_approval(
                _with_policies(policy),
                policy,
                build_policy,
                build_policy_ref=BUILD_POLICY,
                authority_id="authority-user",
                issued_at_utc=ISSUED,
                valid_until_utc=VALID,
            )

    def test_pause_cancel_preserve_exact_candidate_and_world(self) -> None:
        control = open_candidate_control(_preview(_assembly()))
        paused = pause_candidate_control(
            control,
            reason="Player is reviewing the preview.",
        )
        cancelled = cancel_candidate_control(
            paused,
            reason="Player requested a revised candidate.",
        )

        self.assertIs(paused.status, CandidateControlStatus.PAUSED)
        self.assertIs(cancelled.status, CandidateControlStatus.CANCELLED)
        self.assertEqual(control.candidate_assembly_digest, cancelled.candidate_assembly_digest)
        self.assertEqual(control.base, cancelled.base)
        self.assertEqual(control.world, cancelled.world)
        self.assertFalse(cancelled.to_dict()["world_mutation_performed"])

    def test_preference_cannot_waive_hard_failure(self) -> None:
        policy = _approval_policy()
        assembly = _with_policies(policy)
        approval = issue_human_candidate_approval(
            assembly,
            policy,
            identity_receipt=_identity_receipt(assembly),
            authority_id="authority-user",
            approval_event_ref="event://authority/candidate-approval",
            issued_at_utc=ISSUED,
            valid_until_utc=VALID,
        )
        preference = record_candidate_preference(
            assembly,
            policy,
            authority_id="authority-user",
            selected_material_refs=("material:project-choice",),
            aesthetic_observation_refs=("aesthetic:observation-001",),
            rationale="The player prefers this project-supplied material.",
            authority_event_ref="event://authority/preference",
            issued_at_utc=ISSUED,
        )
        hard = ValidationReceipt(
            receipt_id="hard-validation-failed",
            submission_id=assembly.submission.submission_id,
            submission_digest=assembly.submission.content_digest(),
            checked_state=assembly.plan.base,
            passed=False,
            findings=(
                Finding(
                    code="usable.failed",
                    message="A hard usability relationship failed.",
                ),
            ),
        )
        state = OperationalMarkovState(
            branch=BranchRef(
                run=RunRef(
                    project_id=assembly.plan.project_id,
                    run_id=assembly.plan.run_id,
                    base=assembly.plan.base,
                ),
                branch_id="candidate-review",
                epoch=0,
            ),
            compiler_version="player-control-test",
            phase="candidate",
        )
        monitor = monitor_commitments(
            state,
            candidate_id=assembly.submission.submission_id,
            observations=(),
            completion_boundary=True,
        )
        readiness = assess_candidate_promotion_readiness(
            assembly,
            policy,
            approval,
            hard,
            monitor,
            now_utc="2026-07-25T10:20:00Z",
            preference=preference,
        )

        self.assertFalse(readiness.ready)
        self.assertIn("hard_validation_failed", readiness.blockers)
        self.assertFalse(readiness.to_dict()["canonical_write_authority"])
        self.assertFalse(
            preference.to_dict()["hard_gate_waiver_authority"]
        )
        with self.assertRaisesRegex(
            PlayerAuthorityError,
            "not named",
        ):
            record_candidate_preference(
                assembly,
                policy,
                authority_id="authority-other",
                selected_material_refs=("material:project-choice",),
                rationale="Unauthorized selection must fail.",
                authority_event_ref="event://authority/unauthorized",
                issued_at_utc=ISSUED,
            )

    def test_player_cannot_edit_an_unauthorized_commitment(self) -> None:
        commitment = replace(
            _commitment(),
            authority_id="authority-owner",
        )
        state = _state(commitment=commitment)
        request = _request(
            state,
            alternatives=(
                replace(
                    _request(
                        _state(),
                    ).alternatives[0],
                    effect=replace(
                        _request(_state()).alternatives[0].effect,
                        commitment_action=(
                            CommitmentClarificationAction.AUTHORIZE
                        ),
                        commitment_id=commitment.commitment_id,
                    ),
                ),
            ),
        )
        receipt = _receipt(request)

        with self.assertRaises(ClarificationUnauthorizedError):
            apply_player_clarification(
                request,
                state,
                now_utc="2026-07-25T10:20:00Z",
                receipt=receipt,
            )


if __name__ == "__main__":
    unittest.main()
