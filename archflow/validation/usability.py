"""Deterministic hard gates for minimal voxel-building usability."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from archflow.adapters.voxel_observation import (
    ConnectedRegion,
    Coordinate,
    VoxelObservation,
)
from archflow.state import BuildingProgram


class UsabilityGate(StrEnum):
    SIZE = "size"
    CLEAR_HEIGHT = "clear_height"
    ENTRANCE = "entrance"
    CONNECTIVITY = "connectivity"
    USE_ZONES = "use_zones"
    SUPPORT = "support"


@dataclass(frozen=True, slots=True)
class UseZoneEvidence:
    space: str
    region_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.space.strip() or not self.region_id.strip():
            raise ValueError("space and region_id must be non-empty")
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")


@dataclass(frozen=True, slots=True)
class UsabilityFinding:
    code: str
    gate: UsabilityGate
    message: str
    measured: str
    threshold: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UsabilityReceipt:
    receipt_id: str
    observation_id: str
    program_schema: str
    passed: bool
    findings: tuple[UsabilityFinding, ...]

    def __post_init__(self) -> None:
        if self.passed == bool(self.findings):
            raise ValueError("passed must be true exactly when findings are empty")


def validate_usability(
    program: BuildingProgram,
    observation: VoxelObservation,
    *,
    use_zones: tuple[UseZoneEvidence, ...],
) -> UsabilityReceipt:
    """Evaluate hard evidence only; soft evaluation has no input channel."""

    if not isinstance(use_zones, tuple):
        raise TypeError("use_zones must be a tuple")
    findings: list[UsabilityFinding] = []
    if observation.unknown_count:
        for gate in (
            UsabilityGate.SIZE,
            UsabilityGate.CLEAR_HEIGHT,
            UsabilityGate.ENTRANCE,
            UsabilityGate.CONNECTIVITY,
            UsabilityGate.SUPPORT,
        ):
            findings.append(_unknown_finding(gate, observation))
    else:
        findings.extend(_size_findings(program, observation))
        findings.extend(_clear_height_findings(program, observation))
        findings.extend(_entrance_findings(program, observation))
        findings.extend(_connectivity_findings(program, observation))
        findings.extend(_support_findings(observation))
    findings.extend(_use_zone_findings(program, observation, use_zones))
    frozen = tuple(findings)
    payload = {
        "observation_id": observation.observation_id,
        "program": program.to_dict(),
        "findings": [
            {
                "code": item.code,
                "gate": item.gate.value,
                "measured": item.measured,
                "threshold": item.threshold,
                "evidence_refs": item.evidence_refs,
            }
            for item in frozen
        ],
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return UsabilityReceipt(
        receipt_id=f"usability-{digest[:20]}",
        observation_id=observation.observation_id,
        program_schema=program.SCHEMA,
        passed=not frozen,
        findings=frozen,
    )


def _unknown_finding(
    gate: UsabilityGate,
    observation: VoxelObservation,
) -> UsabilityFinding:
    return UsabilityFinding(
        code=f"usability.{gate.value}.evidence_unknown",
        gate=gate,
        message=f"{gate.value} cannot be checked from an incomplete voxel scan",
        measured=f"unknown_cells={observation.unknown_count}",
        threshold="complete evidence for this hard gate",
        evidence_refs=tuple(
            _coordinate_ref(item.coordinate) for item in observation.unknowns[:8]
        ),
    )


def _size_findings(
    program: BuildingProgram,
    observation: VoxelObservation,
) -> tuple[UsabilityFinding, ...]:
    measured = (
        observation.envelope.maximum[0] - observation.envelope.minimum[0] + 1,
        observation.envelope.maximum[2] - observation.envelope.minimum[2] + 1,
    )
    target = (
        program.footprint.width_blocks,
        program.footprint.depth_blocks,
    )
    tolerance = program.footprint.tolerance_blocks

    def fits(candidate: tuple[int, int], expected: tuple[int, int]) -> bool:
        return all(
            abs(actual - wanted) <= tolerance
            for actual, wanted in zip(candidate, expected, strict=True)
        )

    if fits(measured, target) or fits(measured, target[::-1]):
        return ()
    return (
        UsabilityFinding(
            code="usability.size.outside_target",
            gate=UsabilityGate.SIZE,
            message="candidate footprint is outside the permitted target range",
            measured=f"{measured[0]}x{measured[1]} blocks",
            threshold=(
                f"{target[0]}x{target[1]} blocks, rotation allowed, "
                f"tolerance ±{tolerance}"
            ),
            evidence_refs=(
                _coordinate_ref(observation.envelope.minimum),
                _coordinate_ref(observation.envelope.maximum),
            ),
        ),
    )


def _clear_height_findings(
    program: BuildingProgram,
    observation: VoxelObservation,
) -> tuple[UsabilityFinding, ...]:
    occupied = set(observation.occupied_cells)
    measurements: list[tuple[int, Coordinate]] = []
    unknown: list[Coordinate] = []
    for cell in observation.walkable_cells:
        blockers = sorted(
            candidate[1] - cell[1]
            for candidate in occupied
            if candidate[0] == cell[0]
            and candidate[2] == cell[2]
            and candidate[1] > cell[1]
        )
        if blockers:
            measurements.append((blockers[0], cell))
        else:
            unknown.append(cell)
    if unknown or not measurements:
        return (
            UsabilityFinding(
                code="usability.clear_height.unbounded_or_missing",
                gate=UsabilityGate.CLEAR_HEIGHT,
                message="clear height cannot be established for all walkable cells",
                measured=f"unmeasured_walkable_cells={len(unknown)}",
                threshold=(
                    f"minimum_clear_height={program.minimum_clear_height} blocks"
                ),
                evidence_refs=tuple(_coordinate_ref(item) for item in unknown[:8]),
            ),
        )
    minimum, coordinate = min(measurements)
    if minimum >= program.minimum_clear_height:
        return ()
    return (
        UsabilityFinding(
            code="usability.clear_height.below_minimum",
            gate=UsabilityGate.CLEAR_HEIGHT,
            message="minimum measured clear height is too low",
            measured=f"{minimum} blocks",
            threshold=f">={program.minimum_clear_height} blocks",
            evidence_refs=(_coordinate_ref(coordinate),),
        ),
    )


def _entrance_findings(
    program: BuildingProgram,
    observation: VoxelObservation,
) -> tuple[UsabilityFinding, ...]:
    min_x, _, min_z = observation.envelope.minimum
    max_x, _, max_z = observation.envelope.maximum
    exterior_columns = sorted(
        {
            (x, z)
            for x, _, z in observation.openings
            if x in {min_x, max_x} or z in {min_z, max_z}
        }
    )
    if len(exterior_columns) >= program.entrance_count:
        return ()
    return (
        UsabilityFinding(
            code="usability.entrance.insufficient_exterior_openings",
            gate=UsabilityGate.ENTRANCE,
            message="candidate has too few exterior entrance columns",
            measured=f"{len(exterior_columns)} exterior opening columns",
            threshold=f">={program.entrance_count}",
            evidence_refs=tuple(
                f"voxel-column:{x},*,{z}" for x, z in exterior_columns[:8]
            ),
        ),
    )


def _connectivity_findings(
    program: BuildingProgram,
    observation: VoxelObservation,
) -> tuple[UsabilityFinding, ...]:
    regions = observation.connected_regions
    findings: list[UsabilityFinding] = []
    if len(regions) != 1:
        findings.append(
            UsabilityFinding(
                code="usability.connectivity.disconnected",
                gate=UsabilityGate.CONNECTIVITY,
                message="walkable cells do not form one connected region",
                measured=f"{len(regions)} connected regions",
                threshold="exactly 1 connected region",
                evidence_refs=tuple(region.region_id for region in regions[:8]),
            )
        )
        return tuple(findings)
    clearance, coordinate = _minimum_plan_clearance(regions[0])
    if clearance < program.circulation_min_width:
        findings.append(
            UsabilityFinding(
                code="usability.connectivity.width_below_minimum",
                gate=UsabilityGate.CONNECTIVITY,
                message="minimum local walkable width is below the program minimum",
                measured=f"{clearance} blocks",
                threshold=f">={program.circulation_min_width} blocks",
                evidence_refs=(_coordinate_ref(coordinate),),
            )
        )
    return tuple(findings)


def _minimum_plan_clearance(region: ConnectedRegion) -> tuple[int, Coordinate]:
    cells = set(region.cells)
    measurements = []
    for cell in region.cells:
        x_span = _axis_span(cells, cell, axis=0)
        z_span = _axis_span(cells, cell, axis=2)
        measurements.append((min(x_span, z_span), cell))
    return min(measurements)


def _axis_span(
    cells: set[Coordinate],
    cell: Coordinate,
    *,
    axis: int,
) -> int:
    span = 1
    for direction in (-1, 1):
        step = 1
        while True:
            candidate = list(cell)
            candidate[axis] += direction * step
            if tuple(candidate) not in cells:
                break
            span += 1
            step += 1
    return span


def _use_zone_findings(
    program: BuildingProgram,
    observation: VoxelObservation,
    use_zones: tuple[UseZoneEvidence, ...],
) -> tuple[UsabilityFinding, ...]:
    by_space: dict[str, list[UseZoneEvidence]] = {}
    for item in use_zones:
        by_space.setdefault(item.space, []).append(item)
    region_ids = {region.region_id for region in observation.connected_regions}
    findings: list[UsabilityFinding] = []
    for space in program.required_spaces:
        evidence = by_space.get(space, [])
        valid = [
            item
            for item in evidence
            if item.region_id in region_ids and item.evidence_refs
        ]
        if valid:
            continue
        findings.append(
            UsabilityFinding(
                code="usability.use_zone.missing_or_unbound",
                gate=UsabilityGate.USE_ZONES,
                message=f"required use zone has no bound spatial evidence: {space}",
                measured=f"valid_evidence={len(valid)}",
                threshold=">=1 evidence item bound to an observed region",
                evidence_refs=tuple(
                    item.region_id for item in evidence[:8] if item.region_id
                ),
            )
        )
    return tuple(findings)


def _support_findings(
    observation: VoxelObservation,
) -> tuple[UsabilityFinding, ...]:
    if not observation.unsupported_cells:
        return ()
    return (
        UsabilityFinding(
            code="usability.support.floating_component",
            gate=UsabilityGate.SUPPORT,
            message="occupied cells include a component disconnected from ground",
            measured=f"{len(observation.unsupported_cells)} unsupported cells",
            threshold="0 unsupported cells",
            evidence_refs=tuple(
                _coordinate_ref(item)
                for item in observation.unsupported_cells[:8]
            ),
        ),
    )


def _coordinate_ref(coordinate: Coordinate) -> str:
    return f"voxel:{coordinate[0]},{coordinate[1]},{coordinate[2]}"
