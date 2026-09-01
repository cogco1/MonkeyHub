"""Evidence-bound program hypotheses with no spatial-design authority."""

from __future__ import annotations

import hashlib
import json
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


_MAX_ITEMS = 512
_MAX_TEXT = 1_000


class ProgramNodeKind(StrEnum):
    """Kinds of program entities; labels and membership remain project data."""

    USER_GROUP = "user_group"
    ACTIVITY = "activity"
    FUNCTION = "function"


class ProgramMetricKind(StrEnum):
    """Metrics that must not be collapsed into one generic area number."""

    CAPACITY = "capacity"
    NET_AREA = "net_area"
    GROSS_ALLOWANCE = "gross_allowance"
    FOOTPRINT = "footprint"
    TOTAL_FLOOR_AREA = "total_floor_area"


class ProgramMetricApplicability(StrEnum):
    """Whether one program metric belongs to the current project type."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ProgramMetricApplicabilityDecision:
    """Exact-base decision that carries no metric value or gate authority."""

    decision_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    metric: ProgramMetricKind
    applicability: ProgramMetricApplicability
    rationale: str
    authority_id: str
    source_refs: tuple[str, ...]

    SCHEMA = "ProgramMetricApplicabilityDecision@1"

    def __post_init__(self) -> None:
        require_local_id(self.decision_id, "decision_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError(
                "metric applicability decision and base belong to different projects"
            )
        self.base.require_digest()
        if not isinstance(self.metric, ProgramMetricKind):
            raise TypeError("metric must be ProgramMetricKind")
        if not isinstance(self.applicability, ProgramMetricApplicability):
            raise TypeError(
                "applicability must be ProgramMetricApplicability"
            )
        _text(self.rationale, "rationale")
        _text(self.authority_id, "authority_id")
        _refs(self.source_refs, "source_refs")

    @property
    def decision_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "decision_id": self.decision_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.base.project_id,
                "version": self.base.version,
                "state_sha256": self.base.require_digest(),
            },
            "metric": self.metric.value,
            "applicability": self.applicability.value,
            "rationale": self.rationale,
            "authority_id": self.authority_id,
            "source_refs": list(self.source_refs),
            "metric_value_authority": False,
            "generation_authority": False,
            "hard_gate_waiver_authority": False,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> ProgramMetricApplicabilityDecision:
        payload = _mapping(value, "program metric applicability decision")
        _exact(
            payload,
            {
                "schema",
                "decision_id",
                "project_id",
                "run_id",
                "base",
                "metric",
                "applicability",
                "rationale",
                "authority_id",
                "source_refs",
                "metric_value_authority",
                "generation_authority",
                "hard_gate_waiver_authority",
            },
            "program metric applicability decision",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ValueError(
                "unsupported program metric applicability decision schema"
            )
        if any(
            payload[field] is not False
            for field in (
                "metric_value_authority",
                "generation_authority",
                "hard_gate_waiver_authority",
            )
        ):
            raise ValueError(
                "metric applicability cannot claim value, generation, or hard-gate authority"
            )
        base = _mapping(payload["base"], "base")
        _exact(
            base,
            {"project_id", "version", "state_sha256"},
            "base",
        )
        return cls(
            decision_id=payload["decision_id"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=base["project_id"],
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
            metric=_enum(
                ProgramMetricKind,
                payload["metric"],
                "metric",
            ),
            applicability=_enum(
                ProgramMetricApplicability,
                payload["applicability"],
                "applicability",
            ),
            rationale=payload["rationale"],
            authority_id=payload["authority_id"],
            source_refs=_strings(payload["source_refs"], "source_refs"),
        )


class ProgramRelationshipKind(StrEnum):
    """Non-geometric relationship hypotheses available to later design."""

    ADJACENCY = "adjacency"
    SEPARATION = "separation"
    PUBLIC_PRIVATE = "public_private"
    NOISE = "noise"
    CIRCULATION = "circulation"


class ProgramRelationshipStrength(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    AVOID = "avoid"


@dataclass(frozen=True, slots=True)
class ProgramAssumption:
    assumption_id: str
    statement: str
    source_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.assumption_id, "assumption_id")
        _text(self.statement, "statement")
        _refs(self.source_refs, "source_refs")
        _text(self.compiler_id, "compiler_id")
        _sha256(self.base_state_sha256, "base_state_sha256")

    @property
    def ref(self) -> str:
        return f"program-assumption:{self.assumption_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "assumption_id": self.assumption_id,
            "statement": self.statement,
            "source_refs": list(self.source_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProgramAssumption:
        payload = _mapping(value, "program assumption")
        _exact(
            payload,
            {
                "assumption_id",
                "statement",
                "source_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "program assumption",
        )
        return cls(
            assumption_id=payload["assumption_id"],
            statement=payload["statement"],
            source_refs=_strings(payload["source_refs"], "source_refs"),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ProgramNode:
    node_id: str
    kind: ProgramNodeKind
    label: str
    epistemic_status: FactEpistemicStatus
    source_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.node_id, "node_id")
        if not isinstance(self.kind, ProgramNodeKind):
            raise TypeError("kind must be ProgramNodeKind")
        _text(self.label, "label")
        _program_status(self.epistemic_status)
        _refs(self.source_refs, "source_refs")
        _refs(self.assumption_refs, "assumption_refs", allow_empty=True)
        if (
            self.epistemic_status
            in {FactEpistemicStatus.DERIVED, FactEpistemicStatus.HYPOTHESIS}
            and not self.assumption_refs
        ):
            raise ValueError(
                "derived and hypothetical nodes require assumptions"
            )
        _text(self.compiler_id, "compiler_id")
        _sha256(self.base_state_sha256, "base_state_sha256")

    @property
    def ref(self) -> str:
        return f"program-node:{self.node_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "label": self.label,
            "epistemic_status": self.epistemic_status.value,
            "source_refs": list(self.source_refs),
            "assumption_refs": list(self.assumption_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProgramNode:
        payload = _mapping(value, "program node")
        _exact(
            payload,
            {
                "node_id",
                "kind",
                "label",
                "epistemic_status",
                "source_refs",
                "assumption_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "program node",
        )
        return cls(
            node_id=payload["node_id"],
            kind=_enum(ProgramNodeKind, payload["kind"], "kind"),
            label=payload["label"],
            epistemic_status=_enum(
                FactEpistemicStatus,
                payload["epistemic_status"],
                "epistemic_status",
            ),
            source_refs=_strings(payload["source_refs"], "source_refs"),
            assumption_refs=_strings(
                payload["assumption_refs"],
                "assumption_refs",
            ),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ProgramRange:
    """One bounded capacity or area hypothesis, never a hidden point default."""

    range_id: str
    metric: ProgramMetricKind
    applies_to_ref: str
    minimum: float
    maximum: float
    unit: str
    scenario_id: str | None
    epistemic_status: FactEpistemicStatus
    source_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.range_id, "range_id")
        if not isinstance(self.metric, ProgramMetricKind):
            raise TypeError("metric must be ProgramMetricKind")
        require_logical_ref(self.applies_to_ref, "applies_to_ref")
        _number(self.minimum, "minimum")
        _number(self.maximum, "maximum")
        if self.minimum < 0 or self.maximum <= self.minimum:
            raise ValueError(
                "program metrics must be non-negative, non-point ranges"
            )
        _text(self.unit, "unit")
        if self.scenario_id is not None:
            require_local_id(self.scenario_id, "scenario_id")
        _program_status(self.epistemic_status)
        _refs(self.source_refs, "source_refs")
        _refs(self.assumption_refs, "assumption_refs", allow_empty=True)
        if (
            self.epistemic_status
            in {FactEpistemicStatus.DERIVED, FactEpistemicStatus.HYPOTHESIS}
            and not self.assumption_refs
        ):
            raise ValueError(
                "derived and hypothetical ranges require assumptions"
            )
        _text(self.compiler_id, "compiler_id")
        _sha256(self.base_state_sha256, "base_state_sha256")

    @property
    def ref(self) -> str:
        return f"program-range:{self.range_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "range_id": self.range_id,
            "metric": self.metric.value,
            "applies_to_ref": self.applies_to_ref,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unit": self.unit,
            "scenario_id": self.scenario_id,
            "epistemic_status": self.epistemic_status.value,
            "source_refs": list(self.source_refs),
            "assumption_refs": list(self.assumption_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProgramRange:
        payload = _mapping(value, "program range")
        _exact(
            payload,
            {
                "range_id",
                "metric",
                "applies_to_ref",
                "minimum",
                "maximum",
                "unit",
                "scenario_id",
                "epistemic_status",
                "source_refs",
                "assumption_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "program range",
        )
        return cls(
            range_id=payload["range_id"],
            metric=_enum(ProgramMetricKind, payload["metric"], "metric"),
            applies_to_ref=payload["applies_to_ref"],
            minimum=payload["minimum"],
            maximum=payload["maximum"],
            unit=payload["unit"],
            scenario_id=payload["scenario_id"],
            epistemic_status=_enum(
                FactEpistemicStatus,
                payload["epistemic_status"],
                "epistemic_status",
            ),
            source_refs=_strings(payload["source_refs"], "source_refs"),
            assumption_refs=_strings(
                payload["assumption_refs"],
                "assumption_refs",
            ),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ProgramRelationship:
    relationship_id: str
    kind: ProgramRelationshipKind
    source_node_ref: str
    target_node_ref: str
    strength: ProgramRelationshipStrength
    directed: bool
    epistemic_status: FactEpistemicStatus
    source_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.relationship_id, "relationship_id")
        if not isinstance(self.kind, ProgramRelationshipKind):
            raise TypeError("kind must be ProgramRelationshipKind")
        require_logical_ref(self.source_node_ref, "source_node_ref")
        require_logical_ref(self.target_node_ref, "target_node_ref")
        if self.source_node_ref == self.target_node_ref:
            raise ValueError("program relationship cannot be a self edge")
        if not isinstance(self.strength, ProgramRelationshipStrength):
            raise TypeError(
                "strength must be ProgramRelationshipStrength"
            )
        if not isinstance(self.directed, bool):
            raise TypeError("directed must be boolean")
        _program_status(self.epistemic_status)
        _refs(self.source_refs, "source_refs")
        _refs(self.assumption_refs, "assumption_refs", allow_empty=True)
        if (
            self.epistemic_status
            in {FactEpistemicStatus.DERIVED, FactEpistemicStatus.HYPOTHESIS}
            and not self.assumption_refs
        ):
            raise ValueError(
                "derived and hypothetical relationships require assumptions"
            )
        _text(self.compiler_id, "compiler_id")
        _sha256(self.base_state_sha256, "base_state_sha256")

    @property
    def ref(self) -> str:
        return f"program-relationship:{self.relationship_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "relationship_id": self.relationship_id,
            "kind": self.kind.value,
            "source_node_ref": self.source_node_ref,
            "target_node_ref": self.target_node_ref,
            "strength": self.strength.value,
            "directed": self.directed,
            "epistemic_status": self.epistemic_status.value,
            "source_refs": list(self.source_refs),
            "assumption_refs": list(self.assumption_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProgramRelationship:
        payload = _mapping(value, "program relationship")
        _exact(
            payload,
            {
                "relationship_id",
                "kind",
                "source_node_ref",
                "target_node_ref",
                "strength",
                "directed",
                "epistemic_status",
                "source_refs",
                "assumption_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "program relationship",
        )
        return cls(
            relationship_id=payload["relationship_id"],
            kind=_enum(
                ProgramRelationshipKind,
                payload["kind"],
                "kind",
            ),
            source_node_ref=payload["source_node_ref"],
            target_node_ref=payload["target_node_ref"],
            strength=_enum(
                ProgramRelationshipStrength,
                payload["strength"],
                "strength",
            ),
            directed=payload["directed"],
            epistemic_status=_enum(
                FactEpistemicStatus,
                payload["epistemic_status"],
                "epistemic_status",
            ),
            source_refs=_strings(payload["source_refs"], "source_refs"),
            assumption_refs=_strings(
                payload["assumption_refs"],
                "assumption_refs",
            ),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ProgramScenario:
    """A named bundle of bounded hypotheses, not a selected design branch."""

    scenario_id: str
    label: str
    range_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.scenario_id, "scenario_id")
        _text(self.label, "label")
        _ids(self.range_ids, "range_ids")
        _refs(self.source_refs, "source_refs")
        _refs(self.assumption_refs, "assumption_refs")
        _text(self.compiler_id, "compiler_id")
        _sha256(self.base_state_sha256, "base_state_sha256")

    @property
    def ref(self) -> str:
        return f"program-scenario:{self.scenario_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "label": self.label,
            "range_ids": list(self.range_ids),
            "source_refs": list(self.source_refs),
            "assumption_refs": list(self.assumption_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProgramScenario:
        payload = _mapping(value, "program scenario")
        _exact(
            payload,
            {
                "scenario_id",
                "label",
                "range_ids",
                "source_refs",
                "assumption_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "program scenario",
        )
        return cls(
            scenario_id=payload["scenario_id"],
            label=payload["label"],
            range_ids=_strings(payload["range_ids"], "range_ids"),
            source_refs=_strings(payload["source_refs"], "source_refs"),
            assumption_refs=_strings(
                payload["assumption_refs"],
                "assumption_refs",
            ),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class DesignProgram:
    """DesignProgram@1 is sufficient program state, not a form answer."""

    project_id: str
    run_id: str
    base: ProjectVersionRef
    brief_digest: str
    compiler_id: str
    compiler_version: str
    assumptions: tuple[ProgramAssumption, ...]
    nodes: tuple[ProgramNode, ...]
    ranges: tuple[ProgramRange, ...]
    relationships: tuple[ProgramRelationship, ...]
    scenarios: tuple[ProgramScenario, ...]
    obligations: tuple[DesignObligation, ...]
    evidence_refs: tuple[str, ...]
    external_constraint_refs: tuple[str, ...]

    SCHEMA = "DesignProgram@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("program and base belong to different projects")
        base_digest = self.base.require_digest()
        _sha256(self.brief_digest, "brief_digest")
        _text(self.compiler_id, "compiler_id")
        _text(self.compiler_version, "compiler_version")
        _typed(
            self.assumptions,
            ProgramAssumption,
            "assumptions",
        )
        _typed(self.nodes, ProgramNode, "nodes")
        _typed(self.ranges, ProgramRange, "ranges")
        _typed(
            self.relationships,
            ProgramRelationship,
            "relationships",
        )
        _typed(self.scenarios, ProgramScenario, "scenarios")
        _typed(self.obligations, DesignObligation, "obligations")
        _refs(self.evidence_refs, "evidence_refs")
        _refs(
            self.external_constraint_refs,
            "external_constraint_refs",
            allow_empty=True,
        )
        node_refs = {item.ref for item in self.nodes}
        range_id_values = tuple(item.range_id for item in self.ranges)
        range_ids = set(range_id_values)
        scenario_id_values = tuple(
            item.scenario_id for item in self.scenarios
        )
        scenario_ids = set(scenario_id_values)
        assumption_refs = {
            item.ref for item in self.assumptions
        }
        _unique(
            tuple(item.assumption_id for item in self.assumptions),
            "assumption ids",
        )
        _unique(tuple(item.node_id for item in self.nodes), "node ids")
        _unique(range_id_values, "range ids")
        _unique(
            tuple(item.relationship_id for item in self.relationships),
            "relationship ids",
        )
        _unique(scenario_id_values, "scenario ids")
        _unique(
            tuple(item.obligation_id for item in self.obligations),
            "obligation ids",
        )
        evidence = set(self.evidence_refs)
        for item in (
            *self.assumptions,
            *self.nodes,
            *self.ranges,
            *self.relationships,
            *self.scenarios,
        ):
            if (
                item.compiler_id != self.compiler_id
                or item.base_state_sha256 != base_digest
            ):
                raise ValueError(
                    "program item provenance disagrees with compilation"
                )
            if not set(item.source_refs) <= evidence:
                raise ValueError(
                    "program item source is absent from program evidence"
                )
        for item in (*self.nodes, *self.ranges, *self.relationships):
            if not set(item.assumption_refs) <= assumption_refs:
                raise ValueError(
                    "program item cites an unknown assumption"
                )
        for item in self.ranges:
            if (
                item.applies_to_ref not in node_refs
                and item.applies_to_ref
                not in {f"program-scenario:{value}" for value in scenario_ids}
            ):
                raise ValueError("program range cites an unknown subject")
            if (
                item.scenario_id is not None
                and item.scenario_id not in scenario_ids
            ):
                raise ValueError("program range cites an unknown scenario")
        for item in self.relationships:
            if (
                item.source_node_ref not in node_refs
                or item.target_node_ref not in node_refs
            ):
                raise ValueError(
                    "program relationship cites an unknown node"
                )
        for scenario in self.scenarios:
            if not set(scenario.range_ids) <= range_ids:
                raise ValueError("program scenario cites an unknown range")
            if not set(scenario.source_refs) <= evidence:
                raise ValueError(
                    "program scenario source is absent from evidence"
                )
            if not set(scenario.assumption_refs) <= assumption_refs:
                raise ValueError(
                    "program scenario cites an unknown assumption"
                )
            for range_id in scenario.range_ids:
                item = next(
                    value for value in self.ranges
                    if value.range_id == range_id
                )
                if item.scenario_id != scenario.scenario_id:
                    raise ValueError(
                        "scenario and range assignments disagree"
                    )

    @property
    def program_digest(self) -> str:
        return _digest(self.to_dict())

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
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "assumptions": [
                item.to_dict() for item in self.assumptions
            ],
            "nodes": [item.to_dict() for item in self.nodes],
            "ranges": [item.to_dict() for item in self.ranges],
            "relationships": [
                item.to_dict() for item in self.relationships
            ],
            "scenarios": [item.to_dict() for item in self.scenarios],
            "obligations": [item.to_dict() for item in self.obligations],
            "evidence_refs": list(self.evidence_refs),
            "external_constraint_refs": list(
                self.external_constraint_refs
            ),
            "generation_authority": False,
            "footprint_selected": False,
            "topology_selected": False,
            "geometry_selected": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignProgram:
        payload = _mapping(value, "design program")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "brief_digest",
                "compiler_id",
                "compiler_version",
                "assumptions",
                "nodes",
                "ranges",
                "relationships",
                "scenarios",
                "obligations",
                "evidence_refs",
                "external_constraint_refs",
                "generation_authority",
                "footprint_selected",
                "topology_selected",
                "geometry_selected",
            },
            "design program",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("unsupported design program schema")
        if any(
            payload[field] is not False
            for field in (
                "generation_authority",
                "footprint_selected",
                "topology_selected",
                "geometry_selected",
            )
        ):
            raise ValueError("design program cannot claim spatial authority")
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
            compiler_id=payload["compiler_id"],
            compiler_version=payload["compiler_version"],
            assumptions=tuple(
                ProgramAssumption.from_dict(item)
                for item in payload["assumptions"]
            ),
            nodes=tuple(
                ProgramNode.from_dict(item) for item in payload["nodes"]
            ),
            ranges=tuple(
                ProgramRange.from_dict(item) for item in payload["ranges"]
            ),
            relationships=tuple(
                ProgramRelationship.from_dict(item)
                for item in payload["relationships"]
            ),
            scenarios=tuple(
                ProgramScenario.from_dict(item)
                for item in payload["scenarios"]
            ),
            obligations=tuple(
                DesignObligation.from_dict(item)
                for item in payload["obligations"]
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "evidence_refs",
            ),
            external_constraint_refs=_strings(
                payload["external_constraint_refs"],
                "external_constraint_refs",
            ),
        )


def _program_status(value: object) -> None:
    if not isinstance(value, FactEpistemicStatus):
        raise TypeError(
            "epistemic_status must be FactEpistemicStatus"
        )
    if value not in {
        FactEpistemicStatus.DECLARED,
        FactEpistemicStatus.OBSERVED,
        FactEpistemicStatus.DERIVED,
        FactEpistemicStatus.HYPOTHESIS,
    }:
        raise ValueError(
            "program items must be declared, observed, derived, or hypotheses"
        )


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


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _tuple(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def _typed(
    value: object,
    item_type: type,
    field: str,
) -> tuple[object, ...]:
    items = _tuple(value, field)
    if any(not isinstance(item, item_type) for item in items):
        raise TypeError(f"{field} contains the wrong item type")
    return items


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


def _ids(value: object, field: str) -> None:
    items = _tuple(value, field)
    if not items:
        raise ValueError(f"{field} cannot be empty")
    for item in items:
        require_local_id(item, field)
    _unique(tuple(items), field)


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _exact(
    payload: Mapping[str, object],
    keys: set[str],
    field: str,
) -> None:
    if set(payload) != keys:
        raise ValueError(f"{field} schema drifted")


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a list of strings")
    return tuple(value)


def _enum(enum_type: type[StrEnum], value: object, field: str) -> StrEnum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not a supported value") from exc


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
