from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from archive.archflow.capabilities.evidence_sufficiency import (
    DecisionEdge,
    DecisionNode,
    DecisionUniverseRevision,
    EvidenceRule,
    EvidenceSufficiencyPolicy,
    FrontierStatus,
    ResolutionMode,
    ResolutionRecord,
    build_research_frontier,
    compile_decision_universe_closure,
    compile_evidence_sufficiency,
)
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.stage_closure import (
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.runtime.design_controller import (
    ControllerOutcome,
    advance_design_phase,
)
from archflow.state.stage_workflow import DesignPhase
from archive.archflow.state.design_maturity import PhaseGateRequest, evaluate_forward_phase_gate
from archflow.state.operational_state import (
    DesignObligation,
    FactEpistemicStatus,
    ObligationStatus,
    OperationalMarkovState,
    StateDomain,
    StateFact,
    StateLock,
)
from archive.archflow.state.stage_convergence import (
    StageConvergenceEvidence,
    StageConvergenceOutcome,
    StageConvergencePolicy,
    StageTransitionKind,
    StageTransitionRequest,
    evaluate_stage_convergence,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.tests.test_design_controller import (
    _phase_ready_checkpoint,
    _stage_closure_receipt,
    _stage_convergence_receipt,
    _stage_inputs_for_closure,
    _stage_profile_binding,
)


_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "modern_learning_pavilion.json"
)


class ModernDecisionDesignClosureIntegrationTests(unittest.TestCase):
    def test_fixture_closes_research_before_stage_progress(self) -> None:
        fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        self.assertTrue(fixture["synthetic_test"])
        self.assertFalse(fixture["empirical_authority"])

        hard_rows = tuple(
            item
            for item in fixture["decisions"]
            if item["constraint_class"] == "hard"
        )
        soft_refs = {
            item["ref"]
            for item in fixture["decisions"]
            if item["constraint_class"] == "soft"
        }
        nodes = tuple(
            DecisionNode(
                decision_ref=item["ref"],
                ontology_kind=item["ontology_kind"],
                required=item["constraint_class"] == "hard",
            )
            for item in fixture["decisions"]
        )
        edges = tuple(
            DecisionEdge(
                edge_ref=item["ref"],
                source_ref=item["source_ref"],
                target_ref=item["target_ref"],
                relation_kind=item["relation_kind"],
            )
            for item in fixture["required_relations"]
        )
        scope_digest = "a" * 64
        universe = DecisionUniverseRevision(
            universe_id="modern-learning-pavilion",
            revision_id="coordination-r1",
            scope_digest=scope_digest,
            ontology_ref="fixture:modern-learning-pavilion",
            nodes=nodes,
            edges=edges,
            seed_refs=tuple(sorted(item["ref"] for item in hard_rows)),
        )
        required_targets = tuple(
            sorted(
                {
                    *(item.decision_ref for item in nodes if item.required),
                    *(item.edge_ref for item in edges if item.required),
                }
            )
        )
        policy = EvidenceSufficiencyPolicy(
            policy_id="modern-coordination-evidence",
            rules=tuple(
                EvidenceRule(
                    obligation_id=f"resolve-{index:02d}",
                    target_ref=target_ref,
                    allowed_modes=(ResolutionMode.DERIVE,),
                    minimum_bindings=0,
                    minimum_source_families=0,
                )
                for index, target_ref in enumerate(required_targets, start=1)
            ),
        )
        resolutions = tuple(
            ResolutionRecord(
                resolution_id=f"resolution-{index:02d}",
                target_ref=target_ref,
                mode=ResolutionMode.DERIVE,
                refs=(f"fixture-resolution:{index:02d}",),
            )
            for index, target_ref in enumerate(required_targets, start=1)
        )
        closure = compile_decision_universe_closure(
            universe,
            policy,
            resolutions=resolutions,
        )
        sufficiency = compile_evidence_sufficiency(
            universe,
            policy,
            resolutions=resolutions,
        )
        frontier = build_research_frontier(closure, sufficiency)
        self.assertIs(frontier.status, FrontierStatus.COMPLETE)
        self.assertFalse(frontier.work_items)

        project_id = fixture["project_id"]
        run = RunRef(
            project_id=project_id,
            run_id="synthetic-run",
            base=ProjectVersionRef(
                project_id=project_id,
                version=0,
                state_sha256="b" * 64,
            ),
        )

        def branch(epoch: int) -> BranchRef:
            return BranchRef(
                run=run,
                branch_id=fixture["branch_id"],
                epoch=epoch,
            )

        grid_ref = "fact:parameter:structural-grid"
        obligation_id = "coordinate-grid-facade"

        def obligation(status: ObligationStatus) -> DesignObligation:
            return DesignObligation(
                obligation_id=obligation_id,
                statement="Coordinate the selected grid and facade module.",
                source_ref="relation:grid-constrains-facade",
                status=status,
                subject_refs=("decision:facade-module",),
            )

        parent = OperationalMarkovState(
            branch=branch(0),
            compiler_version="integration-fixture",
            phase=fixture["stage"],
            facts=(
                StateFact(
                    domain=StateDomain.PARAMETER,
                    key="structural-grid",
                    value="7.2 m",
                    source_ref="decision:structural-grid",
                    epistemic_status=FactEpistemicStatus.DECLARED,
                ),
            ),
            locks=(
                StateLock(
                    target_ref=grid_ref,
                    authority_id="authority.lead-designer",
                    source_ref="fixture-lock:structural-grid",
                ),
            ),
            obligations=(obligation(ObligationStatus.OPEN),),
        )
        child = replace(
            parent,
            branch=branch(1),
            obligations=(obligation(ObligationStatus.SATISFIED),),
        )
        convergence_policy = StageConvergencePolicy(
            policy_id="modern-coordination-stage",
            stage=fixture["stage"],
            protected_refs=(grid_ref,),
            mandatory_obligation_ids=(obligation_id,),
        )
        receipt = evaluate_stage_convergence(
            convergence_policy,
            StageTransitionRequest(
                request_id="resolve-grid-facade",
                stage=fixture["stage"],
                kind=StageTransitionKind.RESOLVE,
                authority_id="authority.lead-designer",
                parent_state_digest=parent.state_digest,
                child_state_digest=child.state_digest,
            ),
            parent,
            child,
            parent_evidence=StageConvergenceEvidence(
                state_digest=parent.state_digest
            ),
            child_evidence=StageConvergenceEvidence(
                state_digest=child.state_digest
            ),
        )
        self.assertIs(receipt.outcome, StageConvergenceOutcome.PROGRESS)
        self.assertTrue(receipt.stage_ready)
        self.assertEqual(receipt.protected_refs, (grid_ref,))
        self.assertTrue(soft_refs.isdisjoint(receipt.protected_refs))

    def test_compiled_composite_closure_enters_phase_control_path(self) -> None:
        checkpoint = _phase_ready_checkpoint()
        subject = checkpoint.maturity.deliverables[0]
        scope_digest = "c" * 64
        requirements = (
            StageCheckRequirement(
                requirement_id="phase-subject-current",
                checker_id="archflow.phase-subject-current",
                target_kind=RequirementTargetKind.ARTIFACT,
                basis_mode=RequirementBasisMode.UNIVERSAL,
                denominator_refs=(subject.ref,),
            ),
            StageCheckRequirement(
                requirement_id="component-lineage",
                checker_id="component-lineage-validator",
                target_kind=RequirementTargetKind.COMPONENT,
                basis_mode=RequirementBasisMode.UNIVERSAL,
                denominator_refs=("component:all",),
            ),
            StageCheckRequirement(
                requirement_id="spatial-envelope",
                checker_id="spatial-layout-validator",
                target_kind=RequirementTargetKind.ASSEMBLY,
                basis_mode=RequirementBasisMode.UNIVERSAL,
                denominator_refs=("spatial-envelope:all",),
            ),
            StageCheckRequirement(
                requirement_id="assembly-relations",
                checker_id="assembly-relationship-checker",
                target_kind=RequirementTargetKind.ASSEMBLY,
                basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
                denominator_refs=(
                    "assembly-profile:integration-fixture",
                    "assembly-requirement:support:roof-wall:"
                    "1111111111111111",
                    "assembly-requirement:opening_clear:door-clear:"
                    "2222222222222222",
                    "assembly-requirement:load_path_to_foundation:roof-base:"
                    "3333333333333333",
                ),
                required_authority_refs=(
                    "authority:integration-assembly-profile",
                ),
            ),
        )
        profile = StageRequirementProfile(
            profile_id="controller-integration-closure",
            typology_id="synthetic-controller-fixture",
            stage_id=checkpoint.maturity.phase.value,
            branch=checkpoint.maturity.branch,
            predecessor_state_digest=(
                checkpoint.maturity.operational_state_digest
            ),
            scope_digest=scope_digest,
            stage_subject_ref=subject.ref,
            requirements=requirements,
        )
        check_receipts = tuple(
            CheckReceiptEnvelope(
                check_id=requirement.requirement_id,
                checker_id=requirement.checker_id,
                checker_version="1.0.0",
                branch=checkpoint.maturity.branch,
                scope_digest=scope_digest,
                subject_refs=requirement.denominator_refs,
                subject_digest=subject.base_state_digest,
                status=CheckStatus.PASS,
                coverage_denominator=requirement.denominator_refs,
                covered_refs=requirement.denominator_refs,
                authority_refs=requirement.required_authority_refs,
            )
            for requirement in requirements
        )
        closure = compile_composite_stage_closure(
            profile,
            subject_digest=subject.base_state_digest,
            check_receipts=check_receipts,
        )
        self.assertIs(closure.status, StageClosureStatus.SATISFIED)

        phase_gate = evaluate_forward_phase_gate(
            checkpoint.maturity,
            PhaseGateRequest(
                request_id="integrated-phase-advance",
                branch=checkpoint.maturity.branch,
                base_state_digest=(
                    checkpoint.maturity.operational_state_digest
                ),
                from_phase=DesignPhase.SCHEMATIC_DESIGN,
                to_phase=DesignPhase.DESIGN_DEVELOPMENT,
                deliverable_refs=checkpoint.maturity.deliverable_refs,
            ),
        )
        controller_closure = _stage_closure_receipt(checkpoint)
        (
            controller_profile,
            baseline_sources,
            controller_check_receipts,
            subject_inventory,
        ) = _stage_inputs_for_closure(checkpoint, controller_closure)
        result = advance_design_phase(
            checkpoint,
            phase_gate,
            convergence_receipt=_stage_convergence_receipt(checkpoint),
            requirement_profile=controller_profile,
            profile_binding=_stage_profile_binding(
                controller_closure,
                subject_inventory,
            ),
            closure_receipt=controller_closure,
            baseline_sources=baseline_sources,
            subject_inventory=subject_inventory,
            check_receipts=controller_check_receipts,
            history_event_ref="design-event:integrated-phase-advance",
        )

        self.assertIs(result.outcome, ControllerOutcome.PHASE_ADVANCED)
        self.assertIs(result.stage_closure, controller_closure)
        self.assertIn(
            controller_closure.receipt_digest,
            result.stage_closure.receipt_id,
        )


if __name__ == "__main__":
    unittest.main()
