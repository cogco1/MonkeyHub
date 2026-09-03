"""Project-specific Agent proposals for architectural relations.

The controller supplies the exact node roster, question denominator, and
verified evidence/authority bases.  An Agent may only propose how those known
nodes relate.  Deterministic compilation rejects denominator shrinkage,
unknown endpoints, self-authored references, incompatible projections, and
incomplete topology.  The result remains a proposal that requires independent
geometry/checker receipts before it can participate in stage acceptance.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_identifiers,
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
    text,
)
from archflow.project.refs import BranchRef
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
    ArchitecturalRelation,
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
    RelationEpistemicStatus,
    RelationParticipant,
    RelationProjection,
    allowed_relation_roles,
)
from archive.archflow.relations.coverage import (
    GraphCoverageManifest,
    RelationRequirementSlot,
    SemanticKindRelationPolicy,
    SemanticRelationRule,
    compile_graph_coverage,
    compile_requirement_slots,
)
from archive.archflow.relations.traversal import (
    compile_relation_view,
    find_cycle_node_groups,
    projection_rejects_cycles,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}
_MAX_ITEMS = 4_096


class RelationAuthoringError(ValueError):
    """A project relation context, proposal, or compilation is invalid."""


def _list(payload: dict[str, object], field: str) -> list[object]:
    value = payload[field]
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return text(value, field, maximum=4_000)


def _enum_tuple(
    values: object,
    enum_type: type[StrEnum],
    field: str,
) -> tuple[StrEnum, ...]:
    if (
        not isinstance(values, tuple)
        or not values
        or len(values) > _MAX_ITEMS
        or any(not isinstance(item, enum_type) for item in values)
    ):
        raise TypeError(f"{field} must contain {enum_type.__name__} values")
    normalized = tuple(sorted(set(values), key=lambda item: item.value))
    if len(normalized) != len(values):
        raise RelationAuthoringError(f"{field} contains duplicates")
    return normalized


class RelationBasisKind(StrEnum):
    BRIEF = "brief"
    RAG = "rag"
    HUMAN = "human"
    CAD_READBACK = "cad_readback"


class RelationBasisUse(StrEnum):
    TOPOLOGY = "topology"
    POLICY = "policy"


class RelationAnswerStatus(StrEnum):
    PROPOSED = "proposed"
    UNKNOWN = "unknown"
    NOT_APPLICABLE_REQUESTED = "not_applicable_requested"


class RelationAuthoringCompilationStatus(StrEnum):
    PROPOSAL_COMPILED = "proposal_compiled"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class RelationRuleEnvelope:
    """Controller-owned cardinality and role semantics for one relation kind."""

    relation_kind: ArchitecturalRelationKind
    subject_role: str
    counted_role: str
    minimum_count: int
    maximum_count: int | None

    SCHEMA: ClassVar[str] = "RelationRuleEnvelope@1"

    def __post_init__(self) -> None:
        if not isinstance(self.relation_kind, ArchitecturalRelationKind):
            raise TypeError("relation_kind must be ArchitecturalRelationKind")
        identifier(self.subject_role, "relation rule envelope subject_role")
        identifier(self.counted_role, "relation rule envelope counted_role")
        allowed_roles = allowed_relation_roles(self.relation_kind)
        if (
            self.subject_role not in allowed_roles
            or self.counted_role not in allowed_roles
            or self.subject_role == self.counted_role
        ):
            raise RelationAuthoringError(
                "relation rule envelope requires distinct valid endpoint roles"
            )
        if (
            not isinstance(self.minimum_count, int)
            or isinstance(self.minimum_count, bool)
            or self.minimum_count < 1
        ):
            raise RelationAuthoringError(
                "relation rule envelope minimum_count must be at least one"
            )
        if self.maximum_count is not None and (
            not isinstance(self.maximum_count, int)
            or isinstance(self.maximum_count, bool)
            or self.maximum_count < self.minimum_count
        ):
            raise RelationAuthoringError(
                "relation rule envelope maximum_count precedes minimum_count"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_kind": self.relation_kind.value,
            "subject_role": self.subject_role,
            "counted_role": self.counted_role,
            "minimum_count": self.minimum_count,
            "maximum_count": self.maximum_count,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationRuleEnvelope":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_kind",
                "subject_role",
                "counted_role",
                "minimum_count",
                "maximum_count",
                *_AUTHORITY_FIELDS,
            },
            "relation rule envelope",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationAuthoringError("unsupported relation rule envelope schema")
        result = cls(
            relation_kind=ArchitecturalRelationKind(payload["relation_kind"]),
            subject_role=payload["subject_role"],
            counted_role=payload["counted_role"],
            minimum_count=payload["minimum_count"],
            maximum_count=payload["maximum_count"],
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation rule envelope roundtrip changed")
        return result


_PROJECTION_KINDS: dict[RelationProjection, frozenset[ArchitecturalRelationKind]] = {
    RelationProjection.COMPOSITION: frozenset(
        {
            ArchitecturalRelationKind.COMPOSITION,
            ArchitecturalRelationKind.AGGREGATES,
            ArchitecturalRelationKind.PRIMARY_CONTAINS,
        }
    ),
    RelationProjection.IMPACT: frozenset({ArchitecturalRelationKind.DEPENDENCY}),
    RelationProjection.SUPPORT: frozenset(
        {
            ArchitecturalRelationKind.SUPPORT,
            ArchitecturalRelationKind.LOAD_TRANSFER,
        }
    ),
    RelationProjection.HOST: frozenset(
        {
            ArchitecturalRelationKind.HOST,
            ArchitecturalRelationKind.HOSTS_VOID,
            ArchitecturalRelationKind.FILLS_VOID,
        }
    ),
    RelationProjection.ACCESS: frozenset(
        {
            ArchitecturalRelationKind.ACCESS,
            ArchitecturalRelationKind.ALLOWS_PASSAGE,
        }
    ),
    RelationProjection.REALIZATION: frozenset(
        {
            ArchitecturalRelationKind.REALIZATION,
            ArchitecturalRelationKind.REALIZES,
        }
    ),
    RelationProjection.LINEAGE: frozenset(
        {
            ArchitecturalRelationKind.LINEAGE,
            ArchitecturalRelationKind.REFINES,
            ArchitecturalRelationKind.REPLACES,
        }
    ),
    RelationProjection.PROVENANCE: frozenset(
        {ArchitecturalRelationKind.EVIDENCES}
    ),
}


@dataclass(frozen=True, slots=True)
class RelationBasisBinding:
    """Controller-verified basis an Agent may cite only by ``basis_id``."""

    basis_id: str
    basis_kind: RelationBasisKind
    basis_use: RelationBasisUse
    question_refs: tuple[str, ...]
    allowed_relation_kinds: tuple[ArchitecturalRelationKind, ...]
    epistemic_status: RelationEpistemicStatus
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    summary: str

    SCHEMA: ClassVar[str] = "RelationBasisBinding@1"

    def __post_init__(self) -> None:
        identifier(self.basis_id, "relation basis_id")
        if not isinstance(self.basis_kind, RelationBasisKind):
            raise TypeError("basis_kind must be RelationBasisKind")
        if not isinstance(self.basis_use, RelationBasisUse):
            raise TypeError("basis_use must be RelationBasisUse")
        object.__setattr__(
            self,
            "question_refs",
            deterministic_refs(self.question_refs, "relation basis question_refs"),
        )
        object.__setattr__(
            self,
            "allowed_relation_kinds",
            _enum_tuple(
                self.allowed_relation_kinds,
                ArchitecturalRelationKind,
                "relation basis allowed_relation_kinds",
            ),
        )
        if self.epistemic_status not in {
            RelationEpistemicStatus.OBSERVED,
            RelationEpistemicStatus.DECLARED,
            RelationEpistemicStatus.DERIVED,
        }:
            raise RelationAuthoringError(
                "basis epistemic_status must be independently countable"
            )
        if self.basis_kind is RelationBasisKind.CAD_READBACK:
            if (
                self.basis_use is RelationBasisUse.POLICY
                or self.epistemic_status is not RelationEpistemicStatus.OBSERVED
            ):
                raise RelationAuthoringError(
                    "CAD readback may verify observations but cannot author policy"
                )
        elif self.epistemic_status is RelationEpistemicStatus.OBSERVED:
            raise RelationAuthoringError(
                "non-CAD relation basis cannot claim an observed geometry state"
            )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "relation basis evidence_refs"),
        )
        object.__setattr__(
            self,
            "authority_refs",
            deterministic_refs(self.authority_refs, "relation basis authority_refs"),
        )
        text(self.summary, "relation basis summary", maximum=4_000)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "basis_id": self.basis_id,
            "basis_kind": self.basis_kind.value,
            "basis_use": self.basis_use.value,
            "question_refs": list(self.question_refs),
            "allowed_relation_kinds": [item.value for item in self.allowed_relation_kinds],
            "epistemic_status": self.epistemic_status.value,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "summary": self.summary,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationBasisBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "basis_id",
                "basis_kind",
                "basis_use",
                "question_refs",
                "allowed_relation_kinds",
                "epistemic_status",
                "evidence_refs",
                "authority_refs",
                "summary",
                *_AUTHORITY_FIELDS,
            },
            "relation basis binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationAuthoringError("unsupported relation basis schema")
        result = cls(
            basis_id=payload["basis_id"],
            basis_kind=RelationBasisKind(payload["basis_kind"]),
            basis_use=RelationBasisUse(payload["basis_use"]),
            question_refs=tuple(_list(payload, "question_refs")),
            allowed_relation_kinds=tuple(
                ArchitecturalRelationKind(item)
                for item in _list(payload, "allowed_relation_kinds")
            ),
            epistemic_status=RelationEpistemicStatus(payload["epistemic_status"]),
            evidence_refs=tuple(_list(payload, "evidence_refs")),
            authority_refs=tuple(_list(payload, "authority_refs")),
            summary=payload["summary"],
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation basis roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationDerivationQuestion:
    """One controller-authored member of the exact relation denominator."""

    question_id: str
    projection: RelationProjection
    scenario_ref: str
    subject_refs: tuple[str, ...]
    target_refs: tuple[str, ...]
    allowed_relation_kinds: tuple[ArchitecturalRelationKind, ...]
    rule_envelopes: tuple[RelationRuleEnvelope, ...]
    basis_ids: tuple[str, ...]
    prompt: str
    allow_not_applicable: bool = False
    rule_subject_refs: tuple[str, ...] | None = None
    _replay_schema: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    SCHEMA: ClassVar[str] = "RelationDerivationQuestion@2"
    LEGACY_SCHEMA: ClassVar[str] = "RelationDerivationQuestion@1"

    def __post_init__(self) -> None:
        identifier(self.question_id, "relation question_id")
        if not isinstance(self.projection, RelationProjection):
            raise TypeError("projection must be RelationProjection")
        object.__setattr__(
            self,
            "scenario_ref",
            logical_ref(self.scenario_ref, "relation question scenario_ref"),
        )
        object.__setattr__(
            self,
            "subject_refs",
            deterministic_refs(self.subject_refs, "relation question subject_refs"),
        )
        object.__setattr__(
            self,
            "target_refs",
            deterministic_refs(
                self.target_refs,
                "relation question target_refs",
                allow_empty=True,
            ),
        )
        rule_subject_refs = (
            self.subject_refs
            if self.rule_subject_refs is None
            else deterministic_refs(
                self.rule_subject_refs,
                "relation question rule_subject_refs",
            )
        )
        if not set(rule_subject_refs) <= set(
            self.subject_refs + self.target_refs
        ):
            raise RelationAuthoringError(
                "relation question rule subjects are outside its exact endpoints"
            )
        object.__setattr__(self, "rule_subject_refs", rule_subject_refs)
        kinds = _enum_tuple(
            self.allowed_relation_kinds,
            ArchitecturalRelationKind,
            "relation question allowed_relation_kinds",
        )
        if not set(kinds) <= _PROJECTION_KINDS[self.projection]:
            raise RelationAuthoringError(
                "relation question kind is incompatible with its projection"
            )
        object.__setattr__(self, "allowed_relation_kinds", kinds)
        if (
            not isinstance(self.rule_envelopes, tuple)
            or not self.rule_envelopes
            or any(not isinstance(item, RelationRuleEnvelope) for item in self.rule_envelopes)
        ):
            raise TypeError("rule_envelopes must contain RelationRuleEnvelope values")
        envelopes = tuple(
            sorted(self.rule_envelopes, key=lambda item: item.relation_kind.value)
        )
        envelope_kinds = tuple(item.relation_kind for item in envelopes)
        if len(envelope_kinds) != len(set(envelope_kinds)) or set(envelope_kinds) != set(kinds):
            raise RelationAuthoringError(
                "relation question rule envelopes do not equal its allowed kinds"
            )
        object.__setattr__(self, "rule_envelopes", envelopes)
        object.__setattr__(
            self,
            "basis_ids",
            deterministic_identifiers(self.basis_ids, "relation question basis_ids"),
        )
        text(self.prompt, "relation question prompt", maximum=4_000)
        if type(self.allow_not_applicable) is not bool:
            raise TypeError("allow_not_applicable must be bool")

    @property
    def ref(self) -> str:
        return f"relation-question:{self.question_id}"

    def to_dict(self) -> dict[str, object]:
        schema = self._replay_schema or self.SCHEMA
        payload = {
            "schema": schema,
            "question_id": self.question_id,
            "projection": self.projection.value,
            "scenario_ref": self.scenario_ref,
            "subject_refs": list(self.subject_refs),
            "target_refs": list(self.target_refs),
            "allowed_relation_kinds": [item.value for item in self.allowed_relation_kinds],
            "rule_envelopes": [item.to_dict() for item in self.rule_envelopes],
            "basis_ids": list(self.basis_ids),
            "prompt": self.prompt,
            "allow_not_applicable": self.allow_not_applicable,
            **_AUTHORITY_FIELDS,
        }
        if schema == self.SCHEMA:
            payload["rule_subject_refs"] = list(self.rule_subject_refs or ())
        return payload

    @classmethod
    def from_dict(cls, value: object) -> "RelationDerivationQuestion":
        if not isinstance(value, dict):
            raise TypeError("relation derivation question must be an object")
        schema = value.get("schema")
        if schema not in {cls.SCHEMA, cls.LEGACY_SCHEMA}:
            raise RelationAuthoringError("unsupported relation question schema")
        expected = {
            "schema",
            "question_id",
            "projection",
            "scenario_ref",
            "subject_refs",
            "target_refs",
            "allowed_relation_kinds",
            "rule_envelopes",
            "basis_ids",
            "prompt",
            "allow_not_applicable",
            *_AUTHORITY_FIELDS,
        }
        if schema == cls.SCHEMA:
            expected.add("rule_subject_refs")
        payload = exact_mapping(
            value,
            expected,
            "relation derivation question",
        )
        result = cls(
            question_id=payload["question_id"],
            projection=RelationProjection(payload["projection"]),
            scenario_ref=payload["scenario_ref"],
            subject_refs=tuple(_list(payload, "subject_refs")),
            target_refs=tuple(_list(payload, "target_refs")),
            allowed_relation_kinds=tuple(
                ArchitecturalRelationKind(item)
                for item in _list(payload, "allowed_relation_kinds")
            ),
            rule_envelopes=tuple(
                RelationRuleEnvelope.from_dict(item)
                for item in _list(payload, "rule_envelopes")
            ),
            basis_ids=tuple(_list(payload, "basis_ids")),
            prompt=payload["prompt"],
            allow_not_applicable=payload["allow_not_applicable"],
            rule_subject_refs=(
                tuple(_list(payload, "rule_subject_refs"))
                if schema == cls.SCHEMA
                else tuple(_list(payload, "subject_refs"))
            ),
        )
        if schema == cls.LEGACY_SCHEMA:
            object.__setattr__(result, "_replay_schema", cls.LEGACY_SCHEMA)
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation question roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationAuthoringContext:
    """Exact project state and denominator exposed to one Agent call."""

    context_id: str
    branch: BranchRef
    stage_id: str
    state_digest: str
    scope_digest: str
    stage_subject_digest: str
    subject_inventory_ref: str
    subject_inventory_digest: str
    nodes: tuple[ArchitecturalNode, ...]
    questions: tuple[RelationDerivationQuestion, ...]
    bases: tuple[RelationBasisBinding, ...]

    SCHEMA: ClassVar[str] = "RelationAuthoringContext@1"

    def __post_init__(self) -> None:
        identifier(self.context_id, "relation authoring context_id")
        require_exact_branch(self.branch, "relation authoring branch")
        identifier(self.stage_id, "relation authoring stage_id")
        for field in (
            "state_digest",
            "scope_digest",
            "stage_subject_digest",
            "subject_inventory_digest",
        ):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        object.__setattr__(
            self,
            "subject_inventory_ref",
            logical_ref(self.subject_inventory_ref, "subject_inventory_ref"),
        )
        if self.subject_inventory_ref != (
            f"stage-subject-inventory:{self.subject_inventory_digest}"
        ):
            raise RelationAuthoringError(
                "subject_inventory_ref does not bind its exact digest"
            )
        if (
            not isinstance(self.nodes, tuple)
            or not self.nodes
            or len(self.nodes) > _MAX_ITEMS
            or any(not isinstance(item, ArchitecturalNode) for item in self.nodes)
        ):
            raise TypeError("nodes must contain 1..4096 ArchitecturalNode values")
        nodes = tuple(sorted(self.nodes, key=lambda item: item.node_ref))
        node_refs = tuple(item.node_ref for item in nodes)
        if len(node_refs) != len(set(node_refs)):
            raise RelationAuthoringError("relation authoring context repeats a node")
        if any(item.stage_id != self.stage_id for item in nodes):
            raise RelationAuthoringError("relation authoring node crossed the stage")
        object.__setattr__(self, "nodes", nodes)
        if (
            not isinstance(self.questions, tuple)
            or not self.questions
            or len(self.questions) > _MAX_ITEMS
            or any(not isinstance(item, RelationDerivationQuestion) for item in self.questions)
        ):
            raise TypeError("questions must contain relation questions")
        questions = tuple(sorted(self.questions, key=lambda item: item.question_id))
        question_refs = tuple(item.ref for item in questions)
        if len(question_refs) != len(set(question_refs)):
            raise RelationAuthoringError("relation authoring context repeats a question")
        node_universe = set(node_refs)
        if any(
            not set(
                question.subject_refs
                + question.target_refs
                + tuple(question.rule_subject_refs or ())
            )
            <= node_universe
            for question in questions
        ):
            raise RelationAuthoringError("relation question names an unknown node")
        object.__setattr__(self, "questions", questions)
        if (
            not isinstance(self.bases, tuple)
            or not self.bases
            or len(self.bases) > _MAX_ITEMS
            or any(not isinstance(item, RelationBasisBinding) for item in self.bases)
        ):
            raise TypeError("bases must contain relation basis bindings")
        bases = tuple(sorted(self.bases, key=lambda item: item.basis_id))
        basis_ids = tuple(item.basis_id for item in bases)
        if len(basis_ids) != len(set(basis_ids)):
            raise RelationAuthoringError("relation authoring context repeats a basis")
        known_questions = set(question_refs)
        if any(not set(item.question_refs) <= known_questions for item in bases):
            raise RelationAuthoringError("relation basis names an unknown question")
        basis_by_id = {item.basis_id: item for item in bases}
        question_by_ref = {item.ref: item for item in questions}
        if any(
            item.basis_id not in question_by_ref[question_ref].basis_ids
            for item in bases
            for question_ref in item.question_refs
        ):
            raise RelationAuthoringError(
                "relation basis is not inside the question's exact basis denominator"
            )
        for question in questions:
            if not set(question.basis_ids) <= set(basis_by_id):
                raise RelationAuthoringError("relation question names an unknown basis")
            selected = tuple(basis_by_id[item] for item in question.basis_ids)
            if any(question.ref not in item.question_refs for item in selected):
                raise RelationAuthoringError("relation basis is not bound to its question")
            authorized_kinds = {
                kind for item in selected for kind in item.allowed_relation_kinds
            }
            if not set(question.allowed_relation_kinds) <= authorized_kinds:
                raise RelationAuthoringError(
                    "relation question allows a kind without a verified basis"
                )
        object.__setattr__(self, "bases", bases)

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "context_id": self.context_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "state_digest": self.state_digest,
            "scope_digest": self.scope_digest,
            "stage_subject_digest": self.stage_subject_digest,
            "subject_inventory_ref": self.subject_inventory_ref,
            "subject_inventory_digest": self.subject_inventory_digest,
            "nodes": [item.to_dict() for item in self.nodes],
            "questions": [item.to_dict() for item in self.questions],
            "bases": [item.to_dict() for item in self.bases],
            **_AUTHORITY_FIELDS,
        }

    @property
    def context_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "context_digest": self.context_digest}

    @classmethod
    def from_dict(cls, value: object) -> "RelationAuthoringContext":
        payload = exact_mapping(
            value,
            {
                "schema",
                "context_id",
                "branch",
                "stage_id",
                "state_digest",
                "scope_digest",
                "stage_subject_digest",
                "subject_inventory_ref",
                "subject_inventory_digest",
                "nodes",
                "questions",
                "bases",
                "context_digest",
                *_AUTHORITY_FIELDS,
            },
            "relation authoring context",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationAuthoringError("unsupported relation authoring context schema")
        result = cls(
            context_id=payload["context_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            state_digest=payload["state_digest"],
            scope_digest=payload["scope_digest"],
            stage_subject_digest=payload["stage_subject_digest"],
            subject_inventory_ref=payload["subject_inventory_ref"],
            subject_inventory_digest=payload["subject_inventory_digest"],
            nodes=tuple(ArchitecturalNode.from_dict(item) for item in _list(payload, "nodes")),
            questions=tuple(
                RelationDerivationQuestion.from_dict(item)
                for item in _list(payload, "questions")
            ),
            bases=tuple(RelationBasisBinding.from_dict(item) for item in _list(payload, "bases")),
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation authoring context digest changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationProposalSpec:
    """Agent-authored relation shape with basis IDs instead of raw refs."""

    relation_id: str
    question_refs: tuple[str, ...]
    kind: ArchitecturalRelationKind
    participants: tuple[RelationParticipant, ...]
    scenario_ref: str
    basis_ids: tuple[str, ...]

    SCHEMA: ClassVar[str] = "RelationProposalSpec@1"

    def __post_init__(self) -> None:
        identifier(self.relation_id, "relation proposal relation_id")
        object.__setattr__(
            self,
            "question_refs",
            deterministic_refs(self.question_refs, "relation proposal question_refs"),
        )
        if not isinstance(self.kind, ArchitecturalRelationKind):
            raise TypeError("kind must be ArchitecturalRelationKind")
        if (
            not isinstance(self.participants, tuple)
            or len(self.participants) < 2
            or any(not isinstance(item, RelationParticipant) for item in self.participants)
        ):
            raise TypeError("participants must contain at least two relation participants")
        participants = tuple(sorted(self.participants, key=lambda item: item.identity))
        if len({item.identity for item in participants}) != len(participants):
            raise RelationAuthoringError("relation proposal repeats a participant")
        object.__setattr__(self, "participants", participants)
        object.__setattr__(
            self,
            "scenario_ref",
            logical_ref(self.scenario_ref, "relation proposal scenario_ref"),
        )
        object.__setattr__(
            self,
            "basis_ids",
            deterministic_identifiers(self.basis_ids, "relation proposal basis_ids"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_id": self.relation_id,
            "question_refs": list(self.question_refs),
            "kind": self.kind.value,
            "participants": [item.to_dict() for item in self.participants],
            "scenario_ref": self.scenario_ref,
            "basis_ids": list(self.basis_ids),
            "propagation_authority": False,
            "predecessor_binding_authority": False,
            "proposal_only": True,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationProposalSpec":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_id",
                "question_refs",
                "kind",
                "participants",
                "scenario_ref",
                "basis_ids",
                "propagation_authority",
                "predecessor_binding_authority",
                "proposal_only",
                *_AUTHORITY_FIELDS,
            },
            "relation proposal spec",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["proposal_only"] is not True
        ):
            raise RelationAuthoringError("unsupported relation proposal spec schema")
        result = cls(
            relation_id=payload["relation_id"],
            question_refs=tuple(_list(payload, "question_refs")),
            kind=ArchitecturalRelationKind(payload["kind"]),
            participants=tuple(
                RelationParticipant.from_dict(item) for item in _list(payload, "participants")
            ),
            scenario_ref=payload["scenario_ref"],
            basis_ids=tuple(_list(payload, "basis_ids")),
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation proposal spec roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationRuleProposalSpec:
    """Agent-authored coverage rule; N/A authority is intentionally absent."""

    rule_id: str
    question_refs: tuple[str, ...]
    node_kind: ArchitecturalNodeKind
    semantic_kind: str
    relation_kind: ArchitecturalRelationKind
    subject_role: str
    counted_role: str
    minimum_count: int
    maximum_count: int | None
    scenario_ref: str
    basis_ids: tuple[str, ...]

    SCHEMA: ClassVar[str] = "RelationRuleProposalSpec@1"

    def __post_init__(self) -> None:
        identifier(self.rule_id, "relation rule proposal rule_id")
        object.__setattr__(
            self,
            "question_refs",
            deterministic_refs(self.question_refs, "relation rule proposal question_refs"),
        )
        if not isinstance(self.node_kind, ArchitecturalNodeKind):
            raise TypeError("node_kind must be ArchitecturalNodeKind")
        identifier(self.semantic_kind, "relation rule proposal semantic_kind")
        if not isinstance(self.relation_kind, ArchitecturalRelationKind):
            raise TypeError("relation_kind must be ArchitecturalRelationKind")
        identifier(self.subject_role, "relation rule proposal subject_role")
        identifier(self.counted_role, "relation rule proposal counted_role")
        if (
            not isinstance(self.minimum_count, int)
            or isinstance(self.minimum_count, bool)
            or self.minimum_count < 0
        ):
            raise ValueError("minimum_count must be non-negative")
        if self.maximum_count is not None and (
            not isinstance(self.maximum_count, int)
            or isinstance(self.maximum_count, bool)
            or self.maximum_count < self.minimum_count
        ):
            raise ValueError("maximum_count must be None or at least minimum_count")
        object.__setattr__(
            self,
            "scenario_ref",
            logical_ref(self.scenario_ref, "relation rule proposal scenario_ref"),
        )
        object.__setattr__(
            self,
            "basis_ids",
            deterministic_identifiers(self.basis_ids, "relation rule proposal basis_ids"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "rule_id": self.rule_id,
            "question_refs": list(self.question_refs),
            "node_kind": self.node_kind.value,
            "semantic_kind": self.semantic_kind,
            "relation_kind": self.relation_kind.value,
            "subject_role": self.subject_role,
            "counted_role": self.counted_role,
            "minimum_count": self.minimum_count,
            "maximum_count": self.maximum_count,
            "scenario_ref": self.scenario_ref,
            "basis_ids": list(self.basis_ids),
            "allow_not_applicable": False,
            "proposal_only": True,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationRuleProposalSpec":
        payload = exact_mapping(
            value,
            {
                "schema",
                "rule_id",
                "question_refs",
                "node_kind",
                "semantic_kind",
                "relation_kind",
                "subject_role",
                "counted_role",
                "minimum_count",
                "maximum_count",
                "scenario_ref",
                "basis_ids",
                "allow_not_applicable",
                "proposal_only",
                *_AUTHORITY_FIELDS,
            },
            "relation rule proposal spec",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["proposal_only"] is not True
            or payload["allow_not_applicable"] is not False
        ):
            raise RelationAuthoringError("relation rule proposal acquired authority")
        result = cls(
            rule_id=payload["rule_id"],
            question_refs=tuple(_list(payload, "question_refs")),
            node_kind=ArchitecturalNodeKind(payload["node_kind"]),
            semantic_kind=payload["semantic_kind"],
            relation_kind=ArchitecturalRelationKind(payload["relation_kind"]),
            subject_role=payload["subject_role"],
            counted_role=payload["counted_role"],
            minimum_count=payload["minimum_count"],
            maximum_count=payload["maximum_count"],
            scenario_ref=payload["scenario_ref"],
            basis_ids=tuple(_list(payload, "basis_ids")),
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation rule proposal roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationDerivationAnswer:
    question_ref: str
    status: RelationAnswerStatus
    relation_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]
    rationale: str
    human_question: str | None = None

    SCHEMA: ClassVar[str] = "RelationDerivationAnswer@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "question_ref",
            logical_ref(self.question_ref, "relation answer question_ref"),
        )
        if not isinstance(self.status, RelationAnswerStatus):
            raise TypeError("status must be RelationAnswerStatus")
        object.__setattr__(
            self,
            "relation_ids",
            deterministic_identifiers(
                self.relation_ids,
                "relation answer relation_ids",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "rule_ids",
            deterministic_identifiers(
                self.rule_ids,
                "relation answer rule_ids",
                allow_empty=True,
            ),
        )
        text(self.rationale, "relation answer rationale", maximum=4_000)
        object.__setattr__(
            self,
            "human_question",
            _optional_text(self.human_question, "relation answer human_question"),
        )
        if self.status is RelationAnswerStatus.PROPOSED:
            if not self.relation_ids or not self.rule_ids or self.human_question is not None:
                raise RelationAuthoringError("proposed answer is incomplete")
        elif self.relation_ids or self.rule_ids or self.human_question is None:
            raise RelationAuthoringError(
                "open relation answer cannot smuggle relations or omit its question"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "question_ref": self.question_ref,
            "status": self.status.value,
            "relation_ids": list(self.relation_ids),
            "rule_ids": list(self.rule_ids),
            "rationale": self.rationale,
            "human_question": self.human_question,
            "proposal_only": True,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationDerivationAnswer":
        payload = exact_mapping(
            value,
            {
                "schema",
                "question_ref",
                "status",
                "relation_ids",
                "rule_ids",
                "rationale",
                "human_question",
                "proposal_only",
                *_AUTHORITY_FIELDS,
            },
            "relation derivation answer",
        )
        if payload["schema"] != cls.SCHEMA or payload["proposal_only"] is not True:
            raise RelationAuthoringError("unsupported relation answer schema")
        result = cls(
            question_ref=payload["question_ref"],
            status=RelationAnswerStatus(payload["status"]),
            relation_ids=tuple(_list(payload, "relation_ids")),
            rule_ids=tuple(_list(payload, "rule_ids")),
            rationale=payload["rationale"],
            human_question=payload["human_question"],
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation answer roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationAuthoringProposal:
    context_digest: str
    answers: tuple[RelationDerivationAnswer, ...]
    relations: tuple[RelationProposalSpec, ...]
    rules: tuple[RelationRuleProposalSpec, ...]

    SCHEMA: ClassVar[str] = "RelationAuthoringProposal@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "context_digest",
            require_sha256(self.context_digest, "relation proposal context_digest"),
        )
        if not isinstance(self.answers, tuple) or not self.answers or any(
            not isinstance(item, RelationDerivationAnswer) for item in self.answers
        ):
            raise TypeError("answers must contain relation derivation answers")
        answers = tuple(sorted(self.answers, key=lambda item: item.question_ref))
        if len({item.question_ref for item in answers}) != len(answers):
            raise RelationAuthoringError("relation proposal repeats an answer")
        object.__setattr__(self, "answers", answers)
        if not isinstance(self.relations, tuple) or any(
            not isinstance(item, RelationProposalSpec) for item in self.relations
        ):
            raise TypeError("relations must contain relation proposal specs")
        relations = tuple(sorted(self.relations, key=lambda item: item.relation_id))
        if len({item.relation_id for item in relations}) != len(relations):
            raise RelationAuthoringError("relation proposal repeats a relation_id")
        object.__setattr__(self, "relations", relations)
        if not isinstance(self.rules, tuple) or any(
            not isinstance(item, RelationRuleProposalSpec) for item in self.rules
        ):
            raise TypeError("rules must contain relation rule proposal specs")
        rules = tuple(sorted(self.rules, key=lambda item: item.rule_id))
        if len({item.rule_id for item in rules}) != len(rules):
            raise RelationAuthoringError("relation proposal repeats a rule_id")
        object.__setattr__(self, "rules", rules)
        used_relations = {ref for answer in answers for ref in answer.relation_ids}
        used_rules = {ref for answer in answers for ref in answer.rule_ids}
        if used_relations != {item.relation_id for item in relations}:
            raise RelationAuthoringError("answers do not exactly own proposed relations")
        if used_rules != {item.rule_id for item in rules}:
            raise RelationAuthoringError("answers do not exactly own proposed rules")

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "context_digest": self.context_digest,
            "answers": [item.to_dict() for item in self.answers],
            "relations": [item.to_dict() for item in self.relations],
            "rules": [item.to_dict() for item in self.rules],
            "proposal_only": True,
            "validation_authority": False,
            **_AUTHORITY_FIELDS,
        }

    @property
    def proposal_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "proposal_digest": self.proposal_digest}

    @classmethod
    def from_dict(cls, value: object) -> "RelationAuthoringProposal":
        payload = exact_mapping(
            value,
            {
                "schema",
                "context_digest",
                "answers",
                "relations",
                "rules",
                "proposal_only",
                "validation_authority",
                "proposal_digest",
                *_AUTHORITY_FIELDS,
            },
            "relation authoring proposal",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["proposal_only"] is not True
        ):
            raise RelationAuthoringError("relation proposal acquired authority")
        result = cls(
            context_digest=payload["context_digest"],
            answers=tuple(
                RelationDerivationAnswer.from_dict(item) for item in _list(payload, "answers")
            ),
            relations=tuple(
                RelationProposalSpec.from_dict(item) for item in _list(payload, "relations")
            ),
            rules=tuple(
                RelationRuleProposalSpec.from_dict(item) for item in _list(payload, "rules")
            ),
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation proposal digest changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationTopologyWitness:
    question_ref: str
    subject_ref: str
    target_ref: str
    node_refs: tuple[str, ...]
    relation_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "RelationTopologyWitness@1"

    def __post_init__(self) -> None:
        for field in ("question_ref", "subject_ref", "target_ref"):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        if not isinstance(self.node_refs, tuple) or len(self.node_refs) < 2:
            raise RelationAuthoringError("topology witness requires a path")
        for item in self.node_refs:
            logical_ref(item, "topology witness node_ref")
        if self.node_refs[0] != self.subject_ref or self.node_refs[-1] != self.target_ref:
            raise RelationAuthoringError("topology witness endpoints changed")
        if len(self.relation_refs) != len(self.node_refs) - 1:
            raise RelationAuthoringError("topology witness relation count changed")
        for item in self.relation_refs:
            logical_ref(item, "topology witness relation_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "question_ref": self.question_ref,
            "subject_ref": self.subject_ref,
            "target_ref": self.target_ref,
            "node_refs": list(self.node_refs),
            "relation_refs": list(self.relation_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationTopologyWitness":
        payload = exact_mapping(
            value,
            {
                "schema",
                "question_ref",
                "subject_ref",
                "target_ref",
                "node_refs",
                "relation_refs",
                *_AUTHORITY_FIELDS,
            },
            "relation topology witness",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationAuthoringError("unsupported topology witness schema")
        result = cls(
            question_ref=payload["question_ref"],
            subject_ref=payload["subject_ref"],
            target_ref=payload["target_ref"],
            node_refs=tuple(_list(payload, "node_refs")),
            relation_refs=tuple(_list(payload, "relation_refs")),
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("topology witness roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationAuthoringCompilationReceipt:
    status: RelationAuthoringCompilationStatus
    context_digest: str
    proposal_digest: str
    graph_digest: str | None
    policy_digest: str | None
    coverage_manifest_digest: str | None
    unresolved_question_refs: tuple[str, ...]
    topology_witnesses: tuple[RelationTopologyWitness, ...]

    SCHEMA: ClassVar[str] = "RelationAuthoringCompilationReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.status, RelationAuthoringCompilationStatus):
            raise TypeError("status must be RelationAuthoringCompilationStatus")
        for field in ("context_digest", "proposal_digest"):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        for field in ("graph_digest", "policy_digest", "coverage_manifest_digest"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, require_sha256(value, field))
        object.__setattr__(
            self,
            "unresolved_question_refs",
            deterministic_refs(
                self.unresolved_question_refs,
                "unresolved_question_refs",
                allow_empty=True,
            ),
        )
        if not isinstance(self.topology_witnesses, tuple) or any(
            not isinstance(item, RelationTopologyWitness) for item in self.topology_witnesses
        ):
            raise TypeError("topology_witnesses must contain RelationTopologyWitness values")
        witnesses = tuple(
            sorted(
                self.topology_witnesses,
                key=lambda item: (item.question_ref, item.subject_ref, item.target_ref),
            )
        )
        object.__setattr__(self, "topology_witnesses", witnesses)
        compiled_fields = (self.graph_digest, self.policy_digest, self.coverage_manifest_digest)
        if self.status is RelationAuthoringCompilationStatus.PROPOSAL_COMPILED:
            if any(item is None for item in compiled_fields) or self.unresolved_question_refs:
                raise RelationAuthoringError("compiled relation receipt is incomplete")
        elif any(item is not None for item in compiled_fields) or not self.unresolved_question_refs or witnesses:
            raise RelationAuthoringError("open relation receipt acquired compiled artifacts")

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "status": self.status.value,
            "context_digest": self.context_digest,
            "proposal_digest": self.proposal_digest,
            "graph_digest": self.graph_digest,
            "policy_digest": self.policy_digest,
            "coverage_manifest_digest": self.coverage_manifest_digest,
            "unresolved_question_refs": list(self.unresolved_question_refs),
            "topology_witnesses": [item.to_dict() for item in self.topology_witnesses],
            "requires_independent_verification": True,
            "proposal_only": True,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationAuthoringCompilationReceipt":
        payload = exact_mapping(
            value,
            {
                "schema",
                "status",
                "context_digest",
                "proposal_digest",
                "graph_digest",
                "policy_digest",
                "coverage_manifest_digest",
                "unresolved_question_refs",
                "topology_witnesses",
                "requires_independent_verification",
                "proposal_only",
                *_AUTHORITY_FIELDS,
            },
            "relation authoring compilation receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["requires_independent_verification"] is not True
            or payload["proposal_only"] is not True
        ):
            raise RelationAuthoringError("compilation receipt acquired authority")
        result = cls(
            status=RelationAuthoringCompilationStatus(payload["status"]),
            context_digest=payload["context_digest"],
            proposal_digest=payload["proposal_digest"],
            graph_digest=payload["graph_digest"],
            policy_digest=payload["policy_digest"],
            coverage_manifest_digest=payload["coverage_manifest_digest"],
            unresolved_question_refs=tuple(
                _list(payload, "unresolved_question_refs")
            ),
            topology_witnesses=tuple(
                RelationTopologyWitness.from_dict(item)
                for item in _list(payload, "topology_witnesses")
            ),
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("compilation receipt roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationAuthoringCompilation:
    receipt: RelationAuthoringCompilationReceipt
    graph: ArchitecturalRelationGraph | None = None
    policy: SemanticKindRelationPolicy | None = None
    slots: tuple[RelationRequirementSlot, ...] = ()
    coverage_manifest: GraphCoverageManifest | None = None

    SCHEMA: ClassVar[str] = "RelationAuthoringCompilation@1"

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, RelationAuthoringCompilationReceipt):
            raise TypeError("receipt must be RelationAuthoringCompilationReceipt")
        compiled = (
            self.receipt.status
            is RelationAuthoringCompilationStatus.PROPOSAL_COMPILED
        )
        if compiled:
            if (
                not isinstance(self.graph, ArchitecturalRelationGraph)
                or not isinstance(self.policy, SemanticKindRelationPolicy)
                or not isinstance(self.coverage_manifest, GraphCoverageManifest)
                or not self.slots
            ):
                raise RelationAuthoringError("compiled result lacks relation artifacts")
            if (
                self.receipt.graph_digest != self.graph.graph_digest
                or self.receipt.policy_digest != self.policy.policy_digest
                or self.receipt.coverage_manifest_digest
                != self.coverage_manifest.manifest_digest
            ):
                raise RelationAuthoringError("compiled relation artifact digest changed")
            expected_policy_sources = (
                f"relation-authoring-context:{self.receipt.context_digest}",
                f"relation-authoring-proposal:{self.receipt.proposal_digest}",
            )
            if self.policy.source_refs != expected_policy_sources:
                raise RelationAuthoringError(
                    "proposal policy lost its exact context/proposal binding"
                )
            if any(
                relation.epistemic_status is not RelationEpistemicStatus.HYPOTHESIS
                or relation.propagation_rules
                or relation.predecessor_relation_ref is not None
                or expected_policy_sources[0] not in relation.source_refs
                for relation in self.graph.relations
            ):
                raise RelationAuthoringError(
                    "proposal graph acquired verification, propagation, or lineage authority"
                )
            if self.coverage_manifest.closure_ready:
                raise RelationAuthoringError(
                    "proposal graph cannot carry checker-eligible coverage"
                )
            if self.slots != self.coverage_manifest.slots:
                raise RelationAuthoringError(
                    "proposal compilation slots crossed the coverage manifest"
                )
        elif (
            self.graph is not None
            or self.policy is not None
            or self.slots
            or self.coverage_manifest is not None
        ):
            raise RelationAuthoringError("open relation result carries compiled artifacts")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt": self.receipt.to_dict(),
            "graph": None if self.graph is None else self.graph.to_dict(),
            "policy": None if self.policy is None else self.policy.to_dict(),
            "slots": [item.to_dict() for item in self.slots],
            "coverage_manifest": (
                None
                if self.coverage_manifest is None
                else self.coverage_manifest.to_dict()
            ),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationAuthoringCompilation":
        payload = exact_mapping(
            value,
            {
                "schema",
                "receipt",
                "graph",
                "policy",
                "slots",
                "coverage_manifest",
                *_AUTHORITY_FIELDS,
            },
            "relation authoring compilation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationAuthoringError("unsupported relation compilation schema")
        graph = payload["graph"]
        policy = payload["policy"]
        manifest = payload["coverage_manifest"]
        result = cls(
            receipt=RelationAuthoringCompilationReceipt.from_dict(payload["receipt"]),
            graph=(
                None
                if graph is None
                else ArchitecturalRelationGraph.from_dict(graph)
            ),
            policy=(
                None
                if policy is None
                else SemanticKindRelationPolicy.from_dict(policy)
            ),
            slots=tuple(
                RelationRequirementSlot.from_dict(item)
                for item in _list(payload, "slots")
            ),
            coverage_manifest=(
                None
                if manifest is None
                else GraphCoverageManifest.from_dict(manifest)
            ),
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("relation compilation roundtrip changed")
        return result


def _basis_refs(
    basis_ids: tuple[str, ...],
    basis_by_id: dict[str, RelationBasisBinding],
    *,
    question_refs: tuple[str, ...],
    kind: ArchitecturalRelationKind,
    required_use: RelationBasisUse,
) -> tuple[tuple[str, ...], tuple[str, ...], RelationEpistemicStatus]:
    if not set(basis_ids) <= set(basis_by_id):
        raise RelationAuthoringError("Agent proposed an unknown basis_id")
    bindings = tuple(basis_by_id[item] for item in basis_ids)
    if any(
        item.basis_use is not required_use
        or kind not in item.allowed_relation_kinds
        for item in bindings
    ) or not set(question_refs) <= {
        question_ref
        for item in bindings
        for question_ref in item.question_refs
    }:
        raise RelationAuthoringError("Agent used a basis outside its verified scope")
    statuses = {item.epistemic_status for item in bindings}
    if len(statuses) != 1:
        raise RelationAuthoringError(
            "mixed epistemic bases need a controller-authored consolidated basis"
        )
    evidence_refs = tuple(sorted({ref for item in bindings for ref in item.evidence_refs}))
    authority_refs = tuple(sorted({ref for item in bindings for ref in item.authority_refs}))
    return evidence_refs, authority_refs, statuses.pop()


def _path_witness(
    *,
    question: RelationDerivationQuestion,
    graph: ArchitecturalRelationGraph,
    subject_ref: str,
) -> RelationTopologyWitness:
    view = compile_relation_view(
        graph,
        projection=question.projection,
        scenario_ref=question.scenario_ref,
    )
    adjacency: dict[str, list[object]] = {}
    for arc in view.arcs:
        adjacency.setdefault(arc.source_ref, []).append(arc)
    queue = deque([subject_ref])
    predecessor: dict[str, tuple[str, str] | None] = {subject_ref: None}
    target_set = set(question.target_refs)
    found: str | None = None
    while queue:
        current = queue.popleft()
        if current in target_set and current != subject_ref:
            found = current
            break
        for arc in sorted(adjacency.get(current, ()), key=lambda item: item.identity):
            if arc.target_ref not in predecessor:
                predecessor[arc.target_ref] = (current, arc.relation_ref)
                queue.append(arc.target_ref)
    if found is None:
        raise RelationAuthoringError(
            f"{question.ref} has no proposed path from {subject_ref} to a target"
        )
    nodes = [found]
    relations: list[str] = []
    cursor = found
    while cursor != subject_ref:
        prior = predecessor[cursor]
        assert prior is not None
        cursor, relation_ref = prior
        nodes.append(cursor)
        relations.append(relation_ref)
    nodes.reverse()
    relations.reverse()
    return RelationTopologyWitness(
        question_ref=question.ref,
        subject_ref=subject_ref,
        target_ref=found,
        node_refs=tuple(nodes),
        relation_refs=tuple(relations),
    )


def compile_relation_authoring(
    context: RelationAuthoringContext,
    proposal: RelationAuthoringProposal,
) -> RelationAuthoringCompilation:
    """Compile one exact Agent proposal without granting validation authority."""

    if not isinstance(context, RelationAuthoringContext):
        raise TypeError("context must be RelationAuthoringContext")
    if not isinstance(proposal, RelationAuthoringProposal):
        raise TypeError("proposal must be RelationAuthoringProposal")
    if proposal.context_digest != context.context_digest:
        raise RelationAuthoringError("relation proposal crossed its exact context")
    question_by_ref = {item.ref: item for item in context.questions}
    answer_by_ref = {item.question_ref: item for item in proposal.answers}
    if set(answer_by_ref) != set(question_by_ref):
        raise RelationAuthoringError("Agent answers do not equal the exact question denominator")
    for ref, answer in answer_by_ref.items():
        if (
            answer.status is RelationAnswerStatus.NOT_APPLICABLE_REQUESTED
            and not question_by_ref[ref].allow_not_applicable
        ):
            raise RelationAuthoringError("Agent requested forbidden not-applicable")
    unresolved = tuple(
        sorted(
            ref
            for ref, answer in answer_by_ref.items()
            if answer.status is not RelationAnswerStatus.PROPOSED
        )
    )
    if unresolved:
        if proposal.relations or proposal.rules:
            raise RelationAuthoringError(
                "open relation proposal cannot carry partial relation artifacts"
            )
        return RelationAuthoringCompilation(
            receipt=RelationAuthoringCompilationReceipt(
                status=RelationAuthoringCompilationStatus.OPEN,
                context_digest=context.context_digest,
                proposal_digest=proposal.proposal_digest,
                graph_digest=None,
                policy_digest=None,
                coverage_manifest_digest=None,
                unresolved_question_refs=unresolved,
                topology_witnesses=(),
            )
        )
    basis_by_id = {item.basis_id: item for item in context.bases}
    relation_specs = {item.relation_id: item for item in proposal.relations}
    rule_specs = {item.rule_id: item for item in proposal.rules}
    nodes_by_ref = {item.node_ref: item for item in context.nodes}
    for answer in proposal.answers:
        if any(
            answer.question_ref not in relation_specs[relation_id].question_refs
            for relation_id in answer.relation_ids
        ) or any(
            answer.question_ref not in rule_specs[rule_id].question_refs
            for rule_id in answer.rule_ids
        ):
            raise RelationAuthoringError(
                "answer and proposal specs do not have exact bidirectional ownership"
            )
    relations: list[ArchitecturalRelation] = []
    for spec in proposal.relations:
        if not set(spec.question_refs) <= set(question_by_ref):
            raise RelationAuthoringError("relation proposal names an unknown question")
        if any(
            answer_by_ref[ref].status is not RelationAnswerStatus.PROPOSED
            or spec.relation_id not in answer_by_ref[ref].relation_ids
            for ref in spec.question_refs
        ):
            raise RelationAuthoringError("relation proposal escaped its answer ownership")
        linked = tuple(question_by_ref[ref] for ref in spec.question_refs)
        if any(
            spec.kind not in question.allowed_relation_kinds
            or spec.scenario_ref != question.scenario_ref
            for question in linked
        ):
            raise RelationAuthoringError("relation proposal crossed question kind or scenario")
        if any(item.node_ref not in nodes_by_ref for item in spec.participants):
            raise RelationAuthoringError("relation proposal names an unknown endpoint")
        evidence_refs, authority_refs, _ = _basis_refs(
            spec.basis_ids,
            basis_by_id,
            question_refs=spec.question_refs,
            kind=spec.kind,
            required_use=RelationBasisUse.TOPOLOGY,
        )
        relations.append(
            ArchitecturalRelation(
                relation_id=spec.relation_id,
                kind=spec.kind,
                participants=spec.participants,
                scenario_ref=spec.scenario_ref,
                # A provider-authored relation is never checker-eligible merely
                # because its cited basis is valid.  Independent adoption and
                # geometry/check receipts must promote a successor graph.
                epistemic_status=RelationEpistemicStatus.HYPOTHESIS,
                source_refs=tuple(
                    sorted(
                        {
                            f"relation-authoring-context:{context.context_digest}",
                            *spec.question_refs,
                        }
                    )
                ),
                evidence_refs=evidence_refs,
                authority_refs=authority_refs,
                propagation_rules=(),
                source_stage_id=context.stage_id,
                predecessor_relation_ref=None,
            )
        )
    rules: list[SemanticRelationRule] = []
    for spec in proposal.rules:
        if not set(spec.question_refs) <= set(question_by_ref):
            raise RelationAuthoringError("relation rule names an unknown question")
        if any(
            spec.rule_id not in answer_by_ref[ref].rule_ids
            for ref in spec.question_refs
        ):
            raise RelationAuthoringError("relation rule escaped its answer ownership")
        linked = tuple(question_by_ref[ref] for ref in spec.question_refs)
        if any(
            spec.relation_kind not in question.allowed_relation_kinds
            or spec.scenario_ref != question.scenario_ref
            for question in linked
        ):
            raise RelationAuthoringError("relation rule crossed question kind or scenario")
        for question in linked:
            envelope = next(
                item
                for item in question.rule_envelopes
                if item.relation_kind is spec.relation_kind
            )
            if (
                spec.subject_role != envelope.subject_role
                or spec.counted_role != envelope.counted_role
                or spec.minimum_count != envelope.minimum_count
                or spec.maximum_count != envelope.maximum_count
            ):
                raise RelationAuthoringError(
                    "Agent attempted to weaken the controller rule envelope"
                )
        evidence_refs, authority_refs, _ = _basis_refs(
            spec.basis_ids,
            basis_by_id,
            question_refs=spec.question_refs,
            kind=spec.relation_kind,
            required_use=RelationBasisUse.POLICY,
        )
        rules.append(
            SemanticRelationRule(
                rule_id=spec.rule_id,
                node_kind=spec.node_kind,
                semantic_kind=spec.semantic_kind,
                relation_kind=spec.relation_kind,
                subject_role=spec.subject_role,
                counted_role=spec.counted_role,
                minimum_count=spec.minimum_count,
                maximum_count=spec.maximum_count,
                scenario_ref=spec.scenario_ref,
                evidence_refs=evidence_refs,
                authority_refs=authority_refs,
                allow_not_applicable=False,
            )
        )
    graph = ArchitecturalRelationGraph(
        graph_id=f"agent-relations-{context.context_id}",
        branch=context.branch,
        stage_id=context.stage_id,
        state_digest=context.state_digest,
        scope_digest=context.scope_digest,
        stage_subject_digest=context.stage_subject_digest,
        subject_inventory_digest=context.subject_inventory_digest,
        nodes=context.nodes,
        relations=tuple(relations),
    )
    policy = SemanticKindRelationPolicy(
        policy_id=f"agent-relation-policy-{context.context_id}",
        stage_id=context.stage_id,
        rules=tuple(rules),
        source_refs=(
            f"relation-authoring-context:{context.context_digest}",
            f"relation-authoring-proposal:{proposal.proposal_digest}",
        ),
    )
    slots = compile_requirement_slots(graph, policy)
    slot_keys = {(item.rule_id, item.node_ref) for item in slots}
    for question in context.questions:
        answer = answer_by_ref[question.ref]
        if any(
            not any((rule_id, subject_ref) in slot_keys for rule_id in answer.rule_ids)
            for subject_ref in tuple(question.rule_subject_refs or ())
        ):
            raise RelationAuthoringError(
                f"{question.ref} did not cover every controller-authored subject"
            )
        linked_relation_ids = {
            relation_id
            for relation_id in answer.relation_ids
            if relation_id in relation_specs
        }
        if any(
            not any(
                participant.node_ref == subject_ref
                for relation_id in linked_relation_ids
                for participant in relation_specs[relation_id].participants
            )
            for subject_ref in question.subject_refs
        ):
            raise RelationAuthoringError(
                f"{question.ref} omitted a subject from its proposed relations"
            )
    coverage = compile_graph_coverage(graph, policy, slots)
    if coverage.closure_ready:
        raise RelationAuthoringError(
            "Agent proposal graph unexpectedly became checker-eligible"
        )
    witnesses: list[RelationTopologyWitness] = []
    for question in context.questions:
        if not question.target_refs:
            continue
        view = compile_relation_view(
            graph,
            projection=question.projection,
            scenario_ref=question.scenario_ref,
        )
        if projection_rejects_cycles(question.projection) and find_cycle_node_groups(view):
            raise RelationAuthoringError(f"{question.ref} contains a forbidden cycle")
        for subject_ref in question.subject_refs:
            witnesses.append(
                _path_witness(question=question, graph=graph, subject_ref=subject_ref)
            )
    receipt = RelationAuthoringCompilationReceipt(
        status=RelationAuthoringCompilationStatus.PROPOSAL_COMPILED,
        context_digest=context.context_digest,
        proposal_digest=proposal.proposal_digest,
        graph_digest=graph.graph_digest,
        policy_digest=policy.policy_digest,
        coverage_manifest_digest=coverage.manifest_digest,
        unresolved_question_refs=(),
        topology_witnesses=tuple(witnesses),
    )
    return RelationAuthoringCompilation(
        receipt=receipt,
        graph=graph,
        policy=policy,
        slots=slots,
        coverage_manifest=coverage,
    )


__all__ = [
    "RelationAnswerStatus",
    "RelationAuthoringCompilation",
    "RelationAuthoringCompilationReceipt",
    "RelationAuthoringCompilationStatus",
    "RelationAuthoringContext",
    "RelationAuthoringError",
    "RelationAuthoringProposal",
    "RelationBasisBinding",
    "RelationBasisKind",
    "RelationBasisUse",
    "RelationDerivationAnswer",
    "RelationDerivationQuestion",
    "RelationProposalSpec",
    "RelationRuleProposalSpec",
    "RelationRuleEnvelope",
    "RelationTopologyWitness",
    "compile_relation_authoring",
]
