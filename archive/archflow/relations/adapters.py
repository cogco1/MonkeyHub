"""Read-only adapters from the architectural relation graph to existing views."""

from __future__ import annotations

from archflow.relations.contracts import (
    ArchitecturalRelation,
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
    ImpactEffect,
    RelationProjection,
)
from archive.archflow.relations.traversal import (
    MAX_TRAVERSAL_ITEMS,
    UNIVERSAL_SCENARIO_REF,
    RelationTraversalError,
    compile_relation_view,
)
from archflow.state.operational_state import DependencyEdge, DependencyEffect
from archive.archflow.validation.assembly import RelationshipKind, RelationshipRequirement


def _role_refs(relation: ArchitecturalRelation, role: str) -> tuple[str, ...]:
    return tuple(item.node_ref for item in relation.participants if item.role == role)


def compile_dependency_edges(
    graph: ArchitecturalRelationGraph,
    *,
    scenario_ref: str,
) -> tuple[DependencyEdge, ...]:
    """Project explicit impact rules into existing OperationalMarkovState edges."""

    view = compile_relation_view(
        graph,
        projection=RelationProjection.IMPACT,
        scenario_ref=scenario_ref,
    )
    relations = {item.ref: item for item in graph.relations}
    effect_map = {
        ImpactEffect.REVALIDATE: DependencyEffect.REQUIRES_REVALIDATION,
        ImpactEffect.INVALIDATE: DependencyEffect.INVALIDATES,
    }
    edges = []
    for arc in view.arcs:
        if arc.impact_effect not in effect_map:
            raise RelationTraversalError(
                "impact projection contains a non-propagating effect"
            )
        relation = relations[arc.relation_ref]
        edges.append(
            DependencyEdge(
                upstream_ref=arc.source_ref,
                downstream_ref=arc.target_ref,
                relation=f"architectural.{relation.kind.value}",
                source_ref=relation.ref,
                effect=effect_map[arc.impact_effect],
            )
        )
    result = tuple(sorted(edges, key=lambda item: item.identity))
    if len({item.identity for item in result}) != len(result):
        raise RelationTraversalError(
            "architectural impact projection produced duplicate dependency edges"
        )
    return result


def compile_support_requirements(
    graph: ArchitecturalRelationGraph,
    *,
    scenario_ref: str,
) -> tuple[RelationshipRequirement, ...]:
    """Compile exact directed SUPPORT relations into Assembly requirements.

    A hyper-relation with multiple ``supported`` or ``supporter`` participants
    expands deterministically into one binary requirement per ordered pair.
    ``LOAD_TRANSFER`` is intentionally excluded: analysis-level transfer is
    not evidence of direct geometric bearing contact.
    """

    scenario_refs = {UNIVERSAL_SCENARIO_REF, scenario_ref}
    requirements = []
    for relation in graph.relations:
        if (
            relation.kind is not ArchitecturalRelationKind.SUPPORT
            or relation.scenario_ref not in scenario_refs
        ):
            continue
        supported = _role_refs(relation, "supported")
        supporters = _role_refs(relation, "supporter")
        if not supported or not supporters:
            raise RelationTraversalError(
                f"support relation {relation.relation_id} lacks endpoint roles"
            )
        pair_index = 0
        for first in supported:
            for second in supporters:
                if first == second:
                    continue
                if len(requirements) >= MAX_TRAVERSAL_ITEMS:
                    raise RelationTraversalError(
                        "support requirements exceed bounded pair expansion"
                    )
                requirements.append(
                    RelationshipRequirement(
                        requirement_id=(
                            f"{relation.relation_id}-{pair_index:04d}"
                        ),
                        kind=RelationshipKind.SUPPORT,
                        subject_refs=(first, second),
                        evidence_refs=relation.evidence_refs,
                        authority_refs=relation.authority_refs,
                    )
                )
                pair_index += 1
        if pair_index == 0:
            raise RelationTraversalError(
                f"support relation {relation.relation_id} has no valid endpoint pair"
            )
    return tuple(sorted(requirements, key=lambda item: item.requirement_id))


__all__ = [
    "compile_dependency_edges",
    "compile_support_requirements",
]
