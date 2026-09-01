from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.state.operational_state import (
    DependencyEdge,
    DependencyEffect,
    DesignObligation,
    FactEpistemicStatus,
    ObligationStatus,
    OperationalMarkovState,
    StateDomain,
    StateFact,
    StateLock,
)
from archflow.state.stage_convergence import (
    StageConvergenceEvidence,
    StageConvergenceOutcome,
    StageConvergencePolicy,
    StageTransitionKind,
    StageTransitionRequest,
    evaluate_stage_convergence,
)


_BASE_DIGEST = "b" * 64


def branch(
    epoch: int,
    *,
    branch_id: str = "option-a",
) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="synthetic-building",
            run_id="run-001",
            base=ProjectVersionRef(
                project_id="synthetic-building",
                version=3,
                state_sha256=_BASE_DIGEST,
            ),
        ),
        branch_id=branch_id,
        epoch=epoch,
    )


def obligation(
    obligation_id: str,
    status: ObligationStatus,
    *,
    subject_ref: str,
) -> DesignObligation:
    return DesignObligation(
        obligation_id=obligation_id,
        statement=f"Resolve {subject_ref}.",
        source_ref=f"requirement:{obligation_id}",
        status=status,
        subject_refs=(subject_ref,),
    )


def evidence(
    state: OperationalMarkovState,
    *,
    hard: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    tolerance: tuple[str, ...] = (),
    revalidation: tuple[str, ...] = (),
) -> StageConvergenceEvidence:
    return StageConvergenceEvidence(
        state_digest=state.state_digest,
        hard_gate_failure_refs=hard,
        conflict_refs=conflicts,
        tolerance_failure_refs=tolerance,
        revalidation_refs=revalidation,
    )


def request(
    policy: StageConvergencePolicy,
    parent: OperationalMarkovState,
    child: OperationalMarkovState,
    kind: StageTransitionKind,
    *,
    authority_id: str = "authority.architect",
    trigger_refs: tuple[str, ...] = (),
    reopened_refs: tuple[str, ...] = (),
    added: tuple[str, ...] = (),
    authorization_ref: str | None = None,
    parent_digest: str | None = None,
    child_digest: str | None = None,
) -> StageTransitionRequest:
    return StageTransitionRequest(
        request_id=f"request-{kind.value}",
        stage=policy.stage,
        kind=kind,
        authority_id=authority_id,
        parent_state_digest=parent_digest or parent.state_digest,
        child_state_digest=child_digest or child.state_digest,
        trigger_refs=trigger_refs,
        reopened_refs=reopened_refs,
        added_mandatory_obligation_ids=added,
        authorization_ref=authorization_ref,
    )


class ExactBindingAndPotentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = StageConvergencePolicy(
            policy_id="generic-stage",
            stage="schematic-design",
            mandatory_obligation_ids=("coordinate-envelope",),
        )
        self.parent = OperationalMarkovState(
            branch=branch(4),
            compiler_version="test-compiler",
            phase="schematic_design",
            obligations=(
                obligation(
                    "coordinate-envelope",
                    ObligationStatus.OPEN,
                    subject_ref="deliverable:envelope",
                ),
            ),
        )
        self.child = replace(
            self.parent,
            branch=branch(5),
            obligations=(
                obligation(
                    "coordinate-envelope",
                    ObligationStatus.SATISFIED,
                    subject_ref="deliverable:envelope",
                ),
            ),
        )

    def test_exact_parent_child_and_strict_progress(self) -> None:
        receipt = evaluate_stage_convergence(
            self.policy,
            request(
                self.policy,
                self.parent,
                self.child,
                StageTransitionKind.RESOLVE,
            ),
            self.parent,
            self.child,
            parent_evidence=evidence(self.parent),
            child_evidence=evidence(self.child),
        )
        self.assertIs(receipt.outcome, StageConvergenceOutcome.PROGRESS)
        self.assertEqual(receipt.parent_state_digest, self.parent.state_digest)
        self.assertEqual(receipt.child_state_digest, self.child.state_digest)
        self.assertLess(
            receipt.potential_after.vector,
            receipt.potential_before.vector,
        )
        self.assertTrue(receipt.passed)
        self.assertTrue(receipt.stage_ready)
        self.assertFalse(receipt.to_dict()["canonical_write_authority"])

    def test_stale_parent_and_non_successor_child_fail_closed(self) -> None:
        stale = evaluate_stage_convergence(
            self.policy,
            request(
                self.policy,
                self.parent,
                self.child,
                StageTransitionKind.RESOLVE,
                parent_digest="a" * 64,
            ),
            self.parent,
            self.child,
            parent_evidence=evidence(self.parent),
            child_evidence=evidence(self.child),
        )
        self.assertIs(stale.outcome, StageConvergenceOutcome.REJECTED)
        self.assertIn(
            "stage_convergence.stale_parent_digest",
            stale.reason_codes,
        )

        skipped = replace(self.child, branch=branch(7))
        skipped_receipt = evaluate_stage_convergence(
            self.policy,
            request(
                self.policy,
                self.parent,
                skipped,
                StageTransitionKind.RESOLVE,
            ),
            self.parent,
            skipped,
            parent_evidence=evidence(self.parent),
            child_evidence=evidence(skipped),
        )
        self.assertIn(
            "stage_convergence.child_is_not_exact_successor",
            skipped_receipt.reason_codes,
        )

    def test_noop_and_lexicographic_regression_are_rejected(self) -> None:
        noop = replace(self.parent, branch=branch(5))
        noop_receipt = evaluate_stage_convergence(
            self.policy,
            request(
                self.policy,
                self.parent,
                noop,
                StageTransitionKind.REFINE,
            ),
            self.parent,
            noop,
            parent_evidence=evidence(self.parent),
            child_evidence=evidence(noop),
        )
        self.assertIn("stage_convergence.no_op", noop_receipt.reason_codes)

        regressed = replace(
            self.child,
            evidence_refs=("evidence:regressed-gate",),
        )
        regression_receipt = evaluate_stage_convergence(
            self.policy,
            request(
                self.policy,
                self.parent,
                regressed,
                StageTransitionKind.RESOLVE,
            ),
            self.parent,
            regressed,
            parent_evidence=evidence(self.parent),
            child_evidence=evidence(
                regressed,
                hard=("gate-failure:fire-egress",),
            ),
        )
        self.assertIn(
            "stage_convergence.potential_not_strictly_decreased",
            regression_receipt.reason_codes,
        )

    def test_potential_comparison_is_lexicographic(self) -> None:
        parent = OperationalMarkovState(
            branch=branch(10),
            compiler_version="test-compiler",
            phase="schematic_design",
        )
        child = replace(
            parent,
            branch=branch(11),
            evidence_refs=("evidence:lower-severity-review",),
        )
        policy = StageConvergencePolicy(
            policy_id="lexicographic-stage",
            stage="schematic-design",
        )
        receipt = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.REFINE,
            ),
            parent,
            child,
            parent_evidence=evidence(
                parent,
                hard=("gate-failure:primary",),
            ),
            child_evidence=evidence(
                child,
                conflicts=(
                    "conflict:secondary-a",
                    "conflict:secondary-b",
                ),
            ),
        )
        self.assertEqual(receipt.potential_before.vector[:2], (1, 0))
        self.assertEqual(receipt.potential_after.vector[:2], (0, 2))
        self.assertIs(receipt.outcome, StageConvergenceOutcome.PROGRESS)
        self.assertFalse(receipt.stage_ready)


