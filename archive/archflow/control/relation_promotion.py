"""Deterministic promotion of verified Agent relation proposals.

The Agent-authored graph remains a hypothesis.  This module is the only
bridge in the relation-authoring lane that can compile a checker-eligible
``DERIVED`` graph, and it does so only from an exact set of independent PASS
receipts.  The result owns no stage-acceptance, persistence, geometry, or
canonical-write authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from archive.archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
)
from archflow.project.refs import BranchRef
from archive.archflow.relations.authoring import (
    RelationAuthoringCompilation,
    RelationAuthoringCompilationStatus,
    RelationAuthoringContext,
)
from archflow.relations.contracts import (
    ArchitecturalRelationGraph,
    RelationEpistemicStatus,
)
from archive.archflow.relations.coverage import SemanticKindRelationPolicy
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus

if TYPE_CHECKING:
    from archive.archflow.control.stage_subjects import StageSubjectInventory


_AUTHORITY_FIELDS = {
    "agent_authored": False,
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "geometry_mutation_authority": False,
    "canonical_write_authority": False,
}


class RelationPromotionError(ValueError):
    """A verification set or retained promotion changed identity."""


class RelationPromotionStatus(StrEnum):
    VERIFIED_PROMOTION = "verified_promotion"


def relation_verification_receipt_ref(receipt: CheckReceiptEnvelope) -> str:
    if not isinstance(receipt, CheckReceiptEnvelope):
        raise TypeError("receipt must be CheckReceiptEnvelope")
    return f"check-receipt:{receipt.receipt_digest}"


@dataclass(frozen=True, slots=True)
class RelationPromotionBinding:
    relation_ref: str
    hypothesis_relation_digest: str
    promoted_relation_digest: str
    question_refs: tuple[str, ...]
    verification_receipt_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "RelationPromotionBinding@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relation_ref",
            logical_ref(self.relation_ref, "promoted relation_ref"),
        )
        for field in (
            "hypothesis_relation_digest",
            "promoted_relation_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        object.__setattr__(
            self,
            "question_refs",
            deterministic_refs(self.question_refs, "question_refs"),
        )
        object.__setattr__(
            self,
            "verification_receipt_refs",
            deterministic_refs(
                self.verification_receipt_refs,
                "verification_receipt_refs",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_ref": self.relation_ref,
            "hypothesis_relation_digest": self.hypothesis_relation_digest,
            "promoted_relation_digest": self.promoted_relation_digest,
            "question_refs": list(self.question_refs),
            "verification_receipt_refs": list(
                self.verification_receipt_refs
            ),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationPromotionBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_ref",
                "hypothesis_relation_digest",
                "promoted_relation_digest",
                "question_refs",
                "verification_receipt_refs",
                *_AUTHORITY_FIELDS,
            },
            "relation promotion binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationPromotionError("unsupported promotion binding schema")
        if payload["agent_authored"] is not False:
            raise RelationPromotionError("relation promotion binding is agent-authored")
        for field in ("question_refs", "verification_receipt_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            relation_ref=payload["relation_ref"],
            hypothesis_relation_digest=payload["hypothesis_relation_digest"],
            promoted_relation_digest=payload["promoted_relation_digest"],
            question_refs=tuple(payload["question_refs"]),
            verification_receipt_refs=tuple(
                payload["verification_receipt_refs"]
            ),
        )
        if result.to_dict() != payload:
            raise RelationPromotionError("promotion binding identity changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationPromotionReceipt:
    status: RelationPromotionStatus
    branch: BranchRef
    stage_id: str
    state_digest: str
    scope_digest: str
    stage_subject_digest: str
    subject_inventory_digest: str
    subject_inventory_ref: str
    context_digest: str
    proposal_digest: str
    proposal_graph_ref: str
    proposal_graph_digest: str
    promoted_graph_ref: str
    promoted_graph_digest: str
    policy_digest: str
    verification_receipt_refs: tuple[str, ...]
    verification_receipt_digests: tuple[str, ...]
    bindings: tuple[RelationPromotionBinding, ...]

    SCHEMA: ClassVar[str] = "RelationPromotionReceipt@1"

    def __post_init__(self) -> None:
        if self.status is not RelationPromotionStatus.VERIFIED_PROMOTION:
            raise RelationPromotionError("unsupported relation promotion status")
        require_exact_branch(self.branch, "relation promotion branch")
        identifier(self.stage_id, "relation promotion stage_id")
        for field in (
            "state_digest",
            "scope_digest",
            "stage_subject_digest",
            "subject_inventory_digest",
            "context_digest",
            "proposal_digest",
            "proposal_graph_digest",
            "promoted_graph_digest",
            "policy_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        for field in (
            "subject_inventory_ref",
            "proposal_graph_ref",
            "promoted_graph_ref",
        ):
            object.__setattr__(
                self,
                field,
                logical_ref(getattr(self, field), field),
            )
        object.__setattr__(
            self,
            "verification_receipt_refs",
            deterministic_refs(
                self.verification_receipt_refs,
                "verification_receipt_refs",
            ),
        )
        digests = tuple(
            sorted(
                require_sha256(item, "verification_receipt_digest")
                for item in self.verification_receipt_digests
            )
        )
        if not digests or len(digests) != len(set(digests)):
            raise RelationPromotionError(
                "promotion needs unique independent verification receipts"
            )
        object.__setattr__(self, "verification_receipt_digests", digests)
        if len(self.verification_receipt_refs) != len(digests):
            raise RelationPromotionError(
                "verification receipt refs and digests disagree"
            )
        if not isinstance(self.bindings, tuple) or not self.bindings or any(
            not isinstance(item, RelationPromotionBinding)
            for item in self.bindings
        ):
            raise TypeError("bindings must contain RelationPromotionBinding values")
        bindings = tuple(sorted(self.bindings, key=lambda item: item.relation_ref))
        refs = tuple(item.relation_ref for item in bindings)
        if len(refs) != len(set(refs)):
            raise RelationPromotionError("promotion repeats a relation binding")
        if any(
            not set(item.verification_receipt_refs)
            <= set(self.verification_receipt_refs)
            for item in bindings
        ):
            raise RelationPromotionError(
                "relation binding names an unknown verification receipt"
            )
        object.__setattr__(self, "bindings", bindings)

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"relation-promotion:{self.receipt_digest}"

    def require_exact_binding(
        self,
        graph: ArchitecturalRelationGraph,
        policy: SemanticKindRelationPolicy,
        *,
        subject_inventory_ref: str,
    ) -> None:
        if not isinstance(graph, ArchitecturalRelationGraph):
            raise TypeError("graph must be ArchitecturalRelationGraph")
        if not isinstance(policy, SemanticKindRelationPolicy):
            raise TypeError("policy must be SemanticKindRelationPolicy")
        subject_inventory_ref = logical_ref(
            subject_inventory_ref,
            "subject_inventory_ref",
        )
        if (
            graph.ref != self.promoted_graph_ref
            or graph.graph_digest != self.promoted_graph_digest
            or graph.branch != self.branch
            or graph.stage_id != self.stage_id
            or graph.state_digest != self.state_digest
            or graph.scope_digest != self.scope_digest
            or graph.stage_subject_digest != self.stage_subject_digest
            or graph.subject_inventory_digest
            != self.subject_inventory_digest
            or policy.policy_digest != self.policy_digest
            or subject_inventory_ref != self.subject_inventory_ref
        ):
            raise RelationPromotionError(
                "promotion crossed graph, policy, branch, stage, or inventory"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "status": self.status.value,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "state_digest": self.state_digest,
            "scope_digest": self.scope_digest,
            "stage_subject_digest": self.stage_subject_digest,
            "subject_inventory_digest": self.subject_inventory_digest,
            "subject_inventory_ref": self.subject_inventory_ref,
            "context_digest": self.context_digest,
            "proposal_digest": self.proposal_digest,
            "proposal_graph_ref": self.proposal_graph_ref,
            "proposal_graph_digest": self.proposal_graph_digest,
            "promoted_graph_ref": self.promoted_graph_ref,
            "promoted_graph_digest": self.promoted_graph_digest,
            "policy_digest": self.policy_digest,
            "verification_receipt_refs": list(
                self.verification_receipt_refs
            ),
            "verification_receipt_digests": list(
                self.verification_receipt_digests
            ),
            "bindings": [item.to_dict() for item in self.bindings],
            "verified_checks_required": True,
            "promotion_compiler": "relation-verification-promotion@1",
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationPromotionReceipt":
        payload = exact_mapping(
            value,
            {
                "schema",
                "status",
                "branch",
                "stage_id",
                "state_digest",
                "scope_digest",
                "stage_subject_digest",
                "subject_inventory_digest",
                "subject_inventory_ref",
                "context_digest",
                "proposal_digest",
                "proposal_graph_ref",
                "proposal_graph_digest",
                "promoted_graph_ref",
                "promoted_graph_digest",
                "policy_digest",
                "verification_receipt_refs",
                "verification_receipt_digests",
                "bindings",
                "verified_checks_required",
                "promotion_compiler",
                *_AUTHORITY_FIELDS,
            },
            "relation promotion receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["agent_authored"] is not False
            or payload["verified_checks_required"] is not True
            or payload["promotion_compiler"]
            != "relation-verification-promotion@1"
        ):
            raise RelationPromotionError("unsupported promotion receipt schema")
        for field in (
            "verification_receipt_refs",
            "verification_receipt_digests",
            "bindings",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            status=RelationPromotionStatus(payload["status"]),
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            state_digest=payload["state_digest"],
            scope_digest=payload["scope_digest"],
            stage_subject_digest=payload["stage_subject_digest"],
            subject_inventory_digest=payload["subject_inventory_digest"],
            subject_inventory_ref=payload["subject_inventory_ref"],
            context_digest=payload["context_digest"],
            proposal_digest=payload["proposal_digest"],
            proposal_graph_ref=payload["proposal_graph_ref"],
            proposal_graph_digest=payload["proposal_graph_digest"],
            promoted_graph_ref=payload["promoted_graph_ref"],
            promoted_graph_digest=payload["promoted_graph_digest"],
            policy_digest=payload["policy_digest"],
            verification_receipt_refs=tuple(
                payload["verification_receipt_refs"]
            ),
            verification_receipt_digests=tuple(
                payload["verification_receipt_digests"]
            ),
            bindings=tuple(
                RelationPromotionBinding.from_dict(item)
                for item in payload["bindings"]
            ),
        )
        if result.to_dict() != payload:
            raise RelationPromotionError("promotion receipt identity changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationPromotionResult:
    receipt: RelationPromotionReceipt
    proposal_graph: ArchitecturalRelationGraph
    graph: ArchitecturalRelationGraph
    verification_receipts: tuple[CheckReceiptEnvelope, ...]

    SCHEMA: ClassVar[str] = "RelationPromotionResult@1"

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, RelationPromotionReceipt):
            raise TypeError("receipt must be RelationPromotionReceipt")
        if not isinstance(self.proposal_graph, ArchitecturalRelationGraph):
            raise TypeError("proposal_graph must be ArchitecturalRelationGraph")
        if not isinstance(self.graph, ArchitecturalRelationGraph):
            raise TypeError("graph must be ArchitecturalRelationGraph")
        if (
            self.proposal_graph.ref != self.receipt.proposal_graph_ref
            or self.proposal_graph.graph_digest
            != self.receipt.proposal_graph_digest
            or self.graph.ref != self.receipt.promoted_graph_ref
            or self.graph.graph_digest != self.receipt.promoted_graph_digest
            or any(
                item.epistemic_status is not RelationEpistemicStatus.DERIVED
                for item in self.graph.relations
            )
        ):
            raise RelationPromotionError(
                "promoted result lacks its exact DERIVED graph"
            )
        if not isinstance(self.verification_receipts, tuple) or any(
            not isinstance(item, CheckReceiptEnvelope)
            for item in self.verification_receipts
        ):
            raise TypeError(
                "verification_receipts must contain CheckReceiptEnvelope values"
            )
        receipts = tuple(
            sorted(
                self.verification_receipts,
                key=lambda item: item.receipt_digest,
            )
        )
        if (
            tuple(item.receipt_digest for item in receipts)
            != self.receipt.verification_receipt_digests
            or tuple(relation_verification_receipt_ref(item) for item in receipts)
            != self.receipt.verification_receipt_refs
            or any(item.status is not CheckStatus.PASS for item in receipts)
        ):
            raise RelationPromotionError(
                "promoted result lost its independent PASS receipts"
            )
        object.__setattr__(self, "verification_receipts", receipts)
        hypothesis_by_ref = {
            item.ref: item for item in self.proposal_graph.relations
        }
        by_ref = {item.ref: item for item in self.graph.relations}
        if (
            set(hypothesis_by_ref)
            != {item.relation_ref for item in self.receipt.bindings}
            or set(by_ref) != set(hypothesis_by_ref)
        ):
            raise RelationPromotionError(
                "promotion bindings do not equal the graph denominator"
            )
        for binding in self.receipt.bindings:
            hypothesis = hypothesis_by_ref[binding.relation_ref]
            relation = by_ref[binding.relation_ref]
            expected = replace(
                hypothesis,
                epistemic_status=RelationEpistemicStatus.DERIVED,
                source_refs=tuple(
                    sorted(
                        {
                            *hypothesis.source_refs,
                            self.proposal_graph.ref,
                            *binding.verification_receipt_refs,
                        }
                    )
                ),
            )
            if (
                hypothesis.relation_digest
                != binding.hypothesis_relation_digest
                or relation.relation_digest
                != binding.promoted_relation_digest
                or relation != expected
            ):
                raise RelationPromotionError(
                    "promoted relation lost proposal or verification lineage"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt": self.receipt.to_dict(),
            "proposal_graph": self.proposal_graph.to_dict(),
            "graph": self.graph.to_dict(),
            "verification_receipts": [
                item.to_dict() for item in self.verification_receipts
            ],
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationPromotionResult":
        payload = exact_mapping(
            value,
            {
                "schema",
                "receipt",
                "proposal_graph",
                "graph",
                "verification_receipts",
                *_AUTHORITY_FIELDS,
            },
            "relation promotion result",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationPromotionError("unsupported promotion result schema")
        if payload["agent_authored"] is not False:
            raise RelationPromotionError("relation promotion result is agent-authored")
        if not isinstance(payload["verification_receipts"], list):
            raise TypeError("verification_receipts must be a list")
        result = cls(
            receipt=RelationPromotionReceipt.from_dict(payload["receipt"]),
            proposal_graph=ArchitecturalRelationGraph.from_dict(
                payload["proposal_graph"]
            ),
            graph=ArchitecturalRelationGraph.from_dict(payload["graph"]),
            verification_receipts=tuple(
                CheckReceiptEnvelope.from_dict(item)
                for item in payload["verification_receipts"]
            ),
        )
        if result.to_dict() != payload:
            raise RelationPromotionError("promotion result identity changed")
        return result


def promote_verified_relation_graph(
    context: RelationAuthoringContext,
    compilation: RelationAuthoringCompilation,
    inventory: StageSubjectInventory,
    verification_receipts: tuple[CheckReceiptEnvelope, ...],
) -> RelationPromotionResult:
    """Promote exactly one complete, independently verified proposal graph."""

    from archive.archflow.control.relation_checks import (
        RELATION_VERIFICATION_CHECKERS,
        relation_subject_inventory_ref,
        require_relation_authoring_question_coverage,
    )
    from archive.archflow.control.stage_subjects import StageSubjectInventory

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
        raise RelationPromotionError("promotion requires a compiled proposal")
    require_relation_authoring_question_coverage(
        context,
        compilation,
        inventory,
    )
    if not isinstance(verification_receipts, tuple) or any(
        not isinstance(item, CheckReceiptEnvelope)
        for item in verification_receipts
    ):
        raise TypeError(
            "verification_receipts must contain CheckReceiptEnvelope values"
        )
    graph = compilation.graph
    expected_ids = {
        f"relation-verification-{question.question_id}": question
        for question in context.questions
    }
    by_id = {item.check_id: item for item in verification_receipts}
    if (
        len(by_id) != len(verification_receipts)
        or set(by_id) != set(expected_ids)
    ):
        raise RelationPromotionError(
            "verification receipts do not equal the question denominator"
        )
    inventory_ref = relation_subject_inventory_ref(inventory)
    receipt_ref_by_question: dict[str, str] = {}
    for check_id, question in sorted(expected_ids.items()):
        receipt = by_id[check_id]
        relations = tuple(
            item for item in graph.relations if question.ref in item.source_refs
        )
        relation_refs = tuple(sorted(item.ref for item in relations))
        required_sources = {
            graph.ref,
            question.ref,
            inventory_ref,
            *(ref for item in relations for ref in item.evidence_refs),
        }
        required_authorities = {
            ref for item in relations for ref in item.authority_refs
        }
        if (
            not relations
            or receipt.checker_id
            != RELATION_VERIFICATION_CHECKERS[question.projection]
            or receipt.branch != context.branch
            or receipt.scope_digest != context.scope_digest
            or receipt.subject_digest != inventory.stage_subject_digest
            or receipt.status is not CheckStatus.PASS
            or receipt.subject_refs != relation_refs
            or receipt.coverage_denominator != relation_refs
            or receipt.covered_refs != relation_refs
            or not required_sources <= set(receipt.source_refs)
            or not required_authorities <= set(receipt.authority_refs)
            or receipt.revalidation_refs
        ):
            raise RelationPromotionError(
                f"{question.ref} lacks an exact independent PASS receipt"
            )
        receipt_ref_by_question[question.ref] = (
            relation_verification_receipt_ref(receipt)
        )

    promoted_relations = []
    bindings = []
    for relation in graph.relations:
        question_refs = tuple(
            sorted(
                question.ref
                for question in context.questions
                if question.ref in relation.source_refs
            )
        )
        if not question_refs:
            raise RelationPromotionError(
                f"{relation.ref} has no controller question owner"
            )
        check_refs = tuple(
            sorted(receipt_ref_by_question[item] for item in question_refs)
        )
        promoted = replace(
            relation,
            epistemic_status=RelationEpistemicStatus.DERIVED,
            source_refs=tuple(
                sorted({*relation.source_refs, graph.ref, *check_refs})
            ),
        )
        promoted_relations.append(promoted)
        bindings.append(
            RelationPromotionBinding(
                relation_ref=relation.ref,
                hypothesis_relation_digest=relation.relation_digest,
                promoted_relation_digest=promoted.relation_digest,
                question_refs=question_refs,
                verification_receipt_refs=check_refs,
            )
        )
    promoted_graph = replace(
        graph,
        graph_id=f"{graph.graph_id}-verified",
        relations=tuple(promoted_relations),
    )
    verification_receipts = tuple(
        sorted(verification_receipts, key=lambda item: item.receipt_digest)
    )
    receipt = RelationPromotionReceipt(
        status=RelationPromotionStatus.VERIFIED_PROMOTION,
        branch=graph.branch,
        stage_id=graph.stage_id,
        state_digest=graph.state_digest,
        scope_digest=graph.scope_digest,
        stage_subject_digest=graph.stage_subject_digest,
        subject_inventory_digest=graph.subject_inventory_digest,
        subject_inventory_ref=inventory_ref,
        context_digest=context.context_digest,
        proposal_digest=compilation.receipt.proposal_digest,
        proposal_graph_ref=graph.ref,
        proposal_graph_digest=graph.graph_digest,
        promoted_graph_ref=promoted_graph.ref,
        promoted_graph_digest=promoted_graph.graph_digest,
        policy_digest=compilation.policy.policy_digest,
        verification_receipt_refs=tuple(
            relation_verification_receipt_ref(item)
            for item in verification_receipts
        ),
        verification_receipt_digests=tuple(
            item.receipt_digest for item in verification_receipts
        ),
        bindings=tuple(bindings),
    )
    return RelationPromotionResult(
        receipt=receipt,
        proposal_graph=graph,
        graph=promoted_graph,
        verification_receipts=verification_receipts,
    )


__all__ = [
    "RelationPromotionBinding",
    "RelationPromotionError",
    "RelationPromotionReceipt",
    "RelationPromotionResult",
    "RelationPromotionStatus",
    "promote_verified_relation_graph",
    "relation_verification_receipt_ref",
]
