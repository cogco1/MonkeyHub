"""Evidence-bound resource and constructability policy without design answers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.operational_state import (
    DesignObligation,
    FactEpistemicStatus,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_digest, require_sha256


_MAX_ITEMS = 1_024
_MAX_TEXT = 1_000


class ResourcePolicyMode(StrEnum):
    UNKNOWN = "unknown"
    CREATIVE = "creative"
    SURVIVAL = "survival"
    EXTERNALLY_SUPPLIED = "externally_supplied"


class BuildStagingMode(StrEnum):
    UNKNOWN = "unknown"
    SINGLE_PASS = "single_pass"
    STAGED = "staged"


class ProtectedBlockAction(StrEnum):
    DO_NOT_REPLACE = "do_not_replace"
    DO_NOT_REMOVE = "do_not_remove"
    DO_NOT_USE = "do_not_use"


class ConstructabilityTopic(StrEnum):
    RESOURCE = "resource"
    PROTECTION = "protection"
    BUDGET = "budget"
    STAGING = "staging"
    ACCESS = "access"
    SUPPORT = "support"
    REACH = "reach"
    UNKNOWN = "unknown"


class PolicyConstraintStrength(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True, slots=True)
class BuildAssumption:
    assumption_id: str
    statement: str
    authority_id: str
    source_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.assumption_id, "assumption_id")
        _text(self.statement, "statement")
        _text(self.authority_id, "authority_id")
        _refs(self.source_refs, "source_refs")
        _text(self.compiler_id, "compiler_id")
        require_sha256(self.base_state_sha256, "base_state_sha256")

    @property
    def ref(self) -> str:
        return f"build-assumption:{self.assumption_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "assumption_id": self.assumption_id,
            "statement": self.statement,
            "authority_id": self.authority_id,
            "source_refs": list(self.source_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> BuildAssumption:
        payload = _mapping(value, "build assumption")
        _exact(
            payload,
            {
                "assumption_id",
                "statement",
                "authority_id",
                "source_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "build assumption",
        )
        return cls(
            assumption_id=payload["assumption_id"],
            statement=payload["statement"],
            authority_id=payload["authority_id"],
            source_refs=_strings(payload["source_refs"], "source_refs"),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class PolicyProvenance:
    authority_id: str
    source_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        _text(self.authority_id, "authority_id")
        _refs(self.source_refs, "source_refs")
        _refs(
            self.assumption_refs,
            "assumption_refs",
            allow_empty=True,
        )
        _text(self.compiler_id, "compiler_id")
        require_sha256(self.base_state_sha256, "base_state_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "authority_id": self.authority_id,
            "source_refs": list(self.source_refs),
            "assumption_refs": list(self.assumption_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> PolicyProvenance:
        payload = _mapping(value, "policy provenance")
        _exact(
            payload,
            {
                "authority_id",
                "source_refs",
                "assumption_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "policy provenance",
        )
        return cls(
            authority_id=payload["authority_id"],
            source_refs=_strings(payload["source_refs"], "source_refs"),
            assumption_refs=_strings(
                payload["assumption_refs"],
                "assumption_refs",
            ),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ResourceAvailability:
    resource_ref: str
    minimum_available: float | None
    maximum_available: float | None
    unit: str
    epistemic_status: FactEpistemicStatus
    provenance: PolicyProvenance

    def __post_init__(self) -> None:
        require_logical_ref(self.resource_ref, "resource_ref")
        _text(self.unit, "unit")
        if not isinstance(self.epistemic_status, FactEpistemicStatus):
            raise TypeError(
                "epistemic_status must be FactEpistemicStatus"
            )
        if self.epistemic_status is FactEpistemicStatus.UNKNOWN:
            if (
                self.minimum_available is not None
                or self.maximum_available is not None
            ):
                raise ValueError(
                    "unknown availability cannot claim a quantity"
                )
        else:
            _range(
                self.minimum_available,
                self.maximum_available,
                "availability",
            )
        if not isinstance(self.provenance, PolicyProvenance):
            raise TypeError("provenance must be PolicyProvenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_ref": self.resource_ref,
            "minimum_available": self.minimum_available,
            "maximum_available": self.maximum_available,
            "unit": self.unit,
            "epistemic_status": self.epistemic_status.value,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ResourceAvailability:
        payload = _mapping(value, "resource availability")
        _exact(
            payload,
            {
                "resource_ref",
                "minimum_available",
                "maximum_available",
                "unit",
                "epistemic_status",
                "provenance",
            },
            "resource availability",
        )
        return cls(
            resource_ref=payload["resource_ref"],
            minimum_available=payload["minimum_available"],
            maximum_available=payload["maximum_available"],
            unit=payload["unit"],
            epistemic_status=_enum(
                FactEpistemicStatus,
                payload["epistemic_status"],
                "epistemic_status",
            ),
            provenance=PolicyProvenance.from_dict(
                payload["provenance"]
            ),
        )


@dataclass(frozen=True, slots=True)
class ResourceDemand:
    demand_id: str
    resource_ref: str
    minimum_required: float
    maximum_required: float
    unit: str
    provenance: PolicyProvenance

    def __post_init__(self) -> None:
        require_local_id(self.demand_id, "demand_id")
        require_logical_ref(self.resource_ref, "resource_ref")
        _range(
            self.minimum_required,
            self.maximum_required,
            "requirement",
        )
        _text(self.unit, "unit")
        if not isinstance(self.provenance, PolicyProvenance):
            raise TypeError("provenance must be PolicyProvenance")
        if not self.provenance.assumption_refs:
            raise ValueError(
                "pre-topology resource demand requires assumptions"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "demand_id": self.demand_id,
            "resource_ref": self.resource_ref,
            "minimum_required": self.minimum_required,
            "maximum_required": self.maximum_required,
            "unit": self.unit,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ResourceDemand:
        payload = _mapping(value, "resource demand")
        _exact(
            payload,
            {
                "demand_id",
                "resource_ref",
                "minimum_required",
                "maximum_required",
                "unit",
                "provenance",
            },
            "resource demand",
        )
        return cls(
            demand_id=payload["demand_id"],
            resource_ref=payload["resource_ref"],
            minimum_required=payload["minimum_required"],
            maximum_required=payload["maximum_required"],
            unit=payload["unit"],
            provenance=PolicyProvenance.from_dict(
                payload["provenance"]
            ),
        )


@dataclass(frozen=True, slots=True)
class ProtectedBlockRule:
    rule_id: str
    target_ref: str
    action: ProtectedBlockAction
    provenance: PolicyProvenance

    def __post_init__(self) -> None:
        require_local_id(self.rule_id, "rule_id")
        require_logical_ref(self.target_ref, "target_ref")
        if not isinstance(self.action, ProtectedBlockAction):
            raise TypeError("action must be ProtectedBlockAction")
        if not isinstance(self.provenance, PolicyProvenance):
            raise TypeError("provenance must be PolicyProvenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "target_ref": self.target_ref,
            "action": self.action.value,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ProtectedBlockRule:
        payload = _mapping(value, "protected block rule")
        _exact(
            payload,
            {"rule_id", "target_ref", "action", "provenance"},
            "protected block rule",
        )
        return cls(
            rule_id=payload["rule_id"],
            target_ref=payload["target_ref"],
            action=_enum(
                ProtectedBlockAction,
                payload["action"],
                "action",
            ),
            provenance=PolicyProvenance.from_dict(
                payload["provenance"]
            ),
        )


@dataclass(frozen=True, slots=True)
class BuildBudgetLimit:
    budget_id: str
    metric: str
    maximum: float
    unit: str
    provenance: PolicyProvenance

    def __post_init__(self) -> None:
        require_local_id(self.budget_id, "budget_id")
        require_local_id(self.metric, "metric")
        _positive(self.maximum, "maximum")
        _text(self.unit, "unit")
        if not isinstance(self.provenance, PolicyProvenance):
            raise TypeError("provenance must be PolicyProvenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "budget_id": self.budget_id,
            "metric": self.metric,
            "maximum": self.maximum,
            "unit": self.unit,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> BuildBudgetLimit:
        payload = _mapping(value, "build budget limit")
        _exact(
            payload,
            {"budget_id", "metric", "maximum", "unit", "provenance"},
            "build budget limit",
        )
        return cls(
            budget_id=payload["budget_id"],
            metric=payload["metric"],
            maximum=payload["maximum"],
            unit=payload["unit"],
            provenance=PolicyProvenance.from_dict(
                payload["provenance"]
            ),
        )


@dataclass(frozen=True, slots=True)
class StagingAssumption:
    stage_id: str
    statement: str
    predecessor_ids: tuple[str, ...]
    provenance: PolicyProvenance

    def __post_init__(self) -> None:
        require_local_id(self.stage_id, "stage_id")
        _text(self.statement, "statement")
        _ids(
            self.predecessor_ids,
            "predecessor_ids",
            allow_empty=True,
        )
        if self.stage_id in self.predecessor_ids:
            raise ValueError("staging assumption cannot precede itself")
        if not isinstance(self.provenance, PolicyProvenance):
            raise TypeError("provenance must be PolicyProvenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "statement": self.statement,
            "predecessor_ids": list(self.predecessor_ids),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> StagingAssumption:
        payload = _mapping(value, "staging assumption")
        _exact(
            payload,
            {
                "stage_id",
                "statement",
                "predecessor_ids",
                "provenance",
            },
            "staging assumption",
        )
        return cls(
            stage_id=payload["stage_id"],
            statement=payload["statement"],
            predecessor_ids=_strings(
                payload["predecessor_ids"],
                "predecessor_ids",
            ),
            provenance=PolicyProvenance.from_dict(
                payload["provenance"]
            ),
        )


@dataclass(frozen=True, slots=True)
class ConstructabilityConstraint:
    constraint_id: str
    topic: ConstructabilityTopic
    strength: PolicyConstraintStrength
    statement: str
    subject_refs: tuple[str, ...]
    provenance: PolicyProvenance

    def __post_init__(self) -> None:
        require_local_id(self.constraint_id, "constraint_id")
        if not isinstance(self.topic, ConstructabilityTopic):
            raise TypeError("topic must be ConstructabilityTopic")
        if not isinstance(self.strength, PolicyConstraintStrength):
            raise TypeError("strength must be PolicyConstraintStrength")
        _text(self.statement, "statement")
        _refs(self.subject_refs, "subject_refs", allow_empty=True)
        if not isinstance(self.provenance, PolicyProvenance):
            raise TypeError("provenance must be PolicyProvenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "constraint_id": self.constraint_id,
            "topic": self.topic.value,
            "strength": self.strength.value,
            "statement": self.statement,
            "subject_refs": list(self.subject_refs),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ConstructabilityConstraint:
        payload = _mapping(value, "constructability constraint")
        _exact(
            payload,
            {
                "constraint_id",
                "topic",
                "strength",
                "statement",
                "subject_refs",
                "provenance",
            },
            "constructability constraint",
        )
        return cls(
            constraint_id=payload["constraint_id"],
            topic=_enum(
                ConstructabilityTopic,
                payload["topic"],
                "topic",
            ),
            strength=_enum(
                PolicyConstraintStrength,
                payload["strength"],
                "strength",
            ),
            statement=payload["statement"],
            subject_refs=_strings(
                payload["subject_refs"],
                "subject_refs",
            ),
            provenance=PolicyProvenance.from_dict(
                payload["provenance"]
            ),
        )


@dataclass(frozen=True, slots=True)
class BuildPolicy:
    """BuildPolicy@1 constrains alternatives without choosing one."""

    project_id: str
    run_id: str
    base: ProjectVersionRef
    brief_digest: str
    program_digest: str
    site_context_digest: str
    compiler_id: str
    compiler_version: str
    resource_mode: ResourcePolicyMode
    staging_mode: BuildStagingMode
    disposable_sandbox: bool
    unbounded_resources: bool
    policy_provenance: PolicyProvenance
    assumptions: tuple[BuildAssumption, ...]
    availability: tuple[ResourceAvailability, ...]
    demands: tuple[ResourceDemand, ...]
    protected_rules: tuple[ProtectedBlockRule, ...]
    budget_limits: tuple[BuildBudgetLimit, ...]
    staging_assumptions: tuple[StagingAssumption, ...]
    constraints: tuple[ConstructabilityConstraint, ...]
    obligations: tuple[DesignObligation, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "BuildPolicy@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("build policy and base belong to different projects")
        base_digest = self.base.require_digest()
        for value, field in (
            (self.brief_digest, "brief_digest"),
            (self.program_digest, "program_digest"),
            (self.site_context_digest, "site_context_digest"),
        ):
            require_sha256(value, field)
        _text(self.compiler_id, "compiler_id")
        _text(self.compiler_version, "compiler_version")
        if not isinstance(self.resource_mode, ResourcePolicyMode):
            raise TypeError(
                "resource_mode must be ResourcePolicyMode"
            )
        if not isinstance(self.staging_mode, BuildStagingMode):
            raise TypeError("staging_mode must be BuildStagingMode")
        if not isinstance(self.disposable_sandbox, bool):
            raise TypeError("disposable_sandbox must be boolean")
        if not isinstance(self.unbounded_resources, bool):
            raise TypeError("unbounded_resources must be boolean")
        if self.unbounded_resources and (
            self.resource_mode is not ResourcePolicyMode.CREATIVE
            or not self.disposable_sandbox
        ):
            raise ValueError(
                "unbounded resources require explicit creative disposable sandbox"
            )
        if not isinstance(self.policy_provenance, PolicyProvenance):
            raise TypeError(
                "policy_provenance must be PolicyProvenance"
            )
        _typed(self.assumptions, BuildAssumption, "assumptions")
        _typed(
            self.availability,
            ResourceAvailability,
            "availability",
        )
        _typed(self.demands, ResourceDemand, "demands")
        _typed(
            self.protected_rules,
            ProtectedBlockRule,
            "protected_rules",
        )
        _typed(self.budget_limits, BuildBudgetLimit, "budget_limits")
        _typed(
            self.staging_assumptions,
            StagingAssumption,
            "staging_assumptions",
        )
        _typed(
            self.constraints,
            ConstructabilityConstraint,
            "constraints",
        )
        _typed(self.obligations, DesignObligation, "obligations")
        _refs(self.evidence_refs, "evidence_refs")
        assumption_refs = {item.ref for item in self.assumptions}
        evidence = set(self.evidence_refs)
        provenanced = (
            self.policy_provenance,
            *(item.provenance for item in self.availability),
            *(item.provenance for item in self.demands),
            *(item.provenance for item in self.protected_rules),
            *(item.provenance for item in self.budget_limits),
            *(item.provenance for item in self.staging_assumptions),
            *(item.provenance for item in self.constraints),
        )
        for provenance in provenanced:
            if (
                provenance.compiler_id != self.compiler_id
                or provenance.base_state_sha256 != base_digest
            ):
                raise ValueError(
                    "policy item provenance disagrees with compilation"
                )
            if not set(provenance.source_refs) <= evidence:
                raise ValueError(
                    "policy item source is absent from policy evidence"
                )
            if not set(provenance.assumption_refs) <= assumption_refs:
                raise ValueError(
                    "policy item cites an unknown assumption"
                )
        for assumption in self.assumptions:
            if (
                assumption.compiler_id != self.compiler_id
                or assumption.base_state_sha256 != base_digest
                or not set(assumption.source_refs) <= evidence
            ):
                raise ValueError(
                    "build assumption provenance disagrees with compilation"
                )
        _unique(
            tuple(item.assumption_id for item in self.assumptions),
            "assumption ids",
        )
        _unique(
            tuple(item.resource_ref for item in self.availability),
            "availability resource refs",
        )
        _unique(
            tuple(item.demand_id for item in self.demands),
            "demand ids",
        )
        _unique(
            tuple(item.rule_id for item in self.protected_rules),
            "protected rule ids",
        )
        _unique(
            tuple(item.budget_id for item in self.budget_limits),
            "budget ids",
        )
        stage_ids = tuple(
            item.stage_id for item in self.staging_assumptions
        )
        _unique(stage_ids, "stage ids")
        stage_id_set = set(stage_ids)
        if any(
            not set(item.predecessor_ids) <= stage_id_set
            for item in self.staging_assumptions
        ):
            raise ValueError("stage cites an unknown predecessor")
        _assert_acyclic(self.staging_assumptions)
        _unique(
            tuple(item.constraint_id for item in self.constraints),
            "constraint ids",
        )
        _unique(
            tuple(item.obligation_id for item in self.obligations),
            "obligation ids",
        )

    @property
    def policy_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.base.project_id,
                "version": self.base.version,
                "state_sha256": self.base.require_digest(),
            },
            "brief_digest": self.brief_digest,
            "program_digest": self.program_digest,
            "site_context_digest": self.site_context_digest,
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "resource_mode": self.resource_mode.value,
            "staging_mode": self.staging_mode.value,
            "disposable_sandbox": self.disposable_sandbox,
            "unbounded_resources": self.unbounded_resources,
            "policy_provenance": self.policy_provenance.to_dict(),
            "assumptions": [
                item.to_dict() for item in self.assumptions
            ],
            "availability": [
                item.to_dict() for item in self.availability
            ],
            "demands": [item.to_dict() for item in self.demands],
            "protected_rules": [
                item.to_dict() for item in self.protected_rules
            ],
            "budget_limits": [
                item.to_dict() for item in self.budget_limits
            ],
            "staging_assumptions": [
                item.to_dict() for item in self.staging_assumptions
            ],
            "constraints": [
                item.to_dict() for item in self.constraints
            ],
            "obligations": [
                item.to_dict() for item in self.obligations
            ],
            "evidence_refs": list(self.evidence_refs),
            "generation_authority": False,
            "palette_selected": False,
            "geometry_selected": False,
            "world_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> BuildPolicy:
        payload = _mapping(value, "build policy")
        expected = {
            "schema",
            "project_id",
            "run_id",
            "base",
            "brief_digest",
            "program_digest",
            "site_context_digest",
            "compiler_id",
            "compiler_version",
            "resource_mode",
            "staging_mode",
            "disposable_sandbox",
            "unbounded_resources",
            "policy_provenance",
            "assumptions",
            "availability",
            "demands",
            "protected_rules",
            "budget_limits",
            "staging_assumptions",
            "constraints",
            "obligations",
            "evidence_refs",
            "generation_authority",
            "palette_selected",
            "geometry_selected",
            "world_write_authority",
        }
        _exact(payload, expected, "build policy")
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("unsupported build policy schema")
        if any(
            payload[field] is not False
            for field in (
                "generation_authority",
                "palette_selected",
                "geometry_selected",
                "world_write_authority",
            )
        ):
            raise ValueError("build policy cannot gain design authority")
        base = _mapping(payload["base"], "base")
        _exact(
            base,
            {"project_id", "version", "state_sha256"},
            "base",
        )
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=base["project_id"],
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
            brief_digest=payload["brief_digest"],
            program_digest=payload["program_digest"],
            site_context_digest=payload["site_context_digest"],
            compiler_id=payload["compiler_id"],
            compiler_version=payload["compiler_version"],
            resource_mode=_enum(
                ResourcePolicyMode,
                payload["resource_mode"],
                "resource_mode",
            ),
            staging_mode=_enum(
                BuildStagingMode,
                payload["staging_mode"],
                "staging_mode",
            ),
            disposable_sandbox=payload["disposable_sandbox"],
            unbounded_resources=payload["unbounded_resources"],
            policy_provenance=PolicyProvenance.from_dict(
                payload["policy_provenance"]
            ),
            assumptions=tuple(
                BuildAssumption.from_dict(item)
                for item in _list(payload["assumptions"], "assumptions")
            ),
            availability=tuple(
                ResourceAvailability.from_dict(item)
                for item in _list(
                    payload["availability"],
                    "availability",
                )
            ),
            demands=tuple(
                ResourceDemand.from_dict(item)
                for item in _list(payload["demands"], "demands")
            ),
            protected_rules=tuple(
                ProtectedBlockRule.from_dict(item)
                for item in _list(
                    payload["protected_rules"],
                    "protected_rules",
                )
            ),
            budget_limits=tuple(
                BuildBudgetLimit.from_dict(item)
                for item in _list(
                    payload["budget_limits"],
                    "budget_limits",
                )
            ),
            staging_assumptions=tuple(
                StagingAssumption.from_dict(item)
                for item in _list(
                    payload["staging_assumptions"],
                    "staging_assumptions",
                )
            ),
            constraints=tuple(
                ConstructabilityConstraint.from_dict(item)
                for item in _list(
                    payload["constraints"],
                    "constraints",
                )
            ),
            obligations=tuple(
                DesignObligation.from_dict(item)
                for item in _list(
                    payload["obligations"],
                    "obligations",
                )
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "evidence_refs",
            ),
        )


def _assert_acyclic(stages: tuple[StagingAssumption, ...]) -> None:
    predecessors = {
        item.stage_id: set(item.predecessor_ids) for item in stages
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(stage_id: str) -> None:
        if stage_id in visiting:
            raise ValueError("staging assumptions contain a cycle")
        if stage_id in visited:
            return
        visiting.add(stage_id)
        for predecessor in predecessors[stage_id]:
            visit(predecessor)
        visiting.remove(stage_id)
        visited.add(stage_id)

    for stage_id in predecessors:
        visit(stage_id)


def _range(minimum: object, maximum: object, field: str) -> None:
    _number(minimum, f"{field} minimum")
    _number(maximum, f"{field} maximum")
    if minimum < 0 or maximum < minimum:
        raise ValueError(f"{field} must be a non-negative ordered range")


def _positive(value: object, field: str) -> None:
    _number(value, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _number(value: object, field: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{field} must be a finite number")


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _tuple(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def _typed(value: object, item_type: type, field: str) -> None:
    items = _tuple(value, field)
    if any(not isinstance(item, item_type) for item in items):
        raise TypeError(f"{field} contains the wrong item type")


def _refs(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    items = _tuple(value, field)
    if not items and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in items:
        require_logical_ref(item, field)
    _unique(tuple(items), field)


def _ids(
    value: object,
    field: str,
    *,
    allow_empty: bool,
) -> None:
    items = _tuple(value, field)
    if not items and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in items:
        require_local_id(item, field)
    _unique(tuple(items), field)


def _unique(values: tuple[object, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded list")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    values = _list(value, field)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{field} must contain strings")
    return tuple(values)


def _exact(
    payload: Mapping[str, object],
    keys: set[str],
    field: str,
) -> None:
    if set(payload) != keys:
        raise ValueError(f"{field} schema drifted")


def _enum(enum_type: type[StrEnum], value: object, field: str) -> StrEnum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not a supported value") from exc