class ProtectedRefAndRepairTests(unittest.TestCase):
    _ROW_REF = "fact:semantic:colonnade-row-signature"

    def historical_state(
        self,
        *,
        epoch: int,
        row_signature: str = "8+4+4",
        obligation_status: ObligationStatus = ObligationStatus.OPEN,
        invalidated: tuple[str, ...] = ("deliverable:portico",),
    ) -> OperationalMarkovState:
        return OperationalMarkovState(
            branch=branch(epoch),
            compiler_version="test-compiler",
            phase="schematic_design",
            facts=(
                StateFact(
                    domain=StateDomain.SEMANTIC,
                    key="colonnade-row-signature",
                    value=row_signature,
                    source_ref="adoption:historical-plan",
                    epistemic_status=FactEpistemicStatus.DECLARED,
                ),
            ),
            locks=(
                StateLock(
                    target_ref=self._ROW_REF,
                    authority_id="authority.historian",
                    source_ref="decision:adopt-row-signature",
                ),
            ),
            obligations=(
                obligation(
                    "verify-portico",
                    obligation_status,
                    subject_ref="deliverable:portico",
                ),
            ),
            invalidated_refs=invalidated,
        )

    def policy(self) -> StageConvergencePolicy:
        return StageConvergencePolicy(
            policy_id="historical-portico",
            stage="reconstruction-structure",
            protected_refs=(self._ROW_REF,),
            mandatory_obligation_ids=("verify-portico",),
            scope_expansion_authority_ids=("authority.historian",),
        )

    def test_historical_dependency_local_repair_preserves_lock(self) -> None:
        parent = self.historical_state(epoch=2)
        child = self.historical_state(
            epoch=3,
            obligation_status=ObligationStatus.SATISFIED,
            invalidated=(),
        )
        policy = self.policy()
        receipt = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.REPAIR,
                authority_id="authority.historian",
                trigger_refs=("deliverable:portico",),
            ),
            parent,
            child,
            parent_evidence=evidence(
                parent,
                tolerance=("tolerance-failure:column-spacing",),
            ),
            child_evidence=evidence(child),
        )
        self.assertIs(receipt.outcome, StageConvergenceOutcome.REPAIR)
        self.assertEqual(receipt.changed_protected_refs, ())
        self.assertNotIn(self._ROW_REF, receipt.potential_after.deficit_refs)

    def test_ordinary_progress_cannot_change_protected_value(self) -> None:
        parent = self.historical_state(epoch=2)
        child = self.historical_state(
            epoch=3,
            row_signature="8+8",
            obligation_status=ObligationStatus.SATISFIED,
            invalidated=(),
        )
        policy = self.policy()
        receipt = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.RESOLVE,
                authority_id="authority.historian",
            ),
            parent,
            child,
            parent_evidence=evidence(parent),
            child_evidence=evidence(child),
        )
        self.assertIs(receipt.outcome, StageConvergenceOutcome.REJECTED)
        self.assertIn(
            "stage_convergence.protected_ref_changed",
            receipt.reason_codes,
        )

    def test_repair_must_target_existing_deficit_and_add_no_scope(self) -> None:
        parent = self.historical_state(epoch=2)
        child = self.historical_state(
            epoch=3,
            obligation_status=ObligationStatus.SATISFIED,
            invalidated=(),
        )
        policy = self.policy()
        outside = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.REPAIR,
                trigger_refs=("deliverable:unrelated",),
            ),
            parent,
            child,
            parent_evidence=evidence(parent),
            child_evidence=evidence(child),
        )
        self.assertIn(
            "stage_convergence.repair_outside_parent_deficit",
            outside.reason_codes,
        )

        expanded_child = replace(
            child,
            obligations=(
                *child.obligations,
                obligation(
                    "new-detail-review",
                    ObligationStatus.OPEN,
                    subject_ref="deliverable:detail",
                ),
            ),
        )
        expanded = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                expanded_child,
                StageTransitionKind.REPAIR,
                trigger_refs=("deliverable:portico",),
            ),
            parent,
            expanded_child,
            parent_evidence=evidence(parent),
            child_evidence=evidence(expanded_child),
        )
        self.assertIn(
            "stage_convergence.ordinary_transition_added_obligations",
            expanded.reason_codes,
        )


