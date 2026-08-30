"""Compile validator profiles into exact stage-check requirements.

These adapters are requirement-first: they derive the denominator and basis
from an immutable validator profile before the validator runs.  They do not
inspect a receipt and therefore cannot legitimize an incomplete check after
the fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
)
from archflow.control.relation_promotion import (
    RelationPromotionReceipt,
    RelationPromotionResult,
)
from archflow.materials.binding import MaterialBindingProfile
from archflow.relations.checks import (
    RELATION_COVERAGE_CHECKER_ID,
    relation_coverage_check_id,
)
from archflow.relations.contracts import ArchitecturalRelationGraph
from archflow.relations.authoring import (
    RelationAuthoringCompilation,
    RelationAuthoringCompilationStatus,
    RelationAuthoringContext,
)
from archflow.relations.coverage import (
    SemanticKindRelationPolicy,
    compile_requirement_slots,
)
from archflow.validation.assembly import AssemblyProfile
from archflow.validation.cad_readback import CadReadbackProfile
from archflow.validation.check_bridges import (
    ComponentLineageCheckProfile,
    SpatialLayoutCheckProfile,
)

if TYPE_CHECKING:
    from archflow.control.stage_subjects import StageSubjectInventory


def assembly_stage_requirement(
    profile: AssemblyProfile,
) -> StageCheckRequirement:
    """Require the exact assembly denominator and its explicit authorities."""

    if not isinstance(profile, AssemblyProfile):
        raise TypeError("profile must be an AssemblyProfile")
    authority_refs = tuple(
        sorted(
            {
                ref
                for requirement in profile.requirements
                for ref in requirement.authority_refs
            }
            | {
                ref
                for obligation in profile.coverage_manifest.obligations
                for ref in obligation.authority_refs
            }
            | {
                ref
                for candidate in profile.coverage_manifest.relation_candidates
                for ref in candidate.authority_refs
            }
        )
    )
    source_refs = tuple(
        sorted(
            {
                ref
                for requirement in profile.requirements
                for ref in requirement.evidence_refs
            }
            | {
                ref
                for obligation in profile.coverage_manifest.obligations
                for ref in obligation.evidence_refs
            }
            | {
                ref
                for candidate in profile.coverage_manifest.relation_candidates
                for ref in candidate.evidence_refs
            }
        )
    )
    return StageCheckRequirement(
        requirement_id=f"assembly-{profile.profile_digest[:24]}",
        checker_id="assembly-relationship-checker",
        target_kind=RequirementTargetKind.ASSEMBLY,
        basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
        denominator_refs=profile.check_denominator,
        required_source_refs=source_refs,
        required_authority_refs=authority_refs,
    )


def relation_coverage_stage_requirement(
    graph: ArchitecturalRelationGraph,
    policy: SemanticKindRelationPolicy,
    inventory: StageSubjectInventory,
    *,
    promotion: RelationPromotionReceipt | None = None,
) -> StageCheckRequirement:
    """Require every mechanically derived semantic relation slot."""

    from archflow.control.relation_checks import (
        relation_subject_inventory_ref,
        require_relation_subject_inventory,
    )

    if not isinstance(graph, ArchitecturalRelationGraph):
        raise TypeError("graph must be an ArchitecturalRelationGraph")
    if not isinstance(policy, SemanticKindRelationPolicy):
        raise TypeError("policy must be a SemanticKindRelationPolicy")
    require_relation_subject_inventory(graph, policy, inventory)
    promotion_sources: tuple[str, ...] = ()
    if promotion is not None:
        if not isinstance(promotion, RelationPromotionReceipt):
            raise TypeError("promotion must be RelationPromotionReceipt")
        promotion.require_exact_binding(
            graph,
            policy,
            subject_inventory_ref=relation_subject_inventory_ref(inventory),
        )
        promotion_sources = (
            promotion.ref,
            promotion.proposal_graph_ref,
            *promotion.verification_receipt_refs,
        )
    slots = compile_requirement_slots(graph, policy)
    denominator = tuple(item.slot_ref for item in slots)
    source_refs = tuple(
        sorted(
            {
                graph.ref,
                policy.ref,
                relation_subject_inventory_ref(inventory),
                *promotion_sources,
                *policy.source_refs,
                *(ref for slot in slots for ref in slot.evidence_refs),
            }
        )
    )
    authority_refs = tuple(
        sorted(
            {
                ref
                for slot in slots
                for ref in slot.authority_refs
            }
        )
    )
    return StageCheckRequirement(
        requirement_id=relation_coverage_check_id(policy),
        checker_id=RELATION_COVERAGE_CHECKER_ID,
        target_kind=RequirementTargetKind.RELATION,
        basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
        denominator_refs=denominator,
        required_source_refs=source_refs,
        required_authority_refs=authority_refs,
    )


def relation_authoring_stage_requirements(
    context: RelationAuthoringContext,
    compilation: RelationAuthoringCompilation,
    inventory: StageSubjectInventory,
    *,
    promotion: RelationPromotionResult | None = None,
) -> tuple[StageCheckRequirement, ...]:
    """Require semantic coverage plus independent checks for every question.

    A proposal graph is deliberately HYPOTHESIS-only.  This adapter makes the
    missing verification receipts visible to composite stage closure rather
    than allowing relation coverage to stand in for physical validation.
    """

    from archflow.control.relation_checks import (
        RELATION_VERIFICATION_CHECKERS,
        require_relation_authoring_question_coverage,
    )
    from archflow.control.stage_subjects import StageSubjectInventory

    if not isinstance(context, RelationAuthoringContext):
        raise TypeError("context must be RelationAuthoringContext")
    if not isinstance(compilation, RelationAuthoringCompilation):
        raise TypeError("compilation must be RelationAuthoringCompilation")
    if not isinstance(inventory, StageSubjectInventory):
        raise TypeError("inventory must be StageSubjectInventory")
    if (
        compilation.receipt.status
        is not RelationAuthoringCompilationStatus.PROPOSAL_COMPILED
        or compilation.graph is None
        or compilation.policy is None
    ):
        raise ValueError("relation verification requires a proposal-compiled graph")
    require_relation_authoring_question_coverage(
        context,
        compilation,
        inventory,
    )
    proposal_graph = compilation.graph
    coverage_graph = proposal_graph
    promotion_receipt = None
    if promotion is not None:
        if not isinstance(promotion, RelationPromotionResult):
            raise TypeError("promotion must be RelationPromotionResult")
        if (
            promotion.proposal_graph != proposal_graph
            or promotion.receipt.context_digest != context.context_digest
            or promotion.receipt.proposal_digest
            != compilation.receipt.proposal_digest
        ):
            raise ValueError("relation promotion crossed its exact proposal")
        promotion.receipt.require_exact_binding(
            promotion.graph,
            compilation.policy,
            subject_inventory_ref=context.subject_inventory_ref,
        )
        coverage_graph = promotion.graph
        promotion_receipt = promotion.receipt
    requirements = [
        relation_coverage_stage_requirement(
            coverage_graph,
            compilation.policy,
            inventory,
            promotion=promotion_receipt,
        )
    ]
    for question in context.questions:
        relations = tuple(
            item
            for item in proposal_graph.relations
            if question.ref in item.source_refs
        )
        if not relations:
            raise ValueError(
                f"{question.ref} has no exact relation verification denominator"
            )
        relation_refs = tuple(sorted(item.ref for item in relations))
        source_refs = tuple(
            sorted(
                {
                    proposal_graph.ref,
                    question.ref,
                    context.subject_inventory_ref,
                    *(ref for item in relations for ref in item.evidence_refs),
                }
            )
        )
        authority_refs = tuple(
            sorted(
                {
                    ref
                    for item in relations
                    for ref in item.authority_refs
                }
            )
        )
        requirements.append(
            StageCheckRequirement(
                requirement_id=f"relation-verification-{question.question_id}",
                checker_id=RELATION_VERIFICATION_CHECKERS[
                    question.projection
                ],
                target_kind=RequirementTargetKind.RELATION,
                basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
                denominator_refs=relation_refs,
                required_source_refs=source_refs,
                required_authority_refs=authority_refs,
                allow_not_applicable=False,
            )
        )
    return tuple(sorted(requirements, key=lambda item: item.requirement_id))


def material_binding_stage_requirement(
    profile: MaterialBindingProfile,
) -> StageCheckRequirement:
    """Require exact semantic-to-ledger-to-geometry material bindings."""

    if not isinstance(profile, MaterialBindingProfile):
        raise TypeError("profile must be a MaterialBindingProfile")
    return StageCheckRequirement(
        requirement_id="material-binding",
        checker_id="material-binding-validator",
        target_kind=RequirementTargetKind.MATERIAL,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.check_denominator,
    )


def cad_readback_stage_requirement(
    profile: CadReadbackProfile,
) -> StageCheckRequirement:
    """Require exact operation/object correspondence after CAD readback."""

    if not isinstance(profile, CadReadbackProfile):
        raise TypeError("profile must be a CadReadbackProfile")
    return StageCheckRequirement(
        requirement_id="cad-readback",
        checker_id="cad-readback-validator",
        target_kind=RequirementTargetKind.ARTIFACT,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.check_denominator,
    )


def component_lineage_stage_requirement(
    profile: ComponentLineageCheckProfile,
) -> StageCheckRequirement:
    """Require the exact predecessor lineage input and declared subjects."""

    if not isinstance(profile, ComponentLineageCheckProfile):
        raise TypeError("profile must be a ComponentLineageCheckProfile")
    return StageCheckRequirement(
        requirement_id=profile.check_id,
        checker_id="component-lineage-validator",
        target_kind=RequirementTargetKind.COMPONENT,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.denominator_refs,
    )


def spatial_layout_stage_requirement(
    profile: SpatialLayoutCheckProfile,
) -> StageCheckRequirement:
    """Require one exact normalized spatial input and declared subjects."""

    if not isinstance(profile, SpatialLayoutCheckProfile):
        raise TypeError("profile must be a SpatialLayoutCheckProfile")
    return StageCheckRequirement(
        requirement_id=profile.check_id,
        checker_id="spatial-layout-validator",
        target_kind=RequirementTargetKind.ASSEMBLY,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.denominator_refs,
    )


__all__ = [
    "assembly_stage_requirement",
    "cad_readback_stage_requirement",
    "component_lineage_stage_requirement",
    "material_binding_stage_requirement",
    "relation_authoring_stage_requirements",
    "relation_coverage_stage_requirement",
    "spatial_layout_stage_requirement",
]
