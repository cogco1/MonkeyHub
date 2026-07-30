"""Traceable candidate-program projection from coordinated design state.

This module copies project-authored design values into a review boundary.  It
does not infer a building program, select missing values, or write project
records.  The legacy ``BuildingProgram@1`` view is compiled separately in the
validation package.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.developed_design import (
    DevelopedDesignState,
    DevelopmentCoordinationStatus,
    DevelopmentDiscipline,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.operational_state import require_logical_ref


_HEX = frozenset("0123456789abcdef")
_MAX_ITEMS = 8_192


class CandidateProgramError(ValueError):
    """The developed state is incomplete, stale, or lacks derivation evidence."""


class CandidateValueFacet(StrEnum):
    FUNCTION = "function"
    AREA = "area"
    TOPOLOGY = "topology"
    DIMENSION = "dimension"
    MATERIAL = "material"
    COORDINATE = "coordinate"
    DEVELOPED_DETAIL = "developed_detail"
    VALIDATION_INPUT = "validation_input"


_REQUIRED_BUILD_FACETS = frozenset(
    {
        CandidateValueFacet.FUNCTION,
        CandidateValueFacet.AREA,
        CandidateValueFacet.TOPOLOGY,
        CandidateValueFacet.DIMENSION,
        CandidateValueFacet.MATERIAL,
        CandidateValueFacet.COORDINATE,
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise CandidateProgramError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise CandidateProgramError(f"{field} has an invalid item count")
    for value in values:
        require_logical_ref(value, field)
    if len(values) != len(set(values)):
        raise CandidateProgramError(f"{field} contains duplicates")
    return values


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise CandidateProgramError(f"{label} schema drifted")


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "candidate base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "candidate base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


@dataclass(frozen=True, slots=True)
class CandidateProgramValue:
    """One project-authored value plus the path that justifies it."""

    value_id: str
    facet: CandidateValueFacet
    value_json: str
    source_refs: tuple[str, ...]
    derivation_refs: tuple[str, ...]

    SCHEMA = "CandidateProgramValue@1"

    def __post_init__(self) -> None:
        require_identifier(self.value_id, "candidate value_id")
        if not isinstance(self.facet, CandidateValueFacet):
            raise TypeError("facet must be CandidateValueFacet")
        if not isinstance(self.value_json, str):
            raise TypeError("value_json must be text")
        try:
            decoded = json.loads(self.value_json)
        except json.JSONDecodeError as exc:
            raise CandidateProgramError("value_json must contain JSON") from exc
        if _canonical_json(decoded) != self.value_json:
            raise CandidateProgramError("value_json must be canonical JSON")
        _refs(self.source_refs, "candidate value source_refs")
        _refs(self.derivation_refs, "candidate value derivation_refs")

    @classmethod
    def create(
        cls,
        *,
        value_id: str,
        facet: CandidateValueFacet,
        value: object,
        source_refs: tuple[str, ...],
        derivation_refs: tuple[str, ...],
    ) -> CandidateProgramValue:
        return cls(
            value_id=value_id,
            facet=facet,
            value_json=_canonical_json(value),
            source_refs=source_refs,
            derivation_refs=derivation_refs,
        )

    @property
    def decoded_value(self) -> object:
        return json.loads(self.value_json)

    @property
    def ref(self) -> str:
        return f"candidate-value:{self.value_id}:{_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "value_id": self.value_id,
            "facet": self.facet.value,
            "value_json": self.value_json,
            "source_refs": list(self.source_refs),
            "derivation_refs": list(self.derivation_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateProgramValue:
        payload = _mapping(value, "candidate program value")
        _exact(
            payload,
            {
                "schema",
                "value_id",
                "facet",
                "value_json",
                "source_refs",
                "derivation_refs",
            },
            "candidate program value",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CandidateProgramError("candidate program value schema changed")
        return cls(
            value_id=payload["value_id"],
            facet=CandidateValueFacet(payload["facet"]),
            value_json=payload["value_json"],
            source_refs=_strings(payload["source_refs"], "source_refs"),
            derivation_refs=_strings(
                payload["derivation_refs"],
                "derivation_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class CandidateProgramProjection:
    """Exact-base review projection; never an upstream design compiler."""

    project_id: str
    run_id: str
    base: ProjectVersionRef
    developed_state_digest: str
    portfolio_id: str
    portfolio_digest: str
    selected_branch_id: str
    selected_revision_id: str
    selected_revision_digest: str
    selected_option_ref: str
    selection_transition_id: str
    selection_decision_ref: str
    values: tuple[CandidateProgramValue, ...]

    SCHEMA = "CandidateProgramProjection@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise CandidateProgramError("candidate projection and base disagree")
        _sha(self.developed_state_digest, "developed_state_digest")
        require_identifier(self.portfolio_id, "portfolio_id")
        _sha(self.portfolio_digest, "portfolio_digest")
        require_identifier(self.selected_branch_id, "selected_branch_id")
        require_identifier(
            self.selected_revision_id,
            "selected_revision_id",
        )
        _sha(self.selected_revision_digest, "selected_revision_digest")
        require_logical_ref(self.selected_option_ref, "selected_option_ref")
        require_identifier(
            self.selection_transition_id,
            "selection_transition_id",
        )
        require_logical_ref(
            self.selection_decision_ref,
            "selection_decision_ref",
        )
        if not isinstance(self.values, tuple) or not self.values:
            raise CandidateProgramError("candidate values must be non-empty")
        if any(not isinstance(item, CandidateProgramValue) for item in self.values):
            raise TypeError("values contains an invalid item")
        ids = tuple(item.value_id for item in self.values)
        if len(ids) != len(set(ids)) or ids != tuple(sorted(ids)):
            raise CandidateProgramError(
                "candidate values require unique deterministic ids"
            )
        missing = _REQUIRED_BUILD_FACETS - {
            item.facet for item in self.values
        }
        if missing:
            raise CandidateProgramError(
                "candidate lacks traced build facets: "
                + ", ".join(sorted(item.value for item in missing))
            )

    @property
    def projection_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"candidate-program:{self.projection_digest}"

    def value(self, value_id: str) -> CandidateProgramValue:
        require_identifier(value_id, "value_id")
        for item in self.values:
            if item.value_id == value_id:
                return item
        raise CandidateProgramError(f"unknown candidate value: {value_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "developed_state_digest": self.developed_state_digest,
            "portfolio_id": self.portfolio_id,
            "portfolio_digest": self.portfolio_digest,
            "selected_branch_id": self.selected_branch_id,
            "selected_revision_id": self.selected_revision_id,
            "selected_revision_digest": self.selected_revision_digest,
            "selected_option_ref": self.selected_option_ref,
            "selection_transition_id": self.selection_transition_id,
            "selection_decision_ref": self.selection_decision_ref,
            "values": [item.to_dict() for item in self.values],
            "generation_authority": False,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateProgramProjection:
        payload = _mapping(value, "candidate program projection")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "developed_state_digest",
                "portfolio_id",
                "portfolio_digest",
                "selected_branch_id",
                "selected_revision_id",
                "selected_revision_digest",
                "selected_option_ref",
                "selection_transition_id",
                "selection_decision_ref",
                "values",
                "generation_authority",
                "hard_gate_authority",
                "canonical_write_authority",
            },
            "candidate program projection",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["generation_authority"] is not False
            or payload["hard_gate_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise CandidateProgramError(
                "candidate program projection acquired forbidden authority"
            )
        values = payload["values"]
        if not isinstance(values, list):
            raise TypeError("candidate values must be a list")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            developed_state_digest=payload["developed_state_digest"],
            portfolio_id=payload["portfolio_id"],
            portfolio_digest=payload["portfolio_digest"],
            selected_branch_id=payload["selected_branch_id"],
            selected_revision_id=payload["selected_revision_id"],
            selected_revision_digest=payload["selected_revision_digest"],
            selected_option_ref=payload["selected_option_ref"],
            selection_transition_id=payload["selection_transition_id"],
            selection_decision_ref=payload["selection_decision_ref"],
            values=tuple(CandidateProgramValue.from_dict(item) for item in values),
        )


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def _component_facet(discipline: DevelopmentDiscipline) -> CandidateValueFacet:
    return {
        DevelopmentDiscipline.MATERIALS: CandidateValueFacet.MATERIAL,
        DevelopmentDiscipline.CIRCULATION: CandidateValueFacet.TOPOLOGY,
        DevelopmentDiscipline.USE: CandidateValueFacet.FUNCTION,
    }.get(discipline, CandidateValueFacet.DEVELOPED_DETAIL)


def compile_candidate_program(
    state: DevelopedDesignState,
    *,
    additional_values: tuple[CandidateProgramValue, ...] = (),
) -> CandidateProgramProjection:
    """Copy one coordinated P040 state into a fully traced candidate view."""

    if not isinstance(state, DevelopedDesignState):
        raise TypeError("state must be DevelopedDesignState")
    if (
        state.active_phase is not DesignPhase.DESIGN_DEVELOPMENT
        or state.coordination_status
        is not DevelopmentCoordinationStatus.COORDINATED
        or state.latest_invalidation is not None
    ):
        raise CandidateProgramError(
            "candidate assembly requires a coordinated non-invalidated "
            "design-development state"
        )
    if not isinstance(additional_values, tuple):
        raise TypeError("additional_values must be a tuple")
    if any(
        not isinstance(item, CandidateProgramValue)
        for item in additional_values
    ):
        raise TypeError("additional_values contains an invalid item")

    selected = state.selected_schematic
    option = selected.option
    proposal = option.proposal
    selected_refs = (selected.ref, option.ref)
    for item in additional_values:
        if not set(item.derivation_refs) & set(selected_refs):
            raise CandidateProgramError(
                f"additional value {item.value_id} is not derived from the "
                "current selected schematic"
            )
    values: list[CandidateProgramValue] = []

    for zone in proposal.zones:
        values.append(
            CandidateProgramValue.create(
                value_id=f"function-zone-{zone.zone_id}",
                facet=CandidateValueFacet.FUNCTION,
                value={
                    "program_node_refs": list(zone.program_node_refs),
                    "level_ids": list(zone.level_ids),
                    "volume_ids": list(zone.volume_ids),
                },
                source_refs=zone.source_refs,
                derivation_refs=selected_refs,
            )
        )
    values.append(
        CandidateProgramValue.create(
            value_id="area-footprint",
            facet=CandidateValueFacet.AREA,
            value={
                "value": option.footprint_area,
                "unit": proposal.grid_basis.area_unit,
                "horizontal_area_per_cell": (
                    proposal.grid_basis.horizontal_area_per_cell
                ),
            },
            source_refs=tuple(
                dict.fromkeys(
                    (
                        *proposal.grid_basis.source_refs,
                        *proposal.evidence_refs,
                    )
                )
            ),
            derivation_refs=selected_refs,
        )
    )
    values.append(
        CandidateProgramValue.create(
            value_id="topology-signature",
            facet=CandidateValueFacet.TOPOLOGY,
            value={
                "signature": option.topology_signature,
                "connections": [
                    item.to_dict() for item in proposal.connections
                ],
            },
            source_refs=proposal.evidence_refs,
            derivation_refs=selected_refs,
        )
    )
    for level in proposal.levels:
        values.append(
            CandidateProgramValue.create(
                value_id=f"dimension-level-{level.level_id}",
                facet=CandidateValueFacet.DIMENSION,
                value={
                    "base_y": level.base_y,
                    "height": level.height,
                },
                source_refs=level.source_refs,
                derivation_refs=selected_refs,
            )
        )
    for volume in proposal.volumes:
        values.append(
            CandidateProgramValue.create(
                value_id=f"dimension-volume-{volume.volume_id}",
                facet=CandidateValueFacet.DIMENSION,
                value=volume.bounds.to_dict(),
                source_refs=volume.source_refs,
                derivation_refs=selected_refs,
            )
        )
    values.append(
        CandidateProgramValue.create(
            value_id="coordinate-footprint-cells",
            facet=CandidateValueFacet.COORDINATE,
            value=[list(cell) for cell in proposal.footprint_cells],
            source_refs=proposal.evidence_refs,
            derivation_refs=selected_refs,
        )
    )
    if proposal.palette_refs:
        values.append(
            CandidateProgramValue.create(
                value_id="material-schematic-palette",
                facet=CandidateValueFacet.MATERIAL,
                value=list(proposal.palette_refs),
                source_refs=tuple(
                    dict.fromkeys(
                        (*proposal.palette_refs, *proposal.evidence_refs)
                    )
                ),
                derivation_refs=selected_refs,
            )
        )

    for component in state.components:
        facet = _component_facet(component.discipline)
        for attribute in component.attributes:
            values.append(
                CandidateProgramValue(
                    value_id=(
                        f"developed-{component.component_id}-{attribute.key}"
                    ),
                    facet=facet,
                    value_json=attribute.value_json,
                    source_refs=tuple(
                        dict.fromkeys(
                            (
                                *attribute.evidence_refs,
                                *component.evidence_refs,
                            )
                        )
                    ),
                    derivation_refs=tuple(
                        dict.fromkeys(
                            (
                                *selected_refs,
                                component.ref,
                                *component.schematic_dependency_refs,
                                *component.requirement_refs,
                                *attribute.source_claim_refs,
                            )
                        )
                    ),
                )
            )

    values.extend(additional_values)
    ordered = tuple(sorted(values, key=lambda item: item.value_id))
    return CandidateProgramProjection(
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        developed_state_digest=state.state_digest,
        portfolio_id=selected.portfolio_id,
        portfolio_digest=selected.portfolio_digest,
        selected_branch_id=selected.branch_id,
        selected_revision_id=selected.revision.revision_id,
        selected_revision_digest=selected.revision.revision_digest,
        selected_option_ref=option.ref,
        selection_transition_id=selected.selection_transition_id,
        selection_decision_ref=selected.selection_decision_ref,
        values=ordered,
    )
