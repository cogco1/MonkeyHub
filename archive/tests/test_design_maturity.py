from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path

from archive.archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertEvidence,
    ExpertObligation,
    ExpertRegistry,
    ExpertSnapshot,
    ExpertSpec,
)
from archive.archflow.capabilities.phase_gates import (
    PhaseCapabilityError,
    PhaseExpertMetadata,
    discover_phase_experts,
    validate_architect_selected_expert_order,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.state.stage_workflow import DESIGN_PHASES, DesignPhase
from archive.archflow.state.design_maturity import PHASE_DELIVERABLE_ROLES, BackwardRevisionRequest, DeliverableRole, DesignMaturityError, DesignMaturityState, GateCertificationSource, PhaseDeliverable, PhaseGateReceipt, PhaseGateRequest, StageEntryProof, compile_backward_revision, evaluate_forward_phase_gate, next_design_phase, require_current_phase_gate, require_stage_entry_proof
from archflow.state.operational_state import (
    DependencyEdge,
    DependencyEffect,
    DesignObligation,
    ObligationStatus,
    OperationalMarkovState,
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _branch(
    *,
    branch_id: str = "option-a",
    epoch: int = 4,
) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="phase-contract-test",
            run_id="run-001",
            base=ProjectVersionRef(
                project_id="phase-contract-test",
                version=3,
                state_sha256=_hash("canonical-base"),
            ),
        ),
        branch_id=branch_id,
        epoch=epoch,
    )


def _deliverables(
    phase: DesignPhase,
    branch: BranchRef,
) -> tuple[PhaseDeliverable, ...]:
    return tuple(
        PhaseDeliverable(
            deliverable_id=f"{phase.value}-{role.value}",
            role=role,
            produced_phase=phase,
            branch=branch,
            base_state_digest=_hash(f"{phase.value}-source-state"),
            artifact_ref=(
                f"project-record:deliverables/{phase.value}/"
                f"{role.value}.json"
            ),
            evidence_refs=(f"evidence:{phase.value}",),
        )
        for role in sorted(
            PHASE_DELIVERABLE_ROLES[phase],
            key=lambda item: item.value,
        )
    )


def _maturity(
    phase: DesignPhase,
    *,
    branch: BranchRef | None = None,
    deliverables: tuple[PhaseDeliverable, ...] | None = None,
    state_label: str | None = None,
    invalidated_refs: tuple[str, ...] = (),
    revalidation_required_refs: tuple[str, ...] = (),
) -> DesignMaturityState:
    exact_branch = branch or _branch()
    return DesignMaturityState(
        branch=exact_branch,
        operational_state_digest=_hash(
            state_label or f"current-{phase.value}"
        ),
        phase=phase,
        deliverables=(
            _deliverables(phase, exact_branch)
            if deliverables is None
            else deliverables
        ),
        invalidated_refs=invalidated_refs,
        revalidation_required_refs=revalidation_required_refs,
    )


def _stage_entry_proof() -> StageEntryProof:
    maturity = _maturity(DesignPhase.SCHEMATIC_DESIGN)
    target = DesignPhase.DESIGN_DEVELOPMENT
    phase_gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id="enter-design-development",
            branch=maturity.branch,
            base_state_digest=maturity.operational_state_digest,
            from_phase=maturity.phase,
            to_phase=target,
            deliverable_refs=maturity.deliverable_refs,
        ),
    )
    successor = replace(
        maturity.branch,
        epoch=maturity.branch.epoch + 1,
    )
    return StageEntryProof(
        phase_gate=phase_gate,
        stage_exit_checkpoint_ref=ProjectRecordRef(
            project_id=successor.run.project_id,
            relative_path=(
                f"runs/{successor.run.run_id}/branches/"
                f"{successor.branch_id}/records/stage-entry.json"
            ),
            sha256=_hash("stage-entry-record"),
        ),
        stage_exit_proof_digest=_hash("stage-exit-proof"),
        predecessor_checkpoint_digest=_hash("schematic-checkpoint"),
        successor_checkpoint_digest=_hash("developed-checkpoint"),
        successor_branch=successor,
    )


