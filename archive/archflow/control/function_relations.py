"""Exact component-function obligations projected into relation requirements.

The bridge is deliberately controller-only and persistence-neutral.  Project
authored envelopes provide relation vocabulary, projection, endpoint mapping,
and evidence.  The compiler only joins those envelopes to the exact applicable
obligations already present in a :class:`ComponentFunctionLedger`.

Unlike ``compile_requirement_slots``, this module never crosses a semantic-kind
rule with every node of that kind.  Each emitted slot is compiled directly for
the single ledger component named by its obligation, preventing N x N expansion
when several components share a semantic kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archive.archflow.contracts.branch import (
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
from archive.archflow.control.component_functions import (
    ComponentFunctionLedger,
    FunctionEndpointRole,
    FunctionMaturity,
    FunctionObligationEvaluation,
)
from archive.archflow.control.stage_subjects import StageSubjectInventory
from archflow.project.refs import BranchRef
from archive.archflow.relations.authoring import (
    RelationDerivationQuestion,
    RelationRuleEnvelope,
)
from archflow.relations.contracts import (
    ArchitecturalNodeKind,
    ArchitecturalRelationKind,
    RelationProjection,
    allowed_relation_roles,
)
from archive.archflow.relations.coverage import (
    RelationRequirementSlot,
    SemanticKindRelationPolicy,
    SemanticRelationRule,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


class FunctionRelationError(ValueError):
    """A function-relation envelope or compiled bridge is not exact."""


class FunctionRelationConsumer(StrEnum):
    """Non-authoritative consumers for the compiled requirement set."""

    STAGE2_TOPOLOGY = "stage2_topology"
    STAGE3_REALIZATION_READBACK = "stage3_realization_readback"


_CONSUMERS = tuple(sorted(FunctionRelationConsumer, key=lambda item: item.value))


def _list(payload: dict[str, object], field: str) -> list[object]:
    value = payload[field]
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return value


_PROJECTION_ENDPOINT_ROLES: dict[
    tuple[RelationProjection, ArchitecturalRelationKind],
    tuple[str, str],
] = {
    (RelationProjection.COMPOSITION, ArchitecturalRelationKind.COMPOSITION): (
        "whole",
        "part",
    ),
    (RelationProjection.COMPOSITION, ArchitecturalRelationKind.AGGREGATES): (
        "whole",
        "part",
    ),
    (
        RelationProjection.COMPOSITION,
        ArchitecturalRelationKind.PRIMARY_CONTAINS,
    ): ("container", "contained"),
    (RelationProjection.IMPACT, ArchitecturalRelationKind.DEPENDENCY): (
        "upstream",
        "downstream",
    ),
    (RelationProjection.SUPPORT, ArchitecturalRelationKind.SUPPORT): (
        "supported",
        "supporter",
    ),
    (RelationProjection.SUPPORT, ArchitecturalRelationKind.LOAD_TRANSFER): (
        "sender",
        "receiver",
    ),
    (RelationProjection.HOST, ArchitecturalRelationKind.HOST): (
        "hosted",
        "host",
    ),
    (RelationProjection.HOST, ArchitecturalRelationKind.HOSTS_VOID): (
        "void",
        "host",
    ),
    (RelationProjection.HOST, ArchitecturalRelationKind.FILLS_VOID): (
        "fill",
        "void",
    ),
    (RelationProjection.ACCESS, ArchitecturalRelationKind.ACCESS): ("from", "to"),
    (RelationProjection.ACCESS, ArchitecturalRelationKind.ALLOWS_PASSAGE): (
        "from",
        "to",
    ),
    (RelationProjection.REALIZATION, ArchitecturalRelationKind.REALIZATION): (
        "semantic",
        "realization",
    ),
    (RelationProjection.REALIZATION, ArchitecturalRelationKind.REALIZES): (
        "semantic",
        "realization",
    ),
    (RelationProjection.LINEAGE, ArchitecturalRelationKind.LINEAGE): (
        "current",
        "predecessor",
    ),
    (RelationProjection.LINEAGE, ArchitecturalRelationKind.REFINES): (
        "current",
        "predecessor",
    ),
    (RelationProjection.LINEAGE, ArchitecturalRelationKind.REPLACES): (
        "current",
        "predecessor",
    ),
    (RelationProjection.PROVENANCE, ArchitecturalRelationKind.EVIDENCES): (
        "assertion",
        "evidence",
    ),
}


def _question_endpoint_refs(
    *,
    projection: RelationProjection,
    relation_kind: ArchitecturalRelationKind,
    bindings: tuple["FunctionRelationEndpointBinding", ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return projection-directed question endpoints.

    The function-bearing component is not necessarily the traversal source.
    For example, a SUPPORT_OTHERS contract binds the supporter component while
    the support projection runs from the supported component to its supporter.
    """

    roles = _PROJECTION_ENDPOINT_ROLES.get((projection, relation_kind))
    if roles is None:
        raise FunctionRelationError(
            "function relation projection has no deterministic endpoint direction"
        )
    by_relation_role = {
        binding.relation_role: binding
        for binding in bindings
        if binding.relation_role is not None
    }
    source = by_relation_role.get(roles[0])
    target = by_relation_role.get(roles[1])
    if source is None or target is None:
        raise FunctionRelationError(
            "function relation endpoint mapping omits projection source or target"
        )
    return (
        tuple(sorted(item.component_ref for item in source.endpoints)),
        tuple(sorted(item.component_ref for item in target.endpoints)),
    )