class ModernScopeExpansionTests(unittest.TestCase):
    _GRID_REF = "fact:parameter:structural-grid"
    _CORE_REF = "deliverable:core-egress"
    _FACADE_REF = "deliverable:facade-module"
    _MEP_REF = "deliverable:mep-shafts"

    def state(
        self,
        *,
        epoch: int,
        grid: str,
        obligations: tuple[DesignObligation, ...] = (),
        invalidated: tuple[str, ...] = (),
    ) -> OperationalMarkovState:
        return OperationalMarkovState(
            branch=branch(epoch),
            compiler_version="test-compiler",
            phase="schematic_design",
            facts=(
                StateFact(
                    domain=StateDomain.PARAMETER,
                    key="structural-grid",
                    value=grid,
                    source_ref="decision:structural-grid",
                    epistemic_status=FactEpistemicStatus.DECLARED,
                ),
            ),
            locks=(
                StateLock(
                    target_ref=self._GRID_REF,
                    authority_id="authority.lead-architect",
                    source_ref="decision:freeze-grid",
                ),
            ),
            obligations=obligations,
            invalidated_refs=invalidated,
        )

    def policy(self, *, max_refs: int = 8) -> StageConvergencePolicy:
        return StageConvergencePolicy(
            policy_id="modern-coordination",
            stage="schematic-design",
            protected_refs=(self._GRID_REF,),
            dependencies=(
                DependencyEdge(
                    upstream_ref=self._GRID_REF,
                    downstream_ref=self._CORE_REF,
                    relation="coordinates",
                    source_ref="policy:modern-grid",
                    effect=DependencyEffect.INVALIDATES,
                ),
                DependencyEdge(
                    upstream_ref=self._GRID_REF,
                    downstream_ref=self._FACADE_REF,
                    relation="modules",
                    source_ref="policy:modern-grid",
                    effect=DependencyEffect.REQUIRES_REVALIDATION,
                ),
                DependencyEdge(
                    upstream_ref=self._CORE_REF,
                    downstream_ref=self._MEP_REF,
                    relation="routes",
                    source_ref="policy:modern-grid",
                    effect=DependencyEffect.INVALIDATES,
                ),
            ),
            scope_expansion_authority_ids=(
                "authority.lead-architect",
            ),
            max_scope_expansion_refs=max_refs,
        )

    def expanded_pair(self):
        parent = self.state(epoch=20, grid="8.4m")
        added = (
            obligation(
                "recheck-core",
                ObligationStatus.OPEN,
                subject_ref=self._CORE_REF,
            ),
            obligation(
                "recheck-facade",
                ObligationStatus.OPEN,
                subject_ref=self._FACADE_REF,
            ),
            obligation(
                "recheck-mep",
                ObligationStatus.OPEN,
                subject_ref=self._MEP_REF,
            ),
        )
        closure = tuple(
            sorted(
                {
                    self._GRID_REF,
                    self._CORE_REF,
                    self._FACADE_REF,
                    self._MEP_REF,
                }
            )
        )
        child = self.state(
            epoch=21,
            grid="9.0m",
            obligations=added,
            invalidated=closure,
        )
        return parent, child, closure

    def test_authorized_modern_expansion_is_bounded_and_never_progress(self) -> None:
        parent, child, closure = self.expanded_pair()
        policy = self.policy()
        added_ids = ("recheck-core", "recheck-facade", "recheck-mep")
        receipt = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.SCOPE_EXPANSION,
                authority_id="authority.lead-architect",
                trigger_refs=(self._GRID_REF,),
                reopened_refs=closure,
                added=added_ids,
                authorization_ref="authority-receipt:grid-revision",
            ),
            parent,
            child,
            parent_evidence=evidence(parent),
            child_evidence=evidence(child),
        )
        self.assertIs(
            receipt.outcome,
            StageConvergenceOutcome.SCOPE_EXPANSION,
        )
        self.assertIsNot(receipt.outcome, StageConvergenceOutcome.PROGRESS)
        self.assertFalse(receipt.stage_ready)
        self.assertEqual(receipt.dependency_closure, closure)
        self.assertEqual(receipt.changed_protected_refs, (self._GRID_REF,))
        self.assertGreater(
            receipt.potential_after.vector,
            receipt.potential_before.vector,
        )

    def test_unauthorized_or_wider_than_closure_expansion_rejects(self) -> None:
        parent, child, closure = self.expanded_pair()
        policy = self.policy()
        added_ids = ("recheck-core", "recheck-facade", "recheck-mep")
        unauthorized = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.SCOPE_EXPANSION,
                authority_id="authority.unrelated",
                trigger_refs=(self._GRID_REF,),
                reopened_refs=closure,
                added=added_ids,
                authorization_ref="authority-receipt:grid-revision",
            ),
            parent,
            child,
            parent_evidence=evidence(parent),
            child_evidence=evidence(child),
        )
        self.assertIn(
            "stage_convergence.scope_expansion_authority_missing",
            unauthorized.reason_codes,
        )

        wider = (*closure, "deliverable:landscape")
        wider_receipt = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.SCOPE_EXPANSION,
                authority_id="authority.lead-architect",
                trigger_refs=(self._GRID_REF,),
                reopened_refs=tuple(sorted(wider)),
                added=added_ids,
                authorization_ref="authority-receipt:grid-revision",
            ),
            parent,
            child,
            parent_evidence=evidence(parent),
            child_evidence=evidence(child),
        )
        self.assertIn(
            "stage_convergence.dependency_closure_mismatch",
            wider_receipt.reason_codes,
        )

    def test_scope_expansion_bound_is_enforced(self) -> None:
        parent, child, closure = self.expanded_pair()
        policy = self.policy(max_refs=6)
        receipt = evaluate_stage_convergence(
            policy,
            request(
                policy,
                parent,
                child,
                StageTransitionKind.SCOPE_EXPANSION,
                authority_id="authority.lead-architect",
                trigger_refs=(self._GRID_REF,),
                reopened_refs=closure,
                added=("recheck-core", "recheck-facade", "recheck-mep"),
                authorization_ref="authority-receipt:grid-revision",
            ),
            parent,
            child,
            parent_evidence=evidence(parent),
            child_evidence=evidence(child),
        )
        self.assertIn(
            "stage_convergence.scope_expansion_too_wide",
            receipt.reason_codes,
        )


if __name__ == "__main__":
    unittest.main()
