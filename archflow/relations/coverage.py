"""Evidence-bound semantic relation coverage for architectural graphs.

The module compiles a policy-defined denominator from the nodes already in an
``ArchitecturalRelationGraph`` and then assesses exact graph relations against
that denominator.  It does not create architectural relations, infer missing
design intent, grant not-applicable authority, persist records, or accept a
stage.  A closure digest is emitted only when every mechanical slot is either
satisfied or independently justified as not applicable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
    text,
)
from archflow.relations.contracts import (
    ArchitecturalNodeKind,
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
    RelationEpistemicStatus,
    allowed_relation_roles,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


class RelationCoverageError(ValueError):
    """A relation policy, denominator, disposition, or manifest is invalid."""


def _non_negative_count(
    value: object,
    field: str,
    *,
    allow_none: bool = False,
) -> int | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _list(payload: dict[str, object], field: str) -> list[object]:
    value = payload[field]
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return value


@dataclass(frozen=True, slots=True)
class SemanticRelationRule:
    """One policy rule applying a relation denominator to matching nodes."""

    rule_id: str
    node_kind: ArchitecturalNodeKind
    semantic_kind: str
    relation_kind: ArchitecturalRelationKind
    subject_role: str
    counted_role: str
    minimum_count: int
    maximum_count: int | None
    scenario_ref: str
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    allow_not_applicable: bool = False

    SCHEMA = "SemanticRelationRule@1"

    def __post_init__(self) -> None:
        identifier(self.rule_id, "semantic relation rule_id")
        if not isinstance(self.node_kind, ArchitecturalNodeKind):
            raise TypeError("node_kind must be an ArchitecturalNodeKind")
        identifier(self.semantic_kind, "semantic relation semantic_kind")
        if not isinstance(self.relation_kind, ArchitecturalRelationKind):
            raise TypeError(
                "relation_kind must be an ArchitecturalRelationKind"
            )
        identifier(self.subject_role, "semantic relation subject_role")
        if self.subject_role not in allowed_relation_roles(
            self.relation_kind
        ):
            raise RelationCoverageError(
                "semantic relation subject_role is not valid for relation_kind"
            )
        identifier(self.counted_role, "semantic relation counted_role")
        if self.counted_role not in allowed_relation_roles(
            self.relation_kind
        ):
            raise RelationCoverageError(
                "semantic relation counted_role is not valid for relation_kind"
            )
        minimum = _non_negative_count(
            self.minimum_count,
            "semantic relation minimum_count",
        )
        maximum = _non_negative_count(
            self.maximum_count,
            "semantic relation maximum_count",
            allow_none=True,
        )
        assert isinstance(minimum, int)
        if maximum is not None and maximum < minimum:
            raise RelationCoverageError(
                "semantic relation maximum_count precedes minimum_count"
            )
        object.__setattr__(
            self,
            "scenario_ref",
            logical_ref(self.scenario_ref, "semantic relation scenario_ref"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(
                self.evidence_refs,
                "semantic relation evidence_refs",
            ),
        )
        object.__setattr__(
            self,
            "authority_refs",
            deterministic_refs(
                self.authority_refs,
                "semantic relation authority_refs",
            ),
        )
        if type(self.allow_not_applicable) is not bool:
            raise TypeError("allow_not_applicable must be bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "rule_id": self.rule_id,
            "node_kind": self.node_kind.value,
            "semantic_kind": self.semantic_kind,
            "relation_kind": self.relation_kind.value,
            "subject_role": self.subject_role,
            "counted_role": self.counted_role,
            "minimum_count": self.minimum_count,
            "maximum_count": self.maximum_count,
            "scenario_ref": self.scenario_ref,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "allow_not_applicable": self.allow_not_applicable,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticRelationRule":
        payload = exact_mapping(
            value,
            {
                "schema",
                "rule_id",
                "node_kind",
                "semantic_kind",
                "relation_kind",
                "subject_role",
                "counted_role",
                "minimum_count",
                "maximum_count",
                "scenario_ref",
                "evidence_refs",
                "authority_refs",
                "allow_not_applicable",
                *_AUTHORITY_FIELDS,
            },
            "semantic relation rule",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationCoverageError(
                "unsupported semantic relation rule schema"
            )
        evidence_refs = _list(payload, "evidence_refs")
        authority_refs = _list(payload, "authority_refs")
        result = cls(
            rule_id=payload["rule_id"],
            node_kind=ArchitecturalNodeKind(payload["node_kind"]),
            semantic_kind=payload["semantic_kind"],
            relation_kind=ArchitecturalRelationKind(payload["relation_kind"]),
            subject_role=payload["subject_role"],
            counted_role=payload["counted_role"],
            minimum_count=payload["minimum_count"],
            maximum_count=payload["maximum_count"],
            scenario_ref=payload["scenario_ref"],
            evidence_refs=tuple(evidence_refs),
            authority_refs=tuple(authority_refs),
            allow_not_applicable=payload["allow_not_applicable"],
        )
        if result.to_dict() != payload:
            raise RelationCoverageError(
                "semantic relation rule roundtrip changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class SemanticKindRelationPolicy:
    """Stage-bound semantic-kind relation denominator policy."""

    policy_id: str
    stage_id: str
    rules: tuple[SemanticRelationRule, ...]
    source_refs: tuple[str, ...]

    SCHEMA = "SemanticKindRelationPolicy@1"

    def __post_init__(self) -> None:
        identifier(self.policy_id, "semantic relation policy_id")
        identifier(self.stage_id, "semantic relation policy stage_id")
        if (
            not isinstance(self.rules, tuple)
            or not self.rules
            or any(not isinstance(item, SemanticRelationRule) for item in self.rules)
        ):
            raise TypeError(
                "rules must contain SemanticRelationRule values"
            )
        rules = tuple(sorted(self.rules, key=lambda item: item.rule_id))
        rule_ids = tuple(item.rule_id for item in rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise RelationCoverageError(
                "semantic relation policy duplicates a rule_id"
            )
        object.__setattr__(self, "rules", rules)
        object.__setattr__(
            self,
            "source_refs",
            deterministic_refs(
                self.source_refs,
                "semantic relation policy source_refs",
            ),
        )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "stage_id": self.stage_id,
            "rules": [item.to_dict() for item in self.rules],
            "source_refs": list(self.source_refs),
            **_AUTHORITY_FIELDS,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def policy_digest(self) -> str:
        """Explicit alias used by bound coverage manifests."""

        return self.digest

    @property
    def ref(self) -> str:
        return f"semantic-relation-policy:{self.digest}"

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "digest": self.digest}

    @classmethod
    def from_dict(cls, value: object) -> "SemanticKindRelationPolicy":
        payload = exact_mapping(
            value,
            {
                "schema",
                "policy_id",
                "stage_id",
                "rules",
                "source_refs",
                "digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic kind relation policy",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationCoverageError(
                "unsupported semantic kind relation policy schema"
            )
        rules = _list(payload, "rules")
        source_refs = _list(payload, "source_refs")
        result = cls(
            policy_id=payload["policy_id"],
            stage_id=payload["stage_id"],
            rules=tuple(SemanticRelationRule.from_dict(item) for item in rules),
            source_refs=tuple(source_refs),
        )
        if result.to_dict() != payload:
            raise RelationCoverageError(
                "semantic kind relation policy digest changed"
            )
        return result


def _slot_identity(
    *,
    policy_id: str,
    stage_id: str,
    rule: SemanticRelationRule,
    node_ref: str,
) -> dict[str, object]:
    return {
        "schema": "RelationRequirementSlotIdentity@1",
        "policy_id": policy_id,
        "stage_id": stage_id,
        "rule_id": rule.rule_id,
        "node_ref": node_ref,
        "node_kind": rule.node_kind.value,
        "semantic_kind": rule.semantic_kind,
        "relation_kind": rule.relation_kind.value,
        "subject_role": rule.subject_role,
        "counted_role": rule.counted_role,
        "minimum_count": rule.minimum_count,
        "maximum_count": rule.maximum_count,
        "scenario_ref": rule.scenario_ref,
        "evidence_refs": list(rule.evidence_refs),
        "authority_refs": list(rule.authority_refs),
        "allow_not_applicable": rule.allow_not_applicable,
    }


def _slot_ref(
    *,
    policy_id: str,
    stage_id: str,
    rule: SemanticRelationRule,
    node_ref: str,
) -> str:
    return "relation-slot:" + canonical_digest(
        _slot_identity(
            policy_id=policy_id,
            stage_id=stage_id,
            rule=rule,
            node_ref=node_ref,
        )
    )


@dataclass(frozen=True, slots=True)
class RelationRequirementSlot:
    """Mechanical node-and-rule denominator member."""

    slot_ref: str
    policy_id: str
    stage_id: str
    rule_id: str
    node_ref: str
    node_kind: ArchitecturalNodeKind
    semantic_kind: str
    relation_kind: ArchitecturalRelationKind
    subject_role: str
    counted_role: str
    minimum_count: int
    maximum_count: int | None
    scenario_ref: str
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    allow_not_applicable: bool = False

    SCHEMA = "RelationRequirementSlot@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "slot_ref",
            logical_ref(self.slot_ref, "relation requirement slot_ref"),
        )
        identifier(self.policy_id, "relation requirement policy_id")
        identifier(self.stage_id, "relation requirement stage_id")
        identifier(self.rule_id, "relation requirement rule_id")
        object.__setattr__(
            self,
            "node_ref",
            logical_ref(self.node_ref, "relation requirement node_ref"),
        )
        if not isinstance(self.node_kind, ArchitecturalNodeKind):
            raise TypeError("node_kind must be an ArchitecturalNodeKind")
        identifier(self.semantic_kind, "relation requirement semantic_kind")
        if not isinstance(self.relation_kind, ArchitecturalRelationKind):
            raise TypeError(
                "relation_kind must be an ArchitecturalRelationKind"
            )
        identifier(self.subject_role, "relation requirement subject_role")
        if self.subject_role not in allowed_relation_roles(
            self.relation_kind
        ):
            raise RelationCoverageError(
                "relation requirement subject_role is not valid for relation_kind"
            )
        identifier(self.counted_role, "relation requirement counted_role")
        if self.counted_role not in allowed_relation_roles(
            self.relation_kind
        ):
            raise RelationCoverageError(
                "relation requirement counted_role is not valid for relation_kind"
            )
        minimum = _non_negative_count(
            self.minimum_count,
            "relation requirement minimum_count",
        )
        maximum = _non_negative_count(
            self.maximum_count,
            "relation requirement maximum_count",
            allow_none=True,
        )
        assert isinstance(minimum, int)
        if maximum is not None and maximum < minimum:
            raise RelationCoverageError(
                "relation requirement maximum_count precedes minimum_count"
            )
        object.__setattr__(
            self,
            "scenario_ref",
            logical_ref(self.scenario_ref, "relation requirement scenario_ref"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(
                self.evidence_refs,
                "relation requirement evidence_refs",
            ),
        )
        object.__setattr__(
            self,
            "authority_refs",
            deterministic_refs(
                self.authority_refs,
                "relation requirement authority_refs",
            ),
        )
        if type(self.allow_not_applicable) is not bool:
            raise TypeError("allow_not_applicable must be bool")
        rule = SemanticRelationRule(
            rule_id=self.rule_id,
            node_kind=self.node_kind,
            semantic_kind=self.semantic_kind,
            relation_kind=self.relation_kind,
            subject_role=self.subject_role,
            counted_role=self.counted_role,
            minimum_count=self.minimum_count,
            maximum_count=self.maximum_count,
            scenario_ref=self.scenario_ref,
            evidence_refs=self.evidence_refs,
            authority_refs=self.authority_refs,
            allow_not_applicable=self.allow_not_applicable,
        )
        expected = _slot_ref(
            policy_id=self.policy_id,
            stage_id=self.stage_id,
            rule=rule,
            node_ref=self.node_ref,
        )
        if self.slot_ref != expected:
            raise RelationCoverageError(
                "relation requirement slot_ref was not mechanically derived"
            )

    @classmethod
    def compile(
        cls,
        *,
        policy: SemanticKindRelationPolicy,
        rule: SemanticRelationRule,
        node_ref: str,
    ) -> "RelationRequirementSlot":
        normalized_ref = logical_ref(node_ref, "relation requirement node_ref")
        return cls(
            slot_ref=_slot_ref(
                policy_id=policy.policy_id,
                stage_id=policy.stage_id,
                rule=rule,
                node_ref=normalized_ref,
            ),
            policy_id=policy.policy_id,
            stage_id=policy.stage_id,
            rule_id=rule.rule_id,
            node_ref=normalized_ref,
            node_kind=rule.node_kind,
            semantic_kind=rule.semantic_kind,
            relation_kind=rule.relation_kind,
            subject_role=rule.subject_role,
            counted_role=rule.counted_role,
            minimum_count=rule.minimum_count,
            maximum_count=rule.maximum_count,
            scenario_ref=rule.scenario_ref,
            evidence_refs=rule.evidence_refs,
            authority_refs=rule.authority_refs,
            allow_not_applicable=rule.allow_not_applicable,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "slot_ref": self.slot_ref,
            "policy_id": self.policy_id,
            "stage_id": self.stage_id,
            "rule_id": self.rule_id,
            "node_ref": self.node_ref,
            "node_kind": self.node_kind.value,
            "semantic_kind": self.semantic_kind,
            "relation_kind": self.relation_kind.value,
            "subject_role": self.subject_role,
            "counted_role": self.counted_role,
            "minimum_count": self.minimum_count,
            "maximum_count": self.maximum_count,
            "scenario_ref": self.scenario_ref,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "allow_not_applicable": self.allow_not_applicable,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationRequirementSlot":
        payload = exact_mapping(
            value,
            {
                "schema",
                "slot_ref",
                "policy_id",
                "stage_id",
                "rule_id",
                "node_ref",
                "node_kind",
                "semantic_kind",
                "relation_kind",
                "subject_role",
                "counted_role",
                "minimum_count",
                "maximum_count",
                "scenario_ref",
                "evidence_refs",
                "authority_refs",
                "allow_not_applicable",
                *_AUTHORITY_FIELDS,
            },
            "relation requirement slot",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationCoverageError(
                "unsupported relation requirement slot schema"
            )
        evidence_refs = _list(payload, "evidence_refs")
        authority_refs = _list(payload, "authority_refs")
        result = cls(
            slot_ref=payload["slot_ref"],
            policy_id=payload["policy_id"],
            stage_id=payload["stage_id"],
            rule_id=payload["rule_id"],
            node_ref=payload["node_ref"],
            node_kind=ArchitecturalNodeKind(payload["node_kind"]),
            semantic_kind=payload["semantic_kind"],
            relation_kind=ArchitecturalRelationKind(payload["relation_kind"]),
            subject_role=payload["subject_role"],
            counted_role=payload["counted_role"],
            minimum_count=payload["minimum_count"],
            maximum_count=payload["maximum_count"],
            scenario_ref=payload["scenario_ref"],
            evidence_refs=tuple(evidence_refs),
            authority_refs=tuple(authority_refs),
            allow_not_applicable=payload["allow_not_applicable"],
        )
        if result.to_dict() != payload:
            raise RelationCoverageError(
                "relation requirement slot roundtrip changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationNotApplicable:
    """Independent evidence and authority for one zero-match slot."""

    slot_ref: str
    reason: str
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA = "RelationNotApplicable@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "slot_ref",
            logical_ref(self.slot_ref, "relation not-applicable slot_ref"),
        )
        text(self.reason, "relation not-applicable reason")
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(
                self.evidence_refs,
                "relation not-applicable evidence_refs",
            ),
        )
        object.__setattr__(
            self,
            "authority_refs",
            deterministic_refs(
                self.authority_refs,
                "relation not-applicable authority_refs",
            ),
        )
        if set(self.evidence_refs) & set(self.authority_refs):
            raise RelationCoverageError(
                "not-applicable evidence and authority refs must be independent"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "slot_ref": self.slot_ref,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationNotApplicable":
        payload = exact_mapping(
            value,
            {
                "schema",
                "slot_ref",
                "reason",
                "evidence_refs",
                "authority_refs",
                *_AUTHORITY_FIELDS,
            },
            "relation not-applicable declaration",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationCoverageError(
                "unsupported relation not-applicable schema"
            )
        evidence_refs = _list(payload, "evidence_refs")
        authority_refs = _list(payload, "authority_refs")
        result = cls(
            slot_ref=payload["slot_ref"],
            reason=payload["reason"],
            evidence_refs=tuple(evidence_refs),
            authority_refs=tuple(authority_refs),
        )
        if result.to_dict() != payload:
            raise RelationCoverageError(
                "relation not-applicable roundtrip changed"
            )
        return result


class RelationCoverageStatus(StrEnum):
    """Mechanical coverage result for one exact requirement slot."""

    SATISFIED = "SATISFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"
    VIOLATED = "VIOLATED"


@dataclass(frozen=True, slots=True)
class RelationCoverageDisposition:
    """Exact relation matches and resulting status for one slot."""

    slot_ref: str
    status: RelationCoverageStatus
    relation_refs: tuple[str, ...]
    match_refs: tuple[str, ...]
    not_applicable: RelationNotApplicable | None = None

    SCHEMA = "RelationCoverageDisposition@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "slot_ref",
            logical_ref(self.slot_ref, "relation disposition slot_ref"),
        )
        if not isinstance(self.status, RelationCoverageStatus):
            raise TypeError("status must be a RelationCoverageStatus")
        object.__setattr__(
            self,
            "relation_refs",
            deterministic_refs(
                self.relation_refs,
                "relation disposition relation_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "match_refs",
            deterministic_refs(
                self.match_refs,
                "relation disposition match_refs",
                allow_empty=True,
            ),
        )
        if self.match_refs and not self.relation_refs:
            raise RelationCoverageError(
                "relation disposition matches require relation_refs"
            )
        if self.status is RelationCoverageStatus.NOT_APPLICABLE:
            if (
                not isinstance(self.not_applicable, RelationNotApplicable)
                or self.not_applicable.slot_ref != self.slot_ref
            ):
                raise RelationCoverageError(
                    "not-applicable disposition needs its exact declaration"
                )
            if self.relation_refs or self.match_refs:
                raise RelationCoverageError(
                    "not-applicable disposition cannot retain relation matches"
                )
        elif self.not_applicable is not None:
            raise RelationCoverageError(
                "only not-applicable status may retain a declaration"
            )

    @property
    def matched_count(self) -> int:
        return len(self.match_refs)

    @property
    def matched_relation_refs(self) -> tuple[str, ...]:
        """Readability alias for callers rendering the disposition."""

        return self.relation_refs

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "slot_ref": self.slot_ref,
            "status": self.status.value,
            "relation_refs": list(self.relation_refs),
            "match_refs": list(self.match_refs),
            "not_applicable": (
                None
                if self.not_applicable is None
                else self.not_applicable.to_dict()
            ),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationCoverageDisposition":
        payload = exact_mapping(
            value,
            {
                "schema",
                "slot_ref",
                "status",
                "relation_refs",
                "match_refs",
                "not_applicable",
                *_AUTHORITY_FIELDS,
            },
            "relation coverage disposition",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationCoverageError(
                "unsupported relation coverage disposition schema"
            )
        relation_refs = _list(payload, "relation_refs")
        match_refs = _list(payload, "match_refs")
        raw_not_applicable = payload["not_applicable"]
        result = cls(
            slot_ref=payload["slot_ref"],
            status=RelationCoverageStatus(payload["status"]),
            relation_refs=tuple(relation_refs),
            match_refs=tuple(match_refs),
            not_applicable=(
                None
                if raw_not_applicable is None
                else RelationNotApplicable.from_dict(raw_not_applicable)
            ),
        )
        if result.to_dict() != payload:
            raise RelationCoverageError(
                "relation coverage disposition roundtrip changed"
            )
        return result


def _closure_content(
    *,
    graph_digest: str,
    subject_inventory_digest: str,
    policy_digest: str,
    slots: tuple[RelationRequirementSlot, ...],
    dispositions: tuple[RelationCoverageDisposition, ...],
) -> dict[str, object]:
    return {
        "schema": "GraphCoverageClosure@1",
        "graph_digest": graph_digest,
        "subject_inventory_digest": subject_inventory_digest,
        "policy_digest": policy_digest,
        "slots": [item.to_dict() for item in slots],
        "dispositions": [item.to_dict() for item in dispositions],
    }


@dataclass(frozen=True, slots=True)
class GraphCoverageManifest:
    """Graph-bound proof that the complete slot denominator was assessed."""

    graph_digest: str
    subject_inventory_digest: str
    policy_digest: str
    slots: tuple[RelationRequirementSlot, ...]
    dispositions: tuple[RelationCoverageDisposition, ...]
    closure_ready: bool
    closure_digest: str | None

    SCHEMA = "GraphCoverageManifest@1"

    def __post_init__(self) -> None:
        for field in (
            "graph_digest",
            "subject_inventory_digest",
            "policy_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        if not isinstance(self.slots, tuple) or any(
            not isinstance(item, RelationRequirementSlot) for item in self.slots
        ):
            raise TypeError(
                "slots must contain RelationRequirementSlot values"
            )
        slots = tuple(sorted(self.slots, key=lambda item: item.slot_ref))
        slot_refs = tuple(item.slot_ref for item in slots)
        if len(slot_refs) != len(set(slot_refs)):
            raise RelationCoverageError(
                "graph coverage manifest duplicates a requirement slot"
            )
        object.__setattr__(self, "slots", slots)
        if not isinstance(self.dispositions, tuple) or any(
            not isinstance(item, RelationCoverageDisposition)
            for item in self.dispositions
        ):
            raise TypeError(
                "dispositions must contain RelationCoverageDisposition values"
            )
        dispositions = tuple(
            sorted(self.dispositions, key=lambda item: item.slot_ref)
        )
        disposition_refs = tuple(item.slot_ref for item in dispositions)
        if len(disposition_refs) != len(set(disposition_refs)):
            raise RelationCoverageError(
                "graph coverage manifest duplicates a disposition"
            )
        if disposition_refs != slot_refs:
            raise RelationCoverageError(
                "graph coverage dispositions do not exactly cover slots"
            )
        object.__setattr__(self, "dispositions", dispositions)
        for slot, disposition in zip(slots, dispositions, strict=True):
            count = disposition.matched_count
            maximum = slot.maximum_count
            if disposition.status is RelationCoverageStatus.SATISFIED:
                valid = count >= slot.minimum_count and (
                    maximum is None or count <= maximum
                )
            elif disposition.status is RelationCoverageStatus.NOT_APPLICABLE:
                valid = count == 0
            elif disposition.status is RelationCoverageStatus.UNKNOWN:
                valid = count < slot.minimum_count
            else:
                valid = maximum is not None and count > maximum
            if not valid:
                raise RelationCoverageError(
                    "relation coverage disposition contradicts slot counts"
                )
        if type(self.closure_ready) is not bool:
            raise TypeError("closure_ready must be bool")
        expected_ready = all(
            item.status
            in {
                RelationCoverageStatus.SATISFIED,
                RelationCoverageStatus.NOT_APPLICABLE,
            }
            for item in dispositions
        )
        if self.closure_ready is not expected_ready:
            raise RelationCoverageError(
                "graph coverage closure_ready was caller-authored"
            )
        if expected_ready:
            digest = require_sha256(self.closure_digest, "closure_digest")
            expected_digest = canonical_digest(
                _closure_content(
                    graph_digest=self.graph_digest,
                    subject_inventory_digest=self.subject_inventory_digest,
                    policy_digest=self.policy_digest,
                    slots=slots,
                    dispositions=dispositions,
                )
            )
            if digest != expected_digest:
                raise RelationCoverageError(
                    "graph coverage closure_digest changed"
                )
            object.__setattr__(self, "closure_digest", digest)
        elif self.closure_digest is not None:
            raise RelationCoverageError(
                "blocked graph coverage cannot have a closure_digest"
            )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "graph_digest": self.graph_digest,
            "subject_inventory_digest": self.subject_inventory_digest,
            "policy_digest": self.policy_digest,
            "slots": [item.to_dict() for item in self.slots],
            "dispositions": [item.to_dict() for item in self.dispositions],
            "closure_ready": self.closure_ready,
            "closure_digest": self.closure_digest,
            **_AUTHORITY_FIELDS,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def manifest_digest(self) -> str:
        return self.digest

    @property
    def ref(self) -> str:
        return f"graph-coverage-manifest:{self.digest}"

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "digest": self.digest}

    @classmethod
    def from_dict(cls, value: object) -> "GraphCoverageManifest":
        payload = exact_mapping(
            value,
            {
                "schema",
                "graph_digest",
                "subject_inventory_digest",
                "policy_digest",
                "slots",
                "dispositions",
                "closure_ready",
                "closure_digest",
                "digest",
                *_AUTHORITY_FIELDS,
            },
            "graph coverage manifest",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationCoverageError(
                "unsupported graph coverage manifest schema"
            )
        slots = _list(payload, "slots")
        dispositions = _list(payload, "dispositions")
        result = cls(
            graph_digest=payload["graph_digest"],
            subject_inventory_digest=payload["subject_inventory_digest"],
            policy_digest=payload["policy_digest"],
            slots=tuple(RelationRequirementSlot.from_dict(item) for item in slots),
            dispositions=tuple(
                RelationCoverageDisposition.from_dict(item)
                for item in dispositions
            ),
            closure_ready=payload["closure_ready"],
            closure_digest=payload["closure_digest"],
        )
        if result.to_dict() != payload:
            raise RelationCoverageError(
                "graph coverage manifest digest changed"
            )
        return result


def _require_graph_policy_stage(
    graph: ArchitecturalRelationGraph,
    policy: SemanticKindRelationPolicy,
) -> None:
    if not isinstance(graph, ArchitecturalRelationGraph):
        raise TypeError("graph must be an ArchitecturalRelationGraph")
    if not isinstance(policy, SemanticKindRelationPolicy):
        raise TypeError("policy must be a SemanticKindRelationPolicy")
    if graph.stage_id != policy.stage_id:
        raise RelationCoverageError(
            "semantic relation policy stage does not match graph stage"
        )


def compile_requirement_slots(
    graph: ArchitecturalRelationGraph,
    policy: SemanticKindRelationPolicy,
) -> tuple[RelationRequirementSlot, ...]:
    """Mechanically cross matching graph nodes with applicable policy rules."""

    _require_graph_policy_stage(graph, policy)
    slots: list[RelationRequirementSlot] = []
    for node in graph.nodes:
        if node.stage_id != policy.stage_id:
            continue
        for rule in policy.rules:
            if (
                node.node_kind is rule.node_kind
                and node.semantic_kind == rule.semantic_kind
            ):
                slots.append(
                    RelationRequirementSlot.compile(
                        policy=policy,
                        rule=rule,
                        node_ref=node.node_ref,
                    )
                )
    result = tuple(sorted(slots, key=lambda item: item.slot_ref))
    refs = tuple(item.slot_ref for item in result)
    if len(refs) != len(set(refs)):
        raise RelationCoverageError(
            "mechanical relation requirement slots are not unique"
        )
    return result


def _relation_ref(relation: object) -> str:
    for field in ("relation_ref", "ref"):
        value = getattr(relation, field, None)
        if isinstance(value, str):
            return logical_ref(value, "matched relation ref")
    relation_id = getattr(relation, "relation_id", None)
    if isinstance(relation_id, str):
        try:
            return logical_ref(relation_id, "matched relation ref")
        except ValueError:
            identifier(relation_id, "matched relation_id")
            return logical_ref(
                f"relation:{relation_id}",
                "matched relation ref",
            )
    raise TypeError("architectural relation does not expose a logical ref")


def _matching_relation_matches(
    graph: ArchitecturalRelationGraph,
    slot: RelationRequirementSlot,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    relations = tuple(
        relation
        for relation in graph.relations
        if relation.kind is slot.relation_kind
        and relation.epistemic_status
        in {
            RelationEpistemicStatus.OBSERVED,
            RelationEpistemicStatus.DECLARED,
            RelationEpistemicStatus.DERIVED,
        }
        and relation.scenario_ref
        in {slot.scenario_ref, "scenario:universal"}
        and any(
            participant.role == slot.subject_role
            and participant.node_ref == slot.node_ref
            for participant in relation.participants
        )
    )
    relation_refs = tuple(
        sorted({_relation_ref(relation) for relation in relations})
    )
    match_refs = set()
    for relation in relations:
        relation_ref = _relation_ref(relation)
        counted = tuple(
            participant
            for participant in relation.participants
            if participant.role == slot.counted_role
            and (
                slot.counted_role != slot.subject_role
                or participant.node_ref == slot.node_ref
            )
        )
        for participant in counted:
            match_refs.add(
                "relation-match:"
                + canonical_digest(
                    {
                        "schema": "RelationCoverageMatchIdentity@1",
                        "relation_ref": relation_ref,
                        "subject_role": slot.subject_role,
                        "subject_ref": slot.node_ref,
                        "counted_role": slot.counted_role,
                        "counted_ref": participant.node_ref,
                    }
                )
            )
    return relation_refs, tuple(sorted(match_refs))


def compile_graph_coverage(
    graph: ArchitecturalRelationGraph,
    policy: SemanticKindRelationPolicy,
    slots: tuple[RelationRequirementSlot, ...],
    not_applicable: tuple[RelationNotApplicable, ...] = (),
) -> GraphCoverageManifest:
    """Assess exact typed/scenario/role relation counts and compile closure."""

    _require_graph_policy_stage(graph, policy)
    if not isinstance(slots, tuple) or any(
        not isinstance(item, RelationRequirementSlot) for item in slots
    ):
        raise TypeError("slots must contain RelationRequirementSlot values")
    expected_slots = compile_requirement_slots(graph, policy)
    supplied_slots = tuple(sorted(slots, key=lambda item: item.slot_ref))
    if supplied_slots != expected_slots:
        raise RelationCoverageError(
            "coverage slots do not equal the mechanical graph denominator"
        )
    if not isinstance(not_applicable, tuple) or any(
        not isinstance(item, RelationNotApplicable)
        for item in not_applicable
    ):
        raise TypeError(
            "not_applicable must contain RelationNotApplicable values"
        )
    declarations = tuple(
        sorted(not_applicable, key=lambda item: item.slot_ref)
    )
    declaration_refs = tuple(item.slot_ref for item in declarations)
    if len(declaration_refs) != len(set(declaration_refs)):
        raise RelationCoverageError(
            "coverage contains duplicate not-applicable declarations"
        )
    slot_refs = {item.slot_ref for item in supplied_slots}
    if not set(declaration_refs) <= slot_refs:
        raise RelationCoverageError(
            "not-applicable declaration targets an unknown slot"
        )
    declaration_by_slot = {item.slot_ref: item for item in declarations}
    dispositions: list[RelationCoverageDisposition] = []
    for slot in supplied_slots:
        relation_refs, match_refs = _matching_relation_matches(graph, slot)
        count = len(match_refs)
        declaration = declaration_by_slot.get(slot.slot_ref)
        if declaration is not None:
            if not slot.allow_not_applicable:
                raise RelationCoverageError(
                    "relation slot policy forbids not-applicable"
                )
            if count != 0 or relation_refs:
                raise RelationCoverageError(
                    "not-applicable declaration is invalid for a matched slot"
                )
            status = RelationCoverageStatus.NOT_APPLICABLE
        elif count < slot.minimum_count:
            status = RelationCoverageStatus.UNKNOWN
        elif slot.maximum_count is not None and count > slot.maximum_count:
            status = RelationCoverageStatus.VIOLATED
        else:
            status = RelationCoverageStatus.SATISFIED
        dispositions.append(
            RelationCoverageDisposition(
                slot_ref=slot.slot_ref,
                status=status,
                relation_refs=relation_refs,
                match_refs=match_refs,
                not_applicable=declaration,
            )
        )
    compiled_dispositions = tuple(
        sorted(dispositions, key=lambda item: item.slot_ref)
    )
    closure_ready = all(
        item.status
        in {
            RelationCoverageStatus.SATISFIED,
            RelationCoverageStatus.NOT_APPLICABLE,
        }
        for item in compiled_dispositions
    )
    graph_digest = require_sha256(graph.graph_digest, "graph_digest")
    subject_inventory_digest = require_sha256(
        graph.subject_inventory_digest,
        "subject_inventory_digest",
    )
    policy_digest = policy.digest
    closure_digest = (
        canonical_digest(
            _closure_content(
                graph_digest=graph_digest,
                subject_inventory_digest=subject_inventory_digest,
                policy_digest=policy_digest,
                slots=supplied_slots,
                dispositions=compiled_dispositions,
            )
        )
        if closure_ready
        else None
    )
    return GraphCoverageManifest(
        graph_digest=graph_digest,
        subject_inventory_digest=subject_inventory_digest,
        policy_digest=policy_digest,
        slots=supplied_slots,
        dispositions=compiled_dispositions,
        closure_ready=closure_ready,
        closure_digest=closure_digest,
    )


__all__ = [
    "GraphCoverageManifest",
    "RelationCoverageDisposition",
    "RelationCoverageError",
    "RelationCoverageStatus",
    "RelationNotApplicable",
    "RelationRequirementSlot",
    "SemanticKindRelationPolicy",
    "SemanticRelationRule",
    "compile_graph_coverage",
    "compile_requirement_slots",
]
