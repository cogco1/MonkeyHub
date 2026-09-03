"""Building-scoped schematic option contracts with no framework defaults."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.operational_state import (
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_digest, canonical_json, require_sha256
from archflow.contracts.fields import (
    mapping as _mapping,
    string_tuple as _strings_from_json,
    typed_tuple as _tuple,
    unique as _unique,
)
from archflow.contracts.fields import (
    exact_mapping as _exact,
    number,
    refs as _refs,
    text as _text,
)


FootprintCell = tuple[int, int]
_MAX_ITEMS = 4_096
_MAX_TEXT = 2_000
_HEX = frozenset("0123456789abcdef")


# ---------------------------------------------------------------- site bounds
Coordinate = tuple[int, int, int]

def _coordinate(value: object, field: str) -> Coordinate:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in value
        )
    ):
        raise ValueError(f"{field} must be an integer xyz tuple")
    return value

def _coordinate_from_json(value: object, field: str) -> Coordinate:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a JSON coordinate list")
    return _coordinate(tuple(value), field)


@dataclass(frozen=True, slots=True, order=True)
class SiteBounds:
    minimum: Coordinate
    maximum: Coordinate

    def __post_init__(self) -> None:
        _coordinate(self.minimum, "minimum")
        _coordinate(self.maximum, "maximum")
        if any(
            high < low
            for low, high in zip(
                self.minimum,
                self.maximum,
                strict=True,
            )
        ):
            raise ValueError("site bounds maximum precedes minimum")

    @property
    def volume(self) -> int:
        return (
            (self.maximum[0] - self.minimum[0] + 1)
            * (self.maximum[1] - self.minimum[1] + 1)
            * (self.maximum[2] - self.minimum[2] + 1)
        )

    def contains(self, coordinate: Coordinate) -> bool:
        _coordinate(coordinate, "coordinate")
        return all(
            low <= value <= high
            for value, low, high in zip(
                coordinate,
                self.minimum,
                self.maximum,
                strict=True,
            )
        )

    def contains_bounds(self, other: SiteBounds) -> bool:
        if not isinstance(other, SiteBounds):
            raise TypeError("other must be SiteBounds")
        return self.contains(other.minimum) and self.contains(other.maximum)

    def to_dict(self) -> dict[str, object]:
        return {
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
        }

    @classmethod
    def from_dict(cls, value: object) -> SiteBounds:
        payload = _mapping(value, "site bounds")
        _exact(payload, {"minimum", "maximum"}, "site bounds")
        return cls(
            minimum=_coordinate_from_json(payload["minimum"], "minimum"),
            maximum=_coordinate_from_json(payload["maximum"], "maximum"),
        )


class SpatialProposalError(ValueError):
    """A schematic proposal is malformed or has acquired forbidden authority."""


class ConstraintResponseStatus(StrEnum):
    SATISFIED = "satisfied"
    RISK = "risk"
    NOT_APPLICABLE = "not_applicable"


class ComponentMaturity(StrEnum):
    """Current semantic-geometry resolution of one stable component."""

    MASSING = "massing"
    SCHEMATIC = "schematic"
    DEVELOPED = "developed"
    DETAILED = "detailed"


def _ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS:
        raise SpatialProposalError(f"{field} exceeds bounded item count")
    if not values and not allow_empty:
        raise SpatialProposalError(f"{field} must be non-empty")
    for value in values:
        require_local_id(value, field)
    _unique(values, field)
    return values


def _branch_to_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.require_digest(),
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _branch_from_dict(value: object) -> BranchRef:
    payload = _mapping(value, "branch")
    _exact(
        payload,
        {"project_id", "run_id", "base", "branch_id", "epoch"},
        "branch",
    )
    base = _mapping(payload["base"], "branch base")
    _exact(base, {"version", "state_sha256"}, "branch base")
    project_id = payload["project_id"]
    return BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=project_id,
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
        ),
        branch_id=payload["branch_id"],
        epoch=payload["epoch"],
    )


def _cell(value: object, field: str) -> FootprintCell:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in value
        )
    ):
        raise TypeError(f"{field} must be an integer x/z tuple")
    return value


def _cell_from_json(value: object, field: str) -> FootprintCell:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a coordinate list")
    return _cell(tuple(value), field)


@dataclass(frozen=True, slots=True)
class SpatialGridBasis:
    horizontal_area_per_cell: float
    area_unit: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        area = number(
            self.horizontal_area_per_cell,
            "horizontal_area_per_cell",
        )
        if area <= 0:
            raise SpatialProposalError(
                "horizontal_area_per_cell must be positive"
            )
        object.__setattr__(self, "horizontal_area_per_cell", area)
        _text(self.area_unit, "area_unit")
        _refs(self.source_refs, "grid basis source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "horizontal_area_per_cell": self.horizontal_area_per_cell,
            "area_unit": self.area_unit,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SpatialGridBasis:
        payload = _mapping(value, "spatial grid basis")
        _exact(
            payload,
            {
                "horizontal_area_per_cell",
                "area_unit",
                "source_refs",
            },
            "spatial grid basis",
        )
        return cls(
            horizontal_area_per_cell=payload[
                "horizontal_area_per_cell"
            ],
            area_unit=payload["area_unit"],
            source_refs=_strings_from_json(
                payload["source_refs"],
                "grid basis source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class SpatialLevel:
    level_id: str
    base_y: int
    height: int
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.level_id, "level_id")
        for value, field in (
            (self.base_y, "base_y"),
            (self.height, "height"),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field} must be an integer")
        if self.height <= 0:
            raise SpatialProposalError("level height must be positive")
        _refs(self.source_refs, "level source_refs")

    @property
    def top_y(self) -> int:
        return self.base_y + self.height - 1

    def to_dict(self) -> dict[str, object]:
        return {
            "level_id": self.level_id,
            "base_y": self.base_y,
            "height": self.height,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SpatialLevel:
        payload = _mapping(value, "spatial level")
        _exact(
            payload,
            {"level_id", "base_y", "height", "source_refs"},
            "spatial level",
        )
        return cls(
            level_id=payload["level_id"],
            base_y=payload["base_y"],
            height=payload["height"],
            source_refs=_strings_from_json(
                payload["source_refs"],
                "level source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class MassingVolume:
    volume_id: str
    bounds: SiteBounds
    level_ids: tuple[str, ...]
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.volume_id, "volume_id")
        if not isinstance(self.bounds, SiteBounds):
            raise TypeError("bounds must be SiteBounds")
        _ids(self.level_ids, "volume level_ids")
        _refs(self.source_refs, "volume source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "volume_id": self.volume_id,
            "bounds": self.bounds.to_dict(),
            "level_ids": list(self.level_ids),
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> MassingVolume:
        payload = _mapping(value, "massing volume")
        _exact(
            payload,
            {"volume_id", "bounds", "level_ids", "source_refs"},
            "massing volume",
        )
        return cls(
            volume_id=payload["volume_id"],
            bounds=SiteBounds.from_dict(payload["bounds"]),
            level_ids=_strings_from_json(
                payload["level_ids"],
                "volume level_ids",
            ),
            source_refs=_strings_from_json(
                payload["source_refs"],
                "volume source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class SpatialZone:
    zone_id: str
    program_node_refs: tuple[str, ...]
    level_ids: tuple[str, ...]
    volume_ids: tuple[str, ...]
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.zone_id, "zone_id")
        _refs(self.program_node_refs, "zone program_node_refs")
        _ids(self.level_ids, "zone level_ids")
        _ids(self.volume_ids, "zone volume_ids")
        _refs(self.source_refs, "zone source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "zone_id": self.zone_id,
            "program_node_refs": list(self.program_node_refs),
            "level_ids": list(self.level_ids),
            "volume_ids": list(self.volume_ids),
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SpatialZone:
        payload = _mapping(value, "spatial zone")
        _exact(
            payload,
            {
                "zone_id",
                "program_node_refs",
                "level_ids",
                "volume_ids",
                "source_refs",
            },
            "spatial zone",
        )
        return cls(
            zone_id=payload["zone_id"],
            program_node_refs=_strings_from_json(
                payload["program_node_refs"],
                "zone program_node_refs",
            ),
            level_ids=_strings_from_json(
                payload["level_ids"],
                "zone level_ids",
            ),
            volume_ids=_strings_from_json(
                payload["volume_ids"],
                "zone volume_ids",
            ),
            source_refs=_strings_from_json(
                payload["source_refs"],
                "zone source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class SpatialConnection:
    connection_id: str
    source_zone_id: str
    target_zone_id: str
    relationship_refs: tuple[str, ...]
    directed: bool
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.connection_id, "connection_id")
        require_local_id(self.source_zone_id, "source_zone_id")
        require_local_id(self.target_zone_id, "target_zone_id")
        if self.source_zone_id == self.target_zone_id:
            raise SpatialProposalError(
                "spatial connection cannot be a self edge"
            )
        _refs(self.relationship_refs, "connection relationship_refs")
        if not isinstance(self.directed, bool):
            raise TypeError("directed must be boolean")
        _refs(self.source_refs, "connection source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "source_zone_id": self.source_zone_id,
            "target_zone_id": self.target_zone_id,
            "relationship_refs": list(self.relationship_refs),
            "directed": self.directed,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SpatialConnection:
        payload = _mapping(value, "spatial connection")
        _exact(
            payload,
            {
                "connection_id",
                "source_zone_id",
                "target_zone_id",
                "relationship_refs",
                "directed",
                "source_refs",
            },
            "spatial connection",
        )
        return cls(
            connection_id=payload["connection_id"],
            source_zone_id=payload["source_zone_id"],
            target_zone_id=payload["target_zone_id"],
            relationship_refs=_strings_from_json(
                payload["relationship_refs"],
                "connection relationship_refs",
            ),
            directed=payload["directed"],
            source_refs=_strings_from_json(
                payload["source_refs"],
                "connection source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class SpatialConstraintResponse:
    response_id: str
    constraint_ref: str
    status: ConstraintResponseStatus
    rationale: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.response_id, "response_id")
        require_logical_ref(self.constraint_ref, "constraint_ref")
        if not isinstance(self.status, ConstraintResponseStatus):
            raise TypeError("status must be ConstraintResponseStatus")
        _text(self.rationale, "constraint response rationale")
        _refs(self.source_refs, "constraint response source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "response_id": self.response_id,
            "constraint_ref": self.constraint_ref,
            "status": self.status.value,
            "rationale": self.rationale,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SpatialConstraintResponse:
        payload = _mapping(value, "spatial constraint response")
        _exact(
            payload,
            {
                "response_id",
                "constraint_ref",
                "status",
                "rationale",
                "source_refs",
            },
            "spatial constraint response",
        )
        return cls(
            response_id=payload["response_id"],
            constraint_ref=payload["constraint_ref"],
            status=ConstraintResponseStatus(payload["status"]),
            rationale=payload["rationale"],
            source_refs=_strings_from_json(
                payload["source_refs"],
                "constraint response source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class DesignComponent:
    """One stable semantic component and its current coarse geometry."""

    component_id: str
    parent_component_id: str | None
    semantic_kind: str
    intent: str
    maturity: ComponentMaturity
    revision: int
    volume_ids: tuple[str, ...]
    unresolved_child_roles: tuple[str, ...]
    source_refs: tuple[str, ...]

    SCHEMA = "DesignComponent@1"

    def __post_init__(self) -> None:
        require_local_id(self.component_id, "component_id")
        if self.parent_component_id is not None:
            require_local_id(
                self.parent_component_id,
                "parent_component_id",
            )
            if self.parent_component_id == self.component_id:
                raise SpatialProposalError("component cannot parent itself")
        require_local_id(self.semantic_kind, "semantic_kind")
        _text(self.intent, "component intent")
        if not isinstance(self.maturity, ComponentMaturity):
            raise TypeError("maturity must be ComponentMaturity")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 0
        ):
            raise SpatialProposalError(
                "component revision must be non-negative"
            )
        _ids(self.volume_ids, "component volume_ids", allow_empty=True)
        _ids(
            self.unresolved_child_roles,
            "unresolved_child_roles",
            allow_empty=True,
        )
        _refs(self.source_refs, "component source_refs")

    @property
    def identity_ref(self) -> str:
        return f"design-component:{self.component_id}"

    @property
    def component_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return (
            f"{self.identity_ref}:r{self.revision}:"
            f"{self.component_digest}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_id": self.component_id,
            "parent_component_id": self.parent_component_id,
            "semantic_kind": self.semantic_kind,
            "intent": self.intent,
            "maturity": self.maturity.value,
            "revision": self.revision,
            "volume_ids": list(self.volume_ids),
            "unresolved_child_roles": list(
                self.unresolved_child_roles
            ),
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignComponent:
        payload = _mapping(value, "design component")
        _exact(
            payload,
            {
                "schema",
                "component_id",
                "parent_component_id",
                "semantic_kind",
                "intent",
                "maturity",
                "revision",
                "volume_ids",
                "unresolved_child_roles",
                "source_refs",
            },
            "design component",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SpatialProposalError("design component schema changed")
        return cls(
            component_id=payload["component_id"],
            parent_component_id=payload["parent_component_id"],
            semantic_kind=payload["semantic_kind"],
            intent=payload["intent"],
            maturity=ComponentMaturity(payload["maturity"]),
            revision=payload["revision"],
            volume_ids=_strings_from_json(
                payload["volume_ids"],
                "component volume_ids",
            ),
            unresolved_child_roles=_strings_from_json(
                payload["unresolved_child_roles"],
                "unresolved_child_roles",
            ),
            source_refs=_strings_from_json(
                payload["source_refs"],
                "component source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class SpatialOptionProposal:
    """One Architect-authored schematic answer, never a framework default."""

    option_id: str
    label: str
    program_scenario_ref: str | None
    footprint_range_ref: str | None
    grid_basis: SpatialGridBasis
    footprint_cells: tuple[FootprintCell, ...]
    levels: tuple[SpatialLevel, ...]
    volumes: tuple[MassingVolume, ...]
    zones: tuple[SpatialZone, ...]
    components: tuple[DesignComponent, ...]
    connections: tuple[SpatialConnection, ...]
    constraint_responses: tuple[SpatialConstraintResponse, ...]
    typology_hypothesis: str
    palette_refs: tuple[str, ...]
    rationale: str
    responds_to_refs: tuple[str, ...]
    expert_advice_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "SpatialOptionProposal@2"

    def __post_init__(self) -> None:
        require_local_id(self.option_id, "option_id")
        _text(self.label, "label")
        for value, field in (
            (self.program_scenario_ref, "program_scenario_ref"),
            (self.footprint_range_ref, "footprint_range_ref"),
        ):
            if value is not None:
                require_logical_ref(value, field)
        if not isinstance(self.grid_basis, SpatialGridBasis):
            raise TypeError("grid_basis must be SpatialGridBasis")
        if not isinstance(self.footprint_cells, tuple):
            raise TypeError("footprint_cells must be a tuple")
        if not self.footprint_cells or len(self.footprint_cells) > _MAX_ITEMS:
            raise SpatialProposalError(
                "footprint_cells must contain 1..4096 cells"
            )
        for index, cell in enumerate(self.footprint_cells):
            _cell(cell, f"footprint_cells[{index}]")
        if len(self.footprint_cells) != len(set(self.footprint_cells)):
            raise SpatialProposalError("footprint_cells contains duplicates")
        _tuple(self.levels, SpatialLevel, "levels")
        _tuple(self.volumes, MassingVolume, "volumes")
        _tuple(self.zones, SpatialZone, "zones")
        _tuple(self.components, DesignComponent, "components")
        _tuple(self.connections, SpatialConnection, "connections")
        _tuple(
            self.constraint_responses,
            SpatialConstraintResponse,
            "constraint_responses",
        )
        if (
            not self.levels
            or not self.volumes
            or not self.zones
            or not self.components
        ):
            raise SpatialProposalError(
                "schematic option requires levels volumes zones and components"
            )
        for values, field in (
            (tuple(item.level_id for item in self.levels), "level ids"),
            (tuple(item.volume_id for item in self.volumes), "volume ids"),
            (tuple(item.zone_id for item in self.zones), "zone ids"),
            (
                tuple(item.component_id for item in self.components),
                "component ids",
            ),
            (
                tuple(item.connection_id for item in self.connections),
                "connection ids",
            ),
            (
                tuple(
                    item.response_id
                    for item in self.constraint_responses
                ),
                "constraint response ids",
            ),
            (
                tuple(
                    item.constraint_ref
                    for item in self.constraint_responses
                ),
                "constraint response refs",
            ),
        ):
            _unique(values, field)
        component_by_id = {
            item.component_id: item for item in self.components
        }
        roots = tuple(
            item
            for item in self.components
            if item.parent_component_id is None
        )
        if len(roots) != 1:
            raise SpatialProposalError(
                "component tree requires exactly one root"
            )
        for component in self.components:
            parent_id = component.parent_component_id
            if parent_id is not None and parent_id not in component_by_id:
                raise SpatialProposalError(
                    "component parent is absent from the same option"
                )
            seen = {component.component_id}
            cursor = component
            while cursor.parent_component_id is not None:
                parent_id = cursor.parent_component_id
                if parent_id in seen:
                    raise SpatialProposalError(
                        "component ancestry contains a cycle"
                    )
                seen.add(parent_id)
                cursor = component_by_id[parent_id]
        known_volume_ids = {item.volume_id for item in self.volumes}
        volume_owners: dict[str, str] = {}
        for component in self.components:
            for volume_id in component.volume_ids:
                if volume_id not in known_volume_ids:
                    raise SpatialProposalError(
                        "component names an unknown massing volume"
                    )
                if volume_id in volume_owners:
                    raise SpatialProposalError(
                        "massing volume has multiple semantic owners"
                    )
                volume_owners[volume_id] = component.component_id
        if set(volume_owners) != known_volume_ids:
            raise SpatialProposalError(
                "every massing volume requires one semantic owner"
            )
        _text(self.typology_hypothesis, "typology_hypothesis")
        _refs(self.palette_refs, "palette_refs", allow_empty=True)
        _text(self.rationale, "rationale")
        _refs(self.responds_to_refs, "responds_to_refs")
        _refs(
            self.expert_advice_refs,
            "expert_advice_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "evidence_refs")
        known_evidence = set(self.evidence_refs)
        nested_sources = (
            self.grid_basis.source_refs,
            *(item.source_refs for item in self.levels),
            *(item.source_refs for item in self.volumes),
            *(item.source_refs for item in self.zones),
            *(item.source_refs for item in self.components),
            *(item.source_refs for item in self.connections),
            *(
                item.source_refs
                for item in self.constraint_responses
            ),
        )
        if any(
            not set(source_refs) <= known_evidence
            for source_refs in nested_sources
        ):
            raise SpatialProposalError(
                "proposal component source is absent from evidence_refs"
            )

    @property
    def proposal_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"spatial-proposal:{self.option_id}:{self.proposal_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "option_id": self.option_id,
            "label": self.label,
            "program_scenario_ref": self.program_scenario_ref,
            "footprint_range_ref": self.footprint_range_ref,
            "grid_basis": self.grid_basis.to_dict(),
            "footprint_cells": [
                list(item) for item in self.footprint_cells
            ],
            "levels": [item.to_dict() for item in self.levels],
            "volumes": [item.to_dict() for item in self.volumes],
            "zones": [item.to_dict() for item in self.zones],
            "components": [
                item.to_dict() for item in self.components
            ],
            "connections": [
                item.to_dict() for item in self.connections
            ],
            "constraint_responses": [
                item.to_dict() for item in self.constraint_responses
            ],
            "typology_hypothesis": self.typology_hypothesis,
            "palette_refs": list(self.palette_refs),
            "rationale": self.rationale,
            "responds_to_refs": list(self.responds_to_refs),
            "expert_advice_refs": list(self.expert_advice_refs),
            "evidence_refs": list(self.evidence_refs),
            "proposal_only": True,
            "selected": False,
            "hard_usability_verdict": None,
            "design_development_complete": False,
            "execution_ready": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> SpatialOptionProposal:
        payload = _mapping(value, "spatial option proposal")
        _exact(
            payload,
            {
                "schema",
                "option_id",
                "label",
                "program_scenario_ref",
                "footprint_range_ref",
                "grid_basis",
                "footprint_cells",
                "levels",
                "volumes",
                "zones",
                "components",
                "connections",
                "constraint_responses",
                "typology_hypothesis",
                "palette_refs",
                "rationale",
                "responds_to_refs",
                "expert_advice_refs",
                "evidence_refs",
                "proposal_only",
                "selected",
                "hard_usability_verdict",
                "design_development_complete",
                "execution_ready",
            },
            "spatial option proposal",
        )
        if payload["schema"] != cls.SCHEMA or (
            payload["proposal_only"] is not True
            or payload["selected"] is not False
            or payload["hard_usability_verdict"] is not None
            or payload["design_development_complete"] is not False
            or payload["execution_ready"] is not False
        ):
            raise SpatialProposalError(
                "spatial proposal acquired forbidden authority"
            )
        cells = payload["footprint_cells"]
        levels = payload["levels"]
        volumes = payload["volumes"]
        zones = payload["zones"]
        components = payload["components"]
        connections = payload["connections"]
        responses = payload["constraint_responses"]
        for item, field in (
            (cells, "footprint_cells"),
            (levels, "levels"),
            (volumes, "volumes"),
            (zones, "zones"),
            (components, "components"),
            (connections, "connections"),
            (responses, "constraint_responses"),
        ):
            if not isinstance(item, list):
                raise TypeError(f"{field} must be a list")
        return cls(
            option_id=payload["option_id"],
            label=payload["label"],
            program_scenario_ref=payload["program_scenario_ref"],
            footprint_range_ref=payload["footprint_range_ref"],
            grid_basis=SpatialGridBasis.from_dict(payload["grid_basis"]),
            footprint_cells=tuple(
                _cell_from_json(item, "footprint cell")
                for item in cells
            ),
            levels=tuple(SpatialLevel.from_dict(item) for item in levels),
            volumes=tuple(
                MassingVolume.from_dict(item) for item in volumes
            ),
            zones=tuple(SpatialZone.from_dict(item) for item in zones),
            components=tuple(
                DesignComponent.from_dict(item) for item in components
            ),
            connections=tuple(
                SpatialConnection.from_dict(item)
                for item in connections
            ),
            constraint_responses=tuple(
                SpatialConstraintResponse.from_dict(item)
                for item in responses
            ),
            typology_hypothesis=payload["typology_hypothesis"],
            palette_refs=_strings_from_json(
                payload["palette_refs"],
                "palette_refs",
            ),
            rationale=payload["rationale"],
            responds_to_refs=_strings_from_json(
                payload["responds_to_refs"],
                "responds_to_refs",
            ),
            expert_advice_refs=_strings_from_json(
                payload["expert_advice_refs"],
                "expert_advice_refs",
            ),
            evidence_refs=_strings_from_json(
                payload["evidence_refs"],
                "evidence_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class ComponentTransitionReceipt:
    """Deterministic proof of stable-ID progressive component refinement."""

    predecessor_proposal_digest: str
    current_proposal_digest: str
    changed_component_ids: tuple[str, ...]
    invalidated_component_ids: tuple[str, ...]
    preserved_component_ids: tuple[str, ...]
    retired_component_ids: tuple[str, ...]

    SCHEMA = "ComponentTransitionReceipt@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.predecessor_proposal_digest, "predecessor_proposal_digest"),
            (self.current_proposal_digest, "current_proposal_digest"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value.lower())
            ):
                raise SpatialProposalError(f"{field} must be a SHA-256 digest")
        for values, field in (
            (self.changed_component_ids, "changed_component_ids"),
            (self.invalidated_component_ids, "invalidated_component_ids"),
            (self.preserved_component_ids, "preserved_component_ids"),
            (self.retired_component_ids, "retired_component_ids"),
        ):
            _ids(values, field, allow_empty=True)
            if values != tuple(sorted(values)):
                raise SpatialProposalError(f"{field} must be sorted")
        if set(self.invalidated_component_ids) & set(
            self.preserved_component_ids
        ):
            raise SpatialProposalError(
                "component cannot be both invalidated and preserved"
            )

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_proposal_digest": self.predecessor_proposal_digest,
            "current_proposal_digest": self.current_proposal_digest,
            "changed_component_ids": list(self.changed_component_ids),
            "invalidated_component_ids": list(
                self.invalidated_component_ids
            ),
            "preserved_component_ids": list(self.preserved_component_ids),
            "retired_component_ids": list(self.retired_component_ids),
            "canonical_write_authority": False,
        }


def compile_component_transition(
    predecessor: SpatialOptionProposal,
    current: SpatialOptionProposal,
    *,
    retired_component_ids: tuple[str, ...] = (),
) -> ComponentTransitionReceipt:
    """Validate one local refinement without creating a second design tree."""

    if not isinstance(predecessor, SpatialOptionProposal) or not isinstance(
        current,
        SpatialOptionProposal,
    ):
        raise TypeError("predecessor and current must be SpatialOptionProposal")
    if predecessor.option_id != current.option_id:
        raise SpatialProposalError(
            "component refinement cannot cross schematic option identity"
        )
    _ids(retired_component_ids, "retired_component_ids", allow_empty=True)
    if retired_component_ids != tuple(sorted(retired_component_ids)):
        raise SpatialProposalError("retired_component_ids must be sorted")

    before = {item.component_id: item for item in predecessor.components}
    after = {item.component_id: item for item in current.components}
    removed = set(before) - set(after)
    retired = set(retired_component_ids)
    if removed != retired:
        raise SpatialProposalError(
            "removed components require an exact explicit retirement set"
        )

    maturity_rank = {
        ComponentMaturity.MASSING: 0,
        ComponentMaturity.SCHEMATIC: 1,
        ComponentMaturity.DEVELOPED: 2,
        ComponentMaturity.DETAILED: 3,
    }
    changed: set[str] = set(retired)
    for component_id in sorted(set(before) & set(after)):
        old = before[component_id]
        new = after[component_id]
        if (
            old.parent_component_id != new.parent_component_id
            or old.semantic_kind != new.semantic_kind
        ):
            raise SpatialProposalError(
                f"stable component identity changed meaning: {component_id}"
            )
        if maturity_rank[new.maturity] < maturity_rank[old.maturity]:
            raise SpatialProposalError(
                f"component maturity regressed: {component_id}"
            )
        if old == new:
            continue
        if new.revision != old.revision + 1:
            raise SpatialProposalError(
                f"changed component requires revision +1: {component_id}"
            )
        changed.add(component_id)
    for component_id in sorted(set(after) - set(before)):
        if after[component_id].revision != 0:
            raise SpatialProposalError(
                f"new component must start at revision 0: {component_id}"
            )
        changed.add(component_id)

    invalidated = set(changed)

    def include_descendants(
        component_id: str,
        components: dict[str, DesignComponent],
    ) -> None:
        direct = {
            item.component_id
            for item in components.values()
            if item.parent_component_id == component_id
        }
        additions = direct - invalidated
        invalidated.update(additions)
        for child_id in sorted(additions):
            include_descendants(child_id, components)

    for component_id in sorted(changed):
        include_descendants(component_id, after)
        include_descendants(component_id, before)

    surviving_ids = set(after)
    return ComponentTransitionReceipt(
        predecessor_proposal_digest=predecessor.proposal_digest,
        current_proposal_digest=current.proposal_digest,
        changed_component_ids=tuple(sorted(changed)),
        invalidated_component_ids=tuple(sorted(invalidated)),
        preserved_component_ids=tuple(
            sorted(surviving_ids - invalidated)
        ),
        retired_component_ids=tuple(sorted(retired)),
    )


@dataclass(frozen=True, slots=True)
class SchematicOption:
    proposal: SpatialOptionProposal
    footprint_area: float
    topology_signature: str

    SCHEMA = "SchematicOption@1"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, SpatialOptionProposal):
            raise TypeError("proposal must be SpatialOptionProposal")
        area = number(self.footprint_area, "footprint_area")
        if area <= 0:
            raise SpatialProposalError("footprint_area must be positive")
        object.__setattr__(self, "footprint_area", area)
        require_sha256(self.topology_signature, "topology_signature")

    @property
    def option_id(self) -> str:
        return self.proposal.option_id

    @property
    def option_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"schematic-option:{self.option_id}:{self.option_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "proposal": self.proposal.to_dict(),
            "proposal_digest": self.proposal.proposal_digest,
            "footprint_area": self.footprint_area,
            "topology_signature": self.topology_signature,
            "compiled": True,
            "selected": False,
            "rank": None,
        }

    @classmethod
    def from_dict(cls, value: object) -> SchematicOption:
        payload = _mapping(value, "schematic option")
        _exact(
            payload,
            {
                "schema",
                "proposal",
                "proposal_digest",
                "footprint_area",
                "topology_signature",
                "compiled",
                "selected",
                "rank",
            },
            "schematic option",
        )
        proposal = SpatialOptionProposal.from_dict(payload["proposal"])
        if (
            payload["schema"] != cls.SCHEMA
            or payload["proposal_digest"] != proposal.proposal_digest
            or payload["compiled"] is not True
            or payload["selected"] is not False
            or payload["rank"] is not None
        ):
            raise SpatialProposalError(
                "schematic option authority or digest drifted"
            )
        return cls(
            proposal=proposal,
            footprint_area=payload["footprint_area"],
            topology_signature=payload["topology_signature"],
        )


@dataclass(frozen=True, slots=True)
class SchematicOptionSet:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    branch: BranchRef
    operational_state_digest: str
    input_phase: DesignPhase
    output_phase: DesignPhase
    program_digest: str
    site_context_digest: str
    build_policy_digest: str
    phase_gate_receipt_ref: str
    phase_gate_receipt_digest: str
    compiler_id: str
    compiler_version: str
    options: tuple[SchematicOption, ...]

    SCHEMA = "SchematicOptionSet@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        if (
            self.base.project_id != self.project_id
            or self.branch.run.project_id != self.project_id
            or self.branch.run.run_id != self.run_id
            or self.branch.run.base != self.base
        ):
            raise SpatialProposalError(
                "option set project run branch and base disagree"
            )
        require_sha256(
            self.operational_state_digest,
            "operational_state_digest",
        )
        if self.input_phase is not DesignPhase.SITE_RESOURCE_COORDINATION:
            raise SpatialProposalError(
                "schematic options require site/resource input phase"
            )
        if self.output_phase is not DesignPhase.SCHEMATIC_DESIGN:
            raise SpatialProposalError(
                "schematic options must produce schematic_design"
            )
        for value, field in (
            (self.program_digest, "program_digest"),
            (self.site_context_digest, "site_context_digest"),
            (self.build_policy_digest, "build_policy_digest"),
            (self.phase_gate_receipt_digest, "phase_gate_receipt_digest"),
        ):
            require_sha256(value, field)
        require_logical_ref(
            self.phase_gate_receipt_ref,
            "phase_gate_receipt_ref",
        )
        _text(self.compiler_id, "compiler_id")
        _text(self.compiler_version, "compiler_version")
        _tuple(self.options, SchematicOption, "options")
        if len(self.options) < 2:
            raise SpatialProposalError(
                "schematic option set requires at least two alternatives"
            )
        option_ids = tuple(item.option_id for item in self.options)
        option_digests = tuple(
            item.option_digest for item in self.options
        )
        signatures = tuple(
            item.topology_signature for item in self.options
        )
        try:
            _unique(option_ids, "option ids")
        except ValueError as exc:  # the typed error production callers catch
            raise SpatialProposalError(str(exc)) from exc
        _unique(option_digests, "option digests")
        _unique(signatures, "spatial signatures")
        if option_ids != tuple(sorted(option_ids)):
            raise SpatialProposalError(
                "option set must use deterministic id presentation order"
            )

    @property
    def option_set_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"schematic-options:{self.option_set_digest}"

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
            "branch": _branch_to_dict(self.branch),
            "operational_state_digest": self.operational_state_digest,
            "input_phase": self.input_phase.value,
            "output_phase": self.output_phase.value,
            "program_digest": self.program_digest,
            "site_context_digest": self.site_context_digest,
            "build_policy_digest": self.build_policy_digest,
            "phase_gate_receipt_ref": self.phase_gate_receipt_ref,
            "phase_gate_receipt_digest": self.phase_gate_receipt_digest,
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "options": [item.to_dict() for item in self.options],
            "selected_option_id": None,
            "ranked": False,
            "canonical_write_authority": False,
            "hard_usability_verdict": None,
            "design_development_complete": False,
            "execution_ready": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> SchematicOptionSet:
        payload = _mapping(value, "schematic option set")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "branch",
                "operational_state_digest",
                "input_phase",
                "output_phase",
                "program_digest",
                "site_context_digest",
                "build_policy_digest",
                "phase_gate_receipt_ref",
                "phase_gate_receipt_digest",
                "compiler_id",
                "compiler_version",
                "options",
                "selected_option_id",
                "ranked",
                "canonical_write_authority",
                "hard_usability_verdict",
                "design_development_complete",
                "execution_ready",
            },
            "schematic option set",
        )
        if payload["schema"] != cls.SCHEMA or (
            payload["selected_option_id"] is not None
            or payload["ranked"] is not False
            or payload["hard_usability_verdict"] is not None
            or payload["design_development_complete"] is not False
            or payload["execution_ready"] is not False
        ):
            raise SpatialProposalError(
                "schematic option set acquired forbidden authority"
            )
        base = _mapping(payload["base"], "base")
        _exact(
            base,
            {"project_id", "version", "state_sha256"},
            "base",
        )
        options = payload["options"]
        if not isinstance(options, list):
            raise TypeError("options must be a list")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=base["project_id"],
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
            branch=_branch_from_dict(payload["branch"]),
            operational_state_digest=payload[
                "operational_state_digest"
            ],
            input_phase=DesignPhase(payload["input_phase"]),
            output_phase=DesignPhase(payload["output_phase"]),
            program_digest=payload["program_digest"],
            site_context_digest=payload["site_context_digest"],
            build_policy_digest=payload["build_policy_digest"],
            phase_gate_receipt_ref=payload["phase_gate_receipt_ref"],
            phase_gate_receipt_digest=payload[
                "phase_gate_receipt_digest"
            ],
            compiler_id=payload["compiler_id"],
            compiler_version=payload["compiler_version"],
            options=tuple(
                SchematicOption.from_dict(item) for item in options
            ),
        )