@dataclass(frozen=True, slots=True)
class FunctionRelationEndpoint:
    """One exact, digest-bound component endpoint authored by the project."""

    component_ref: str
    component_digest: str

    SCHEMA: ClassVar[str] = "FunctionRelationEndpoint@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_ref",
            logical_ref(self.component_ref, "function relation endpoint component_ref"),
        )
        object.__setattr__(
            self,
            "component_digest",
            require_sha256(
                self.component_digest,
                "function relation endpoint component_digest",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_ref": self.component_ref,
            "component_digest": self.component_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionRelationEndpoint":
        payload = exact_mapping(
            value,
            {"schema", "component_ref", "component_digest"},
            "function relation endpoint",
        )
        if payload["schema"] != cls.SCHEMA:
            raise FunctionRelationError("unsupported function relation endpoint schema")
        return cls(
            component_ref=payload["component_ref"],
            component_digest=payload["component_digest"],
        )


@dataclass(frozen=True, slots=True)
class FunctionRelationEndpointBinding:
    """Project-authored mapping from a function role to relation semantics."""

    function_role: str
    relation_role: str | None
    endpoints: tuple[FunctionRelationEndpoint, ...]

    SCHEMA: ClassVar[str] = "FunctionRelationEndpointBinding@1"

    def __post_init__(self) -> None:
        identifier(self.function_role, "function relation function_role")
        if self.relation_role is not None:
            identifier(self.relation_role, "function relation relation_role")
        if not isinstance(self.endpoints, tuple) or any(
            not isinstance(item, FunctionRelationEndpoint) for item in self.endpoints
        ):
            raise TypeError("endpoints must contain FunctionRelationEndpoint values")
        ordered = tuple(sorted(self.endpoints, key=lambda item: item.component_ref))
        if ordered != self.endpoints or len(ordered) != len(
            {item.component_ref for item in ordered}
        ):
            raise FunctionRelationError(
                "function relation endpoints must be sorted with unique components"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "function_role": self.function_role,
            "relation_role": self.relation_role,
            "endpoints": [item.to_dict() for item in self.endpoints],
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionRelationEndpointBinding":
        payload = exact_mapping(
            value,
            {"schema", "function_role", "relation_role", "endpoints"},
            "function relation endpoint binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise FunctionRelationError(
                "unsupported function relation endpoint binding schema"
            )
        return cls(
            function_role=payload["function_role"],
            relation_role=payload["relation_role"],
            endpoints=tuple(
                FunctionRelationEndpoint.from_dict(item)
                for item in _list(payload, "endpoints")
            ),
        )


@dataclass(frozen=True, slots=True)
class FunctionRelationEvidenceEnvelope:
    """Evidence-bound project choice for one exact ledger obligation.

    Relation kind, projection, roles, and targets are all explicit fields.  No
    value is inferred from ``function_id``, component name, or semantic kind.
    """

    envelope_id: str
    branch: BranchRef
    stage_id: str
    subject_inventory_digest: str
    function_ledger_ref: str
    function_ledger_digest: str
    component_ref: str
    component_digest: str
    functional_obligation_ref: str
    projection: RelationProjection
    relation_kind: ArchitecturalRelationKind
    scenario_ref: str
    endpoint_bindings: tuple[FunctionRelationEndpointBinding, ...]
    counted_function_role: str
    basis_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    prompt: str

    SCHEMA: ClassVar[str] = "FunctionRelationEvidenceEnvelope@1"

    def __post_init__(self) -> None:
        identifier(self.envelope_id, "function relation envelope_id")
        require_exact_branch(self.branch)
        identifier(self.stage_id, "function relation envelope stage_id")
        object.__setattr__(
            self,
            "subject_inventory_digest",
            require_sha256(
                self.subject_inventory_digest,
                "function relation subject_inventory_digest",
            ),
        )
        object.__setattr__(
            self,
            "function_ledger_ref",
            logical_ref(self.function_ledger_ref, "function relation ledger_ref"),
        )
        object.__setattr__(
            self,
            "function_ledger_digest",
            require_sha256(
                self.function_ledger_digest,
                "function relation ledger_digest",
            ),
        )
        object.__setattr__(
            self,
            "component_ref",
            logical_ref(self.component_ref, "function relation component_ref"),
        )
        object.__setattr__(
            self,
            "component_digest",
            require_sha256(self.component_digest, "function relation component_digest"),
        )
        object.__setattr__(
            self,
            "functional_obligation_ref",
            logical_ref(
                self.functional_obligation_ref,
                "function relation functional_obligation_ref",
            ),
        )
        if not isinstance(self.projection, RelationProjection):
            raise TypeError("projection must be RelationProjection")
        if not isinstance(self.relation_kind, ArchitecturalRelationKind):
            raise TypeError("relation_kind must be ArchitecturalRelationKind")
        if (self.projection, self.relation_kind) not in _PROJECTION_ENDPOINT_ROLES:
            raise FunctionRelationError(
                "relation kind is incompatible with its projection"
            )
        object.__setattr__(
            self,
            "scenario_ref",
            logical_ref(self.scenario_ref, "function relation scenario_ref"),
        )
        if not isinstance(self.endpoint_bindings, tuple) or not self.endpoint_bindings or any(
            not isinstance(item, FunctionRelationEndpointBinding)
            for item in self.endpoint_bindings
        ):
            raise TypeError(
                "endpoint_bindings must contain FunctionRelationEndpointBinding values"
            )
        ordered = tuple(sorted(self.endpoint_bindings, key=lambda item: item.function_role))
        roles = tuple(item.function_role for item in ordered)
        if ordered != self.endpoint_bindings or len(roles) != len(set(roles)):
            raise FunctionRelationError(
                "endpoint_bindings must be sorted with unique function roles"
            )
        mapped_roles = tuple(
            item.relation_role for item in ordered if item.relation_role is not None
        )
        if len(mapped_roles) != len(set(mapped_roles)):
            raise FunctionRelationError("relation endpoint roles are ambiguous")
        allowed = allowed_relation_roles(self.relation_kind)
        if not set(mapped_roles) <= allowed:
            raise FunctionRelationError(
                "project-authored endpoint role is invalid for relation kind"
            )
        identifier(self.counted_function_role, "counted_function_role")
        if self.counted_function_role not in roles:
            raise FunctionRelationError("counted_function_role is not an endpoint role")
        object.__setattr__(
            self,
            "basis_ids",
            deterministic_identifiers(self.basis_ids, "function relation basis_ids"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "function relation evidence_refs"),
        )
        object.__setattr__(
            self,
            "authority_refs",
            deterministic_refs(self.authority_refs, "function relation authority_refs"),
        )
        text(self.prompt, "function relation prompt", maximum=4_000)

    @property
    def envelope_ref(self) -> str:
        return f"function-relation-envelope:{self.envelope_id}"

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "envelope_id": self.envelope_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "subject_inventory_digest": self.subject_inventory_digest,
            "function_ledger_ref": self.function_ledger_ref,
            "function_ledger_digest": self.function_ledger_digest,
            "component_ref": self.component_ref,
            "component_digest": self.component_digest,
            "functional_obligation_ref": self.functional_obligation_ref,
            "projection": self.projection.value,
            "relation_kind": self.relation_kind.value,
            "scenario_ref": self.scenario_ref,
            "endpoint_bindings": [item.to_dict() for item in self.endpoint_bindings],
            "counted_function_role": self.counted_function_role,
            "basis_ids": list(self.basis_ids),
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "prompt": self.prompt,
            **_AUTHORITY_FIELDS,
        }

    @property
    def envelope_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "envelope_digest": self.envelope_digest}

    @classmethod
    def from_dict(cls, value: object) -> "FunctionRelationEvidenceEnvelope":
        payload = exact_mapping(
            value,
            {
                "schema",
                "envelope_id",
                "branch",
                "stage_id",
                "subject_inventory_digest",
                "function_ledger_ref",
                "function_ledger_digest",
                "component_ref",
                "component_digest",
                "functional_obligation_ref",
                "projection",
                "relation_kind",
                "scenario_ref",
                "endpoint_bindings",
                "counted_function_role",
                "basis_ids",
                "evidence_refs",
                "authority_refs",
                "prompt",
                "envelope_digest",
                *_AUTHORITY_FIELDS,
            },
            "function relation evidence envelope",
        )
        if payload["schema"] != cls.SCHEMA:
            raise FunctionRelationError("unsupported function relation envelope schema")
        result = cls(
            envelope_id=payload["envelope_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            subject_inventory_digest=payload["subject_inventory_digest"],
            function_ledger_ref=payload["function_ledger_ref"],
            function_ledger_digest=payload["function_ledger_digest"],
            component_ref=payload["component_ref"],
            component_digest=payload["component_digest"],
            functional_obligation_ref=payload["functional_obligation_ref"],
            projection=RelationProjection(payload["projection"]),
            relation_kind=ArchitecturalRelationKind(payload["relation_kind"]),
            scenario_ref=payload["scenario_ref"],
            endpoint_bindings=tuple(
                FunctionRelationEndpointBinding.from_dict(item)
                for item in _list(payload, "endpoint_bindings")
            ),
            counted_function_role=payload["counted_function_role"],
            basis_ids=tuple(_list(payload, "basis_ids")),
            evidence_refs=tuple(_list(payload, "evidence_refs")),
            authority_refs=tuple(_list(payload, "authority_refs")),
            prompt=payload["prompt"],
        )
        if result.to_dict() != payload:
            raise FunctionRelationError("function relation envelope digest changed")
        return result


@dataclass(frozen=True, slots=True)
class FunctionRelationRequirement:
    """One exact obligation and its question/rule/component-bound slot."""

    source_envelope_ref: str
    source_envelope_digest: str
    component_ref: str
    component_digest: str
    functional_obligation_ref: str
    purpose: str
    endpoint_roles: tuple[FunctionEndpointRole, ...]
    endpoint_bindings: tuple[FunctionRelationEndpointBinding, ...]
    required_maturity: FunctionMaturity
    counted_function_role: str
    question: RelationDerivationQuestion
    rule: SemanticRelationRule
    slot: RelationRequirementSlot
    consumers: tuple[FunctionRelationConsumer, ...] = _CONSUMERS

    SCHEMA: ClassVar[str] = "FunctionRelationRequirement@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_envelope_ref",
            logical_ref(self.source_envelope_ref, "source envelope ref"),
        )
        object.__setattr__(
            self,
            "source_envelope_digest",
            require_sha256(self.source_envelope_digest, "source envelope digest"),
        )
        object.__setattr__(
            self,
            "component_ref",
            logical_ref(self.component_ref, "function relation requirement component_ref"),
        )
        object.__setattr__(
            self,
            "component_digest",
            require_sha256(self.component_digest, "requirement component_digest"),
        )
        object.__setattr__(
            self,
            "functional_obligation_ref",
            logical_ref(self.functional_obligation_ref, "functional_obligation_ref"),
        )
        text(self.purpose, "function relation purpose")
        if not isinstance(self.endpoint_roles, tuple) or not self.endpoint_roles or any(
            not isinstance(item, FunctionEndpointRole) for item in self.endpoint_roles
        ):
            raise TypeError("endpoint_roles must contain FunctionEndpointRole values")
        if tuple(sorted(self.endpoint_roles, key=lambda item: item.role)) != self.endpoint_roles:
            raise FunctionRelationError("endpoint_roles must be sorted")
        if not isinstance(self.endpoint_bindings, tuple) or any(
            not isinstance(item, FunctionRelationEndpointBinding)
            for item in self.endpoint_bindings
        ):
            raise TypeError("endpoint_bindings are invalid")
        if tuple(sorted(self.endpoint_bindings, key=lambda item: item.function_role)) != self.endpoint_bindings:
            raise FunctionRelationError("endpoint_bindings must be sorted")
        if {item.role for item in self.endpoint_roles} != {
            item.function_role for item in self.endpoint_bindings
        }:
            raise FunctionRelationError("endpoint roles and bindings differ")
        if not isinstance(self.required_maturity, FunctionMaturity):
            raise TypeError("required_maturity must be FunctionMaturity")
        identifier(self.counted_function_role, "counted_function_role")
        if not isinstance(self.question, RelationDerivationQuestion):
            raise TypeError("question must be RelationDerivationQuestion")
        if not isinstance(self.rule, SemanticRelationRule):
            raise TypeError("rule must be SemanticRelationRule")
        if not isinstance(self.slot, RelationRequirementSlot):
            raise TypeError("slot must be RelationRequirementSlot")
        if self.slot.node_ref != self.component_ref:
            raise FunctionRelationError("slot node is not the exact component")
        if (
            self.slot.rule_id != self.rule.rule_id
            or self.slot.relation_kind is not self.rule.relation_kind
            or self.slot.subject_role != self.rule.subject_role
            or self.slot.counted_role != self.rule.counted_role
            or self.slot.scenario_ref != self.rule.scenario_ref
        ):
            raise FunctionRelationError("slot drifted from its exact relation rule")
        if self.question.allowed_relation_kinds != (self.rule.relation_kind,):
            raise FunctionRelationError("question drifted from its exact relation rule")
        expected_subjects, expected_targets = _question_endpoint_refs(
            projection=self.question.projection,
            relation_kind=self.rule.relation_kind,
            bindings=self.endpoint_bindings,
        )
        if self.question.subject_refs != expected_subjects:
            raise FunctionRelationError(
                "question subjects drifted from projection-directed endpoints"
            )
        if self.question.target_refs != expected_targets:
            raise FunctionRelationError("question targets drifted from endpoint bindings")
        if self.consumers != _CONSUMERS:
            raise FunctionRelationError("function relation consumers changed")

    @property
    def requirement_ref(self) -> str:
        return "function-relation-requirement:" + canonical_digest(
            {
                "component_ref": self.component_ref,
                "functional_obligation_ref": self.functional_obligation_ref,
                "source_envelope_digest": self.source_envelope_digest,
            }
        )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_envelope_ref": self.source_envelope_ref,
            "source_envelope_digest": self.source_envelope_digest,
            "component_ref": self.component_ref,
            "component_digest": self.component_digest,
            "functional_obligation_ref": self.functional_obligation_ref,
            "purpose": self.purpose,
            "endpoint_roles": [item.to_dict() for item in self.endpoint_roles],
            "endpoint_bindings": [item.to_dict() for item in self.endpoint_bindings],
            "required_maturity": self.required_maturity.value,
            "counted_function_role": self.counted_function_role,
            "question": self.question.to_dict(),
            "rule": self.rule.to_dict(),
            "slot": self.slot.to_dict(),
            "consumers": [item.value for item in self.consumers],
            **_AUTHORITY_FIELDS,
        }

    @property
    def requirement_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "requirement_digest": self.requirement_digest}

    @classmethod
    def from_dict(cls, value: object) -> "FunctionRelationRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "source_envelope_ref",
                "source_envelope_digest",
                "component_ref",
                "component_digest",
                "functional_obligation_ref",
                "purpose",
                "endpoint_roles",
                "endpoint_bindings",
                "required_maturity",
                "counted_function_role",
                "question",
                "rule",
                "slot",
                "consumers",
                "requirement_digest",
                *_AUTHORITY_FIELDS,
            },
            "function relation requirement",
        )
        if payload["schema"] != cls.SCHEMA:
            raise FunctionRelationError("unsupported function relation requirement schema")
        result = cls(
            source_envelope_ref=payload["source_envelope_ref"],
            source_envelope_digest=payload["source_envelope_digest"],
            component_ref=payload["component_ref"],
            component_digest=payload["component_digest"],
            functional_obligation_ref=payload["functional_obligation_ref"],
            purpose=payload["purpose"],
            endpoint_roles=tuple(
                FunctionEndpointRole.from_dict(item)
                for item in _list(payload, "endpoint_roles")
            ),
            endpoint_bindings=tuple(
                FunctionRelationEndpointBinding.from_dict(item)
                for item in _list(payload, "endpoint_bindings")
            ),
            required_maturity=FunctionMaturity(payload["required_maturity"]),
            counted_function_role=payload["counted_function_role"],
            question=RelationDerivationQuestion.from_dict(payload["question"]),
            rule=SemanticRelationRule.from_dict(payload["rule"]),
            slot=RelationRequirementSlot.from_dict(payload["slot"]),
            consumers=tuple(
                FunctionRelationConsumer(item) for item in _list(payload, "consumers")
            ),
        )
        if result.to_dict() != payload:
            raise FunctionRelationError("function relation requirement digest changed")
        return result


