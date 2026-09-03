"""Pure forward compiler for the current visual/function/relation stage chain.

The runtime has two deliberately separate boundaries.  ``prepare`` compiles
the exact stage subject, component-function, and function-relation
denominators and constructs the controller context exposed to an Agent.
``finalize`` consumes that Agent proposal plus independent verification
receipts and returns validator source wrappers.  Neither boundary performs
I/O, invokes a provider, accepts a design, or writes canonical state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from archive.archflow.capabilities.visual_inventory import VisualEvidenceInventoryReceipt
from archflow.contracts.fields import exact_mapping
from archive.archflow.control.baseline import (
    RelationTopologyBaselineSource,
    StageBaselineLevel,
    StageBaselineSourceSet,
)
from archive.archflow.control.component_functions import (
    ComponentFunctionContract,
    ComponentFunctionLedger,
    compile_component_function_ledger,
)
from archive.archflow.control.function_relations import (
    FunctionRelationEvidenceEnvelope,
    FunctionRelationRequirementSet,
    compile_function_relation_requirements,
)
from archive.archflow.control.relation_checks import relation_subject_inventory_ref
from archive.archflow.control.relation_promotion import (
    RelationPromotionResult,
    promote_verified_relation_graph,
)
from archive.archflow.control.semantic_capabilities import SemanticCapabilityPolicy
from archive.archflow.control.stage_control_sources import (
    ComponentFunctionBaselineSource,
    VisualInventoryBaselineSource,
)
from archive.archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectRoleObligation,
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archive.archflow.relations.authoring import (
    RelationAuthoringCompilation,
    RelationAuthoringContext,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisUse,
    RelationDerivationQuestion,
    compile_relation_authoring,
)
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
)
from archive.archflow.runtime.component_index import ComponentIndex
from archive.archflow.runtime.stage_subject_inventory import compile_stage_subject_inventory
from archflow.state.spatial import SpatialOptionProposal
from archflow.validation.contracts import CheckReceiptEnvelope


class StageControlChainError(ValueError):
    """Exact forward-chain inputs cannot be joined without inference."""


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "provider_call_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


@dataclass(frozen=True, slots=True)
class PreparedStageControlChain:
    """Typed, authority-free values ready for one relation Agent call."""

    inventory: StageSubjectInventory
    function_ledger: ComponentFunctionLedger
    relation_requirements: FunctionRelationRequirementSet
    relation_context: RelationAuthoringContext
    visual_source: VisualInventoryBaselineSource

    SCHEMA = "PreparedStageControlChain@1"

    def __post_init__(self) -> None:
        if not isinstance(self.inventory, StageSubjectInventory):
            raise TypeError("inventory must be a StageSubjectInventory")
        if not isinstance(self.function_ledger, ComponentFunctionLedger):
            raise TypeError("function_ledger must be a ComponentFunctionLedger")
        if not isinstance(
            self.relation_requirements, FunctionRelationRequirementSet
        ):
            raise TypeError(
                "relation_requirements must be a FunctionRelationRequirementSet"
            )
        if not isinstance(self.relation_context, RelationAuthoringContext):
            raise TypeError("relation_context must be a RelationAuthoringContext")
        if not isinstance(self.visual_source, VisualInventoryBaselineSource):
            raise TypeError("visual_source must be a VisualInventoryBaselineSource")
        if (
            self.function_ledger.branch != self.inventory.branch
            or self.function_ledger.stage_id != self.inventory.stage_id
            or self.function_ledger.subject_inventory_digest
            != self.inventory.inventory_digest
        ):
            raise StageControlChainError(
                "function ledger crossed the exact stage subject inventory"
            )
        if (
            self.relation_requirements.branch != self.inventory.branch
            or self.relation_requirements.stage_id != self.inventory.stage_id
            or self.relation_requirements.subject_inventory_digest
            != self.inventory.inventory_digest
            or self.relation_requirements.function_ledger_ref
            != self.function_ledger.ledger_ref
            or self.relation_requirements.function_ledger_digest
            != self.function_ledger.ledger_digest
        ):
            raise StageControlChainError(
                "function relation requirements crossed their exact ledger"
            )
        if (
            self.relation_context.branch != self.inventory.branch
            or self.relation_context.stage_id != self.inventory.stage_id
            or self.relation_context.stage_subject_digest
            != self.inventory.stage_subject_digest
            or self.relation_context.subject_inventory_ref
            != relation_subject_inventory_ref(self.inventory)
            or self.relation_context.subject_inventory_digest
            != self.inventory.inventory_digest
        ):
            raise StageControlChainError(
                "relation authoring context crossed the exact inventory"
            )
        required_questions = {
            item.question.ref for item in self.relation_requirements.requirements
        }
        context_questions = {item.ref for item in self.relation_context.questions}
        if not required_questions <= context_questions:
            raise StageControlChainError(
                "relation context omitted a functional requirement question"
            )
        if (
            self.visual_source.branch != self.inventory.branch
            or self.visual_source.stage_id != self.inventory.stage_id
            or self.visual_source.stage_subject_inventory_digest
            != self.inventory.inventory_digest
            or self.visual_source.inventory_ref
            != self.inventory.visual_inventory_ref
            or self.visual_source.inventory.inventory_digest
            != self.inventory.visual_inventory_digest
        ):
            raise StageControlChainError(
                "visual baseline source crossed the exact stage inventory"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "inventory": self.inventory.to_dict(),
            "function_ledger": self.function_ledger.to_dict(),
            "relation_requirements": self.relation_requirements.to_dict(),
            "relation_context": self.relation_context.to_dict(),
            "visual_source": self.visual_source.to_dict(),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "PreparedStageControlChain":
        payload = exact_mapping(
            value,
            {
                "schema",
                "inventory",
                "function_ledger",
                "relation_requirements",
                "relation_context",
                "visual_source",
                *_AUTHORITY_FIELDS,
            },
            "prepared stage control chain",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageControlChainError(
                "unsupported prepared stage control chain schema"
            )
        result = cls(
            inventory=StageSubjectInventory.from_dict(payload["inventory"]),
            function_ledger=ComponentFunctionLedger.from_dict(
                payload["function_ledger"]
            ),
            relation_requirements=FunctionRelationRequirementSet.from_dict(
                payload["relation_requirements"]
            ),
            relation_context=RelationAuthoringContext.from_dict(
                payload["relation_context"]
            ),
            visual_source=VisualInventoryBaselineSource.from_dict(
                payload["visual_source"]
            ),
        )
        if result.to_dict() != dict(payload):
            raise StageControlChainError(
                "prepared stage control chain identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class FinalizedStageControlChain:
    """Verified relation outputs packaged as exact baseline source inputs."""

    prepared: PreparedStageControlChain
    compilation: RelationAuthoringCompilation
    promotion: RelationPromotionResult
    function_source: ComponentFunctionBaselineSource
    topology_source: RelationTopologyBaselineSource
    baseline_sources: StageBaselineSourceSet

    SCHEMA = "FinalizedStageControlChain@1"

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedStageControlChain):
            raise TypeError("prepared must be a PreparedStageControlChain")
        if not isinstance(self.compilation, RelationAuthoringCompilation):
            raise TypeError("compilation must be a RelationAuthoringCompilation")
        if not isinstance(self.promotion, RelationPromotionResult):
            raise TypeError("promotion must be a RelationPromotionResult")
        if not isinstance(
            self.function_source, ComponentFunctionBaselineSource
        ):
            raise TypeError(
                "function_source must be a ComponentFunctionBaselineSource"
            )
        if not isinstance(self.topology_source, RelationTopologyBaselineSource):
            raise TypeError(
                "topology_source must be a RelationTopologyBaselineSource"
            )
        if not isinstance(self.baseline_sources, StageBaselineSourceSet):
            raise TypeError("baseline_sources must be a StageBaselineSourceSet")
        if (
            self.function_source.ledger != self.prepared.function_ledger
            or self.function_source.relation_requirements
            != self.prepared.relation_requirements
            or self.topology_source.context != self.prepared.relation_context
            or self.topology_source.compilation != self.compilation
            or self.topology_source.promotion != self.promotion
            or self.baseline_sources.visual_inventory
            != (self.prepared.visual_source,)
            or self.baseline_sources.component_functions
            != (self.function_source,)
            or self.baseline_sources.relation_topology
            != (self.topology_source,)
        ):
            raise StageControlChainError(
                "finalized package crossed its exact prepared values or sources"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "prepared": self.prepared.to_dict(),
            "compilation": self.compilation.to_dict(),
            "promotion": self.promotion.to_dict(),
            "function_source": self.function_source.to_dict(),
            "topology_source": self.topology_source.to_dict(),
            "baseline_sources": self.baseline_sources.to_dict(),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FinalizedStageControlChain":
        payload = exact_mapping(
            value,
            {
                "schema",
                "prepared",
                "compilation",
                "promotion",
                "function_source",
                "topology_source",
                "baseline_sources",
                *_AUTHORITY_FIELDS,
            },
            "finalized stage control chain",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageControlChainError(
                "unsupported finalized stage control chain schema"
            )
        result = cls(
            prepared=PreparedStageControlChain.from_dict(payload["prepared"]),
            compilation=RelationAuthoringCompilation.from_dict(
                payload["compilation"]
            ),
            promotion=RelationPromotionResult.from_dict(payload["promotion"]),
            function_source=ComponentFunctionBaselineSource.from_dict(
                payload["function_source"]
            ),
            topology_source=RelationTopologyBaselineSource.from_dict(
                payload["topology_source"]
            ),
            baseline_sources=StageBaselineSourceSet.from_dict(
                payload["baseline_sources"]
            ),
        )
        if result.to_dict() != dict(payload):
            raise StageControlChainError(
                "finalized stage control chain identity changed"
            )
        return result


def _require_exact_relation_nodes(
    inventory: StageSubjectInventory,
    nodes: tuple[ArchitecturalNode, ...],
) -> None:
    if not isinstance(nodes, tuple) or any(
        not isinstance(item, ArchitecturalNode) for item in nodes
    ):
        raise TypeError("relation_nodes must contain ArchitecturalNode values")
    entries = {item.identity_ref: item for item in inventory.entries}
    component_nodes = {
        item.node_ref: item
        for item in nodes
        if item.node_kind is ArchitecturalNodeKind.COMPONENT
    }
    if len(component_nodes) != sum(
        item.node_kind is ArchitecturalNodeKind.COMPONENT for item in nodes
    ):
        raise StageControlChainError("relation nodes repeat a component")
    if set(component_nodes) != set(entries):
        raise StageControlChainError(
            "relation component nodes do not exactly cover the inventory"
        )
    for node_ref, entry in entries.items():
        node = component_nodes[node_ref]
        if (
            node.stage_id != inventory.stage_id
            or node.semantic_kind != entry.semantic_kind
            or f"stage-subject-entry:{entry.entry_digest}" not in node.source_refs
        ):
            raise StageControlChainError(
                "relation component node changed its exact inventory binding"
            )


def _inject_requirement_questions(
    project_questions: tuple[RelationDerivationQuestion, ...],
    requirements: FunctionRelationRequirementSet,
) -> tuple[RelationDerivationQuestion, ...]:
    if not isinstance(project_questions, tuple) or any(
        not isinstance(item, RelationDerivationQuestion)
        for item in project_questions
    ):
        raise TypeError(
            "relation_questions must contain RelationDerivationQuestion values"
        )
    requirement_questions = requirements.topology_questions
    project_refs = tuple(item.ref for item in project_questions)
    requirement_refs = tuple(item.ref for item in requirement_questions)
    if (
        len(project_refs) != len(set(project_refs))
        or set(project_refs) & set(requirement_refs)
    ):
        raise StageControlChainError(
            "project questions duplicate a functional requirement question"
        )
    return tuple(
        sorted(
            (*project_questions, *requirement_questions),
            key=lambda item: item.question_id,
        )
    )


def _require_project_bases(
    requirements: FunctionRelationRequirementSet,
    bases: tuple[RelationBasisBinding, ...],
) -> None:
    if not isinstance(bases, tuple) or any(
        not isinstance(item, RelationBasisBinding) for item in bases
    ):
        raise TypeError("relation_bases must contain RelationBasisBinding values")
    basis_by_id = {item.basis_id: item for item in bases}
    if len(basis_by_id) != len(bases):
        raise StageControlChainError("project relation bases repeat a basis_id")
    for requirement in requirements.requirements:
        question = requirement.question
        try:
            selected = tuple(basis_by_id[item] for item in question.basis_ids)
        except KeyError as exc:
            raise StageControlChainError(
                f"{question.ref} lacks a project-provided evidence basis"
            ) from exc
        for basis in selected:
            if (
                question.ref not in basis.question_refs
                or requirement.rule.relation_kind
                not in basis.allowed_relation_kinds
                or not set(requirement.rule.evidence_refs).issubset(
                    basis.evidence_refs
                )
                or not set(requirement.rule.authority_refs).issubset(
                    basis.authority_refs
                )
            ):
                raise StageControlChainError(
                    f"{question.ref} has a stale or unbound project relation basis"
                )
        uses = {item.basis_use for item in selected}
        if not {
            RelationBasisUse.POLICY,
            RelationBasisUse.TOPOLOGY,
        } <= uses:
            raise StageControlChainError(
                f"{question.ref} needs explicit policy and topology bases"
            )


def prepare_stage_control_chain(
    *,
    inventory_id: str,
    function_ledger_id: str,
    relation_requirements_id: str,
    relation_context_id: str,
    branch: BranchRef,
    stage_id: str,
    state_digest: str,
    scope_digest: str,
    stage_subject_ref: str,
    stage_subject_digest: str,
    baseline_level: StageBaselineLevel,
    component_proposal: SpatialOptionProposal,
    component_proposal_ref: ProjectRecordRef,
    component_index: ComponentIndex,
    component_index_ref: ProjectRecordRef,
    visual_inventory: VisualEvidenceInventoryReceipt,
    visual_inventory_ref: ProjectRecordRef,
    semantic_policy: SemanticCapabilityPolicy,
    semantic_policy_ref: ProjectRecordRef,
    role_obligations: Mapping[str, tuple[StageSubjectRoleObligation, ...]],
    function_contracts: tuple[ComponentFunctionContract, ...],
    function_relation_envelopes: tuple[
        FunctionRelationEvidenceEnvelope, ...
    ],
    relation_nodes: tuple[ArchitecturalNode, ...],
    relation_questions: tuple[RelationDerivationQuestion, ...],
    relation_bases: tuple[RelationBasisBinding, ...],
) -> PreparedStageControlChain:
    """Compile the exact pre-Agent chain without authoring missing inputs."""

    inventory = compile_stage_subject_inventory(
        inventory_id=inventory_id,
        branch=branch,
        stage_id=stage_id,
        stage_subject_ref=stage_subject_ref,
        stage_subject_digest=stage_subject_digest,
        baseline_level=baseline_level,
        component_proposal=component_proposal,
        component_proposal_ref=component_proposal_ref,
        component_index=component_index,
        component_index_ref=component_index_ref,
        visual_inventory=visual_inventory,
        visual_inventory_ref=visual_inventory_ref,
        semantic_policy=semantic_policy,
        semantic_policy_ref=semantic_policy_ref,
        role_obligations=role_obligations,
    )
    ledger = compile_component_function_ledger(
        ledger_id=function_ledger_id,
        inventory=inventory,
        contracts=function_contracts,
    )
    requirements = compile_function_relation_requirements(
        set_id=relation_requirements_id,
        ledger=ledger,
        inventory=inventory,
        envelopes=function_relation_envelopes,
    )
    _require_exact_relation_nodes(inventory, relation_nodes)
    questions = _inject_requirement_questions(
        relation_questions,
        requirements,
    )
    _require_project_bases(requirements, relation_bases)
    context = RelationAuthoringContext(
        context_id=relation_context_id,
        branch=branch,
        stage_id=stage_id,
        state_digest=state_digest,
        scope_digest=scope_digest,
        stage_subject_digest=stage_subject_digest,
        subject_inventory_ref=relation_subject_inventory_ref(inventory),
        subject_inventory_digest=inventory.inventory_digest,
        nodes=relation_nodes,
        questions=questions,
        bases=relation_bases,
    )
    visual_source = VisualInventoryBaselineSource(
        branch=branch,
        stage_id=stage_id,
        stage_subject_inventory_digest=inventory.inventory_digest,
        inventory_ref=visual_inventory_ref,
        inventory=visual_inventory,
    )
    return PreparedStageControlChain(
        inventory=inventory,
        function_ledger=ledger,
        relation_requirements=requirements,
        relation_context=context,
        visual_source=visual_source,
    )


def finalize_stage_control_chain(
    *,
    prepared: PreparedStageControlChain,
    relation_proposal: RelationAuthoringProposal,
    function_ledger_ref: ProjectRecordRef,
    relation_requirements_ref: ProjectRecordRef,
    verification_receipts: tuple[CheckReceiptEnvelope, ...],
) -> FinalizedStageControlChain:
    """Compile and independently promote one exact Agent relation proposal."""

    if not isinstance(prepared, PreparedStageControlChain):
        raise TypeError("prepared must be a PreparedStageControlChain")
    compilation = compile_relation_authoring(
        prepared.relation_context,
        relation_proposal,
    )
    promotion = promote_verified_relation_graph(
        prepared.relation_context,
        compilation,
        prepared.inventory,
        verification_receipts,
    )
    function_source = ComponentFunctionBaselineSource(
        ledger_ref=function_ledger_ref,
        ledger=prepared.function_ledger,
        relation_requirements_ref=relation_requirements_ref,
        relation_requirements=prepared.relation_requirements,
    )
    topology_source = RelationTopologyBaselineSource(
        context=prepared.relation_context,
        compilation=compilation,
        promotion=promotion,
    )
    sources = StageBaselineSourceSet(
        visual_inventory=(prepared.visual_source,),
        component_functions=(function_source,),
        relation_topology=(topology_source,),
    )
    return FinalizedStageControlChain(
        prepared=prepared,
        compilation=compilation,
        promotion=promotion,
        function_source=function_source,
        topology_source=topology_source,
        baseline_sources=sources,
    )


__all__ = [
    "FinalizedStageControlChain",
    "PreparedStageControlChain",
    "StageControlChainError",
    "finalize_stage_control_chain",
    "prepare_stage_control_chain",
]
