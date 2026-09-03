from __future__ import annotations

import unittest

from archive.archflow.capabilities.design_development import (
    DesignDevelopmentCompilationError,
    compile_development_step,
    discover_development_experts,
    initialize_developed_design,
    invalidate_developed_design,
    resume_development_after_selection,
    select_development_expert_order,
)
from archive.archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertRegistry,
    ExpertSpec,
)
from archive.archflow.capabilities.phase_gates import PhaseExpertMetadata
from archflow.project.refs import RunRef
from archflow.state.developed_design import ArchitectDevelopmentDecision, DetachedDevelopmentAdvice, DevelopedAttribute, DevelopedComponent, DevelopedDesignState, DevelopmentClaim, DevelopmentClaimDisposition, DevelopmentClaimResolution, DevelopmentCoordinationStatus, DevelopmentDependency, DevelopmentDependencyImpact, DevelopmentDiscipline, DevelopmentObligation, DevelopmentObligationPriority, DevelopmentObligationStatus
from archflow.state.stage_workflow import DesignPhase
from archflow.state.design_portfolio import compile_selected_branch_handoff, park_branch, revise_branch, select_branch
from tests.test_design_portfolio import (
    DECISION,
    EVIDENCE,
    _option,
    _portfolio,
    _run,
    _transition_kwargs,
)


OPENING_REF = "schematic-part:opening"
SURFACE_REF = "schematic-part:surface"


def _selected_handoff(
    run: RunRef | None = None,
):
    portfolio = _portfolio(run)
    portfolio = select_branch(
        portfolio,
        branch_id="branch-a",
        **_transition_kwargs(
            portfolio,
            transition_id="select-for-development",
            authority_id="user-owner",
        ),
    )
    branch = portfolio.branch("branch-a")
    return (
        portfolio,
        compile_selected_branch_handoff(
            portfolio,
            expected_portfolio_digest=portfolio.portfolio_digest,
            expected_revision_digest=branch.head.revision_digest,
        ),
    )


def _obligations(handoff) -> tuple[DevelopmentObligation, ...]:
    return tuple(
        DevelopmentObligation(
            obligation_id=f"coordinate-{discipline.value}",
            discipline=discipline,
            statement=(
                f"Coordinate the project-authored {discipline.value} "
                "requirements against the selected schematic."
            ),
            priority=DevelopmentObligationPriority.BLOCKING,
            status=DevelopmentObligationStatus.OPEN,
            source_refs=(EVIDENCE,),
            dependency_refs=(
                f"selected-schematic:{handoff.branch_id}:"
                f"{handoff.revision.revision_digest}",
            ),
        )
        for discipline in DevelopmentDiscipline
    )


def _state(run: RunRef | None = None):
    portfolio, handoff = _selected_handoff(run)
    return (
        portfolio,
        handoff,
        initialize_developed_design(
            handoff,
            obligations=_obligations(handoff),
            assumption_refs=("project-assumption:coordination-basis",),
        ),
    )


def _registry():
    registry = ExpertRegistry()
    metadata = {}
    for discipline in DevelopmentDiscipline:
        expert_id = f"expert.{discipline.value}"
        registry.register(
            ExpertSpec(
                expert_id=expert_id,
                description=(
                    f"Read-only {discipline.value} development review."
                ),
                topics=frozenset({discipline.value}),
            ),
            lambda snapshot: ExpertAdvice(summary="Detached advice."),
        )
        metadata[expert_id] = PhaseExpertMetadata(
            expert_id=expert_id,
            allowed_phases=frozenset(
                {DesignPhase.DESIGN_DEVELOPMENT}
            ),
        )
    return registry, metadata


def _claim(
    state: DevelopedDesignState,
    discipline: DevelopmentDiscipline,
    *,
    value: object,
    claim_id: str,
) -> DevelopmentClaim:
    import json

    return DevelopmentClaim(
        claim_id=claim_id,
        expert_id=f"expert.{discipline.value}",
        discipline=discipline,
        subject_ref=OPENING_REF,
        attribute="clearance_strategy",
        value_json=json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        rationale=f"{discipline.value} rationale.",
        evidence_refs=(EVIDENCE,),
    )


def _advice(
    state: DevelopedDesignState,
    discipline: DevelopmentDiscipline,
    claim: DevelopmentClaim,
) -> DetachedDevelopmentAdvice:
    return DetachedDevelopmentAdvice(
        advice_id=f"advice-{discipline.value}",
        expert_id=f"expert.{discipline.value}",
        discipline=discipline,
        state_digest=state.state_digest,
        claims=(claim,),
        suggested_obligations=(),
        summary=f"Detached {discipline.value} advice.",
        evidence_refs=(EVIDENCE,),
    )


