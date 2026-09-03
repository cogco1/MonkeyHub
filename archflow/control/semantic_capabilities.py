"""Versioned semantic rule packs that expand stage-control obligations.

The policy answers only which generic questions become mandatory when a
semantic component is present.  It carries no project dimensions, design
answer, persistence authority, stage-acceptance authority, or canonical-write
authority.  Project evidence and a project-authored solver input still answer
the questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.fields import (
    deterministic_identifiers,
    identifier,
    logical_ref,
)
from archflow.control.baseline import (
    StageBaselineLevel,
    StageBaselineRole,
)
from archflow.project.refs import BranchRef


class SemanticCapabilityPolicyError(ValueError):
    """A semantic capability policy or binding is malformed or unsupported."""


class SemanticCapabilityId(StrEnum):
    STAIR = "stair"


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


def _exact_mapping(
    value: object,
    expected: set[str],
    field: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SemanticCapabilityPolicyError(f"{field} schema drifted")
    return value


@dataclass(frozen=True, slots=True)
class SemanticStageRuleSet:
    """Rules activated by one semantic capability at one stage level."""

    baseline_level: StageBaselineLevel
    mandatory_roles: tuple[StageBaselineRole, ...]
    rule_ids: tuple[str, ...]

    SCHEMA = "SemanticStageRuleSet@1"

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_level, StageBaselineLevel):
            raise TypeError("baseline_level must be a StageBaselineLevel")
        if not isinstance(self.mandatory_roles, tuple) or any(
            not isinstance(item, StageBaselineRole)
            for item in self.mandatory_roles
        ):
            raise TypeError(
                "mandatory_roles must contain StageBaselineRole values"
            )
        roles = tuple(sorted(set(self.mandatory_roles), key=lambda item: item.value))
        if roles != self.mandatory_roles:
            raise SemanticCapabilityPolicyError(
                "mandatory_roles must be sorted and unique"
            )
        object.__setattr__(
            self,
            "rule_ids",
            deterministic_identifiers(
                self.rule_ids,
                "semantic stage rule_ids",
                allow_empty=True,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "baseline_level": self.baseline_level.value,
            "mandatory_roles": [item.value for item in self.mandatory_roles],
            "rule_ids": list(self.rule_ids),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticStageRuleSet":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "baseline_level",
                "mandatory_roles",
                "rule_ids",
                *_AUTHORITY_FIELDS,
            },
            "semantic stage rule set",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SemanticCapabilityPolicyError(
                "unsupported semantic stage rule-set schema"
            )
        if not isinstance(payload["mandatory_roles"], list) or not isinstance(
            payload["rule_ids"], list
        ):
            raise TypeError("semantic stage rule-set arrays must be lists")
        return cls(
            baseline_level=StageBaselineLevel(payload["baseline_level"]),
            mandatory_roles=tuple(
                StageBaselineRole(item)
                for item in payload["mandatory_roles"]
            ),
            rule_ids=tuple(payload["rule_ids"]),
        )


@dataclass(frozen=True, slots=True)
class SemanticRulePack:
    """One framework-owned capability trigger and its stage questions."""

    pack_id: str
    pack_version: int
    capability_id: SemanticCapabilityId
    semantic_kinds: tuple[str, ...]
    stage_rules: tuple[SemanticStageRuleSet, ...]
    basis_ref: str
    authority_ref: str

    SCHEMA = "SemanticRulePack@1"

    def __post_init__(self) -> None:
        identifier(self.pack_id, "semantic rule pack_id")
        if (
            not isinstance(self.pack_version, int)
            or isinstance(self.pack_version, bool)
            or self.pack_version < 1
        ):
            raise SemanticCapabilityPolicyError(
                "semantic rule pack_version must be positive"
            )
        if not isinstance(self.capability_id, SemanticCapabilityId):
            raise TypeError("capability_id must be a SemanticCapabilityId")
        object.__setattr__(
            self,
            "semantic_kinds",
            deterministic_identifiers(
                self.semantic_kinds,
                "semantic rule semantic_kinds",
            ),
        )
        if not isinstance(self.stage_rules, tuple) or any(
            not isinstance(item, SemanticStageRuleSet)
            for item in self.stage_rules
        ):
            raise TypeError(
                "stage_rules must contain SemanticStageRuleSet values"
            )
        rules = tuple(
            sorted(self.stage_rules, key=lambda item: item.baseline_level.value)
        )
        if rules != self.stage_rules or {
            item.baseline_level for item in rules
        } != set(StageBaselineLevel):
            raise SemanticCapabilityPolicyError(
                "stage_rules must cover every baseline level exactly once"
            )
        object.__setattr__(
            self,
            "basis_ref",
            logical_ref(self.basis_ref, "semantic rule basis_ref"),
        )
        object.__setattr__(
            self,
            "authority_ref",
            logical_ref(self.authority_ref, "semantic rule authority_ref"),
        )

    @property
    def pack_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "capability_id": self.capability_id.value,
            "semantic_kinds": list(self.semantic_kinds),
            "stage_rules": [item.to_dict() for item in self.stage_rules],
            "basis_ref": self.basis_ref,
            "authority_ref": self.authority_ref,
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "pack_digest": self.pack_digest}

    @classmethod
    def from_dict(cls, value: object) -> "SemanticRulePack":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "pack_id",
                "pack_version",
                "capability_id",
                "semantic_kinds",
                "stage_rules",
                "basis_ref",
                "authority_ref",
                "pack_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic rule pack",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SemanticCapabilityPolicyError(
                "unsupported semantic rule-pack schema"
            )
        if not isinstance(payload["semantic_kinds"], list) or not isinstance(
            payload["stage_rules"], list
        ):
            raise TypeError("semantic rule-pack arrays must be lists")
        result = cls(
            pack_id=payload["pack_id"],
            pack_version=payload["pack_version"],
            capability_id=SemanticCapabilityId(payload["capability_id"]),
            semantic_kinds=tuple(payload["semantic_kinds"]),
            stage_rules=tuple(
                SemanticStageRuleSet.from_dict(item)
                for item in payload["stage_rules"]
            ),
            basis_ref=payload["basis_ref"],
            authority_ref=payload["authority_ref"],
        )
        if result.to_dict() != payload:
            raise SemanticCapabilityPolicyError(
                "semantic rule-pack digest changed"
            )
        return result

    def rule_set_for(
        self,
        level: StageBaselineLevel,
    ) -> SemanticStageRuleSet:
        if not isinstance(level, StageBaselineLevel):
            raise TypeError("level must be a StageBaselineLevel")
        return next(
            item for item in self.stage_rules if item.baseline_level is level
        )

    def matches(self, semantic_kind: str) -> bool:
        identifier(semantic_kind, "semantic_kind")
        normalized = "-".join(
            semantic_kind.casefold().replace("_", "-").split()
        )
        return normalized in self.semantic_kinds


@dataclass(frozen=True, slots=True)
class SemanticCapabilityPolicy:
    """Exact portable rule-pack set persisted beside a stage subject."""

    policy_id: str
    policy_version: int
    packs: tuple[SemanticRulePack, ...]

    SCHEMA = "SemanticCapabilityPolicy@1"

    def __post_init__(self) -> None:
        identifier(self.policy_id, "semantic capability policy_id")
        if (
            not isinstance(self.policy_version, int)
            or isinstance(self.policy_version, bool)
            or self.policy_version < 1
        ):
            raise SemanticCapabilityPolicyError(
                "semantic capability policy_version must be positive"
            )
        if not isinstance(self.packs, tuple) or not self.packs or any(
            not isinstance(item, SemanticRulePack) for item in self.packs
        ):
            raise TypeError("packs must contain SemanticRulePack values")
        packs = tuple(sorted(self.packs, key=lambda item: item.pack_id))
        identities = tuple(item.pack_id for item in packs)
        if packs != self.packs or len(identities) != len(set(identities)):
            raise SemanticCapabilityPolicyError(
                "semantic capability packs must be sorted and unique"
            )

    @property
    def policy_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "packs": [item.to_dict() for item in self.packs],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "policy_digest": self.policy_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticCapabilityPolicy":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "policy_id",
                "policy_version",
                "packs",
                "policy_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic capability policy",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SemanticCapabilityPolicyError(
                "unsupported semantic capability policy schema"
            )
        if not isinstance(payload["packs"], list):
            raise TypeError("semantic capability policy packs must be a list")
        result = cls(
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            packs=tuple(
                SemanticRulePack.from_dict(item) for item in payload["packs"]
            ),
        )
        if result.to_dict() != payload:
            raise SemanticCapabilityPolicyError(
                "semantic capability policy digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class SemanticRulePackBinding:
    """Exact automatic binding of one component to one rule-pack stage view."""

    branch: BranchRef
    stage_id: str
    stage_subject_digest: str
    component_ref: str
    component_digest: str
    semantic_kind: str
    baseline_level: StageBaselineLevel
    pack_id: str
    pack_version: int
    capability_id: SemanticCapabilityId
    policy_digest: str
    pack_digest: str
    mandatory_roles: tuple[StageBaselineRole, ...]
    active_rule_ids: tuple[str, ...]
    basis_ref: str
    authority_ref: str

    SCHEMA = "SemanticRulePackBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        self.branch.run.base.require_digest()
        identifier(self.stage_id, "semantic binding stage_id")
        object.__setattr__(
            self,
            "stage_subject_digest",
            require_sha256(
                self.stage_subject_digest,
                "semantic binding stage_subject_digest",
            ),
        )
        object.__setattr__(
            self,
            "component_ref",
            logical_ref(self.component_ref, "semantic binding component_ref"),
        )
        object.__setattr__(
            self,
            "component_digest",
            require_sha256(
                self.component_digest,
                "semantic binding component_digest",
            ),
        )
        identifier(self.semantic_kind, "semantic binding semantic_kind")
        if not isinstance(self.baseline_level, StageBaselineLevel):
            raise TypeError("baseline_level must be a StageBaselineLevel")
        identifier(self.pack_id, "semantic binding pack_id")
        if (
            not isinstance(self.pack_version, int)
            or isinstance(self.pack_version, bool)
            or self.pack_version < 1
        ):
            raise SemanticCapabilityPolicyError(
                "semantic binding pack_version must be positive"
            )
        if not isinstance(self.capability_id, SemanticCapabilityId):
            raise TypeError("capability_id must be a SemanticCapabilityId")
        object.__setattr__(
            self,
            "policy_digest",
            require_sha256(
                self.policy_digest,
                "semantic binding policy_digest",
            ),
        )
        object.__setattr__(
            self,
            "pack_digest",
            require_sha256(self.pack_digest, "semantic binding pack_digest"),
        )
        if not isinstance(self.mandatory_roles, tuple) or any(
            not isinstance(item, StageBaselineRole)
            for item in self.mandatory_roles
        ):
            raise TypeError(
                "mandatory_roles must contain StageBaselineRole values"
            )
        if self.mandatory_roles != tuple(
            sorted(set(self.mandatory_roles), key=lambda item: item.value)
        ):
            raise SemanticCapabilityPolicyError(
                "semantic binding mandatory_roles must be sorted and unique"
            )
        object.__setattr__(
            self,
            "active_rule_ids",
            deterministic_identifiers(
                self.active_rule_ids,
                "semantic binding active_rule_ids",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "basis_ref",
            logical_ref(self.basis_ref, "semantic binding basis_ref"),
        )
        object.__setattr__(
            self,
            "authority_ref",
            logical_ref(self.authority_ref, "semantic binding authority_ref"),
        )

    @property
    def binding_digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def ref(self) -> str:
        return f"semantic-rule-pack-binding:{self.binding_digest}"

    @property
    def rule_refs(self) -> tuple[str, ...]:
        return tuple(
            f"semantic-rule:{self.binding_digest}:{rule_id}"
            for rule_id in self.active_rule_ids
        )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "stage_subject_digest": self.stage_subject_digest,
            "component_ref": self.component_ref,
            "component_digest": self.component_digest,
            "semantic_kind": self.semantic_kind,
            "baseline_level": self.baseline_level.value,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "capability_id": self.capability_id.value,
            "policy_digest": self.policy_digest,
            "pack_digest": self.pack_digest,
            "mandatory_roles": [item.value for item in self.mandatory_roles],
            "active_rule_ids": list(self.active_rule_ids),
            "basis_ref": self.basis_ref,
            "authority_ref": self.authority_ref,
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "binding_digest": self.binding_digest}

    @classmethod
    def from_dict(cls, value: object) -> "SemanticRulePackBinding":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "branch",
                "stage_id",
                "stage_subject_digest",
                "component_ref",
                "component_digest",
                "semantic_kind",
                "baseline_level",
                "pack_id",
                "pack_version",
                "capability_id",
                "policy_digest",
                "pack_digest",
                "mandatory_roles",
                "active_rule_ids",
                "basis_ref",
                "authority_ref",
                "binding_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic rule-pack binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SemanticCapabilityPolicyError(
                "unsupported semantic rule-pack binding schema"
            )
        if not isinstance(payload["mandatory_roles"], list) or not isinstance(
            payload["active_rule_ids"], list
        ):
            raise TypeError("semantic rule-pack binding arrays must be lists")
        result = cls(
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            stage_subject_digest=payload["stage_subject_digest"],
            component_ref=payload["component_ref"],
            component_digest=payload["component_digest"],
            semantic_kind=payload["semantic_kind"],
            baseline_level=StageBaselineLevel(payload["baseline_level"]),
            pack_id=payload["pack_id"],
            pack_version=payload["pack_version"],
            capability_id=SemanticCapabilityId(payload["capability_id"]),
            policy_digest=payload["policy_digest"],
            pack_digest=payload["pack_digest"],
            mandatory_roles=tuple(
                StageBaselineRole(item) for item in payload["mandatory_roles"]
            ),
            active_rule_ids=tuple(payload["active_rule_ids"]),
            basis_ref=payload["basis_ref"],
            authority_ref=payload["authority_ref"],
        )
        if result.to_dict() != payload:
            raise SemanticCapabilityPolicyError(
                "semantic rule-pack binding digest changed"
            )
        return result


_CAPABILITY_TOPICS = {
    SemanticCapabilityId.STAIR: "vertical-circulation",
}


@dataclass(frozen=True, slots=True)
class SemanticDesignWorkItem:
    """Non-dischargeable turn work mechanically projected from one binding.

    A work item can direct an Architect or expert toward the questions that
    must be resolved.  It has no status and no acceptance authority: citing
    every response ref proves only that the turn addressed the questions.
    Stage closure still requires independently replayed typed check receipts.
    """

    inventory_digest: str
    binding: SemanticRulePackBinding
    topic: str

    SCHEMA = "SemanticDesignWorkItem@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "inventory_digest",
            require_sha256(self.inventory_digest, "semantic inventory_digest"),
        )
        if not isinstance(self.binding, SemanticRulePackBinding):
            raise TypeError("binding must be a SemanticRulePackBinding")
        expected_topic = _CAPABILITY_TOPICS[self.binding.capability_id]
        identifier(self.topic, "semantic work topic")
        if self.topic != expected_topic:
            raise SemanticCapabilityPolicyError(
                "semantic work topic differs from the framework capability"
            )
        if not self.binding.active_rule_ids:
            raise SemanticCapabilityPolicyError(
                "semantic design work requires at least one active rule"
            )

    @property
    def work_item_digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def ref(self) -> str:
        return f"semantic-work:{self.work_item_digest}"

    @property
    def rule_refs(self) -> tuple[str, ...]:
        return self.binding.rule_refs

    @property
    def required_response_refs(self) -> tuple[str, ...]:
        return (self.ref, *self.rule_refs)

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "inventory_digest": self.inventory_digest,
            "binding": self.binding.to_dict(),
            "topic": self.topic,
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "work_item_digest": self.work_item_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticDesignWorkItem":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "inventory_digest",
                "binding",
                "topic",
                "work_item_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic design work item",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SemanticCapabilityPolicyError(
                "unsupported semantic design work-item schema"
            )
        result = cls(
            inventory_digest=payload["inventory_digest"],
            binding=SemanticRulePackBinding.from_dict(payload["binding"]),
            topic=payload["topic"],
        )
        if result.to_dict() != payload:
            raise SemanticCapabilityPolicyError(
                "semantic design work-item digest changed"
            )
        return result


def compile_semantic_design_work_items(
    *,
    inventory_digest: str,
    bindings: tuple[SemanticRulePackBinding, ...],
) -> tuple[SemanticDesignWorkItem, ...]:
    """Project exact bindings into bounded, non-accepting turn work."""

    inventory_digest = require_sha256(
        inventory_digest,
        "semantic inventory_digest",
    )
    if not isinstance(bindings, tuple) or any(
        not isinstance(item, SemanticRulePackBinding) for item in bindings
    ):
        raise TypeError("bindings must contain SemanticRulePackBinding values")
    work_items = tuple(
        sorted(
            (
                SemanticDesignWorkItem(
                    inventory_digest=inventory_digest,
                    binding=binding,
                    topic=_CAPABILITY_TOPICS[binding.capability_id],
                )
                for binding in bindings
                if binding.active_rule_ids
            ),
            key=lambda item: (item.binding.component_ref, item.binding.pack_id),
        )
    )
    if len({item.ref for item in work_items}) != len(work_items):
        raise SemanticCapabilityPolicyError(
            "semantic design work items are not unique"
        )
    return work_items


def _stair_policy_v1() -> SemanticCapabilityPolicy:
    baseline_rule_ids = (
        "stair-evidence-applicability",
        "stair-integer-step-solution",
        "stair-landing-ownership",
        "stair-plan-envelope",
        "stair-terminal-datums",
        "stair-terminal-topology",
    )
    developed_rule_ids = tuple(
        sorted(
            (
                *baseline_rule_ids,
                "stair-cad-readback",
                "stair-headroom",
                "stair-host-cut",
                "stair-walking-surface-continuity",
            )
        )
    )
    coordinated_rule_ids = tuple(
        sorted(
            (
                *developed_rule_ids,
                "stair-collision-clearance",
                "stair-guard-handrail-applicability",
                "stair-support-load-path",
            )
        )
    )
    vertical_role = (StageBaselineRole.VERTICAL_CIRCULATION,)
    stage_rules = tuple(
        sorted(
            (
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.PRE_GEOMETRY,
                    mandatory_roles=(),
                    rule_ids=(),
                ),
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.SPATIAL,
                    mandatory_roles=vertical_role,
                    rule_ids=baseline_rule_ids,
                ),
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.DEVELOPED,
                    mandatory_roles=vertical_role,
                    rule_ids=developed_rule_ids,
                ),
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.COORDINATED,
                    mandatory_roles=vertical_role,
                    rule_ids=coordinated_rule_ids,
                ),
            ),
            key=lambda item: item.baseline_level.value,
        )
    )
    return SemanticCapabilityPolicy(
        policy_id="archflow-semantic-capabilities",
        policy_version=1,
        packs=(
            SemanticRulePack(
                pack_id="stair-control",
                pack_version=1,
                capability_id=SemanticCapabilityId.STAIR,
                semantic_kinds=(
                    "exterior-stair",
                    "exterior-stair-envelope",
                    "interior-stair",
                    "spiral-stair",
                    "spiral-stair-reservation",
                    "stair",
                    "stair-assembly",
                    "stair-reservation",
                    "staircase",
                    "stairs",
                    "stairway",
                    "stairwell",
                    "vertical-circulation",
                ),
                stage_rules=stage_rules,
                basis_ref="archflow-policy:semantic-stair-v1",
                authority_ref="archflow-control:mandatory-semantic-dispatch-v1",
            ),
        ),
    )


def _stair_policy_v2() -> SemanticCapabilityPolicy:
    """Require stair materialization prerequisites at developed maturity.

    V1 remains replayable evidence.  V2 is the authoring policy: a stair may
    still be reserved spatially, but developed work must address site support,
    load path, host cuts, and hosted-underpass applicability before it can be
    represented as a mature physical object.  The rules state generic
    questions only; they contain no project dimensions or form decisions.
    """

    spatial_rule_ids = (
        "stair-evidence-applicability",
        "stair-integer-step-solution",
        "stair-landing-ownership",
        "stair-plan-envelope",
        "stair-terminal-datums",
        "stair-terminal-topology",
    )
    developed_rule_ids = tuple(
        sorted(
            (
                *spatial_rule_ids,
                "stair-cad-readback",
                "stair-headroom",
                "stair-host-cut",
                "stair-site-support",
                "stair-support-load-path",
                "stair-underpass-applicability",
                "stair-walking-surface-continuity",
            )
        )
    )
    coordinated_rule_ids = tuple(
        sorted(
            (
                *developed_rule_ids,
                "stair-collision-clearance",
                "stair-guard-handrail-applicability",
            )
        )
    )
    vertical_role = (StageBaselineRole.VERTICAL_CIRCULATION,)
    stage_rules = tuple(
        sorted(
            (
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.PRE_GEOMETRY,
                    mandatory_roles=(),
                    rule_ids=(),
                ),
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.SPATIAL,
                    mandatory_roles=vertical_role,
                    rule_ids=spatial_rule_ids,
                ),
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.DEVELOPED,
                    mandatory_roles=vertical_role,
                    rule_ids=developed_rule_ids,
                ),
                SemanticStageRuleSet(
                    baseline_level=StageBaselineLevel.COORDINATED,
                    mandatory_roles=vertical_role,
                    rule_ids=coordinated_rule_ids,
                ),
            ),
            key=lambda item: item.baseline_level.value,
        )
    )
    return SemanticCapabilityPolicy(
        policy_id="archflow-semantic-capabilities",
        policy_version=2,
        packs=(
            SemanticRulePack(
                pack_id="stair-control",
                pack_version=2,
                capability_id=SemanticCapabilityId.STAIR,
                semantic_kinds=(
                    "exterior-stair",
                    "exterior-stair-envelope",
                    "interior-stair",
                    "spiral-stair",
                    "spiral-stair-reservation",
                    "stair",
                    "stair-assembly",
                    "stair-reservation",
                    "staircase",
                    "stairs",
                    "stairway",
                    "stairwell",
                    "vertical-circulation",
                ),
                stage_rules=stage_rules,
                basis_ref="archflow-policy:semantic-stair-v2",
                authority_ref=(
                    "archflow-control:mandatory-semantic-dispatch-v2"
                ),
            ),
        ),
    )


_SUPPORTED_POLICIES = (_stair_policy_v1(), _stair_policy_v2())
_CURRENT_POLICY = _SUPPORTED_POLICIES[-1]


def current_semantic_capability_policy() -> SemanticCapabilityPolicy:
    """Return the immutable current framework policy value."""

    return _CURRENT_POLICY


def require_supported_semantic_capability_policy(
    policy: SemanticCapabilityPolicy,
) -> SemanticCapabilityPolicy:
    if not isinstance(policy, SemanticCapabilityPolicy):
        raise TypeError("policy must be a SemanticCapabilityPolicy")
    for supported in _SUPPORTED_POLICIES:
        if policy.policy_digest == supported.policy_digest:
            if policy != supported:
                raise SemanticCapabilityPolicyError(
                    "semantic capability policy digest collision"
                )
            return policy
    raise SemanticCapabilityPolicyError(
        "semantic capability policy is not a supported framework policy"
    )


def require_current_semantic_capability_policy(
    policy: SemanticCapabilityPolicy,
) -> SemanticCapabilityPolicy:
    """Require the authoring policy; historical replay may use supported."""

    policy = require_supported_semantic_capability_policy(policy)
    if policy != _CURRENT_POLICY:
        raise SemanticCapabilityPolicyError(
            "new semantic capability authoring requires the current policy"
        )
    return policy


def bind_semantic_rule_packs(
    *,
    policy: SemanticCapabilityPolicy,
    branch: BranchRef,
    stage_id: str,
    stage_subject_digest: str,
    component_ref: str,
    component_digest: str,
    semantic_kind: str,
    baseline_level: StageBaselineLevel,
) -> tuple[SemanticRulePackBinding, ...]:
    """Derive, never accept from a caller, the packs for one component."""

    policy = require_supported_semantic_capability_policy(policy)
    if not isinstance(branch, BranchRef):
        raise TypeError("branch must be a BranchRef")
    branch.run.base.require_digest()
    identifier(stage_id, "stage_id")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )
    component_ref = logical_ref(component_ref, "component_ref")
    component_digest = require_sha256(component_digest, "component_digest")
    identifier(semantic_kind, "semantic_kind")
    if not isinstance(baseline_level, StageBaselineLevel):
        raise TypeError("baseline_level must be a StageBaselineLevel")
    result = []
    for pack in policy.packs:
        if not pack.matches(semantic_kind):
            continue
        rules = pack.rule_set_for(baseline_level)
        result.append(
            SemanticRulePackBinding(
                branch=branch,
                stage_id=stage_id,
                stage_subject_digest=stage_subject_digest,
                component_ref=component_ref,
                component_digest=component_digest,
                semantic_kind=semantic_kind,
                baseline_level=baseline_level,
                pack_id=pack.pack_id,
                pack_version=pack.pack_version,
                capability_id=pack.capability_id,
                policy_digest=policy.policy_digest,
                pack_digest=pack.pack_digest,
                mandatory_roles=rules.mandatory_roles,
                active_rule_ids=rules.rule_ids,
                basis_ref=pack.basis_ref,
                authority_ref=pack.authority_ref,
            )
        )
    return tuple(sorted(result, key=lambda item: item.pack_id))


__all__ = [
    "SemanticCapabilityId",
    "SemanticCapabilityPolicy",
    "SemanticCapabilityPolicyError",
    "SemanticDesignWorkItem",
    "SemanticRulePack",
    "SemanticRulePackBinding",
    "SemanticStageRuleSet",
    "bind_semantic_rule_packs",
    "compile_semantic_design_work_items",
    "current_semantic_capability_policy",
    "require_current_semantic_capability_policy",
    "require_supported_semantic_capability_policy",
]
