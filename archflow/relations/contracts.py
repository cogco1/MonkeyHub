"""Persistence-neutral typed contracts for an architectural relation graph.

The graph is a deterministic projection over exact project records.  It owns
no design, stage-acceptance, persistence, or canonical-write authority.  Graph
coverage and traversal deliberately live outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    enum_value,
    exact_mapping,
    identifier,
    logical_ref,
)
from archflow.project.refs import BranchRef


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}
_MAX_ITEMS = 4_096


class ArchitecturalRelationContractError(ValueError):
    """An architectural relation contract is malformed or has drifted."""


class ArchitecturalNodeKind(StrEnum):
    DECISION = "decision"
    FACT = "fact"
    COMMITMENT = "commitment"
    OBLIGATION = "obligation"
    SYSTEM = "system"
    COMPONENT = "component"
    SPACE = "space"
    OPENING = "opening"
    INTERFACE = "interface"
    ASSEMBLY = "assembly"
    GEOMETRY_BINDING = "geometry_binding"
    GEOMETRY_OBJECT = "geometry_object"
    OPERATION = "operation"
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"


class ArchitecturalRelationKind(StrEnum):
    """Core relation vocabulary; participant roles carry endpoint semantics."""

    COMPOSITION = "composition"
    AGGREGATES = "aggregates"
    PRIMARY_CONTAINS = "primary_contains"
    REFERENCES_ZONE = "references_zone"
    ADJACENT = "adjacent"
    INTERSECTS = "intersects"
    DEPENDENCY = "dependency"
    SUPPORT = "support"
    LOAD_TRANSFER = "load_transfer"
    HOST = "host"
    HOSTS_VOID = "hosts_void"
    FILLS_VOID = "fills_void"
    ACCESS = "access"
    ALLOWS_PASSAGE = "allows_passage"
    CLEARANCE = "clearance"
    REALIZATION = "realization"
    REALIZES = "realizes"
    LINEAGE = "lineage"
    REFINES = "refines"
    REPLACES = "replaces"
    INTERFACE = "interface"
    ALIGNMENT = "alignment"
    SYMMETRIC_WITH = "symmetric_with"
    BLOCKS = "blocks"
    EVIDENCES = "evidences"


class RelationProjection(StrEnum):
    COMPOSITION = "composition"
    IMPACT = "impact"
    SUPPORT = "support"
    HOST = "host"
    ACCESS = "access"
    REALIZATION = "realization"
    LINEAGE = "lineage"
    PROVENANCE = "provenance"


class RelationEpistemicStatus(StrEnum):
    OBSERVED = "observed"
    DECLARED = "declared"
    DERIVED = "derived"
    HYPOTHESIS = "hypothesis"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


class ImpactEffect(StrEnum):
    """Monotone impact values ordered by their intended severity."""

    UNCHANGED = "unchanged"
    REVALIDATE = "revalidate"
    INVALIDATE = "invalidate"


_REQUIRED_RELATION_ROLES: dict[
    ArchitecturalRelationKind,
    frozenset[str],
] = {
    ArchitecturalRelationKind.COMPOSITION: frozenset({"whole", "part"}),
    ArchitecturalRelationKind.AGGREGATES: frozenset({"whole", "part"}),
    ArchitecturalRelationKind.PRIMARY_CONTAINS: frozenset(
        {"container", "contained"}
    ),
    ArchitecturalRelationKind.REFERENCES_ZONE: frozenset(
        {"subject", "zone"}
    ),
    ArchitecturalRelationKind.ADJACENT: frozenset({"first", "second"}),
    ArchitecturalRelationKind.INTERSECTS: frozenset({"first", "second"}),
    ArchitecturalRelationKind.DEPENDENCY: frozenset(
        {"upstream", "downstream"}
    ),
    ArchitecturalRelationKind.SUPPORT: frozenset(
        {"supported", "supporter"}
    ),
    ArchitecturalRelationKind.LOAD_TRANSFER: frozenset(
        {"sender", "receiver"}
    ),
    ArchitecturalRelationKind.HOST: frozenset({"hosted", "host"}),
    ArchitecturalRelationKind.HOSTS_VOID: frozenset({"host", "void"}),
    ArchitecturalRelationKind.FILLS_VOID: frozenset({"void", "fill"}),
    ArchitecturalRelationKind.ACCESS: frozenset({"from", "to"}),
    ArchitecturalRelationKind.ALLOWS_PASSAGE: frozenset({"from", "to"}),
    ArchitecturalRelationKind.CLEARANCE: frozenset(
        {"subject", "clearance"}
    ),
    ArchitecturalRelationKind.REALIZATION: frozenset(
        {"semantic", "realization"}
    ),
    ArchitecturalRelationKind.REALIZES: frozenset(
        {"semantic", "realization"}
    ),
    ArchitecturalRelationKind.LINEAGE: frozenset(
        {"current", "predecessor"}
    ),
    ArchitecturalRelationKind.REFINES: frozenset(
        {"current", "predecessor"}
    ),
    ArchitecturalRelationKind.REPLACES: frozenset(
        {"current", "predecessor"}
    ),
    ArchitecturalRelationKind.INTERFACE: frozenset({"first", "second"}),
    ArchitecturalRelationKind.ALIGNMENT: frozenset({"first", "second"}),
    ArchitecturalRelationKind.SYMMETRIC_WITH: frozenset(
        {"first", "second", "axis"}
    ),
    ArchitecturalRelationKind.BLOCKS: frozenset({"blocker", "blocked"}),
    ArchitecturalRelationKind.EVIDENCES: frozenset(
        {"assertion", "evidence"}
    ),
}

_OPTIONAL_RELATION_ROLES: dict[
    ArchitecturalRelationKind,
    frozenset[str],
] = {
    ArchitecturalRelationKind.LOAD_TRANSFER: frozenset({"via"}),
    ArchitecturalRelationKind.ACCESS: frozenset({"via"}),
    ArchitecturalRelationKind.ALLOWS_PASSAGE: frozenset({"via"}),
}


def allowed_relation_roles(
    kind: ArchitecturalRelationKind,
) -> frozenset[str]:
    """Return the closed participant-role vocabulary for one relation kind."""

    enum_value(kind, ArchitecturalRelationKind, "relation kind")
    return _REQUIRED_RELATION_ROLES[kind] | _OPTIONAL_RELATION_ROLES.get(
        kind,
        frozenset(),
    )


def _require_false_authority(payload: dict[str, object]) -> None:
    if any(
        payload.get(field) is not expected
        for field, expected in _AUTHORITY_FIELDS.items()
    ):
        raise ArchitecturalRelationContractError(
            "architectural relation authority flags changed"
        )


def _optional_ref(value: object, field: str) -> str | None:
    if value is None:
        return None
    return logical_ref(value, field)


@dataclass(frozen=True, slots=True)
class ArchitecturalNode:
    node_ref: str
    node_kind: ArchitecturalNodeKind
    semantic_kind: str
    stage_id: str
    source_refs: tuple[str, ...]
    predecessor_ref: str | None = None

    SCHEMA: ClassVar[str] = "ArchitecturalNode@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "node_ref",
            logical_ref(self.node_ref, "architectural node_ref"),
        )
        enum_value(self.node_kind, ArchitecturalNodeKind, "node_kind")
        identifier(self.semantic_kind, "architectural node semantic_kind")
        identifier(self.stage_id, "architectural node stage_id")
        object.__setattr__(
            self,
            "source_refs",
            deterministic_refs(
                self.source_refs,
                "architectural node source_refs",
            ),
        )
        predecessor = _optional_ref(
            self.predecessor_ref,
            "architectural node predecessor_ref",
        )
        if predecessor == self.node_ref:
            raise ArchitecturalRelationContractError(
                "architectural node cannot precede itself"
            )
        object.__setattr__(self, "predecessor_ref", predecessor)

    @property
    def node_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "node_ref": self.node_ref,
            "node_kind": self.node_kind.value,
            "semantic_kind": self.semantic_kind,
            "stage_id": self.stage_id,
            "source_refs": list(self.source_refs),
            "predecessor_ref": self.predecessor_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ArchitecturalNode":
        payload = exact_mapping(
            value,
            {
                "schema",
                "node_ref",
                "node_kind",
                "semantic_kind",
                "stage_id",
                "source_refs",
                "predecessor_ref",
                *_AUTHORITY_FIELDS,
            },
            "architectural node",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalRelationContractError(
                "unsupported architectural node schema"
            )
        _require_false_authority(payload)
        if not isinstance(payload["source_refs"], list):
            raise TypeError("architectural node source_refs must be a list")
        result = cls(
            node_ref=payload["node_ref"],
            node_kind=ArchitecturalNodeKind(payload["node_kind"]),
            semantic_kind=payload["semantic_kind"],
            stage_id=payload["stage_id"],
            source_refs=tuple(payload["source_refs"]),
            predecessor_ref=payload["predecessor_ref"],
        )
        if result.to_dict() != payload:
            raise ArchitecturalRelationContractError(
                "architectural node serialization is not deterministic"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationParticipant:
    role: str
    node_ref: str
    ordinal: int | None = None

    SCHEMA: ClassVar[str] = "RelationParticipant@1"

    def __post_init__(self) -> None:
        identifier(self.role, "relation participant role")
        object.__setattr__(
            self,
            "node_ref",
            logical_ref(self.node_ref, "relation participant node_ref"),
        )
        if self.ordinal is not None and (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
            or self.ordinal >= _MAX_ITEMS
        ):
            raise ArchitecturalRelationContractError(
                "relation participant ordinal must be inside 0..4095"
            )

    @property
    def identity(self) -> tuple[str, int, str]:
        return (
            self.role,
            -1 if self.ordinal is None else self.ordinal,
            self.node_ref,
        )

    @property
    def participant_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role,
            "node_ref": self.node_ref,
            "ordinal": self.ordinal,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationParticipant":
        payload = exact_mapping(
            value,
            {
                "schema",
                "role",
                "node_ref",
                "ordinal",
                *_AUTHORITY_FIELDS,
            },
            "relation participant",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalRelationContractError(
                "unsupported relation participant schema"
            )
        _require_false_authority(payload)
        result = cls(
            role=payload["role"],
            node_ref=payload["node_ref"],
            ordinal=payload["ordinal"],
        )
        if result.to_dict() != payload:
            raise ArchitecturalRelationContractError(
                "relation participant serialization is not deterministic"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationPropagationRule:
    trigger_role: str
    affected_role: str
    effect: ImpactEffect

    SCHEMA: ClassVar[str] = "RelationPropagationRule@1"

    def __post_init__(self) -> None:
        identifier(self.trigger_role, "propagation trigger_role")
        identifier(self.affected_role, "propagation affected_role")
        if self.trigger_role == self.affected_role:
            raise ArchitecturalRelationContractError(
                "propagation roles must be distinct"
            )
        enum_value(self.effect, ImpactEffect, "propagation effect")
        if self.effect is ImpactEffect.UNCHANGED:
            raise ArchitecturalRelationContractError(
                "propagation rule cannot declare an unchanged effect"
            )

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.trigger_role, self.affected_role, self.effect.value)

    @property
    def rule_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "trigger_role": self.trigger_role,
            "affected_role": self.affected_role,
            "effect": self.effect.value,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationPropagationRule":
        payload = exact_mapping(
            value,
            {
                "schema",
                "trigger_role",
                "affected_role",
                "effect",
                *_AUTHORITY_FIELDS,
            },
            "relation propagation rule",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalRelationContractError(
                "unsupported relation propagation rule schema"
            )
        _require_false_authority(payload)
        result = cls(
            trigger_role=payload["trigger_role"],
            affected_role=payload["affected_role"],
            effect=ImpactEffect(payload["effect"]),
        )
        if result.to_dict() != payload:
            raise ArchitecturalRelationContractError(
                "relation propagation rule serialization is not deterministic"
            )
        return result


@dataclass(frozen=True, slots=True)
class ArchitecturalRelation:
    relation_id: str
    kind: ArchitecturalRelationKind
    participants: tuple[RelationParticipant, ...]
    scenario_ref: str
    epistemic_status: RelationEpistemicStatus
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    propagation_rules: tuple[RelationPropagationRule, ...]
    source_stage_id: str
    predecessor_relation_ref: str | None = None

    SCHEMA: ClassVar[str] = "ArchitecturalRelation@1"

    def __post_init__(self) -> None:
        identifier(self.relation_id, "architectural relation_id")
        enum_value(self.kind, ArchitecturalRelationKind, "relation kind")
        if (
            not isinstance(self.participants, tuple)
            or len(self.participants) < 2
            or len(self.participants) > _MAX_ITEMS
            or any(
                not isinstance(item, RelationParticipant)
                for item in self.participants
            )
        ):
            raise ArchitecturalRelationContractError(
                "architectural relation requires 2..4096 participants"
            )
        participants = tuple(sorted(self.participants, key=lambda item: item.identity))
        identities = tuple(item.identity for item in participants)
        node_refs = tuple(item.node_ref for item in participants)
        if len(identities) != len(set(identities)):
            raise ArchitecturalRelationContractError(
                "architectural relation contains duplicate participants"
            )
        if len(node_refs) != len(set(node_refs)):
            raise ArchitecturalRelationContractError(
                "one node cannot occupy multiple roles in one relation"
            )
        role_members: dict[str, list[RelationParticipant]] = {}
        for participant in participants:
            role_members.setdefault(participant.role, []).append(participant)
        if any(
            len(members) > 1
            and (
                any(item.ordinal is None for item in members)
                or len({item.ordinal for item in members}) != len(members)
            )
            for members in role_members.values()
        ):
            raise ArchitecturalRelationContractError(
                "repeated participant roles require unique explicit ordinals"
            )
        required_roles = _REQUIRED_RELATION_ROLES[self.kind]
        missing_roles = tuple(sorted(required_roles - set(role_members)))
        if missing_roles:
            raise ArchitecturalRelationContractError(
                f"{self.kind.value} relation lacks required roles: "
                + ", ".join(missing_roles)
            )
        unexpected_roles = tuple(
            sorted(set(role_members) - allowed_relation_roles(self.kind))
        )
        if unexpected_roles:
            raise ArchitecturalRelationContractError(
                f"{self.kind.value} relation contains undeclared roles: "
                + ", ".join(unexpected_roles)
            )
        object.__setattr__(self, "participants", participants)

        object.__setattr__(
            self,
            "scenario_ref",
            logical_ref(self.scenario_ref, "architectural relation scenario_ref"),
        )
        enum_value(
            self.epistemic_status,
            RelationEpistemicStatus,
            "relation epistemic_status",
        )
        for field in ("source_refs", "evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    f"architectural relation {field}",
                ),
            )
        if (
            not isinstance(self.propagation_rules, tuple)
            or len(self.propagation_rules) > _MAX_ITEMS
            or any(
                not isinstance(item, RelationPropagationRule)
                for item in self.propagation_rules
            )
        ):
            raise TypeError(
                "propagation_rules must contain RelationPropagationRule values"
            )
        rules = tuple(
            sorted(self.propagation_rules, key=lambda item: item.identity)
        )
        rule_identities = tuple(item.identity for item in rules)
        if len(rule_identities) != len(set(rule_identities)):
            raise ArchitecturalRelationContractError(
                "architectural relation contains duplicate propagation rules"
            )
        participant_roles = set(role_members)
        if any(
            rule.trigger_role not in participant_roles
            or rule.affected_role not in participant_roles
            for rule in rules
        ):
            raise ArchitecturalRelationContractError(
                "propagation rule names an absent participant role"
            )
        object.__setattr__(self, "propagation_rules", rules)
        identifier(self.source_stage_id, "relation source_stage_id")
        predecessor = _optional_ref(
            self.predecessor_relation_ref,
            "architectural relation predecessor_relation_ref",
        )
        if predecessor == self.ref:
            raise ArchitecturalRelationContractError(
                "architectural relation cannot precede itself"
            )
        object.__setattr__(self, "predecessor_relation_ref", predecessor)

    @property
    def ref(self) -> str:
        return f"architectural-relation:{self.relation_id}"

    @property
    def relation_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_id": self.relation_id,
            "kind": self.kind.value,
            "participants": [item.to_dict() for item in self.participants],
            "scenario_ref": self.scenario_ref,
            "epistemic_status": self.epistemic_status.value,
            "source_refs": list(self.source_refs),
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "propagation_rules": [
                item.to_dict() for item in self.propagation_rules
            ],
            "source_stage_id": self.source_stage_id,
            "predecessor_relation_ref": self.predecessor_relation_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ArchitecturalRelation":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_id",
                "kind",
                "participants",
                "scenario_ref",
                "epistemic_status",
                "source_refs",
                "evidence_refs",
                "authority_refs",
                "propagation_rules",
                "source_stage_id",
                "predecessor_relation_ref",
                *_AUTHORITY_FIELDS,
            },
            "architectural relation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalRelationContractError(
                "unsupported architectural relation schema"
            )
        _require_false_authority(payload)
        for field in (
            "participants",
            "source_refs",
            "evidence_refs",
            "authority_refs",
            "propagation_rules",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"architectural relation {field} must be a list")
        result = cls(
            relation_id=payload["relation_id"],
            kind=ArchitecturalRelationKind(payload["kind"]),
            participants=tuple(
                RelationParticipant.from_dict(item)
                for item in payload["participants"]
            ),
            scenario_ref=payload["scenario_ref"],
            epistemic_status=RelationEpistemicStatus(
                payload["epistemic_status"]
            ),
            source_refs=tuple(payload["source_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
            propagation_rules=tuple(
                RelationPropagationRule.from_dict(item)
                for item in payload["propagation_rules"]
            ),
            source_stage_id=payload["source_stage_id"],
            predecessor_relation_ref=payload["predecessor_relation_ref"],
        )
        if result.to_dict() != payload:
            raise ArchitecturalRelationContractError(
                "architectural relation serialization is not deterministic"
            )
        return result


@dataclass(frozen=True, slots=True)
class ArchitecturalRelationGraph:
    graph_id: str
    branch: BranchRef
    stage_id: str
    state_digest: str
    scope_digest: str
    stage_subject_digest: str
    subject_inventory_digest: str
    nodes: tuple[ArchitecturalNode, ...]
    relations: tuple[ArchitecturalRelation, ...]

    SCHEMA: ClassVar[str] = "ArchitecturalRelationGraph@1"

    def __post_init__(self) -> None:
        identifier(self.graph_id, "architectural relation graph_id")
        require_exact_branch(self.branch, "architectural relation graph branch")
        identifier(self.stage_id, "architectural relation graph stage_id")
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "relation graph state_digest"),
        )
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "relation graph scope_digest"),
        )
        object.__setattr__(
            self,
            "stage_subject_digest",
            require_sha256(
                self.stage_subject_digest,
                "relation graph stage_subject_digest",
            ),
        )
        object.__setattr__(
            self,
            "subject_inventory_digest",
            require_sha256(
                self.subject_inventory_digest,
                "relation graph subject_inventory_digest",
            ),
        )
        if (
            not isinstance(self.nodes, tuple)
            or not self.nodes
            or len(self.nodes) > _MAX_ITEMS
            or any(not isinstance(item, ArchitecturalNode) for item in self.nodes)
        ):
            raise ArchitecturalRelationContractError(
                "architectural relation graph requires 1..4096 nodes"
            )
        nodes = tuple(sorted(self.nodes, key=lambda item: item.node_ref))
        node_refs = tuple(item.node_ref for item in nodes)
        if len(node_refs) != len(set(node_refs)):
            raise ArchitecturalRelationContractError(
                "architectural relation graph contains duplicate node refs"
            )
        object.__setattr__(self, "nodes", nodes)

        if (
            not isinstance(self.relations, tuple)
            or len(self.relations) > _MAX_ITEMS
            or any(
                not isinstance(item, ArchitecturalRelation)
                for item in self.relations
            )
        ):
            raise TypeError(
                "relations must contain ArchitecturalRelation values"
            )
        relations = tuple(
            sorted(self.relations, key=lambda item: item.relation_id)
        )
        relation_ids = tuple(item.relation_id for item in relations)
        if len(relation_ids) != len(set(relation_ids)):
            raise ArchitecturalRelationContractError(
                "architectural relation graph contains duplicate relation ids"
            )
        node_universe = set(node_refs)
        unknown_endpoints = tuple(
            sorted(
                {
                    participant.node_ref
                    for relation in relations
                    for participant in relation.participants
                    if participant.node_ref not in node_universe
                }
            )
        )
        if unknown_endpoints:
            raise ArchitecturalRelationContractError(
                "architectural relation endpoint is absent from graph nodes: "
                + ", ".join(unknown_endpoints)
            )
        object.__setattr__(self, "relations", relations)

    @property
    def graph_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"architectural-relation-graph:{self.graph_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "graph_id": self.graph_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "state_digest": self.state_digest,
            "scope_digest": self.scope_digest,
            "stage_subject_digest": self.stage_subject_digest,
            "subject_inventory_digest": self.subject_inventory_digest,
            "nodes": [item.to_dict() for item in self.nodes],
            "relations": [item.to_dict() for item in self.relations],
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ArchitecturalRelationGraph":
        payload = exact_mapping(
            value,
            {
                "schema",
                "graph_id",
                "branch",
                "stage_id",
                "state_digest",
                "scope_digest",
                "stage_subject_digest",
                "subject_inventory_digest",
                "nodes",
                "relations",
                *_AUTHORITY_FIELDS,
            },
            "architectural relation graph",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalRelationContractError(
                "unsupported architectural relation graph schema"
            )
        _require_false_authority(payload)
        if not isinstance(payload["nodes"], list) or not isinstance(
            payload["relations"], list
        ):
            raise TypeError("relation graph nodes and relations must be lists")
        result = cls(
            graph_id=payload["graph_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            state_digest=payload["state_digest"],
            scope_digest=payload["scope_digest"],
            stage_subject_digest=payload["stage_subject_digest"],
            subject_inventory_digest=payload["subject_inventory_digest"],
            nodes=tuple(
                ArchitecturalNode.from_dict(item) for item in payload["nodes"]
            ),
            relations=tuple(
                ArchitecturalRelation.from_dict(item)
                for item in payload["relations"]
            ),
        )
        if result.to_dict() != payload:
            raise ArchitecturalRelationContractError(
                "architectural relation graph serialization is not deterministic"
            )
        return result


__all__ = [
    "ArchitecturalNode",
    "ArchitecturalNodeKind",
    "ArchitecturalRelation",
    "ArchitecturalRelationContractError",
    "ArchitecturalRelationGraph",
    "ArchitecturalRelationKind",
    "ImpactEffect",
    "RelationEpistemicStatus",
    "RelationParticipant",
    "RelationProjection",
    "RelationPropagationRule",
    "allowed_relation_roles",
]
