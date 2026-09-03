"""Deterministic coordination over detached design-development advice."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from archive.archflow.capabilities.experts import (
    ExpertEvidence,
    ExpertObligation,
    ExpertRegistry,
    ExpertSnapshot,
)
from archive.archflow.capabilities.phase_gates import (
    PhaseExpertMetadata,
    discover_phase_experts,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.design_portfolio import SelectedBranchHandoff
from archflow.state.developed_design import (
    ArchitectDevelopmentDecision,
    DetachedDevelopmentAdvice,
    DevelopedComponent,
    DevelopedDesignError,
    DevelopedDesignState,
    DevelopmentClaimDisposition,
    DevelopmentCoordinationStatus,
    DevelopmentDependencyImpact,
    DevelopmentDiscipline,
    DevelopmentInvalidationReceipt,
    DevelopmentObligation,
    DevelopmentObligationStatus,
    DevelopmentObligationPriority,
    DevelopmentTransition,
    SelectedSchematicInput,
    canonical_json,
)
from archflow.state.model import StateRef


class DesignDevelopmentCompilationError(DevelopedDesignError):
    """A development step cannot be compiled from its exact current state."""


@dataclass(frozen=True, slots=True)
class DevelopmentExpertPlan:
    state_digest: str
    context_digest: str
    discovered_expert_ids: tuple[str, ...]
    selected_expert_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, field in (
            (self.state_digest, "state_digest"),
            (self.context_digest, "context_digest"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(
                    char not in frozenset("0123456789abcdef")
                    for char in value.lower()
                )
            ):
                raise DesignDevelopmentCompilationError(
                    f"{field} must be a SHA-256 digest"
                )
        for values, field in (
            (self.discovered_expert_ids, "discovered_expert_ids"),
            (self.selected_expert_ids, "selected_expert_ids"),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(item, str) or not item.strip()
                for item in values
            ):
                raise TypeError(f"{field} must be a text tuple")
            if len(values) != len(set(values)):
                raise DesignDevelopmentCompilationError(
                    f"{field} contains duplicates"
                )
        if self.discovered_expert_ids != tuple(
            sorted(self.discovered_expert_ids)
        ):
            raise DesignDevelopmentCompilationError(
                "discovered expert ids must use stable presentation order"
            )
        if not set(self.selected_expert_ids) <= set(
            self.discovered_expert_ids
        ):
            raise DesignDevelopmentCompilationError(
                "selected expert was not discovered"
            )


def initialize_developed_design(
    handoff: SelectedBranchHandoff,
    *,
    obligations: tuple[DevelopmentObligation, ...],
    assumption_refs: tuple[str, ...] = (),
) -> DevelopedDesignState:
    if not isinstance(handoff, SelectedBranchHandoff):
        raise TypeError("handoff must be SelectedBranchHandoff")
    if not isinstance(obligations, tuple) or any(
        not isinstance(item, DevelopmentObligation)
        for item in obligations
    ):
        raise TypeError("obligations contains an invalid item")
    covered = {
        item.discipline
        for item in obligations
        if item.priority is DevelopmentObligationPriority.BLOCKING
        and item.status is not DevelopmentObligationStatus.RESOLVED
    }
    missing = set(DevelopmentDiscipline) - covered
    if missing:
        raise DesignDevelopmentCompilationError(
            "design-development input lacks blocking discipline coverage: "
            f"{sorted(item.value for item in missing)}"
        )
    selected = SelectedSchematicInput.from_handoff(handoff)
    if any(
        selected.ref not in item.dependency_refs
        and selected.option.ref not in item.dependency_refs
        for item in obligations
    ):
        raise DesignDevelopmentCompilationError(
            "every initial obligation must bind the selected schematic"
        )
    return DevelopedDesignState(
        selected_schematic=selected,
        active_phase=DesignPhase.DESIGN_DEVELOPMENT,
        coordination_status=DevelopmentCoordinationStatus.IN_PROGRESS,
        obligations=obligations,
        components=(),
        dependencies=(),
        advice=(),
        decisions=(),
        transitions=(),
        assumption_refs=assumption_refs,
    )


def build_development_expert_snapshot(
    state: DevelopedDesignState,
    *,
    evidence: Sequence[ExpertEvidence] = (),
) -> ExpertSnapshot:
    if not isinstance(state, DevelopedDesignState):
        raise TypeError("state must be DevelopedDesignState")
    if (
        state.active_phase is not DesignPhase.DESIGN_DEVELOPMENT
        or state.coordination_status
        is DevelopmentCoordinationStatus.INVALIDATED
    ):
        raise DesignDevelopmentCompilationError(
            "experts cannot inspect an invalidated or earlier-phase state"
        )
    detached_evidence = tuple(evidence)
    if any(
        not isinstance(item, ExpertEvidence)
        for item in detached_evidence
    ):
        raise TypeError("evidence contains an invalid item")
    open_obligations = tuple(
        item
        for item in state.obligations
        if item.status is not DevelopmentObligationStatus.RESOLVED
    )
    context = {
        "schema": "DevelopmentExpertContext@1",
        "state_digest": state.state_digest,
        "selected_schematic_ref": state.selected_schematic.ref,
        "selected_option_ref": state.selected_schematic.option.ref,
        "selected_topology_signature": (
            state.selected_schematic.option.topology_signature
        ),
        "obligation_refs": [item.ref for item in open_obligations],
        "component_refs": [item.ref for item in state.components],
        "phase": DesignPhase.DESIGN_DEVELOPMENT.value,
        "read_only": True,
    }
    return ExpertSnapshot(
        base_state=StateRef(
            state.project_id,
            state.base.version,
            state.base.require_digest(),
        ),
        program_json=canonical_json(context),
        obligations=tuple(
            ExpertObligation(
                obligation_id=item.obligation_id,
                topic=item.discipline.value,
                statement=item.statement,
                source_ref=item.source_refs[0],
            )
            for item in open_obligations
        ),
        evidence=detached_evidence,
    )


def discover_development_experts(
    registry: ExpertRegistry,
    state: DevelopedDesignState,
    *,
    metadata: Mapping[str, PhaseExpertMetadata],
    evidence: Sequence[ExpertEvidence] = (),
) -> tuple[DevelopmentExpertPlan, ExpertSnapshot]:
    snapshot = build_development_expert_snapshot(
        state,
        evidence=evidence,
    )
    discovered = discover_phase_experts(
        registry,
        snapshot,
        phase=DesignPhase.DESIGN_DEVELOPMENT,
        metadata=metadata,
    )
    context_digest = hashlib.sha256(
        snapshot.program_json.encode("utf-8")
    ).hexdigest()
    return (
        DevelopmentExpertPlan(
            state_digest=state.state_digest,
            context_digest=context_digest,
            discovered_expert_ids=tuple(
                item.expert_id for item in discovered
            ),
            selected_expert_ids=(),
        ),
        snapshot,
    )


def select_development_expert_order(
    plan: DevelopmentExpertPlan,
    selected_expert_ids: Sequence[str],
) -> DevelopmentExpertPlan:
    if not isinstance(plan, DevelopmentExpertPlan):
        raise TypeError("plan must be DevelopmentExpertPlan")

    selected = tuple(selected_expert_ids)
    if any(
        not isinstance(item, str) or not item.strip() for item in selected
    ):
        raise DesignDevelopmentCompilationError(
            "selected expert ids must be non-empty text"
        )
    if len(selected) != len(set(selected)):
        raise DesignDevelopmentCompilationError(
            "selected expert order contains duplicates"
        )
    unavailable = set(selected) - set(plan.discovered_expert_ids)
    if unavailable:
        raise DesignDevelopmentCompilationError(
            f"selected experts were not discovered: {sorted(unavailable)}"
        )
    return replace(plan, selected_expert_ids=selected)


def _claim_conflicts(
    advice: tuple[DetachedDevelopmentAdvice, ...],
) -> tuple[tuple[str, ...], ...]:
    grouped: dict[tuple[str, str], list[object]] = {}
    for item in advice:
        for claim in item.claims:
            grouped.setdefault(
                (claim.subject_ref, claim.attribute),
                [],
            ).append(claim)
    conflicts = []
    for claims in grouped.values():
        if len({item.value_json for item in claims}) > 1:
            conflicts.append(
                tuple(sorted(item.ref for item in claims))
            )
    return tuple(sorted(conflicts))


def compile_development_step(
    state: DevelopedDesignState,
    *,
    plan: DevelopmentExpertPlan,
    advice: tuple[DetachedDevelopmentAdvice, ...],
    decision: ArchitectDevelopmentDecision,
) -> DevelopedDesignState:
    if not isinstance(state, DevelopedDesignState):
        raise TypeError("state must be DevelopedDesignState")
    if state.active_phase is not DesignPhase.DESIGN_DEVELOPMENT or (
        state.coordination_status
        is DevelopmentCoordinationStatus.INVALIDATED
    ):
        raise DesignDevelopmentCompilationError(
            "development step requires an active development state"
        )
    if not isinstance(plan, DevelopmentExpertPlan):
        raise TypeError("plan must be DevelopmentExpertPlan")
    if plan.state_digest != state.state_digest:
        raise DesignDevelopmentCompilationError(
            "expert plan is stale"
        )
    expected_context_digest = hashlib.sha256(
        build_development_expert_snapshot(state).program_json.encode(
            "utf-8"
        )
    ).hexdigest()
    if plan.context_digest != expected_context_digest:
        raise DesignDevelopmentCompilationError(
            "expert context digest is stale or changed"
        )
    if not isinstance(advice, tuple) or any(
        not isinstance(item, DetachedDevelopmentAdvice)
        for item in advice
    ):
        raise TypeError("advice contains an invalid item")
    if not isinstance(decision, ArchitectDevelopmentDecision):
        raise TypeError(
            "decision must be ArchitectDevelopmentDecision"
        )
    advice_ids = tuple(item.advice_id for item in advice)
    expert_ids = tuple(item.expert_id for item in advice)
    if expert_ids != plan.selected_expert_ids:
        raise DesignDevelopmentCompilationError(
            "advice order differs from the Architect-selected expert order"
        )
    if advice_ids != decision.selected_advice_ids:
        raise DesignDevelopmentCompilationError(
            "decision does not cite the exact ordered advice"
        )
    if decision.state_digest != state.state_digest or any(
        item.state_digest != state.state_digest for item in advice
    ):
        raise DesignDevelopmentCompilationError(
            "advice or decision is stale"
        )
    if len(advice_ids) != len(set(advice_ids)):
        raise DesignDevelopmentCompilationError(
            "advice ids contain duplicates"
        )

    claims = tuple(
        claim for item in advice for claim in item.claims
    )
    claim_by_ref = {item.ref: item for item in claims}
    resolution_by_ref = {
        item.claim_ref: item for item in decision.claim_resolutions
    }
    if set(resolution_by_ref) != set(claim_by_ref):
        raise DesignDevelopmentCompilationError(
            "Architect must explicitly resolve every selected claim"
        )
    adopted = {
        ref
        for ref, resolution in resolution_by_ref.items()
        if resolution.disposition
        is DevelopmentClaimDisposition.ADOPTED
    }
    deferred = {
        ref
        for ref, resolution in resolution_by_ref.items()
        if resolution.disposition
        is DevelopmentClaimDisposition.DEFERRED
    }
    obligation_dependencies = tuple(
        frozenset(item.dependency_refs)
        for item in decision.new_obligations
        if item.status is not DevelopmentObligationStatus.RESOLVED
    )
    conflicts = _claim_conflicts(advice)
    for group in conflicts:
        group_set = set(group)
        adopted_count = len(group_set & adopted)
        if adopted_count > 1:
            raise DesignDevelopmentCompilationError(
                "conflicting claims cannot both be adopted"
            )
        if adopted_count == 1:
            if group_set - adopted != {
                ref
                for ref in group_set
                if resolution_by_ref[ref].disposition
                is DevelopmentClaimDisposition.REJECTED
            }:
                raise DesignDevelopmentCompilationError(
                    "conflict requires one adopted and the rest rejected"
                )
        elif not any(group_set <= deps for deps in obligation_dependencies):
            raise DesignDevelopmentCompilationError(
                "unresolved conflict requires a traceable obligation"
            )
    for claim_ref in deferred:
        if not any(
            claim_ref in dependencies
            for dependencies in obligation_dependencies
        ):
            raise DesignDevelopmentCompilationError(
                "deferred claim requires a traceable obligation"
            )

    existing_components = {
        item.component_id: item for item in state.components
    }
    next_components = dict(existing_components)
    for component in decision.component_updates:
        existing = existing_components.get(component.component_id)
        expected_revision = 0 if existing is None else existing.revision + 1
        if component.revision != expected_revision:
            raise DesignDevelopmentCompilationError(
                "component revision does not follow its exact predecessor"
            )
        if existing is not None and (
            existing.discipline is not component.discipline
        ):
            raise DesignDevelopmentCompilationError(
                "component discipline cannot change in place"
            )
        if not {
            state.selected_schematic.ref,
            state.selected_schematic.option.ref,
        } & set(component.schematic_dependency_refs):
            raise DesignDevelopmentCompilationError(
                "component is not bound to the selected schematic"
            )
        source_claim_refs = {
            ref
            for attribute in component.attributes
            for ref in attribute.source_claim_refs
        }
        if not source_claim_refs <= adopted:
            raise DesignDevelopmentCompilationError(
                "component uses a claim not adopted by the Architect"
            )
        next_components[component.component_id] = component

    existing_obligations = {
        item.obligation_id: item for item in state.obligations
    }
    if set(decision.resolved_obligation_ids) - set(
        existing_obligations
    ):
        raise DesignDevelopmentCompilationError(
            "decision resolves an unknown obligation"
        )
    new_ids = {item.obligation_id for item in decision.new_obligations}
    if new_ids & set(existing_obligations):
        raise DesignDevelopmentCompilationError(
            "new obligation id already exists"
        )
    next_obligations = tuple(
        replace(
            item,
            status=DevelopmentObligationStatus.RESOLVED,
        )
        if item.obligation_id in decision.resolved_obligation_ids
        else item
        for item in state.obligations
    ) + decision.new_obligations

    next_dependencies = {
        item.dependency_id: item for item in state.dependencies
    }
    for dependency in decision.dependencies:
        if (
            dependency.target_component_id not in next_components
            or next_components[
                dependency.target_component_id
            ].discipline
            is not dependency.target_discipline
        ):
            raise DesignDevelopmentCompilationError(
                "dependency target is absent or has another discipline"
            )
        existing = next_dependencies.get(dependency.dependency_id)
        if existing is not None and existing != dependency:
            raise DesignDevelopmentCompilationError(
                "dependency id cannot be silently replaced"
            )
        next_dependencies[dependency.dependency_id] = dependency

    blocking = tuple(
        item
        for item in next_obligations
        if item.priority is DevelopmentObligationPriority.BLOCKING
        and item.status is not DevelopmentObligationStatus.RESOLVED
    )
    status = (
        DevelopmentCoordinationStatus.IN_PROGRESS
        if blocking
        else DevelopmentCoordinationStatus.COORDINATED
    )
    transition = DevelopmentTransition(
        sequence=len(state.transitions) + 1,
        predecessor_state_digest=state.state_digest,
        decision_id=decision.decision_id,
        selected_advice_ids=decision.selected_advice_ids,
        conflict_groups=conflicts,
        changed_component_ids=tuple(
            sorted(
                item.component_id
                for item in decision.component_updates
            )
        ),
    )
    return DevelopedDesignState(
        selected_schematic=state.selected_schematic,
        active_phase=DesignPhase.DESIGN_DEVELOPMENT,
        coordination_status=status,
        obligations=next_obligations,
        components=tuple(
            sorted(
                next_components.values(),
                key=lambda item: item.component_id,
            )
        ),
        dependencies=tuple(
            sorted(
                next_dependencies.values(),
                key=lambda item: item.dependency_id,
            )
        ),
        advice=state.advice + advice,
        decisions=state.decisions + (decision,),
        transitions=state.transitions + (transition,),
        assumption_refs=state.assumption_refs,
        latest_invalidation=state.latest_invalidation,
    )


def invalidate_developed_design(
    state: DevelopedDesignState,
    *,
    changed_schematic_refs: tuple[str, ...],
    receipt_id: str,
    evidence_refs: tuple[str, ...],
) -> DevelopedDesignState:
    if not isinstance(state, DevelopedDesignState):
        raise TypeError("state must be DevelopedDesignState")
    if (
        state.coordination_status
        is DevelopmentCoordinationStatus.INVALIDATED
    ):
        raise DesignDevelopmentCompilationError(
            "developed design is already invalidated"
        )
    if not changed_schematic_refs:
        raise DesignDevelopmentCompilationError(
            "invalidation requires changed schematic refs"
        )
    changed = set(changed_schematic_refs)
    impacts = tuple(
        item
        for item in state.dependencies
        if item.source_ref in changed
    )
    if not impacts:
        raise DesignDevelopmentCompilationError(
            "schematic change has no registered development dependency"
        )
    invalidated_ids = {
        item.target_component_id
        for item in impacts
        if item.impact is DevelopmentDependencyImpact.INVALIDATE
    }
    affected_disciplines = tuple(
        sorted(
            {item.target_discipline for item in impacts},
            key=lambda item: item.value,
        )
    )
    required_phase = (
        DesignPhase.SCHEMATIC_DESIGN
        if any(
            item.return_phase is DesignPhase.SCHEMATIC_DESIGN
            for item in impacts
        )
        else DesignPhase.DESIGN_DEVELOPMENT
    )
    preserved_ids = tuple(
        sorted(
            item.component_id
            for item in state.components
            if item.component_id not in invalidated_ids
        )
    )
    receipt = DevelopmentInvalidationReceipt(
        receipt_id=receipt_id,
        predecessor_state_digest=state.state_digest,
        selected_schematic_ref=state.selected_schematic.ref,
        changed_schematic_refs=changed_schematic_refs,
        invalidated_component_ids=tuple(sorted(invalidated_ids)),
        preserved_component_ids=preserved_ids,
        affected_disciplines=affected_disciplines,
        required_return_phase=required_phase,
        evidence_refs=evidence_refs,
    )
    obligations = tuple(
        replace(item, status=DevelopmentObligationStatus.OPEN)
        if item.discipline in affected_disciplines
        else item
        for item in state.obligations
    )
    return DevelopedDesignState(
        selected_schematic=state.selected_schematic,
        active_phase=required_phase,
        coordination_status=DevelopmentCoordinationStatus.INVALIDATED,
        obligations=obligations,
        components=tuple(
            item
            for item in state.components
            if item.component_id not in invalidated_ids
        ),
        dependencies=tuple(
            item
            for item in state.dependencies
            if item.target_component_id not in invalidated_ids
        ),
        advice=state.advice,
        decisions=state.decisions,
        transitions=state.transitions,
        assumption_refs=state.assumption_refs,
        latest_invalidation=receipt,
    )


def resume_development_after_selection(
    state: DevelopedDesignState,
    *,
    replacement_handoff: SelectedBranchHandoff,
) -> DevelopedDesignState:
    if not isinstance(state, DevelopedDesignState):
        raise TypeError("state must be DevelopedDesignState")
    if (
        state.coordination_status
        is not DevelopmentCoordinationStatus.INVALIDATED
        or state.latest_invalidation is None
    ):
        raise DesignDevelopmentCompilationError(
            "only an invalidated state can resume"
        )
    replacement = SelectedSchematicInput.from_handoff(
        replacement_handoff
    )
    if (
        replacement.project_id != state.project_id
        or replacement.run_id != state.run_id
        or replacement.base != state.base
    ):
        raise DesignDevelopmentCompilationError(
            "replacement selection belongs to another exact-base run"
        )
    if replacement.revision == state.selected_schematic.revision:
        raise DesignDevelopmentCompilationError(
            "replacement selection did not change revision"
        )
    previous_refs = {
        state.selected_schematic.ref,
        state.selected_schematic.option.ref,
    }

    def rebind_ref(ref: str) -> str:
        if ref == state.selected_schematic.ref:
            return replacement.ref
        if ref == state.selected_schematic.option.ref:
            return replacement.option.ref
        return ref

    rebound_components = tuple(
        replace(
            component,
            revision=component.revision + 1,
            schematic_dependency_refs=tuple(
                rebind_ref(ref)
                for ref in component.schematic_dependency_refs
            ),
        )
        if previous_refs & set(component.schematic_dependency_refs)
        else component
        for component in state.components
    )
    component_ref_rebind = {
        previous.ref: current.ref
        for previous, current in zip(
            state.components,
            rebound_components,
            strict=True,
        )
        if previous.ref != current.ref
    }

    def rebind_dependency_ref(ref: str) -> str:
        rebound = rebind_ref(ref)
        return component_ref_rebind.get(rebound, rebound)

    rebound_obligations = tuple(
        replace(
            obligation,
            dependency_refs=tuple(
                rebind_dependency_ref(ref)
                for ref in obligation.dependency_refs
            ),
        )
        for obligation in state.obligations
    )
    rebound_dependencies = tuple(
        replace(
            dependency,
            source_ref=rebind_dependency_ref(dependency.source_ref),
        )
        for dependency in state.dependencies
    )
    return DevelopedDesignState(
        selected_schematic=replacement,
        active_phase=DesignPhase.DESIGN_DEVELOPMENT,
        coordination_status=DevelopmentCoordinationStatus.IN_PROGRESS,
        obligations=rebound_obligations,
        components=rebound_components,
        dependencies=rebound_dependencies,
        advice=state.advice,
        decisions=state.decisions,
        transitions=state.transitions,
        assumption_refs=state.assumption_refs,
        latest_invalidation=state.latest_invalidation,
    )
