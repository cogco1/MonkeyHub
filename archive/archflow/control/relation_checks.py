"""Stage-bound architectural relation coverage checks.

This adapter joins the generic relation graph to the exact typed
``StageSubjectInventory``. It prevents a graph from shrinking its own node
universe and emits a no-authority receipt for composite stage closure.
"""

from __future__ import annotations

from archive.archflow.control.stage_subjects import StageSubjectInventory
from archive.archflow.control.baseline import StageBaselineRole
from archive.archflow.control.stage_subjects import StageSubjectDisposition
from archive.archflow.relations.authoring import (
    RelationAuthoringCompilation,
    RelationAuthoringCompilationStatus,
    RelationAuthoringContext,
)
from archive.archflow.relations.checks import (
    RELATION_COVERAGE_CHECKER_ID,
    relation_coverage_check_id,
)
from archflow.relations.contracts import (
    ArchitecturalNodeKind,
    ArchitecturalRelationGraph,
    RelationProjection,
)
from archive.archflow.control.relation_promotion import RelationPromotionReceipt
from archive.archflow.relations.coverage import (
    GraphCoverageManifest,
    RelationCoverageError,
    RelationCoverageStatus,
    RelationNotApplicable,
    RelationRequirementSlot,
    SemanticKindRelationPolicy,
    compile_graph_coverage,
)
from archive.archflow.validation.contracts import CheckFinding, CheckReceiptEnvelope, CheckStatus, FindingSeverity


def relation_subject_inventory_ref(
    inventory: StageSubjectInventory,
) -> str:
    if not isinstance(inventory, StageSubjectInventory):
        raise TypeError("inventory must be a StageSubjectInventory")
    return f"stage-subject-inventory:{inventory.inventory_digest}"


def _entry_ref(entry_digest: str) -> str:
    return f"stage-subject-entry:{entry_digest}"


def require_relation_subject_inventory(
    graph: ArchitecturalRelationGraph,
    policy: SemanticKindRelationPolicy,
    inventory: StageSubjectInventory,
) -> None:
    """Require an exact current-stage policy/node join to the inventory."""

    if not isinstance(graph, ArchitecturalRelationGraph):
        raise TypeError("graph must be an ArchitecturalRelationGraph")
    if not isinstance(policy, SemanticKindRelationPolicy):
        raise TypeError("policy must be a SemanticKindRelationPolicy")
    if not isinstance(inventory, StageSubjectInventory):
        raise TypeError("inventory must be a StageSubjectInventory")
    if any(
        rule.node_kind is not ArchitecturalNodeKind.COMPONENT
        for rule in policy.rules
    ):
        raise RelationCoverageError(
            "relation policy names a node kind without a typed stage inventory"
        )
    if graph.branch != inventory.branch:
        raise RelationCoverageError(
            "relation graph crossed the stage subject branch"
        )
    if graph.stage_id != inventory.stage_id:
        raise RelationCoverageError(
            "relation graph crossed the stage subject stage"
        )
    if graph.stage_subject_digest != inventory.stage_subject_digest:
        raise RelationCoverageError(
            "relation graph crossed the stage subject digest"
        )
    if graph.subject_inventory_digest != inventory.inventory_digest:
        raise RelationCoverageError(
            "relation graph crossed the subject inventory digest"
        )

    entries = {item.identity_ref: item for item in inventory.entries}
    if len(entries) != len(inventory.entries):
        raise RelationCoverageError(
            "stage subject inventory repeats an identity_ref"
        )
    subject_nodes = {
        item.node_ref: item
        for item in graph.nodes
        if item.stage_id == graph.stage_id
        and item.node_kind is ArchitecturalNodeKind.COMPONENT
    }
    if set(subject_nodes) != set(entries):
        raise RelationCoverageError(
            "relation graph semantic nodes do not exactly cover the inventory"
        )
    for node_ref, entry in entries.items():
        node = subject_nodes[node_ref]
        if (
            node.semantic_kind != entry.semantic_kind
            or _entry_ref(entry.entry_digest) not in node.source_refs
        ):
            raise RelationCoverageError(
                "relation graph semantic node changed its inventory binding"
            )


_ROLE_PROJECTIONS: dict[StageBaselineRole, frozenset[RelationProjection]] = {
    StageBaselineRole.ASSEMBLY_RELATIONSHIPS: frozenset(
        {
            RelationProjection.COMPOSITION,
            RelationProjection.SUPPORT,
            RelationProjection.HOST,
        }
    ),
    StageBaselineRole.OPENING_CLEARANCE: frozenset(
        {RelationProjection.ACCESS}
    ),
    StageBaselineRole.LOAD_PATH: frozenset({RelationProjection.SUPPORT}),
}


RELATION_VERIFICATION_CHECKERS: dict[RelationProjection, str] = {
    RelationProjection.COMPOSITION: "architectural-composition-verifier",
    RelationProjection.IMPACT: "architectural-impact-verifier",
    RelationProjection.SUPPORT: "architectural-support-verifier",
    RelationProjection.HOST: "architectural-host-verifier",
    RelationProjection.ACCESS: "architectural-access-verifier",
    RelationProjection.REALIZATION: "architectural-realization-verifier",
    RelationProjection.LINEAGE: "architectural-lineage-verifier",
    RelationProjection.PROVENANCE: "architectural-provenance-verifier",
}