@dataclass(frozen=True, slots=True)
class FunctionRelationRequirementSet:
    """Exact, authority-free requirement denominator for Stage 2 and Stage 3."""

    set_id: str
    branch: BranchRef
    stage_id: str
    subject_inventory_digest: str
    function_ledger_ref: str
    function_ledger_digest: str
    requirements: tuple[FunctionRelationRequirement, ...]

    SCHEMA: ClassVar[str] = "FunctionRelationRequirementSet@1"

    def __post_init__(self) -> None:
        identifier(self.set_id, "function relation requirement set_id")
        require_exact_branch(self.branch)
        identifier(self.stage_id, "function relation requirement stage_id")
        object.__setattr__(
            self,
            "subject_inventory_digest",
            require_sha256(self.subject_inventory_digest, "subject_inventory_digest"),
        )
        object.__setattr__(
            self,
            "function_ledger_ref",
            logical_ref(self.function_ledger_ref, "function_ledger_ref"),
        )
        object.__setattr__(
            self,
            "function_ledger_digest",
            require_sha256(self.function_ledger_digest, "function_ledger_digest"),
        )
        if not isinstance(self.requirements, tuple) or any(
            not isinstance(item, FunctionRelationRequirement)
            for item in self.requirements
        ):
            raise TypeError("requirements must contain FunctionRelationRequirement values")
        ordered = tuple(
            sorted(
                self.requirements,
                key=lambda item: (item.component_ref, item.functional_obligation_ref),
            )
        )
        keys = tuple(
            (item.component_ref, item.functional_obligation_ref) for item in ordered
        )
        if ordered != self.requirements or len(keys) != len(set(keys)):
            raise FunctionRelationError(
                "requirements must be sorted and exact-once per component obligation"
            )
        if len({item.source_envelope_ref for item in ordered}) != len(ordered):
            raise FunctionRelationError("requirements reuse a source envelope")
        expected_policy_id = f"{self.set_id}-exact-components"
        for item in ordered:
            if item.slot.policy_id != expected_policy_id or item.slot.stage_id != self.stage_id:
                raise FunctionRelationError("requirement slot crossed its set or stage")

    @property
    def set_ref(self) -> str:
        return f"function-relation-requirement-set:{self.set_id}"

    @property
    def topology_questions(self) -> tuple[RelationDerivationQuestion, ...]:
        return tuple(item.question for item in self.requirements)

    @property
    def exact_relation_slots(self) -> tuple[RelationRequirementSlot, ...]:
        return tuple(item.slot for item in self.requirements)

    @property
    def realization_requirements(self) -> tuple[FunctionRelationRequirement, ...]:
        return self.requirements

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "set_id": self.set_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "subject_inventory_digest": self.subject_inventory_digest,
            "function_ledger_ref": self.function_ledger_ref,
            "function_ledger_digest": self.function_ledger_digest,
            "requirements": [item.to_dict() for item in self.requirements],
            **_AUTHORITY_FIELDS,
        }

    @property
    def set_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "set_digest": self.set_digest}

    @classmethod
    def from_dict(cls, value: object) -> "FunctionRelationRequirementSet":
        payload = exact_mapping(
            value,
            {
                "schema",
                "set_id",
                "branch",
                "stage_id",
                "subject_inventory_digest",
                "function_ledger_ref",
                "function_ledger_digest",
                "requirements",
                "set_digest",
                *_AUTHORITY_FIELDS,
            },
            "function relation requirement set",
        )
        if payload["schema"] != cls.SCHEMA:
            raise FunctionRelationError("unsupported function relation set schema")
        result = cls(
            set_id=payload["set_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            subject_inventory_digest=payload["subject_inventory_digest"],
            function_ledger_ref=payload["function_ledger_ref"],
            function_ledger_digest=payload["function_ledger_digest"],
            requirements=tuple(
                FunctionRelationRequirement.from_dict(item)
                for item in _list(payload, "requirements")
            ),
        )
        if result.to_dict() != payload:
            raise FunctionRelationError("function relation requirement set digest changed")
        return result


def _require_ledger_inventory_binding(
    ledger: ComponentFunctionLedger,
    inventory: StageSubjectInventory,
) -> dict[str, object]:
    if not isinstance(ledger, ComponentFunctionLedger):
        raise TypeError("ledger must be ComponentFunctionLedger")
    if not isinstance(inventory, StageSubjectInventory):
        raise TypeError("inventory must be StageSubjectInventory")
    if ledger.branch != inventory.branch or ledger.stage_id != inventory.stage_id:
        raise FunctionRelationError("function ledger crossed inventory branch or stage")
    if ledger.subject_inventory_digest != inventory.inventory_digest:
        raise FunctionRelationError("function ledger is stale against inventory")
    entries = {item.identity_ref: item for item in inventory.entries}
    if tuple(item.component_ref for item in ledger.rows) != tuple(sorted(entries)):
        raise FunctionRelationError("function ledger denominator differs from inventory")
    for row in ledger.rows:
        if row.component_digest != entries[row.component_ref].component_digest:
            raise FunctionRelationError("function ledger component is stale")
    return entries


def _validate_endpoint_bindings(
    *,
    envelope: FunctionRelationEvidenceEnvelope,
    evaluation: FunctionObligationEvaluation,
    entries: dict[str, object],
) -> tuple[FunctionEndpointRole, FunctionEndpointRole]:
    specs = {item.role: item for item in evaluation.endpoint_roles}
    bindings = {item.function_role: item for item in envelope.endpoint_bindings}
    if set(bindings) != set(specs):
        raise FunctionRelationError("envelope endpoint roles do not exactly cover obligation")
    claim_bindings = {
        item.role: item.endpoint_refs for item in evaluation.endpoint_bindings
    }
    if set(claim_bindings) != set(specs):
        raise FunctionRelationError("applicable obligation is missing endpoint targets")
    seen_refs: set[str] = set()
    component_spec = next(item for item in evaluation.endpoint_roles if item.component_slot)
    counted_spec = specs[envelope.counted_function_role]
    if counted_spec.component_slot:
        raise FunctionRelationError("counted endpoint cannot be the component slot")
    for role_name, spec in specs.items():
        binding = bindings[role_name]
        refs = tuple(item.component_ref for item in binding.endpoints)
        if len(refs) < spec.minimum or (
            spec.maximum is not None and len(refs) > spec.maximum
        ):
            raise FunctionRelationError("envelope endpoint cardinality is invalid")
        if spec.component_slot and refs != (envelope.component_ref,):
            raise FunctionRelationError("component endpoint is not exact")
        for endpoint in binding.endpoints:
            entry = entries.get(endpoint.component_ref)
            if entry is None:
                raise FunctionRelationError("envelope names a foreign target")
            if endpoint.component_digest != entry.component_digest:
                raise FunctionRelationError("envelope target digest is stale")
            if endpoint.component_ref in seen_refs:
                raise FunctionRelationError("envelope target is ambiguous across endpoint roles")
            seen_refs.add(endpoint.component_ref)
        if refs != claim_bindings[role_name]:
            raise FunctionRelationError("envelope targets differ from exact ledger obligation")
    component_binding = bindings[component_spec.role]
    counted_binding = bindings[counted_spec.role]
    if component_binding.relation_role is None or counted_binding.relation_role is None:
        raise FunctionRelationError("component and counted roles need relation role mappings")
    if component_binding.relation_role == counted_binding.relation_role:
        raise FunctionRelationError("relation subject and counted roles are ambiguous")
    return component_spec, counted_spec


def _compile_requirement(
    *,
    set_id: str,
    envelope: FunctionRelationEvidenceEnvelope,
    evaluation: FunctionObligationEvaluation,
    semantic_kind: str,
    entries: dict[str, object],
) -> FunctionRelationRequirement:
    component_spec, counted_spec = _validate_endpoint_bindings(
        envelope=envelope,
        evaluation=evaluation,
        entries=entries,
    )
    bindings = {item.function_role: item for item in envelope.endpoint_bindings}
    subject_role = bindings[component_spec.role].relation_role
    counted_role = bindings[counted_spec.role].relation_role
    assert subject_role is not None and counted_role is not None
    identity = canonical_digest(
        {
            "schema": "FunctionRelationCompiledIdentity@1",
            "set_id": set_id,
            "envelope_digest": envelope.envelope_digest,
            "component_ref": envelope.component_ref,
            "functional_obligation_ref": envelope.functional_obligation_ref,
        }
    )[:24]
    question_subjects, question_targets = _question_endpoint_refs(
        projection=envelope.projection,
        relation_kind=envelope.relation_kind,
        bindings=envelope.endpoint_bindings,
    )
    question = RelationDerivationQuestion(
        question_id=f"function-relation-{identity}",
        projection=envelope.projection,
        scenario_ref=envelope.scenario_ref,
        subject_refs=question_subjects,
        target_refs=question_targets,
        allowed_relation_kinds=(envelope.relation_kind,),
        rule_envelopes=(
            RelationRuleEnvelope(
                relation_kind=envelope.relation_kind,
                subject_role=subject_role,
                counted_role=counted_role,
                minimum_count=counted_spec.minimum,
                maximum_count=counted_spec.maximum,
            ),
        ),
        basis_ids=envelope.basis_ids,
        prompt=envelope.prompt,
        allow_not_applicable=False,
        rule_subject_refs=(envelope.component_ref,),
    )
    rule = SemanticRelationRule(
        rule_id=f"function-relation-{identity}",
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind=semantic_kind,
        relation_kind=envelope.relation_kind,
        subject_role=subject_role,
        counted_role=counted_role,
        minimum_count=counted_spec.minimum,
        maximum_count=counted_spec.maximum,
        scenario_ref=envelope.scenario_ref,
        evidence_refs=envelope.evidence_refs,
        authority_refs=envelope.authority_refs,
        allow_not_applicable=False,
    )
    # The policy exists only long enough to use the canonical slot constructor.
    # It is intentionally not exposed: broad semantic-kind expansion would be
    # incorrect for component-bound function obligations.
    policy = SemanticKindRelationPolicy(
        policy_id=f"{set_id}-exact-components",
        stage_id=envelope.stage_id,
        rules=(rule,),
        source_refs=(envelope.envelope_ref,),
    )
    slot = RelationRequirementSlot.compile(
        policy=policy,
        rule=rule,
        node_ref=envelope.component_ref,
    )
    return FunctionRelationRequirement(
        source_envelope_ref=envelope.envelope_ref,
        source_envelope_digest=envelope.envelope_digest,
        component_ref=envelope.component_ref,
        component_digest=envelope.component_digest,
        functional_obligation_ref=evaluation.obligation_ref,
        purpose=evaluation.purpose,
        endpoint_roles=evaluation.endpoint_roles,
        endpoint_bindings=envelope.endpoint_bindings,
        required_maturity=evaluation.required_maturity,
        counted_function_role=envelope.counted_function_role,
        question=question,
        rule=rule,
        slot=slot,
    )


def compile_function_relation_requirements(
    *,
    set_id: str,
    ledger: ComponentFunctionLedger,
    inventory: StageSubjectInventory,
    envelopes: tuple[FunctionRelationEvidenceEnvelope, ...],
) -> FunctionRelationRequirementSet:
    """Compile exactly one relation requirement per applicable obligation.

    The function rejects missing, duplicate, foreign, stale, cross-stage,
    cross-branch, and ambiguous project inputs.  It returns typed values only;
    no persistence, acceptance, or canonical-state operation is performed.
    """

    identifier(set_id, "function relation requirement set_id")
    entries = _require_ledger_inventory_binding(ledger, inventory)
    if not isinstance(envelopes, tuple) or any(
        not isinstance(item, FunctionRelationEvidenceEnvelope) for item in envelopes
    ):
        raise TypeError("envelopes must contain FunctionRelationEvidenceEnvelope values")
    expected: dict[tuple[str, str], FunctionObligationEvaluation] = {}
    for row in ledger.rows:
        for evaluation in row.evaluations:
            key = (row.component_ref, evaluation.obligation_ref)
            if key in expected:
                raise FunctionRelationError("ledger duplicates an applicable obligation")
            expected[key] = evaluation
    provided: dict[tuple[str, str], FunctionRelationEvidenceEnvelope] = {}
    envelope_ids: set[str] = set()
    for envelope in envelopes:
        if envelope.envelope_id in envelope_ids:
            raise FunctionRelationError("duplicate function relation envelope_id")
        envelope_ids.add(envelope.envelope_id)
        key = (envelope.component_ref, envelope.functional_obligation_ref)
        if key in provided:
            raise FunctionRelationError("duplicate envelope for component obligation")
        if key not in expected:
            raise FunctionRelationError("foreign function relation envelope")
        if envelope.branch != ledger.branch or envelope.stage_id != ledger.stage_id:
            raise FunctionRelationError("function relation envelope crossed branch or stage")
        if envelope.subject_inventory_digest != ledger.subject_inventory_digest:
            raise FunctionRelationError("function relation envelope inventory is stale")
        if (
            envelope.function_ledger_ref != ledger.ledger_ref
            or envelope.function_ledger_digest != ledger.ledger_digest
        ):
            raise FunctionRelationError("function relation envelope ledger is stale")
        entry = entries[envelope.component_ref]
        if envelope.component_digest != entry.component_digest:
            raise FunctionRelationError("function relation envelope component is stale")
        provided[key] = envelope
    missing = set(expected) - set(provided)
    if missing:
        raise FunctionRelationError("missing envelope for applicable function obligation")
    requirements = tuple(
        sorted(
            (
                _compile_requirement(
                    set_id=set_id,
                    envelope=provided[key],
                    evaluation=evaluation,
                    semantic_kind=entries[key[0]].semantic_kind,
                    entries=entries,
                )
                for key, evaluation in expected.items()
            ),
            key=lambda item: (item.component_ref, item.functional_obligation_ref),
        )
    )
    if len(requirements) != len(expected):
        raise FunctionRelationError("applicable obligation denominator changed")
    return FunctionRelationRequirementSet(
        set_id=set_id,
        branch=ledger.branch,
        stage_id=ledger.stage_id,
        subject_inventory_digest=ledger.subject_inventory_digest,
        function_ledger_ref=ledger.ledger_ref,
        function_ledger_digest=ledger.ledger_digest,
        requirements=requirements,
    )


__all__ = [
    "FunctionRelationConsumer",
    "FunctionRelationEndpoint",
    "FunctionRelationEndpointBinding",
    "FunctionRelationError",
    "FunctionRelationEvidenceEnvelope",
    "FunctionRelationRequirement",
    "FunctionRelationRequirementSet",
    "compile_function_relation_requirements",
]
