"""Deterministic, read-only extraction from a saved bounded voxel scan."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from archflow.state import ArtifactRef, StateRef
from archflow.workspace import WorkspaceRef


Coordinate = tuple[int, int, int]
CELL_KINDS = frozenset({"air", "solid", "opening"})
READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "minecraft_session",
        "minecraft_buildsite",
        "minecraft_get_block_info",
        "minecraft_scan_volume",
    }
)
FORBIDDEN_WRITE_TOOLS = frozenset(
    {
        "minecraft_execute_build_plan",
        "minecraft_execute_commands",
        "minecraft_undo_last_batch",
        "minecraft_place_block",
        "minecraft_dig_block",
    }
)


class VoxelObservationErrorCode(StrEnum):
    FILE_OUTSIDE_WORKSPACE = "voxel_observation.file_outside_workspace"
    OVERSIZED_SCAN = "voxel_observation.oversized_scan"
    MALFORMED_JSON = "voxel_observation.malformed_json"
    UNSUPPORTED_SCHEMA = "voxel_observation.unsupported_schema"
    SOURCE_DIGEST_MISMATCH = "voxel_observation.source_digest_mismatch"
    BASE_STATE_MISMATCH = "voxel_observation.base_state_mismatch"
    WORKSPACE_MISMATCH = "voxel_observation.workspace_mismatch"
    INVALID_BOUNDS = "voxel_observation.invalid_bounds"
    INVALID_CELL = "voxel_observation.invalid_cell"
    TOO_MANY_CELLS = "voxel_observation.too_many_cells"


class VoxelObservationError(ValueError):
    def __init__(
        self,
        code: VoxelObservationErrorCode,
        field: str,
        message: str,
    ) -> None:
        bounded_message = message[:1000]
        super().__init__(f"{code.value}: {field}: {bounded_message}")
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True, order=True)
class VoxelBounds:
    minimum: Coordinate
    maximum: Coordinate

    @property
    def volume(self) -> int:
        return (
            (self.maximum[0] - self.minimum[0] + 1)
            * (self.maximum[1] - self.minimum[1] + 1)
            * (self.maximum[2] - self.minimum[2] + 1)
        )

    def contains(self, coordinate: Coordinate) -> bool:
        return all(
            low <= value <= high
            for value, low, high in zip(
                coordinate,
                self.minimum,
                self.maximum,
                strict=True,
            )
        )


@dataclass(frozen=True, slots=True)
class ConnectedRegion:
    region_id: str
    bounds: VoxelBounds
    cells: tuple[Coordinate, ...]
    touches_opening: bool


@dataclass(frozen=True, slots=True, order=True)
class SupportRelation:
    first: Coordinate
    second: Coordinate
    kind: str = "face_adjacent"


@dataclass(frozen=True, slots=True, order=True)
class ObservationUnknown:
    code: str
    coordinate: Coordinate


@dataclass(frozen=True, slots=True)
class VoxelObservation:
    SCHEMA: ClassVar[str] = "VoxelObservation@1"

    observation_id: str
    source_artifact_id: str
    source_artifact_sha256: str
    source_scan_sha256: str
    workspace_id: str
    base_state: StateRef
    envelope: VoxelBounds
    occupied_cells: tuple[Coordinate, ...]
    walkable_cells: tuple[Coordinate, ...]
    openings: tuple[Coordinate, ...]
    connected_regions: tuple[ConnectedRegion, ...]
    support_relations: tuple[SupportRelation, ...]
    unsupported_cells: tuple[Coordinate, ...]
    unknown_count: int
    unknowns: tuple[ObservationUnknown, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "observation_id": self.observation_id,
            "source_artifact_id": self.source_artifact_id,
            "source_artifact_sha256": self.source_artifact_sha256,
            "source_scan_sha256": self.source_scan_sha256,
            "workspace_id": self.workspace_id,
            "base_state": {
                "run_id": self.base_state.run_id,
                "version": self.base_state.version,
            },
            "envelope": _bounds_dict(self.envelope),
            "occupied_cells": [_coordinate_list(item) for item in self.occupied_cells],
            "walkable_cells": [_coordinate_list(item) for item in self.walkable_cells],
            "openings": [_coordinate_list(item) for item in self.openings],
            "connected_regions": [
                {
                    "region_id": region.region_id,
                    "bounds": _bounds_dict(region.bounds),
                    "cells": [_coordinate_list(item) for item in region.cells],
                    "touches_opening": region.touches_opening,
                }
                for region in self.connected_regions
            ],
            "support_relations": [
                {
                    "first": _coordinate_list(item.first),
                    "second": _coordinate_list(item.second),
                    "kind": item.kind,
                }
                for item in self.support_relations
            ],
            "unsupported_cells": [
                _coordinate_list(item) for item in self.unsupported_cells
            ],
            "unknown_count": self.unknown_count,
            "unknowns": [
                {
                    "code": item.code,
                    "coordinate": _coordinate_list(item.coordinate),
                }
                for item in self.unknowns
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class VoxelObservationExtractor:
    """Consumes files only; it has no MCP client or mutation capability."""

    def __init__(
        self,
        *,
        max_scan_bytes: int = 2_000_000,
        max_cells: int = 100_000,
        max_unknown_samples: int = 200,
    ) -> None:
        if min(max_scan_bytes, max_cells, max_unknown_samples) <= 0:
            raise ValueError("observation limits must be positive")
        self._max_scan_bytes = max_scan_bytes
        self._max_cells = max_cells
        self._max_unknown_samples = max_unknown_samples

    def extract(
        self,
        *,
        source_artifact: ArtifactRef,
        base_state: StateRef,
        workspace: WorkspaceRef,
        scan_path: Path,
    ) -> VoxelObservation:
        if workspace.base != base_state:
            raise VoxelObservationError(
                VoxelObservationErrorCode.BASE_STATE_MISMATCH,
                "workspace.base",
                "workspace was forked from another state",
            )
        path = scan_path.resolve()
        try:
            path.relative_to(workspace.root.resolve())
        except ValueError as exc:
            raise VoxelObservationError(
                VoxelObservationErrorCode.FILE_OUTSIDE_WORKSPACE,
                "scan_path",
                "scan must be owned by the speculative workspace",
            ) from exc
        if path.stat().st_size > self._max_scan_bytes:
            raise VoxelObservationError(
                VoxelObservationErrorCode.OVERSIZED_SCAN,
                "scan_path",
                f"scan exceeds {self._max_scan_bytes} bytes",
            )
        encoded = path.read_bytes()
        scan_sha256 = hashlib.sha256(encoded).hexdigest()
        try:
            payload = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VoxelObservationError(
                VoxelObservationErrorCode.MALFORMED_JSON,
                "scan",
                str(exc),
            ) from exc
        if not isinstance(payload, dict):
            raise VoxelObservationError(
                VoxelObservationErrorCode.MALFORMED_JSON,
                "scan",
                "top-level scan must be an object",
            )
        self._check_binding(payload, source_artifact, base_state, workspace)
        bounds = _parse_bounds(payload.get("bounds"))
        if bounds.volume > self._max_cells:
            raise VoxelObservationError(
                VoxelObservationErrorCode.TOO_MANY_CELLS,
                "bounds",
                f"bounded volume {bounds.volume} exceeds {self._max_cells}",
            )
        cells = self._expand_cells(payload, bounds)
        return self._observe(
            source_artifact=source_artifact,
            base_state=base_state,
            workspace=workspace,
            scan_sha256=scan_sha256,
            bounds=bounds,
            cells=cells,
        )

    @staticmethod
    def _check_binding(
        payload: dict[str, Any],
        source_artifact: ArtifactRef,
        base_state: StateRef,
        workspace: WorkspaceRef,
    ) -> None:
        if payload.get("schema") != "VoxelScan@1":
            raise VoxelObservationError(
                VoxelObservationErrorCode.UNSUPPORTED_SCHEMA,
                "schema",
                "must equal VoxelScan@1",
            )
        if payload.get("source_artifact_sha256") != source_artifact.sha256:
            raise VoxelObservationError(
                VoxelObservationErrorCode.SOURCE_DIGEST_MISMATCH,
                "source_artifact_sha256",
                "scan does not describe the supplied artifact digest",
            )
        expected_state = {
            "run_id": base_state.run_id,
            "version": base_state.version,
        }
        if payload.get("base_state") != expected_state:
            raise VoxelObservationError(
                VoxelObservationErrorCode.BASE_STATE_MISMATCH,
                "base_state",
                "scan is bound to a different canonical state",
            )
        if payload.get("workspace_id") != workspace.workspace_id:
            raise VoxelObservationError(
                VoxelObservationErrorCode.WORKSPACE_MISMATCH,
                "workspace_id",
                "scan is bound to a different workspace",
            )

    def _expand_cells(
        self,
        payload: dict[str, Any],
        bounds: VoxelBounds,
    ) -> dict[Coordinate, str]:
        default_kind = payload.get("default_kind", "unknown")
        if default_kind not in CELL_KINDS | {"unknown"}:
            raise _invalid_cell("default_kind", f"unknown kind: {default_kind!r}")
        cells: dict[Coordinate, str] = {}
        if default_kind != "unknown":
            for coordinate in _coordinates(bounds):
                cells[coordinate] = default_kind
        runs = payload.get("runs", [])
        if not isinstance(runs, list):
            raise _invalid_cell("runs", "must be an array")
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                raise _invalid_cell(f"runs[{index}]", "must be an object")
            kind = _parse_kind(run.get("kind"), f"runs[{index}].kind")
            run_bounds = VoxelBounds(
                _parse_coordinate(run.get("from"), f"runs[{index}].from"),
                _parse_coordinate(run.get("to"), f"runs[{index}].to"),
            )
            _validate_bounds(run_bounds, f"runs[{index}]")
            if not bounds.contains(run_bounds.minimum) or not bounds.contains(
                run_bounds.maximum
            ):
                raise _invalid_cell(f"runs[{index}]", "extends outside scan bounds")
            for coordinate in _coordinates(run_bounds):
                cells[coordinate] = kind
        overrides = payload.get("cells", [])
        if not isinstance(overrides, list):
            raise _invalid_cell("cells", "must be an array")
        seen: set[Coordinate] = set()
        for index, item in enumerate(overrides):
            if not isinstance(item, dict):
                raise _invalid_cell(f"cells[{index}]", "must be an object")
            coordinate = _parse_coordinate(
                item.get("at"),
                f"cells[{index}].at",
            )
            if coordinate in seen:
                raise _invalid_cell(
                    f"cells[{index}].at",
                    "duplicates an earlier explicit cell",
                )
            seen.add(coordinate)
            if not bounds.contains(coordinate):
                raise _invalid_cell(
                    f"cells[{index}].at",
                    "lies outside scan bounds",
                )
            cells[coordinate] = _parse_kind(
                item.get("kind"),
                f"cells[{index}].kind",
            )
        return cells

    def _observe(
        self,
        *,
        source_artifact: ArtifactRef,
        base_state: StateRef,
        workspace: WorkspaceRef,
        scan_sha256: str,
        bounds: VoxelBounds,
        cells: dict[Coordinate, str],
    ) -> VoxelObservation:
        occupied = tuple(sorted(at for at, kind in cells.items() if kind == "solid"))
        openings = tuple(
            sorted(at for at, kind in cells.items() if kind == "opening")
        )
        architectural = tuple(sorted(set(occupied) | set(openings)))
        if not architectural:
            raise _invalid_cell("cells", "scan contains no architectural cells")
        envelope = _bounds_for(architectural)
        walkable = tuple(
            sorted(
                coordinate
                for coordinate, kind in cells.items()
                if kind in {"air", "opening"}
                and cells.get(_offset(coordinate, 0, -1, 0)) == "solid"
                and cells.get(_offset(coordinate, 0, 1, 0))
                in {"air", "opening"}
            )
        )
        regions = _connected_regions(walkable, set(openings))
        support_relations, unsupported = _support_graph(occupied, bounds.minimum[1])
        unknown_coordinates = tuple(
            coordinate
            for coordinate in _coordinates(bounds)
            if coordinate not in cells
        )
        unknowns = tuple(
            ObservationUnknown("scan.cell_unknown", coordinate)
            for coordinate in unknown_coordinates[: self._max_unknown_samples]
        )
        identity_payload = {
            "source_artifact_sha256": source_artifact.sha256,
            "source_scan_sha256": scan_sha256,
            "workspace_id": workspace.workspace_id,
            "base_state": [base_state.run_id, base_state.version],
            "envelope": [envelope.minimum, envelope.maximum],
            "occupied_cells": occupied,
            "walkable_cells": walkable,
            "openings": openings,
            "regions": [region.cells for region in regions],
            "support_relations": [
                (item.first, item.second) for item in support_relations
            ],
            "unsupported_cells": unsupported,
            "unknown_count": len(unknown_coordinates),
        }
        digest = hashlib.sha256(
            json.dumps(
                identity_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return VoxelObservation(
            observation_id=f"voxel-observation-{digest[:20]}",
            source_artifact_id=source_artifact.artifact_id,
            source_artifact_sha256=source_artifact.sha256,
            source_scan_sha256=scan_sha256,
            workspace_id=workspace.workspace_id,
            base_state=base_state,
            envelope=envelope,
            occupied_cells=occupied,
            walkable_cells=walkable,
            openings=openings,
            connected_regions=regions,
            support_relations=support_relations,
            unsupported_cells=unsupported,
            unknown_count=len(unknown_coordinates),
            unknowns=unknowns,
        )


def _parse_bounds(value: object) -> VoxelBounds:
    if not isinstance(value, dict):
        raise VoxelObservationError(
            VoxelObservationErrorCode.INVALID_BOUNDS,
            "bounds",
            "must be an object",
        )
    bounds = VoxelBounds(
        _parse_coordinate(value.get("min"), "bounds.min"),
        _parse_coordinate(value.get("max"), "bounds.max"),
    )
    _validate_bounds(bounds, "bounds")
    return bounds


def _validate_bounds(bounds: VoxelBounds, field: str) -> None:
    if any(
        low > high
        for low, high in zip(bounds.minimum, bounds.maximum, strict=True)
    ):
        raise VoxelObservationError(
            VoxelObservationErrorCode.INVALID_BOUNDS,
            field,
            "minimum coordinate must not exceed maximum",
        )


def _parse_coordinate(value: object, field: str) -> Coordinate:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(item) is not int for item in value)
    ):
        raise _invalid_cell(field, "must be an array of three integers")
    return value[0], value[1], value[2]


def _parse_kind(value: object, field: str) -> str:
    if value not in CELL_KINDS:
        raise _invalid_cell(field, f"must be one of {sorted(CELL_KINDS)}")
    return str(value)


def _invalid_cell(field: str, message: str) -> VoxelObservationError:
    return VoxelObservationError(
        VoxelObservationErrorCode.INVALID_CELL,
        field,
        message,
    )


def _coordinates(bounds: VoxelBounds):
    for x in range(bounds.minimum[0], bounds.maximum[0] + 1):
        for y in range(bounds.minimum[1], bounds.maximum[1] + 1):
            for z in range(bounds.minimum[2], bounds.maximum[2] + 1):
                yield x, y, z


def _offset(at: Coordinate, x: int, y: int, z: int) -> Coordinate:
    return at[0] + x, at[1] + y, at[2] + z


def _bounds_for(cells: tuple[Coordinate, ...]) -> VoxelBounds:
    return VoxelBounds(
        tuple(min(item[index] for item in cells) for index in range(3)),
        tuple(max(item[index] for item in cells) for index in range(3)),
    )


def _connected_regions(
    walkable: tuple[Coordinate, ...],
    openings: set[Coordinate],
) -> tuple[ConnectedRegion, ...]:
    remaining = set(walkable)
    components: list[tuple[Coordinate, ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        pending = [seed]
        component: list[Coordinate] = []
        while pending:
            current = pending.pop()
            component.append(current)
            for neighbor in (
                _offset(current, -1, 0, 0),
                _offset(current, 1, 0, 0),
                _offset(current, 0, 0, -1),
                _offset(current, 0, 0, 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        components.append(tuple(sorted(component)))
    components.sort(key=lambda item: item[0])
    return tuple(
        ConnectedRegion(
            region_id=f"region-{index:03d}",
            bounds=_bounds_for(component),
            cells=component,
            touches_opening=bool(set(component) & openings),
        )
        for index, component in enumerate(components, start=1)
    )


def _support_graph(
    occupied: tuple[Coordinate, ...],
    ground_y: int,
) -> tuple[tuple[SupportRelation, ...], tuple[Coordinate, ...]]:
    occupied_set = set(occupied)
    relations: list[SupportRelation] = []
    for cell in occupied:
        for delta in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            neighbor = _offset(cell, *delta)
            if neighbor in occupied_set:
                relations.append(SupportRelation(cell, neighbor))

    remaining = set(occupied)
    unsupported: list[Coordinate] = []
    neighbor_deltas = (
        (-1, 0, 0),
        (1, 0, 0),
        (0, -1, 0),
        (0, 1, 0),
        (0, 0, -1),
        (0, 0, 1),
    )
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        pending = [seed]
        component: list[Coordinate] = []
        while pending:
            current = pending.pop()
            component.append(current)
            for delta in neighbor_deltas:
                neighbor = _offset(current, *delta)
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        if not any(cell[1] == ground_y for cell in component):
            unsupported.extend(component)
    return tuple(relations), tuple(sorted(unsupported))


def _coordinate_list(coordinate: Coordinate) -> list[int]:
    return list(coordinate)


def _bounds_dict(bounds: VoxelBounds) -> dict[str, list[int]]:
    return {
        "min": _coordinate_list(bounds.minimum),
        "max": _coordinate_list(bounds.maximum),
    }