class DesignMaturityGateTests(unittest.TestCase):
    def test_stage_entry_proof_roundtrip_and_exact_guard(self) -> None:
        proof = _stage_entry_proof()

        self.assertEqual(StageEntryProof.from_dict(proof.to_dict()), proof)
        self.assertEqual(
            require_stage_entry_proof(
                proof,
                successor_branch=proof.successor_branch,
                from_phase=DesignPhase.SCHEMATIC_DESIGN,
                to_phase=DesignPhase.DESIGN_DEVELOPMENT,
            ),
            proof,
        )
        payload = proof.to_dict()
        self.assertFalse(payload["stage_acceptance_authority"])
        self.assertFalse(payload["geometry_mutation_authority"])
        self.assertFalse(payload["persistence_authority"])
        self.assertFalse(payload["canonical_write_authority"])

    def test_stage_entry_guard_rejects_stale_successor_epoch(self) -> None:
        proof = _stage_entry_proof()
        stale = replace(
            proof.successor_branch,
            epoch=proof.successor_branch.epoch + 1,
        )

        with self.assertRaisesRegex(DesignMaturityError, "stale"):
            require_stage_entry_proof(
                proof,
                successor_branch=stale,
                from_phase=DesignPhase.SCHEMATIC_DESIGN,
                to_phase=DesignPhase.DESIGN_DEVELOPMENT,
            )

    def test_stage_entry_guard_rejects_cross_branch_successor(self) -> None:
        proof = _stage_entry_proof()
        foreign = replace(
            proof.successor_branch,
            branch_id="option-b",
        )

        with self.assertRaisesRegex(DesignMaturityError, "cross-branch"):
            require_stage_entry_proof(
                proof,
                successor_branch=foreign,
                from_phase=DesignPhase.SCHEMATIC_DESIGN,
                to_phase=DesignPhase.DESIGN_DEVELOPMENT,
            )

    def test_stage_entry_guard_rejects_wrong_phase_claim(self) -> None:
        proof = _stage_entry_proof()

        with self.assertRaisesRegex(DesignMaturityError, "wrong-phase"):
            require_stage_entry_proof(
                proof,
                successor_branch=proof.successor_branch,
                from_phase=DesignPhase.DESIGN_DEVELOPMENT,
                to_phase=DesignPhase.CANDIDATE_COORDINATION,
            )

    def test_forward_matrix_accepts_only_immediate_next_phase(self) -> None:
        for phase in DESIGN_PHASES[:-1]:
            with self.subTest(phase=phase):
                maturity = _maturity(phase)
                target = next_design_phase(phase)
                assert target is not None
                request = PhaseGateRequest(
                    request_id=f"advance-{phase.value}",
                    branch=maturity.branch,
                    base_state_digest=maturity.operational_state_digest,
                    from_phase=phase,
                    to_phase=target,
                    deliverable_refs=maturity.deliverable_refs,
                )
                receipt = evaluate_forward_phase_gate(
                    maturity,
                    request,
                )
                self.assertEqual(receipt.to_phase, target)
                self.assertEqual(
                    set(receipt.required_roles),
                    PHASE_DELIVERABLE_ROLES[phase],
                )
                require_current_phase_gate(maturity, receipt)
                self.assertEqual(
                    PhaseGateReceipt.from_dict(receipt.to_dict()),
                    receipt,
                )

                later = DESIGN_PHASES[
                    min(DESIGN_PHASES.index(phase) + 2, 6)
                ]
                if later is not target:
                    with self.assertRaisesRegex(
                        DesignMaturityError,
                        "cannot skip",
                    ):
                        evaluate_forward_phase_gate(
                            maturity,
                            PhaseGateRequest(
                                request_id=f"skip-{phase.value}",
                                branch=maturity.branch,
                                base_state_digest=(
                                    maturity.operational_state_digest
                                ),
                                from_phase=phase,
                                to_phase=later,
                                deliverable_refs=(
                                    maturity.deliverable_refs
                                ),
                            ),
                        )

    def test_forward_gate_rejects_missing_stale_and_expert_claims(self) -> None:
        maturity = _maturity(DesignPhase.SCHEMATIC_DESIGN)
        target = DesignPhase.DESIGN_DEVELOPMENT
        with self.assertRaisesRegex(
            DesignMaturityError,
            "missing required roles",
        ):
            evaluate_forward_phase_gate(
                maturity,
                PhaseGateRequest(
                    request_id="missing-role",
                    branch=maturity.branch,
                    base_state_digest=maturity.operational_state_digest,
                    from_phase=maturity.phase,
                    to_phase=target,
                    deliverable_refs=maturity.deliverable_refs[:1],
                ),
            )

        stale = DesignMaturityState(
            branch=maturity.branch,
            operational_state_digest=maturity.operational_state_digest,
            phase=maturity.phase,
            deliverables=maturity.deliverables,
            invalidated_refs=(maturity.deliverable_refs[0],),
        )
        with self.assertRaisesRegex(DesignMaturityError, "stale"):
            evaluate_forward_phase_gate(
                stale,
                PhaseGateRequest(
                    request_id="stale-deliverable",
                    branch=stale.branch,
                    base_state_digest=stale.operational_state_digest,
                    from_phase=stale.phase,
                    to_phase=target,
                    deliverable_refs=stale.deliverable_refs,
                ),
            )

        with self.assertRaisesRegex(
            DesignMaturityError,
            "expert assertions",
        ):
            evaluate_forward_phase_gate(
                maturity,
                PhaseGateRequest(
                    request_id="expert-certification",
                    branch=maturity.branch,
                    base_state_digest=maturity.operational_state_digest,
                    from_phase=maturity.phase,
                    to_phase=target,
                    deliverable_refs=maturity.deliverable_refs,
                    certification_source=(
                        GateCertificationSource.EXPERT_ASSERTION
                    ),
                ),
            )

    def test_gate_request_and_receipt_are_exact_base(self) -> None:
        maturity = _maturity(DesignPhase.PROGRAMMING)
        request = PhaseGateRequest(
            request_id="exact-gate",
            branch=maturity.branch,
            base_state_digest=maturity.operational_state_digest,
            from_phase=maturity.phase,
            to_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            deliverable_refs=maturity.deliverable_refs,
        )
        receipt = evaluate_forward_phase_gate(maturity, request)

        advanced_branch = _branch(epoch=maturity.branch.epoch + 1)
        advanced = _maturity(
            DesignPhase.PROGRAMMING,
            branch=advanced_branch,
            deliverables=maturity.deliverables,
            state_label="advanced-state",
        )
        with self.assertRaisesRegex(
            DesignMaturityError,
            "stale or cross-branch",
        ):
            require_current_phase_gate(advanced, receipt)

        other_branch = _branch(branch_id="option-b")
        other = _maturity(
            DesignPhase.PROGRAMMING,
            branch=other_branch,
        )
        with self.assertRaisesRegex(
            DesignMaturityError,
            "stale or cross-branch",
        ):
            evaluate_forward_phase_gate(
                other,
                request,
            )

    def test_maturity_round_trip_and_operational_binding(self) -> None:
        branch = _branch()
        operational = OperationalMarkovState(
            branch=branch,
            compiler_version="test-compiler",
            phase=DesignPhase.PROGRAMMING.value,
        )
        maturity = DesignMaturityState.from_operational_state(
            operational,
            deliverables=_deliverables(
                DesignPhase.PROGRAMMING,
                branch,
            ),
        )
        maturity.require_exact_operational_state(operational)
        self.assertEqual(
            DesignMaturityState.from_dict(maturity.to_dict()),
            maturity,
        )

        stale = OperationalMarkovState(
            branch=_branch(epoch=5),
            compiler_version="test-compiler",
            phase=DesignPhase.PROGRAMMING.value,
        )
        with self.assertRaisesRegex(
            DesignMaturityError,
            "stale or cross-branch",
        ):
            maturity.require_exact_operational_state(stale)