def _plan_and_advice(
    state: DevelopedDesignState,
    order: tuple[DevelopmentDiscipline, ...] = (
        DevelopmentDiscipline.STRUCTURE_SUPPORT,
        DevelopmentDiscipline.CIRCULATION,
    ),
):
    registry, metadata = _registry()
    plan, _ = discover_development_experts(
        registry,
        state,
        metadata=metadata,
    )
    plan = select_development_expert_order(
        plan,
        tuple(f"expert.{item.value}" for item in order),
    )
    claims = {
        DevelopmentDiscipline.STRUCTURE_SUPPORT: _claim(
            state,
            DevelopmentDiscipline.STRUCTURE_SUPPORT,
            value={"support_offset": 1},
            claim_id="structure-opening",
        ),
        DevelopmentDiscipline.CIRCULATION: _claim(
            state,
            DevelopmentDiscipline.CIRCULATION,
            value={"route_clearance": 2},
            claim_id="circulation-opening",
        ),
    }
    advice = tuple(
        _advice(state, item, claims[item]) for item in order
    )
    return plan, advice, claims


def _component(
    state: DevelopedDesignState,
    *,
    component_id: str,
    discipline: DevelopmentDiscipline,
    source_claim_refs: tuple[str, ...],
    schematic_ref: str,
) -> DevelopedComponent:
    return DevelopedComponent(
        component_id=component_id,
        revision=0,
        discipline=discipline,
        attributes=(
            DevelopedAttribute(
                key="project_resolution",
                value_json='{"status":"coordinated"}',
                source_claim_refs=source_claim_refs,
                evidence_refs=(EVIDENCE,),
            ),
        ),
        schematic_dependency_refs=(
            state.selected_schematic.ref,
            schematic_ref,
        ),
        requirement_refs=(
            f"development-obligation:coordinate-{discipline.value}",
        ),
        evidence_refs=(EVIDENCE,),
    )


def _coordinated_state(
    run: RunRef | None = None,
    *,
    order: tuple[DevelopmentDiscipline, ...] = (
        DevelopmentDiscipline.STRUCTURE_SUPPORT,
        DevelopmentDiscipline.CIRCULATION,
    ),
    include_material: bool = True,
):
    portfolio, handoff, state = _state(run)
    plan, advice, claims = _plan_and_advice(state, order)
    structure_claim = claims[DevelopmentDiscipline.STRUCTURE_SUPPORT]
    circulation_claim = claims[DevelopmentDiscipline.CIRCULATION]
    structure = _component(
        state,
        component_id="primary-support",
        discipline=DevelopmentDiscipline.STRUCTURE_SUPPORT,
        source_claim_refs=(structure_claim.ref,),
        schematic_ref=OPENING_REF,
    )
    material = _component(
        state,
        component_id="primary-surface",
        discipline=DevelopmentDiscipline.MATERIALS,
        source_claim_refs=(),
        schematic_ref=SURFACE_REF,
    )
    component_updates = (
        (structure, material) if include_material else (structure,)
    )
    dependencies = (
        (
            DevelopmentDependency(
                dependency_id="opening-to-support",
                source_ref=OPENING_REF,
                target_component_id=structure.component_id,
                target_discipline=structure.discipline,
                impact=DevelopmentDependencyImpact.INVALIDATE,
                return_phase=DesignPhase.SCHEMATIC_DESIGN,
                evidence_refs=(EVIDENCE,),
            ),
            DevelopmentDependency(
                dependency_id="surface-to-material",
                source_ref=SURFACE_REF,
                target_component_id=material.component_id,
                target_discipline=material.discipline,
                impact=DevelopmentDependencyImpact.RECHECK,
                return_phase=DesignPhase.DESIGN_DEVELOPMENT,
                evidence_refs=(EVIDENCE,),
            ),
        )
        if include_material
        else (
            DevelopmentDependency(
                dependency_id="opening-to-support",
                source_ref=OPENING_REF,
                target_component_id=structure.component_id,
                target_discipline=structure.discipline,
                impact=DevelopmentDependencyImpact.INVALIDATE,
                return_phase=DesignPhase.SCHEMATIC_DESIGN,
                evidence_refs=(EVIDENCE,),
            ),
        )
    )
    advisory_dependency = (
        material.ref
        if include_material
        else state.selected_schematic.ref
    )
    decision = ArchitectDevelopmentDecision(
        decision_id=f"integrate-{'-'.join(item.value for item in order)}",
        state_digest=state.state_digest,
        selected_advice_ids=tuple(item.advice_id for item in advice),
        claim_resolutions=tuple(
            DevelopmentClaimResolution(
                claim_ref=claim.ref,
                disposition=(
                    DevelopmentClaimDisposition.ADOPTED
                    if claim is structure_claim
                    else DevelopmentClaimDisposition.REJECTED
                ),
                rationale=(
                    "Architect retained the support response and rejected "
                    "the conflicting route claim with explicit trade-off."
                ),
            )
            for claim in (
                structure_claim,
                circulation_claim,
            )
        ),
        component_updates=component_updates,
        dependencies=dependencies,
        new_obligations=(
            DevelopmentObligation(
                obligation_id="observe-future-material-choice",
                discipline=DevelopmentDiscipline.MATERIALS,
                statement=(
                    "Keep the project-specific final material choice open."
                ),
                priority=DevelopmentObligationPriority.ADVISORY,
                status=DevelopmentObligationStatus.OPEN,
                source_refs=(EVIDENCE,),
                dependency_refs=(advisory_dependency,),
            ),
        ),
        resolved_obligation_ids=tuple(
            item.obligation_id
            for item in state.obligations
            if include_material
            or item.discipline is not DevelopmentDiscipline.MATERIALS
        ),
        authority_id="architect-lead",
        decision_ref=DECISION,
        rationale=(
            "Architect integrated detached advice without automatic "
            "consensus."
        ),
        evidence_refs=(EVIDENCE,),
    )
    coordinated = compile_development_step(
        state,
        plan=plan,
        advice=advice,
        decision=decision,
    )
    return portfolio, handoff, state, coordinated


