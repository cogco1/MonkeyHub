"""Typed design-development state with no candidate or execution authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from archflow.project.refs import ProjectVersionRef
from archflow.project.refs import require_identifier
from archflow.state.stage_workflow import DesignPhase
from archflow.state.design_portfolio import (
    BranchRevisionRef,
    SelectedBranchHandoff,
)
from archflow.state.operational_state import require_logical_ref
from archflow.state.spatial import SchematicOption
from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.contracts.fields import (
    mapping as _mapping,
    string_tuple as _strings,
)
from archflow.contracts.fields import (
    exact_mapping as _exact,
    ids as _ids,
    refs as _refs,
    text as _text,
)


_HEX = frozenset("0123456789abcdef")
_MAX_ITEMS = 4_096
_MAX_TEXT = 4_000


class DevelopedDesignError(ValueError):
    """A developed-design value is stale, malformed, or over-authorized."""


class DevelopmentDiscipline(StrEnum):
    STRUCTURE_SUPPORT = "structure_support"
    CIRCULATION = "circulation"
    ENVELOPE_OPENINGS = "envelope_openings"
    MATERIALS = "materials"
    CONSTRUCTION = "construction"
    USE = "use"


class DevelopmentObligationStatus(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    RESOLVED = "resolved"


class DevelopmentObligationPriority(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


class DevelopmentCoordinationStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COORDINATED = "coordinated"
    INVALIDATED = "invalidated"


class DevelopmentClaimDisposition(StrEnum):
    ADOPTED = "adopted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class DevelopmentDependencyImpact(StrEnum):
    RECHECK = "recheck"
    INVALIDATE = "invalidate"


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise DevelopedDesignError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _canonical_value(value: object, field: str) -> str:
    _text(value, field)
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise DevelopedDesignError(
            f"{field} must contain JSON"
        ) from exc
    encoded = canonical_json(decoded)
    if encoded != value:
        raise DevelopedDesignError(f"{field} must be canonical JSON")
    return encoded


@dataclass(frozen=True, slots=True)
class SelectedSchematicInput:
    portfolio_id: str
    portfolio_digest: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    branch_id: str
    revision: BranchRevisionRef
    option: SchematicOption
    selection_transition_id: str
    selection_decision_ref: str

    SCHEMA = "SelectedSchematicInput@1"

    def __post_init__(self) -> None:
        require_identifier(self.portfolio_id, "portfolio_id")
        _sha(self.portfolio_digest, "portfolio_digest")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise DevelopedDesignError(
                "selected schematic and base belong to different projects"
            )
        require_identifier(self.branch_id, "branch_id")
        if (
            not isinstance(self.revision, BranchRevisionRef)
            or self.revision.branch_id != self.branch_id
        ):
            raise DevelopedDesignError(
                "selected revision does not match branch"
            )
        if not isinstance(self.option, SchematicOption):
            raise TypeError("option must be SchematicOption")
        require_identifier(
            self.selection_transition_id,
            "selection_transition_id",
        )
        require_logical_ref(
            self.selection_decision_ref,
            "selection_decision_ref",
        )

    @classmethod
    def from_handoff(
        cls,
        handoff: SelectedBranchHandoff,
    ) -> SelectedSchematicInput:
        if not isinstance(handoff, SelectedBranchHandoff):
            raise TypeError("handoff must be SelectedBranchHandoff")
        return cls(
            portfolio_id=handoff.portfolio_id,
            portfolio_digest=handoff.portfolio_digest,
            project_id=handoff.project_id,
            run_id=handoff.run_id,
            base=handoff.base,
            branch_id=handoff.branch_id,
            revision=handoff.revision,
            option=handoff.option,
            selection_transition_id=handoff.selection_transition_id,
            selection_decision_ref=handoff.selection_decision_ref,
        )

    @property
    def ref(self) -> str:
        return (
            f"selected-schematic:{self.branch_id}:"
            f"{self.revision.revision_digest}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "portfolio_id": self.portfolio_id,
            "portfolio_digest": self.portfolio_digest,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": self.base.to_dict(),
            "branch_id": self.branch_id,
            "revision": self.revision.to_dict(),
            "option": self.option.to_dict(),
            "selection_transition_id": self.selection_transition_id,
            "selection_decision_ref": self.selection_decision_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> SelectedSchematicInput:
        payload = _mapping(value, "selected schematic input")
        _exact(
            payload,
            {
                "schema",
                "portfolio_id",
                "portfolio_digest",
                "project_id",
                "run_id",
                "base",
                "branch_id",
                "revision",
                "option",
                "selection_transition_id",
                "selection_decision_ref",
            },
            "selected schematic input",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError(
                "selected schematic input schema changed"
            )
        return cls(
            portfolio_id=payload["portfolio_id"],
            portfolio_digest=payload["portfolio_digest"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef.from_dict(payload["base"]),
            branch_id=payload["branch_id"],
            revision=BranchRevisionRef.from_dict(payload["revision"]),
            option=SchematicOption.from_dict(payload["option"]),
            selection_transition_id=payload["selection_transition_id"],
            selection_decision_ref=payload["selection_decision_ref"],
        )


@dataclass(frozen=True, slots=True)
class DevelopmentObligation:
    obligation_id: str
    discipline: DevelopmentDiscipline
    statement: str
    priority: DevelopmentObligationPriority
    status: DevelopmentObligationStatus
    source_refs: tuple[str, ...]
    dependency_refs: tuple[str, ...]

    SCHEMA = "DevelopmentObligation@1"

    def __post_init__(self) -> None:
        require_identifier(self.obligation_id, "obligation_id")
        if not isinstance(self.discipline, DevelopmentDiscipline):
            raise TypeError("discipline must be DevelopmentDiscipline")
        _text(self.statement, "obligation statement")
        if not isinstance(self.priority, DevelopmentObligationPriority):
            raise TypeError(
                "priority must be DevelopmentObligationPriority"
            )
        if not isinstance(self.status, DevelopmentObligationStatus):
            raise TypeError("status must be DevelopmentObligationStatus")
        _refs(self.source_refs, "obligation source_refs")
        _refs(
            self.dependency_refs,
            "obligation dependency_refs",
            allow_empty=True,
        )

    @property
    def ref(self) -> str:
        return f"development-obligation:{self.obligation_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "obligation_id": self.obligation_id,
            "discipline": self.discipline.value,
            "statement": self.statement,
            "priority": self.priority.value,
            "status": self.status.value,
            "source_refs": list(self.source_refs),
            "dependency_refs": list(self.dependency_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopmentObligation:
        payload = _mapping(value, "development obligation")
        _exact(
            payload,
            {
                "schema",
                "obligation_id",
                "discipline",
                "statement",
                "priority",
                "status",
                "source_refs",
                "dependency_refs",
            },
            "development obligation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("obligation schema changed")
        return cls(
            obligation_id=payload["obligation_id"],
            discipline=DevelopmentDiscipline(payload["discipline"]),
            statement=payload["statement"],
            priority=DevelopmentObligationPriority(payload["priority"]),
            status=DevelopmentObligationStatus(payload["status"]),
            source_refs=_strings(
                payload["source_refs"],
                "obligation source_refs",
            ),
            dependency_refs=_strings(
                payload["dependency_refs"],
                "obligation dependency_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopmentClaim:
    claim_id: str
    expert_id: str
    discipline: DevelopmentDiscipline
    subject_ref: str
    attribute: str
    value_json: str
    rationale: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "DevelopmentClaim@1"

    def __post_init__(self) -> None:
        require_identifier(self.claim_id, "claim_id")
        require_identifier(self.expert_id, "expert_id")
        if not isinstance(self.discipline, DevelopmentDiscipline):
            raise TypeError("discipline must be DevelopmentDiscipline")
        require_logical_ref(self.subject_ref, "subject_ref")
        require_identifier(self.attribute, "attribute")
        _canonical_value(self.value_json, "value_json")
        _text(self.rationale, "claim rationale")
        _refs(self.evidence_refs, "claim evidence_refs")

    @property
    def ref(self) -> str:
        return f"development-claim:{self.claim_id}:{self.claim_digest}"

    @property
    def claim_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "claim_id": self.claim_id,
            "expert_id": self.expert_id,
            "discipline": self.discipline.value,
            "subject_ref": self.subject_ref,
            "attribute": self.attribute,
            "value_json": self.value_json,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopmentClaim:
        payload = _mapping(value, "development claim")
        _exact(
            payload,
            {
                "schema",
                "claim_id",
                "expert_id",
                "discipline",
                "subject_ref",
                "attribute",
                "value_json",
                "rationale",
                "evidence_refs",
            },
            "development claim",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("development claim schema changed")
        return cls(
            claim_id=payload["claim_id"],
            expert_id=payload["expert_id"],
            discipline=DevelopmentDiscipline(payload["discipline"]),
            subject_ref=payload["subject_ref"],
            attribute=payload["attribute"],
            value_json=payload["value_json"],
            rationale=payload["rationale"],
            evidence_refs=_strings(
                payload["evidence_refs"],
                "claim evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DetachedDevelopmentAdvice:
    advice_id: str
    expert_id: str
    discipline: DevelopmentDiscipline
    state_digest: str
    claims: tuple[DevelopmentClaim, ...]
    suggested_obligations: tuple[DevelopmentObligation, ...]
    summary: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "DetachedDevelopmentAdvice@1"

    def __post_init__(self) -> None:
        require_identifier(self.advice_id, "advice_id")
        require_identifier(self.expert_id, "expert_id")
        if not isinstance(self.discipline, DevelopmentDiscipline):
            raise TypeError("discipline must be DevelopmentDiscipline")
        _sha(self.state_digest, "state_digest")
        if not isinstance(self.claims, tuple) or any(
            not isinstance(item, DevelopmentClaim) for item in self.claims
        ):
            raise TypeError("claims contains an invalid item")
        if any(
            item.expert_id != self.expert_id
            or item.discipline is not self.discipline
            for item in self.claims
        ):
            raise DevelopedDesignError(
                "advice claim identity or discipline disagrees"
            )
        claim_ids = tuple(item.claim_id for item in self.claims)
        _ids(claim_ids, "advice claim ids", allow_empty=True)
        if not isinstance(self.suggested_obligations, tuple) or any(
            not isinstance(item, DevelopmentObligation)
            for item in self.suggested_obligations
        ):
            raise TypeError(
                "suggested_obligations contains an invalid item"
            )
        _text(self.summary, "advice summary")
        _refs(self.evidence_refs, "advice evidence_refs")

    @property
    def advice_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"development-advice:{self.advice_id}:{self.advice_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "advice_id": self.advice_id,
            "expert_id": self.expert_id,
            "discipline": self.discipline.value,
            "state_digest": self.state_digest,
            "claims": [item.to_dict() for item in self.claims],
            "suggested_obligations": [
                item.to_dict() for item in self.suggested_obligations
            ],
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "read_only": True,
            "mutation_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> DetachedDevelopmentAdvice:
        payload = _mapping(value, "detached development advice")
        _exact(
            payload,
            {
                "schema",
                "advice_id",
                "expert_id",
                "discipline",
                "state_digest",
                "claims",
                "suggested_obligations",
                "summary",
                "evidence_refs",
                "read_only",
                "mutation_authority",
            },
            "detached development advice",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["read_only"] is not True
        ):
            raise DevelopedDesignError(
                "expert advice acquired mutation authority"
            )
        claims = payload["claims"]
        obligations = payload["suggested_obligations"]
        if not isinstance(claims, list) or not isinstance(
            obligations,
            list,
        ):
            raise TypeError("advice nested values must be lists")
        return cls(
            advice_id=payload["advice_id"],
            expert_id=payload["expert_id"],
            discipline=DevelopmentDiscipline(payload["discipline"]),
            state_digest=payload["state_digest"],
            claims=tuple(
                DevelopmentClaim.from_dict(item) for item in claims
            ),
            suggested_obligations=tuple(
                DevelopmentObligation.from_dict(item)
                for item in obligations
            ),
            summary=payload["summary"],
            evidence_refs=_strings(
                payload["evidence_refs"],
                "advice evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopmentClaimResolution:
    claim_ref: str
    disposition: DevelopmentClaimDisposition
    rationale: str

    SCHEMA = "DevelopmentClaimResolution@1"

    def __post_init__(self) -> None:
        require_logical_ref(self.claim_ref, "claim_ref")
        if not isinstance(
            self.disposition,
            DevelopmentClaimDisposition,
        ):
            raise TypeError(
                "disposition must be DevelopmentClaimDisposition"
            )
        _text(self.rationale, "claim resolution rationale")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "claim_ref": self.claim_ref,
            "disposition": self.disposition.value,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopmentClaimResolution:
        payload = _mapping(value, "claim resolution")
        _exact(
            payload,
            {"schema", "claim_ref", "disposition", "rationale"},
            "claim resolution",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("claim resolution schema changed")
        return cls(
            claim_ref=payload["claim_ref"],
            disposition=DevelopmentClaimDisposition(
                payload["disposition"]
            ),
            rationale=payload["rationale"],
        )


@dataclass(frozen=True, slots=True)
class DevelopedAttribute:
    key: str
    value_json: str
    source_claim_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "DevelopedAttribute@1"

    def __post_init__(self) -> None:
        require_identifier(self.key, "attribute key")
        _canonical_value(self.value_json, "attribute value_json")
        _refs(
            self.source_claim_refs,
            "attribute source_claim_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "attribute evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "key": self.key,
            "value_json": self.value_json,
            "source_claim_refs": list(self.source_claim_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopedAttribute:
        payload = _mapping(value, "developed attribute")
        _exact(
            payload,
            {
                "schema",
                "key",
                "value_json",
                "source_claim_refs",
                "evidence_refs",
            },
            "developed attribute",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("developed attribute schema changed")
        return cls(
            key=payload["key"],
            value_json=payload["value_json"],
            source_claim_refs=_strings(
                payload["source_claim_refs"],
                "attribute source_claim_refs",
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "attribute evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopedComponent:
    component_id: str
    revision: int
    discipline: DevelopmentDiscipline
    attributes: tuple[DevelopedAttribute, ...]
    schematic_dependency_refs: tuple[str, ...]
    requirement_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "DevelopedComponent@2"

    def __post_init__(self) -> None:
        require_identifier(self.component_id, "component_id")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 0
        ):
            raise DevelopedDesignError(
                "component revision must be non-negative"
            )
        if not isinstance(self.discipline, DevelopmentDiscipline):
            raise TypeError("discipline must be DevelopmentDiscipline")
        if not isinstance(self.attributes, tuple) or not self.attributes:
            raise DevelopedDesignError("component requires attributes")
        if any(
            not isinstance(item, DevelopedAttribute)
            for item in self.attributes
        ):
            raise TypeError("attributes contains an invalid item")
        attribute_keys = tuple(item.key for item in self.attributes)
        _ids(attribute_keys, "component attribute keys")
        _refs(
            self.schematic_dependency_refs,
            "schematic_dependency_refs",
        )
        _refs(self.requirement_refs, "component requirement_refs")
        _refs(self.evidence_refs, "component evidence_refs")

    @property
    def component_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return (
            f"developed-component:{self.component_id}:"
            f"{self.component_digest}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_id": self.component_id,
            "revision": self.revision,
            "discipline": self.discipline.value,
            "attributes": [item.to_dict() for item in self.attributes],
            "schematic_dependency_refs": list(
                self.schematic_dependency_refs
            ),
            "requirement_refs": list(self.requirement_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopedComponent:
        payload = _mapping(value, "developed component")
        _exact(
            payload,
            {
                "schema",
                "component_id",
                "revision",
                "discipline",
                "attributes",
                "schematic_dependency_refs",
                "requirement_refs",
                "evidence_refs",
            },
            "developed component",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("developed component schema changed")
        attributes = payload["attributes"]
        if not isinstance(attributes, list):
            raise TypeError("attributes must be a list")
        return cls(
            component_id=payload["component_id"],
            revision=payload["revision"],
            discipline=DevelopmentDiscipline(payload["discipline"]),
            attributes=tuple(
                DevelopedAttribute.from_dict(item) for item in attributes
            ),
            schematic_dependency_refs=_strings(
                payload["schematic_dependency_refs"],
                "schematic_dependency_refs",
            ),
            requirement_refs=_strings(
                payload["requirement_refs"],
                "component requirement_refs",
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "component evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopmentDependency:
    dependency_id: str
    source_ref: str
    target_component_id: str
    target_discipline: DevelopmentDiscipline
    impact: DevelopmentDependencyImpact
    return_phase: DesignPhase
    evidence_refs: tuple[str, ...]

    SCHEMA = "DevelopmentDependency@1"

    def __post_init__(self) -> None:
        require_identifier(self.dependency_id, "dependency_id")
        require_logical_ref(self.source_ref, "source_ref")
        require_identifier(
            self.target_component_id,
            "target_component_id",
        )
        if not isinstance(
            self.target_discipline,
            DevelopmentDiscipline,
        ):
            raise TypeError(
                "target_discipline must be DevelopmentDiscipline"
            )
        if not isinstance(self.impact, DevelopmentDependencyImpact):
            raise TypeError(
                "impact must be DevelopmentDependencyImpact"
            )
        if self.return_phase not in {
            DesignPhase.SCHEMATIC_DESIGN,
            DesignPhase.DESIGN_DEVELOPMENT,
        }:
            raise DevelopedDesignError(
                "dependency return phase must be schematic or development"
            )
        _refs(self.evidence_refs, "dependency evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "dependency_id": self.dependency_id,
            "source_ref": self.source_ref,
            "target_component_id": self.target_component_id,
            "target_discipline": self.target_discipline.value,
            "impact": self.impact.value,
            "return_phase": self.return_phase.value,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopmentDependency:
        payload = _mapping(value, "development dependency")
        _exact(
            payload,
            {
                "schema",
                "dependency_id",
                "source_ref",
                "target_component_id",
                "target_discipline",
                "impact",
                "return_phase",
                "evidence_refs",
            },
            "development dependency",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("dependency schema changed")
        return cls(
            dependency_id=payload["dependency_id"],
            source_ref=payload["source_ref"],
            target_component_id=payload["target_component_id"],
            target_discipline=DevelopmentDiscipline(
                payload["target_discipline"]
            ),
            impact=DevelopmentDependencyImpact(payload["impact"]),
            return_phase=DesignPhase(payload["return_phase"]),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "dependency evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class ArchitectDevelopmentDecision:
    decision_id: str
    state_digest: str
    selected_advice_ids: tuple[str, ...]
    claim_resolutions: tuple[DevelopmentClaimResolution, ...]
    component_updates: tuple[DevelopedComponent, ...]
    dependencies: tuple[DevelopmentDependency, ...]
    new_obligations: tuple[DevelopmentObligation, ...]
    resolved_obligation_ids: tuple[str, ...]
    authority_id: str
    decision_ref: str
    rationale: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "ArchitectDevelopmentDecision@1"

    def __post_init__(self) -> None:
        require_identifier(self.decision_id, "decision_id")
        _sha(self.state_digest, "state_digest")
        _ids(
            self.selected_advice_ids,
            "selected_advice_ids",
            allow_empty=True,
        )
        for values, item_type, field in (
            (
                self.claim_resolutions,
                DevelopmentClaimResolution,
                "claim_resolutions",
            ),
            (
                self.component_updates,
                DevelopedComponent,
                "component_updates",
            ),
            (
                self.dependencies,
                DevelopmentDependency,
                "dependencies",
            ),
            (
                self.new_obligations,
                DevelopmentObligation,
                "new_obligations",
            ),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(item, item_type) for item in values
            ):
                raise TypeError(f"{field} contains an invalid item")
        resolution_refs = tuple(
            item.claim_ref for item in self.claim_resolutions
        )
        if len(resolution_refs) != len(set(resolution_refs)):
            raise DevelopedDesignError(
                "claim_resolutions contains duplicates"
            )
        _ids(
            self.resolved_obligation_ids,
            "resolved_obligation_ids",
            allow_empty=True,
        )
        require_identifier(self.authority_id, "authority_id")
        require_logical_ref(self.decision_ref, "decision_ref")
        _text(self.rationale, "decision rationale")
        _refs(self.evidence_refs, "decision evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "decision_id": self.decision_id,
            "state_digest": self.state_digest,
            "selected_advice_ids": list(self.selected_advice_ids),
            "claim_resolutions": [
                item.to_dict() for item in self.claim_resolutions
            ],
            "component_updates": [
                item.to_dict() for item in self.component_updates
            ],
            "dependencies": [
                item.to_dict() for item in self.dependencies
            ],
            "new_obligations": [
                item.to_dict() for item in self.new_obligations
            ],
            "resolved_obligation_ids": list(
                self.resolved_obligation_ids
            ),
            "authority_id": self.authority_id,
            "decision_ref": self.decision_ref,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> ArchitectDevelopmentDecision:
        payload = _mapping(value, "Architect development decision")
        _exact(
            payload,
            {
                "schema",
                "decision_id",
                "state_digest",
                "selected_advice_ids",
                "claim_resolutions",
                "component_updates",
                "dependencies",
                "new_obligations",
                "resolved_obligation_ids",
                "authority_id",
                "decision_ref",
                "rationale",
                "evidence_refs",
            },
            "Architect development decision",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("decision schema changed")
        nested = (
            payload["claim_resolutions"],
            payload["component_updates"],
            payload["dependencies"],
            payload["new_obligations"],
        )
        if any(not isinstance(item, list) for item in nested):
            raise TypeError("decision nested values must be lists")
        return cls(
            decision_id=payload["decision_id"],
            state_digest=payload["state_digest"],
            selected_advice_ids=_strings(
                payload["selected_advice_ids"],
                "selected_advice_ids",
            ),
            claim_resolutions=tuple(
                DevelopmentClaimResolution.from_dict(item)
                for item in payload["claim_resolutions"]
            ),
            component_updates=tuple(
                DevelopedComponent.from_dict(item)
                for item in payload["component_updates"]
            ),
            dependencies=tuple(
                DevelopmentDependency.from_dict(item)
                for item in payload["dependencies"]
            ),
            new_obligations=tuple(
                DevelopmentObligation.from_dict(item)
                for item in payload["new_obligations"]
            ),
            resolved_obligation_ids=_strings(
                payload["resolved_obligation_ids"],
                "resolved_obligation_ids",
            ),
            authority_id=payload["authority_id"],
            decision_ref=payload["decision_ref"],
            rationale=payload["rationale"],
            evidence_refs=_strings(
                payload["evidence_refs"],
                "decision evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopmentTransition:
    sequence: int
    predecessor_state_digest: str
    decision_id: str
    selected_advice_ids: tuple[str, ...]
    conflict_groups: tuple[tuple[str, ...], ...]
    changed_component_ids: tuple[str, ...]

    SCHEMA = "DevelopmentTransition@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise DevelopedDesignError("transition sequence must be positive")
        _sha(self.predecessor_state_digest, "predecessor_state_digest")
        require_identifier(self.decision_id, "decision_id")
        _ids(
            self.selected_advice_ids,
            "transition selected_advice_ids",
            allow_empty=True,
        )
        if not isinstance(self.conflict_groups, tuple):
            raise TypeError("conflict_groups must be a tuple")
        for group in self.conflict_groups:
            _refs(group, "conflict group")
            if len(group) < 2:
                raise DevelopedDesignError(
                    "conflict group requires at least two claims"
                )
        _ids(
            self.changed_component_ids,
            "changed_component_ids",
            allow_empty=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "sequence": self.sequence,
            "predecessor_state_digest": self.predecessor_state_digest,
            "decision_id": self.decision_id,
            "selected_advice_ids": list(self.selected_advice_ids),
            "conflict_groups": [
                list(item) for item in self.conflict_groups
            ],
            "changed_component_ids": list(self.changed_component_ids),
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopmentTransition:
        payload = _mapping(value, "development transition")
        _exact(
            payload,
            {
                "schema",
                "sequence",
                "predecessor_state_digest",
                "decision_id",
                "selected_advice_ids",
                "conflict_groups",
                "changed_component_ids",
            },
            "development transition",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError("transition schema changed")
        groups = payload["conflict_groups"]
        if not isinstance(groups, list) or any(
            not isinstance(item, list) for item in groups
        ):
            raise TypeError("conflict_groups must be nested lists")
        return cls(
            sequence=payload["sequence"],
            predecessor_state_digest=payload[
                "predecessor_state_digest"
            ],
            decision_id=payload["decision_id"],
            selected_advice_ids=_strings(
                payload["selected_advice_ids"],
                "transition selected_advice_ids",
            ),
            conflict_groups=tuple(
                _strings(item, "conflict group") for item in groups
            ),
            changed_component_ids=_strings(
                payload["changed_component_ids"],
                "changed_component_ids",
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopmentInvalidationReceipt:
    receipt_id: str
    predecessor_state_digest: str
    selected_schematic_ref: str
    changed_schematic_refs: tuple[str, ...]
    invalidated_component_ids: tuple[str, ...]
    preserved_component_ids: tuple[str, ...]
    affected_disciplines: tuple[DevelopmentDiscipline, ...]
    required_return_phase: DesignPhase
    evidence_refs: tuple[str, ...]

    SCHEMA = "DevelopmentInvalidationReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        _sha(self.predecessor_state_digest, "predecessor_state_digest")
        require_logical_ref(
            self.selected_schematic_ref,
            "selected_schematic_ref",
        )
        _refs(self.changed_schematic_refs, "changed_schematic_refs")
        _ids(
            self.invalidated_component_ids,
            "invalidated_component_ids",
            allow_empty=True,
        )
        _ids(
            self.preserved_component_ids,
            "preserved_component_ids",
            allow_empty=True,
        )
        if set(self.invalidated_component_ids) & set(
            self.preserved_component_ids
        ):
            raise DevelopedDesignError(
                "component cannot be preserved and invalidated"
            )
        if not isinstance(self.affected_disciplines, tuple) or any(
            not isinstance(item, DevelopmentDiscipline)
            for item in self.affected_disciplines
        ):
            raise TypeError("affected_disciplines contains invalid item")
        if self.required_return_phase not in {
            DesignPhase.SCHEMATIC_DESIGN,
            DesignPhase.DESIGN_DEVELOPMENT,
        }:
            raise DevelopedDesignError(
                "invalidation return phase is invalid"
            )
        _refs(self.evidence_refs, "invalidation evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "predecessor_state_digest": self.predecessor_state_digest,
            "selected_schematic_ref": self.selected_schematic_ref,
            "changed_schematic_refs": list(
                self.changed_schematic_refs
            ),
            "invalidated_component_ids": list(
                self.invalidated_component_ids
            ),
            "preserved_component_ids": list(
                self.preserved_component_ids
            ),
            "affected_disciplines": [
                item.value for item in self.affected_disciplines
            ],
            "required_return_phase": self.required_return_phase.value,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopmentInvalidationReceipt:
        payload = _mapping(value, "development invalidation receipt")
        _exact(
            payload,
            {
                "schema",
                "receipt_id",
                "predecessor_state_digest",
                "selected_schematic_ref",
                "changed_schematic_refs",
                "invalidated_component_ids",
                "preserved_component_ids",
                "affected_disciplines",
                "required_return_phase",
                "evidence_refs",
            },
            "development invalidation receipt",
        )
        if payload["schema"] != cls.SCHEMA:
            raise DevelopedDesignError(
                "invalidation receipt schema changed"
            )
        return cls(
            receipt_id=payload["receipt_id"],
            predecessor_state_digest=payload[
                "predecessor_state_digest"
            ],
            selected_schematic_ref=payload["selected_schematic_ref"],
            changed_schematic_refs=_strings(
                payload["changed_schematic_refs"],
                "changed_schematic_refs",
            ),
            invalidated_component_ids=_strings(
                payload["invalidated_component_ids"],
                "invalidated_component_ids",
            ),
            preserved_component_ids=_strings(
                payload["preserved_component_ids"],
                "preserved_component_ids",
            ),
            affected_disciplines=tuple(
                DevelopmentDiscipline(item)
                for item in _strings(
                    payload["affected_disciplines"],
                    "affected_disciplines",
                )
            ),
            required_return_phase=DesignPhase(
                payload["required_return_phase"]
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "invalidation evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopedDesignState:
    selected_schematic: SelectedSchematicInput
    active_phase: DesignPhase
    coordination_status: DevelopmentCoordinationStatus
    obligations: tuple[DevelopmentObligation, ...]
    components: tuple[DevelopedComponent, ...]
    dependencies: tuple[DevelopmentDependency, ...]
    advice: tuple[DetachedDevelopmentAdvice, ...]
    decisions: tuple[ArchitectDevelopmentDecision, ...]
    transitions: tuple[DevelopmentTransition, ...]
    assumption_refs: tuple[str, ...]
    latest_invalidation: DevelopmentInvalidationReceipt | None = None

    SCHEMA = "DevelopedDesignState@1"

    def __post_init__(self) -> None:
        if not isinstance(self.selected_schematic, SelectedSchematicInput):
            raise TypeError(
                "selected_schematic must be SelectedSchematicInput"
            )
        if self.active_phase not in {
            DesignPhase.SCHEMATIC_DESIGN,
            DesignPhase.DESIGN_DEVELOPMENT,
        }:
            raise DevelopedDesignError("active phase is invalid")
        if not isinstance(
            self.coordination_status,
            DevelopmentCoordinationStatus,
        ):
            raise TypeError(
                "coordination_status must be DevelopmentCoordinationStatus"
            )
        typed_collections = (
            (
                self.obligations,
                DevelopmentObligation,
                "obligations",
            ),
            (self.components, DevelopedComponent, "components"),
            (
                self.dependencies,
                DevelopmentDependency,
                "dependencies",
            ),
            (self.advice, DetachedDevelopmentAdvice, "advice"),
            (
                self.decisions,
                ArchitectDevelopmentDecision,
                "decisions",
            ),
            (
                self.transitions,
                DevelopmentTransition,
                "transitions",
            ),
        )
        for values, item_type, field in typed_collections:
            if not isinstance(values, tuple) or any(
                not isinstance(item, item_type) for item in values
            ):
                raise TypeError(f"{field} contains an invalid item")
        for values, field in (
            (
                tuple(item.obligation_id for item in self.obligations),
                "obligation ids",
            ),
            (
                tuple(item.component_id for item in self.components),
                "component ids",
            ),
            (
                tuple(item.dependency_id for item in self.dependencies),
                "dependency ids",
            ),
            (
                tuple(item.advice_id for item in self.advice),
                "advice ids",
            ),
            (
                tuple(item.decision_id for item in self.decisions),
                "decision ids",
            ),
        ):
            _ids(values, field, allow_empty=True)
        component_map = {
            item.component_id: item for item in self.components
        }
        schematic_component_ids = {
            item.component_id
            for item in self.selected_schematic.option.proposal.components
        }
        unknown_component_ids = set(component_map) - schematic_component_ids
        if unknown_component_ids:
            raise DevelopedDesignError(
                "developed component does not exist in the selected "
                f"semantic component tree: {sorted(unknown_component_ids)}"
            )
        schematic_parent_ids = {
            item.parent_component_id
            for item in self.selected_schematic.option.proposal.components
            if item.parent_component_id is not None
        }
        schematic_leaf_ids = schematic_component_ids - schematic_parent_ids
        missing_component_ids = schematic_leaf_ids - set(component_map)
        if (
            self.coordination_status
            is DevelopmentCoordinationStatus.COORDINATED
            and missing_component_ids
        ):
            raise DevelopedDesignError(
                "coordinated development omitted selected semantic leaves: "
                f"{sorted(missing_component_ids)}"
            )
        selected_refs = {
            self.selected_schematic.ref,
            self.selected_schematic.option.ref,
        }
        if any(
            not selected_refs & set(item.schematic_dependency_refs)
            for item in self.components
        ):
            raise DevelopedDesignError(
                "component is not bound to the current selected schematic"
            )
        for dependency in self.dependencies:
            component = component_map.get(dependency.target_component_id)
            if (
                component is None
                or component.discipline is not dependency.target_discipline
            ):
                raise DevelopedDesignError(
                    "dependency target component is missing or mismatched"
                )
        if tuple(item.sequence for item in self.transitions) != tuple(
            range(1, len(self.transitions) + 1)
        ):
            raise DevelopedDesignError(
                "development transition sequence is not contiguous"
            )
        blocking = tuple(
            item
            for item in self.obligations
            if item.priority is DevelopmentObligationPriority.BLOCKING
            and item.status is not DevelopmentObligationStatus.RESOLVED
        )
        if (
            self.coordination_status
            is DevelopmentCoordinationStatus.COORDINATED
            and (
                blocking
                or self.active_phase is not DesignPhase.DESIGN_DEVELOPMENT
            )
        ):
            raise DevelopedDesignError(
                "coordinated state retains blocking work"
            )
        if (
            self.coordination_status
            is DevelopmentCoordinationStatus.INVALIDATED
            and self.latest_invalidation is None
        ):
            raise DevelopedDesignError(
                "invalidated state requires a receipt"
            )
        if self.latest_invalidation is not None and not isinstance(
            self.latest_invalidation,
            DevelopmentInvalidationReceipt,
        ):
            raise TypeError(
                "latest_invalidation must be a receipt or None"
            )
        _refs(
            self.assumption_refs,
            "assumption_refs",
            allow_empty=True,
        )

    @property
    def project_id(self) -> str:
        return self.selected_schematic.project_id

    @property
    def run_id(self) -> str:
        return self.selected_schematic.run_id

    @property
    def base(self) -> ProjectVersionRef:
        return self.selected_schematic.base

    @property
    def state_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "selected_schematic": self.selected_schematic.to_dict(),
            "active_phase": self.active_phase.value,
            "coordination_status": self.coordination_status.value,
            "obligations": [item.to_dict() for item in self.obligations],
            "components": [item.to_dict() for item in self.components],
            "dependencies": [
                item.to_dict() for item in self.dependencies
            ],
            "advice": [item.to_dict() for item in self.advice],
            "decisions": [item.to_dict() for item in self.decisions],
            "transitions": [
                item.to_dict() for item in self.transitions
            ],
            "assumption_refs": list(self.assumption_refs),
            "latest_invalidation": (
                self.latest_invalidation.to_dict()
                if self.latest_invalidation is not None
                else None
            ),
            "candidate_created": False,
            "mcp_execution_authority": False,
            "hard_usability_verdict": None,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> DevelopedDesignState:
        payload = _mapping(value, "developed design state")
        _exact(
            payload,
            {
                "schema",
                "selected_schematic",
                "active_phase",
                "coordination_status",
                "obligations",
                "components",
                "dependencies",
                "advice",
                "decisions",
                "transitions",
                "assumption_refs",
                "latest_invalidation",
                "candidate_created",
                "mcp_execution_authority",
                "hard_usability_verdict",
                "canonical_write_authority",
            },
            "developed design state",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["candidate_created"] is not False
            or payload["hard_usability_verdict"] is not None
        ):
            raise DevelopedDesignError(
                "developed design acquired forbidden authority"
            )
        collection_fields = (
            "obligations",
            "components",
            "dependencies",
            "advice",
            "decisions",
            "transitions",
        )
        if any(
            not isinstance(payload[field], list)
            for field in collection_fields
        ):
            raise TypeError(
                "developed design collections must be lists"
            )
        invalidation = payload["latest_invalidation"]
        return cls(
            selected_schematic=SelectedSchematicInput.from_dict(
                payload["selected_schematic"]
            ),
            active_phase=DesignPhase(payload["active_phase"]),
            coordination_status=DevelopmentCoordinationStatus(
                payload["coordination_status"]
            ),
            obligations=tuple(
                DevelopmentObligation.from_dict(item)
                for item in payload["obligations"]
            ),
            components=tuple(
                DevelopedComponent.from_dict(item)
                for item in payload["components"]
            ),
            dependencies=tuple(
                DevelopmentDependency.from_dict(item)
                for item in payload["dependencies"]
            ),
            advice=tuple(
                DetachedDevelopmentAdvice.from_dict(item)
                for item in payload["advice"]
            ),
            decisions=tuple(
                ArchitectDevelopmentDecision.from_dict(item)
                for item in payload["decisions"]
            ),
            transitions=tuple(
                DevelopmentTransition.from_dict(item)
                for item in payload["transitions"]
            ),
            assumption_refs=_strings(
                payload["assumption_refs"],
                "assumption_refs",
            ),
            latest_invalidation=(
                DevelopmentInvalidationReceipt.from_dict(invalidation)
                if invalidation is not None
                else None
            ),
        )