class BackwardRevisionTests(unittest.TestCase):
    def test_backward_revision_invalidates_only_typed_dependency_closure(
        self,
    ) -> None:
        branch = _branch(epoch=8)
        brief = _deliverables(DesignPhase.RESEARCH_BRIEF, branch)[0]
        program = _deliverables(DesignPhase.PROGRAMMING, branch)[0]
        site = _deliverables(
            DesignPhase.SITE_RESOURCE_COORDINATION,
            branch,
        )[0]
        schematic = _deliverables(
            DesignPhase.SCHEMATIC_DESIGN,
            branch,
        )[0]
        developed = _deliverables(
            DesignPhase.DESIGN_DEVELOPMENT,
            branch,
        )[0]
        maturity = _maturity(
            DesignPhase.DESIGN_DEVELOPMENT,
            branch=branch,
            deliverables=(
                brief,
                program,
                site,
                schematic,
                developed,
            ),
        )
        source = "fact:program-requirement"
        dependencies = (
            DependencyEdge(
                upstream_ref=source,
                downstream_ref=program.ref,
                relation="compiled_into",
                source_ref="test:dependency",
                effect=DependencyEffect.INVALIDATES,
            ),
            DependencyEdge(
                upstream_ref=program.ref,
                downstream_ref=schematic.ref,
                relation="shapes",
                source_ref="test:dependency",
                effect=DependencyEffect.INVALIDATES,
            ),
            DependencyEdge(
                upstream_ref=schematic.ref,
                downstream_ref=developed.ref,
                relation="developed_as",
                source_ref="test:dependency",
                effect=DependencyEffect.INVALIDATES,
            ),
            DependencyEdge(
                upstream_ref=program.ref,
                downstream_ref=site.ref,
                relation="coordinates_with",
                source_ref="test:dependency",
                effect=DependencyEffect.REQUIRES_REVALIDATION,
            ),
            DependencyEdge(
                upstream_ref=source,
                downstream_ref=brief.ref,
                relation="background_only",
                source_ref="test:dependency",
                effect=DependencyEffect.SUPPORTS_ONLY,
            ),
            DependencyEdge(
                upstream_ref="obligation:upstream-review",
                downstream_ref="obligation:downstream-review",
                relation="must_precede",
                source_ref="test:dependency",
                effect=DependencyEffect.BLOCKS,
            ),
        )
        result = compile_backward_revision(
            maturity,
            BackwardRevisionRequest(
                revision_id="program-change-001",
                branch=maturity.branch,
                base_state_digest=maturity.operational_state_digest,
                from_phase=maturity.phase,
                to_phase=DesignPhase.PROGRAMMING,
                changed_refs=(source,),
            ),
            dependencies,
        )
        self.assertEqual(
            set(result.invalidated_deliverable_refs),
            {program.ref, schematic.ref, developed.ref},
        )
        self.assertEqual(
            result.revalidation_required_refs,
            (site.ref,),
        )
        self.assertNotIn(brief.ref, result.invalidated_deliverable_refs)
        self.assertEqual(
            {item.subject_refs[0] for item in result.spawned_obligations},
            {
                program.ref,
                schematic.ref,
                developed.ref,
                site.ref,
            },
        )
        self.assertTrue(
            all(
                item.status is ObligationStatus.OPEN
                for item in result.spawned_obligations
            )
        )
        self.assertEqual(
            type(result).from_dict(result.to_dict()),
            result,
        )
        self.assertFalse(
            any(
                ref.startswith("obligation:")
                for ref in (
                    result.invalidated_deliverable_refs
                    + result.revalidation_required_refs
                )
            )
        )

    def test_backward_revision_requires_earlier_phase_and_exact_base(
        self,
    ) -> None:
        maturity = _maturity(DesignPhase.SCHEMATIC_DESIGN)
        direct = DependencyEdge(
            upstream_ref="fact:changed",
            downstream_ref=maturity.deliverable_refs[0],
            relation="changes",
            source_ref="test:dependency",
            effect=DependencyEffect.INVALIDATES,
        )
        with self.assertRaisesRegex(
            DesignMaturityError,
            "earlier design phase",
        ):
            compile_backward_revision(
                maturity,
                BackwardRevisionRequest(
                    revision_id="not-backward",
                    branch=maturity.branch,
                    base_state_digest=maturity.operational_state_digest,
                    from_phase=maturity.phase,
                    to_phase=maturity.phase,
                    changed_refs=("fact:changed",),
                ),
                (direct,),
            )

        with self.assertRaisesRegex(
            DesignMaturityError,
            "stale or cross-branch",
        ):
            compile_backward_revision(
                maturity,
                BackwardRevisionRequest(
                    revision_id="wrong-branch",
                    branch=_branch(branch_id="option-b"),
                    base_state_digest=maturity.operational_state_digest,
                    from_phase=maturity.phase,
                    to_phase=DesignPhase.PROGRAMMING,
                    changed_refs=("fact:changed",),
                ),
                (direct,),
            )