class DesignDevelopmentTests(unittest.TestCase):
    def test_selected_exact_base_and_six_discipline_coverage_required(
        self,
    ) -> None:
        _, handoff = _selected_handoff()
        with self.assertRaises(TypeError):
            initialize_developed_design(
                handoff.option,
                obligations=(),
            )
        with self.assertRaisesRegex(
            DesignDevelopmentCompilationError,
            "lacks blocking discipline coverage",
        ):
            initialize_developed_design(
                handoff,
                obligations=_obligations(handoff)[:-1],
            )

    def test_expert_discovery_is_phase_filtered_and_order_is_architect_chosen(
        self,
    ) -> None:
        _, _, state = _state()
        registry, metadata = _registry()
        excluded_id = "expert.materials"
        metadata[excluded_id] = PhaseExpertMetadata(
            expert_id=excluded_id,
            allowed_phases=frozenset({DesignPhase.SCHEMATIC_DESIGN}),
        )
        plan, snapshot = discover_development_experts(
            registry,
            state,
            metadata=metadata,
        )

        self.assertNotIn(excluded_id, plan.discovered_expert_ids)
        first = select_development_expert_order(
            plan,
            (
                "expert.circulation",
                "expert.structure_support",
            ),
        )
        second = select_development_expert_order(
            plan,
            (
                "expert.structure_support",
                "expert.circulation",
            ),
        )
        self.assertNotEqual(
            first.selected_expert_ids,
            second.selected_expert_ids,
        )
        self.assertIn(
            state.selected_schematic.ref,
            snapshot.program_json,
        )
        self.assertNotIn("candidate", snapshot.program_json.lower())

    def test_conflict_needs_architect_tradeoff_or_open_obligation(
        self,
    ) -> None:
        _, _, state = _state()
        plan, advice, claims = _plan_and_advice(state)
        deferred = ArchitectDevelopmentDecision(
            decision_id="defer-hidden-conflict",
            state_digest=state.state_digest,
            selected_advice_ids=tuple(
                item.advice_id for item in advice
            ),
            claim_resolutions=tuple(
                DevelopmentClaimResolution(
                    claim_ref=item.ref,
                    disposition=DevelopmentClaimDisposition.DEFERRED,
                    rationale="Architect has not selected a response.",
                )
                for item in claims.values()
            ),
            component_updates=(),
            dependencies=(),
            new_obligations=(),
            resolved_obligation_ids=(),
            authority_id="architect-lead",
            decision_ref=DECISION,
            rationale="No hidden consensus is allowed.",
            evidence_refs=(EVIDENCE,),
        )
        with self.assertRaisesRegex(
            DesignDevelopmentCompilationError,
            "requires a traceable obligation",
        ):
            compile_development_step(
                state,
                plan=plan,
                advice=advice,
                decision=deferred,
            )

    def test_coordinated_state_keeps_advisory_unknown_and_no_mcp_authority(
        self,
    ) -> None:
        _, _, _, coordinated = _coordinated_state()

        self.assertEqual(
            coordinated.coordination_status,
            DevelopmentCoordinationStatus.COORDINATED,
        )
        self.assertTrue(
            any(
                item.status is DevelopmentObligationStatus.OPEN
                and item.priority
                is DevelopmentObligationPriority.ADVISORY
                for item in coordinated.obligations
            )
        )
        payload = coordinated.to_dict()
        self.assertFalse(payload["candidate_created"])
        self.assertFalse(payload["mcp_execution_authority"])
        self.assertIsNone(payload["hard_usability_verdict"])
        self.assertFalse(payload["canonical_write_authority"])
        self.assertTrue(
            all(
                item.to_dict()["mutation_authority"] is False
                for item in coordinated.advice
            )
        )
        self.assertEqual(
            DevelopedDesignState.from_dict(payload),
            coordinated,
        )

    def test_coordinated_state_cannot_omit_selected_components(self) -> None:
        _, _, _, coordinated = _coordinated_state()
        payload = coordinated.to_dict()
        payload["components"] = payload["components"][:-1]

        with self.assertRaisesRegex(
            ValueError,
            "omitted selected semantic leaves",
        ):
            DevelopedDesignState.from_dict(payload)

    def test_schematic_change_invalidates_only_dependency_closure(
        self,
    ) -> None:
        portfolio, _, _, coordinated = _coordinated_state()
        with self.assertRaisesRegex(
            DesignDevelopmentCompilationError,
            "no registered development dependency",
        ):
            invalidate_developed_design(
                coordinated,
                changed_schematic_refs=("schematic-part:unrelated",),
                receipt_id="unrelated-change",
                evidence_refs=(EVIDENCE,),
            )
        invalidated = invalidate_developed_design(
            coordinated,
            changed_schematic_refs=(OPENING_REF,),
            receipt_id="opening-change",
            evidence_refs=(EVIDENCE,),
        )

        self.assertEqual(
            invalidated.coordination_status,
            DevelopmentCoordinationStatus.INVALIDATED,
        )
        self.assertEqual(
            invalidated.active_phase,
            DesignPhase.SCHEMATIC_DESIGN,
        )
        self.assertEqual(
            invalidated.latest_invalidation.invalidated_component_ids,
            ("primary-support",),
        )
        self.assertEqual(
            invalidated.latest_invalidation.preserved_component_ids,
            ("primary-surface",),
        )

        portfolio = park_branch(
            portfolio,
            branch_id="branch-a",
            **_transition_kwargs(
                portfolio,
                transition_id="release-before-revision",
                authority_id="user-owner",
            ),
        )
        portfolio = revise_branch(
            portfolio,
            branch_id="branch-a",
            revision_id="branch-a-r1",
            option=_option("branch-a-r1", shape=2),
            **_transition_kwargs(
                portfolio,
                transition_id="revise-selected-schematic",
            ),
        )
        portfolio = select_branch(
            portfolio,
            branch_id="branch-a",
            **_transition_kwargs(
                portfolio,
                transition_id="reselect-after-revision",
                authority_id="user-owner",
            ),
        )
        replacement = compile_selected_branch_handoff(
            portfolio,
            expected_portfolio_digest=portfolio.portfolio_digest,
            expected_revision_digest=(
                portfolio.branch("branch-a").head.revision_digest
            ),
        )
        previous_surface_ref = invalidated.components[0].ref
        resumed = resume_development_after_selection(
            invalidated,
            replacement_handoff=replacement,
        )
        self.assertEqual(
            resumed.coordination_status,
            DevelopmentCoordinationStatus.IN_PROGRESS,
        )
        self.assertEqual(
            tuple(item.component_id for item in resumed.components),
            ("primary-surface",),
        )
        self.assertEqual(resumed.components[0].revision, 1)
        self.assertIn(
            resumed.selected_schematic.ref,
            resumed.components[0].schematic_dependency_refs,
        )
        self.assertTrue(
            all(
                resumed.selected_schematic.ref
                in item.dependency_refs
                or resumed.selected_schematic.option.ref
                in item.dependency_refs
                or not {
                    invalidated.selected_schematic.ref,
                    invalidated.selected_schematic.option.ref,
                }
                & set(item.dependency_refs)
                for item in resumed.obligations
            )
        )
        self.assertTrue(
            all(
                previous_surface_ref not in item.dependency_refs
                for item in resumed.obligations
            )
        )
        self.assertTrue(
            any(
                resumed.components[0].ref in item.dependency_refs
                for item in resumed.obligations
            )
        )
        self.assertIsNotNone(resumed.latest_invalidation)

    def test_non_isomorphic_cases_do_not_share_components_or_order(
        self,
    ) -> None:
        _, _, _, first = _coordinated_state()
        _, _, _, second = _coordinated_state(
            order=(
                DevelopmentDiscipline.CIRCULATION,
                DevelopmentDiscipline.STRUCTURE_SUPPORT,
            ),
            include_material=False,
        )

        self.assertNotEqual(
            first.transitions[0].selected_advice_ids,
            second.transitions[0].selected_advice_ids,
        )
        self.assertNotEqual(
            tuple(item.component_id for item in first.components),
            tuple(item.component_id for item in second.components),
        )
        self.assertNotEqual(
            first.state_digest,
            second.state_digest,
        )


if __name__ == "__main__":
    unittest.main()
