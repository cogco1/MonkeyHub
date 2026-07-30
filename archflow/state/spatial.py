"""Building-scoped schematic option contracts with no framework defaults."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.operational_state import (
    require_local_id,
    require_logical_ref,
)
from archflow.state.site_context import SiteBounds


FootprintCell = tuple[int, int]
_MAX_ITEMS = 4_096
_MAX_TEXT = 2_000
_HEX = frozenset("0123456789abcdef")


class SpatialProposalError(ValueError):
    """A schematic proposal is malformed or has acquired forbidden authority."""


class ConstraintResponseStatus(StrEnum):
    SATISFIED = "satisfied"
    RISK = "risk"
    NOT_APPLICABLE = "not_applicable"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpatialProposalError(f"{field} must be non-empty text")
    if len(value) > _MAX_TEXT:
        raise SpatialProposalError(f"{field} exceeds bounded text")
    return value


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise SpatialProposalError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _tuple(value: object, item_type: type, field: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise SpatialProposalError(f"{field} exceeds bounded item count")
    if any(not isinstance(item, item_type) for item in value):
        raise TypeError(f"{field} contains an invalid item")
    return value


def _refs(
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
        require_logical_ref(value, field)
    _unique(values, field)
    return values


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


def _strings_from_json(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise SpatialProposalError(f"{field} contains duplicates")


def _number(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise SpatialProposalError(f"{field} must be finite")
    return float(value)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _exact(
    value: Mapping[str, Any],
    fields: set[str],
    label: str,
) -> None:
    if set(value) != fields:
        raise SpatialProposalError(f"{label} schema drifted")


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
        area = _number(
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
    connections: tuple[SpatialConnection, ...]
    constraint_responses: tuple[SpatialConstraintResponse, ...]
    typology_hypothesis: str
    palette_refs: tuple[str, ...]
    rationale: str
    responds_to_refs: tuple[str, ...]
    expert_advice_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "SpatialOptionProposal@1"

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
        _tuple(self.connections, SpatialConnection, "connections")
        _tuple(
            self.constraint_responses,
            SpatialConstraintResponse,
            "constraint_responses",
        )
        if not self.levels or not self.volumes or not self.zones:
            raise SpatialProposalError(
                "schematic option requires levels volumes and zones"
            )
        for values, field in (
            (tuple(item.level_id for item in self.levels), "level ids"),
            (tuple(item.volume_id for item in self.volumes), "volume ids"),
            (tuple(item.zone_id for item in self.zones), "zone ids"),
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
        return _digest(self.to_dict())

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
        connections = payload["connections"]
        responses = payload["constraint_responses"]
        for item, field in (
            (cells, "footprint_cells"),
            (levels, "levels"),
            (volumes, "volumes"),
            (zones, "zones"),
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
class SchematicOption:
    proposal: SpatialOptionProposal
    footprint_area: float
    topology_signature: str

    SCHEMA = "SchematicOption@1"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, SpatialOptionProposal):
            raise TypeError("proposal must be SpatialOptionProposal")
        area = _number(self.footprint_area, "footprint_area")
        if area <= 0:
            raise SpatialProposalError("footprint_area must be positive")
        object.__setattr__(self, "footprint_area", area)
        _sha256(self.topology_signature, "topology_signature")

    @property
    def option_id(self) -> str:
        return self.proposal.option_id

    @property
    def option_digest(self) -> str:
        return _digest(self.to_dict())

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
        _sha256(
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
            _sha256(value, field)
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
        _unique(option_ids, "option ids")
        _unique(option_digests, "option digests")
        _unique(signatures, "spatial signatures")
        if option_ids != tuple(sorted(option_ids)):
            raise SpatialProposalError(
                "option set must use deterministic id presentation order"
            )

    @property
    def option_set_digest(self) -> str:
        return _digest(self.to_dict())

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
            or payload["canonical_write_authority"] is not False
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