def _advice(_: ExpertSnapshot) -> ExpertAdvice:
    return ExpertAdvice(summary="Detached phase-local advice.")


class PhaseExpertDiscoveryTests(unittest.TestCase):
    def _snapshot(self) -> ExpertSnapshot:
        return ExpertSnapshot(
            base_state=ProjectVersionRef(
                project_id="phase-contract-test",
                version=3,
                state_sha256=_hash("canonical-base"),
            ),
            program_json=None,
            obligations=(
                ExpertObligation(
                    obligation_id="coordinate-current-design",
                    topic="coordination",
                    statement="Coordinate the current phase.",
                    source_ref="obligation-source:test",
                ),
            ),
            evidence=(
                ExpertEvidence(
                    kind="current_state",
                    evidence_ref="evidence:current-state",
                    summary="Current exact-base state is available.",
                ),
            ),
        )

    def test_discovery_intersects_phase_obligation_evidence_and_metadata(
        self,
    ) -> None:
        registry = ExpertRegistry()
        specs = (
            ExpertSpec(
                expert_id="alpha",
                description="First eligible capability.",
                topics=frozenset({"coordination"}),
                required_evidence_kinds=frozenset({"current_state"}),
            ),
            ExpertSpec(
                expert_id="beta",
                description="Second eligible capability.",
                topics=frozenset({"coordination"}),
            ),
            ExpertSpec(
                expert_id="later",
                description="Capability for a later phase.",
                topics=frozenset({"coordination"}),
            ),
            ExpertSpec(
                expert_id="missing-evidence",
                description="Needs unavailable evidence.",
                topics=frozenset({"coordination"}),
                required_evidence_kinds=frozenset({"survey"}),
            ),
        )
        for spec in reversed(specs):
            registry.register(spec, _advice)
        metadata = {
            "alpha": PhaseExpertMetadata(
                "alpha",
                frozenset({DesignPhase.SCHEMATIC_DESIGN}),
            ),
            "beta": PhaseExpertMetadata(
                "beta",
                frozenset({DesignPhase.SCHEMATIC_DESIGN}),
            ),
            "later": PhaseExpertMetadata(
                "later",
                frozenset({DesignPhase.DESIGN_DEVELOPMENT}),
            ),
            "missing-evidence": PhaseExpertMetadata(
                "missing-evidence",
                frozenset({DesignPhase.SCHEMATIC_DESIGN}),
            ),
        }
        discovered = discover_phase_experts(
            registry,
            self._snapshot(),
            phase=DesignPhase.SCHEMATIC_DESIGN,
            metadata=metadata,
        )
        self.assertEqual(
            tuple(item.expert_id for item in discovered),
            ("alpha", "beta"),
        )

        first_order = validate_architect_selected_expert_order(
            ("alpha", "beta"),
            discovered,
        )
        second_order = validate_architect_selected_expert_order(
            ("beta", "alpha"),
            discovered,
        )
        self.assertEqual(first_order, ("alpha", "beta"))
        self.assertEqual(second_order, ("beta", "alpha"))
        self.assertNotEqual(first_order, second_order)

        with self.assertRaisesRegex(
            PhaseCapabilityError,
            "not discovered",
        ):
            validate_architect_selected_expert_order(
                ("later",),
                discovered,
            )

    def test_registration_order_does_not_change_discovered_set(self) -> None:
        alpha = ExpertSpec(
            expert_id="alpha",
            description="Alpha capability.",
            topics=frozenset({"coordination"}),
        )
        beta = ExpertSpec(
            expert_id="beta",
            description="Beta capability.",
            topics=frozenset({"coordination"}),
        )
        metadata = {
            item.expert_id: PhaseExpertMetadata(
                item.expert_id,
                frozenset({DesignPhase.SCHEMATIC_DESIGN}),
            )
            for item in (alpha, beta)
        }
        ids_by_registration: list[tuple[str, ...]] = []
        for registration in ((alpha, beta), (beta, alpha)):
            registry = ExpertRegistry()
            for item in registration:
                registry.register(item, _advice)
            ids_by_registration.append(
                tuple(
                    item.expert_id
                    for item in discover_phase_experts(
                        registry,
                        self._snapshot(),
                        phase=DesignPhase.SCHEMATIC_DESIGN,
                        metadata=metadata,
                    )
                )
            )
        self.assertEqual(ids_by_registration[0], ids_by_registration[1])

    def test_phase_metadata_rejects_later_phase_deliverable_authority(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            PhaseCapabilityError,
            "disallowed phase",
        ):
            PhaseExpertMetadata(
                expert_id="premature-output",
                allowed_phases=frozenset(
                    {DesignPhase.SCHEMATIC_DESIGN}
                ),
                advisory_deliverable_roles=frozenset(
                    {DeliverableRole.DESIGN_DEVELOPMENT_PACKAGE}
                ),
            )


class FrameworkPurityTests(unittest.TestCase):
    def test_maturity_framework_contains_no_instance_answer_defaults(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        text = "\n".join(
            (root / relative).read_text(encoding="utf-8").lower()
            for relative in (
                "archive/archflow/state/design_maturity.py",
                "archive/archflow/capabilities/phase_gates.py",
            )
        )
        for forbidden in (
            "16x12",
            "pantheon",
            "rotunda",
            "library",
            "marble",
            "concrete",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