def require_relation_authoring_question_coverage(
    context: RelationAuthoringContext,
    compilation: RelationAuthoringCompilation,
    inventory: StageSubjectInventory,
) -> None:
    """Join every relevant inventory obligation to an exact Agent question.

    This is the controller-side denominator check.  It prevents a complete
    component class (for example roof members or upper capitals) from being
    omitted before the Agent is called.  A missing question is permitted only
    when the exact typed inventory obligation is independently NOT_APPLICABLE.
    """

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
        raise RelationCoverageError(
            "relation authoring questions require a proposal-compiled graph"
        )
    if compilation.receipt.context_digest != context.context_digest:
        raise RelationCoverageError(
            "relation authoring compilation crossed its exact context"
        )
    if (
        context.branch != inventory.branch
        or context.stage_id != inventory.stage_id
        or context.stage_subject_digest != inventory.stage_subject_digest
        or context.subject_inventory_digest != inventory.inventory_digest
        or context.subject_inventory_ref
        != relation_subject_inventory_ref(inventory)
    ):
        raise RelationCoverageError(
            "relation authoring context crossed its exact inventory"
        )
    require_relation_subject_inventory(
        compilation.graph,
        compilation.policy,
        inventory,
    )
    questions_by_subject: dict[str, set[RelationProjection]] = {}
    for question in context.questions:
        for subject_ref in question.subject_refs:
            questions_by_subject.setdefault(subject_ref, set()).add(
                question.projection
            )
    missing: list[str] = []
    for entry in inventory.entries:
        projections = questions_by_subject.get(entry.identity_ref, set())
        obligation_by_role = {
            item.role: item for item in entry.role_obligations
        }
        for role, allowed in _ROLE_PROJECTIONS.items():
            obligation = obligation_by_role.get(role)
            if obligation is None:
                continue
            if obligation.disposition is StageSubjectDisposition.NOT_APPLICABLE:
                continue
            if not projections.intersection(allowed):
                missing.append(f"{entry.identity_ref}:{role.value}")
    if missing:
        raise RelationCoverageError(
            "relation questions omit required inventory obligations: "
            + ", ".join(sorted(missing))
        )


def check_relation_coverage(
    graph: ArchitecturalRelationGraph,
    policy: SemanticKindRelationPolicy,
    inventory: StageSubjectInventory,
    slots: tuple[RelationRequirementSlot, ...],
    *,
    not_applicable: tuple[RelationNotApplicable, ...] = (),
    promotion: RelationPromotionReceipt | None = None,
) -> tuple[GraphCoverageManifest, CheckReceiptEnvelope]:
    """Compile exact graph coverage and its composite-closure receipt."""

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
    manifest = compile_graph_coverage(
        graph,
        policy,
        slots,
        not_applicable,
    )
    slot_by_ref = {item.slot_ref: item for item in manifest.slots}
    findings: list[CheckFinding] = []
    for disposition in manifest.dispositions:
        slot = slot_by_ref[disposition.slot_ref]
        if disposition.status is RelationCoverageStatus.UNKNOWN:
            findings.append(
                CheckFinding(
                    code="required-relation-unresolved",
                    severity=FindingSeverity.UNKNOWN,
                    message=(
                        f"Required {slot.relation_kind.value} relation slot "
                        f"has {disposition.matched_count} match(es), below "
                        f"minimum {slot.minimum_count}."
                    ),
                    subject_refs=(slot.slot_ref,),
                    evidence_refs=slot.evidence_refs,
                )
            )
        elif disposition.status is RelationCoverageStatus.VIOLATED:
            findings.append(
                CheckFinding(
                    code="relation-cardinality-violated",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"Required {slot.relation_kind.value} relation slot "
                        f"has {disposition.matched_count} match(es), above "
                        f"maximum {slot.maximum_count}."
                    ),
                    subject_refs=(slot.slot_ref,),
                    evidence_refs=slot.evidence_refs,
                )
            )

    statuses = {item.status for item in manifest.dispositions}
    if RelationCoverageStatus.VIOLATED in statuses:
        status = CheckStatus.FAIL
    elif RelationCoverageStatus.UNKNOWN in statuses:
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    source_refs = tuple(
        sorted(
            {
                graph.ref,
                policy.ref,
                manifest.ref,
                relation_subject_inventory_ref(inventory),
                *promotion_sources,
                *policy.source_refs,
                *(ref for slot in manifest.slots for ref in slot.evidence_refs),
                *(
                    ref
                    for declaration in not_applicable
                    for ref in declaration.evidence_refs
                ),
            }
        )
    )
    authority_refs = tuple(
        sorted(
            {
                *(ref for slot in manifest.slots for ref in slot.authority_refs),
                *(
                    ref
                    for declaration in not_applicable
                    for ref in declaration.authority_refs
                ),
            }
        )
    )
    denominator = tuple(item.slot_ref for item in manifest.slots)
    receipt = CheckReceiptEnvelope(
        check_id=relation_coverage_check_id(policy),
        checker_id=RELATION_COVERAGE_CHECKER_ID,
        checker_version="1.0.0",
        branch=graph.branch,
        scope_digest=graph.scope_digest,
        subject_refs=denominator,
        subject_digest=inventory.stage_subject_digest,
        status=status,
        source_refs=source_refs,
        authority_refs=authority_refs,
        findings=tuple(findings),
        coverage_denominator=denominator,
        covered_refs=denominator,
    )
    return manifest, receipt


__all__ = [
    "RELATION_VERIFICATION_CHECKERS",
    "check_relation_coverage",
    "relation_subject_inventory_ref",
    "require_relation_authoring_question_coverage",
    "require_relation_subject_inventory",
]
