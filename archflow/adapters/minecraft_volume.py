"""Strict bridge from a read-only Minecraft volume scan to ``VoxelScan@1``."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Protocol

from archflow.state import ArtifactRef, StateRef
from archflow.workspace import WorkspaceRef


Coordinate = tuple[int, int, int]
CELL_KINDS = frozenset({"air", "solid", "opening", "unknown"})


class MinecraftVolumeErrorCode(StrEnum):
    BASE_STATE_MISMATCH = "minecraft_volume.base_state_mismatch"
    INVALID_BOUNDS = "minecraft_volume.invalid_bounds"
    OVERSIZED = "minecraft_volume.oversized"
    TRANSPORT = "minecraft_volume.transport"
    OVERSIZED_RESPONSE = "minecraft_volume.oversized_response"
    MALFORMED_RESPONSE = "minecraft_volume.malformed_response"
    BOUNDS_MISMATCH = "minecraft_volume.bounds_mismatch"
    INVALID_CELL = "minecraft_volume.invalid_cell"
    DUPLICATE_CELL = "minecraft_volume.duplicate_cell"
    MISSING_CELL = "minecraft_volume.missing_cell"
    COUNT_MISMATCH = "minecraft_volume.count_mismatch"


class MinecraftVolumeError(ValueError):
    def __init__(
        self,
        code: MinecraftVolumeErrorCode,
        field: str,
        message: str,
    ) -> None:
        super().__init__(f"{code.value}: {field}: {message[:1000]}")
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True, order=True)
class MinecraftVolumeBounds:
    minimum: Coordinate
    maximum: Coordinate

    def __post_init__(self) -> None:
        for field, coordinate in (
            ("minimum", self.minimum),
            ("maximum", self.maximum),
        ):
            if (
                not isinstance(coordinate, tuple)
                or len(coordinate) != 3
                or any(type(value) is not int for value in coordinate)
            ):
                raise MinecraftVolumeError(
                    MinecraftVolumeErrorCode.INVALID_BOUNDS,
                    field,
                    "must be a tuple of three integers",
                )
        if any(
            low > high
            for low, high in zip(
                self.minimum,
                self.maximum,
                strict=True,
            )
        ):
            raise MinecraftVolumeError(
                MinecraftVolumeErrorCode.INVALID_BOUNDS,
                "bounds",
                "minimum must not exceed maximum",
            )

    @property
    def volume(self) -> int:
        return (
            (self.maximum[0] - self.minimum[0] + 1)
            * (self.maximum[1] - self.minimum[1] + 1)
            * (self.maximum[2] - self.minimum[2] + 1)
        )

    def to_dict(self) -> dict[str, list[int]]:
        return {
            "min": list(self.minimum),
            "max": list(self.maximum),
        }


class MinecraftVolumeTransport(Protocol):
    """Read-only provider port; implementations return one bounded JSON object."""

    def scan_volume(self, bounds: MinecraftVolumeBounds) -> object: ...


@dataclass(frozen=True, slots=True)
class MinecraftVolumeHttpTransport:
    """Loopback HTTP transport for the selected Minecraft bridge."""

    bridge_url: str = "http://127.0.0.1:7766"
    bearer_token: str = ""
    timeout_seconds: float = 10.0
    max_response_bytes: int = 4_000_000

    def __post_init__(self) -> None:
        if not self.bridge_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("bridge_url must name a loopback HTTP endpoint")
        if not 0.1 <= self.timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0.1 and 60")
        if not 1_024 <= self.max_response_bytes <= 16_000_000:
            raise ValueError(
                "max_response_bytes must be between 1024 and 16000000"
            )

    def scan_volume(self, bounds: MinecraftVolumeBounds) -> object:
        body = json.dumps(
            {
                "minX": bounds.minimum[0],
                "minY": bounds.minimum[1],
                "minZ": bounds.minimum[2],
                "maxX": bounds.maximum[0],
                "maxY": bounds.maximum[1],
                "maxZ": bounds.maximum[2],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.bridge_url.rstrip('/')}/v1/tools/volume",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        if self.bearer_token:
            request.add_header(
                "Authorization",
                f"Bearer {self.bearer_token}",
            )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                encoded = response.read(self.max_response_bytes + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise MinecraftVolumeError(
                MinecraftVolumeErrorCode.TRANSPORT,
                "bridge",
                str(exc),
            ) from exc
        if len(encoded) > self.max_response_bytes:
            raise MinecraftVolumeError(
                MinecraftVolumeErrorCode.OVERSIZED_RESPONSE,
                "bridge",
                f"response exceeds {self.max_response_bytes} bytes",
            )
        try:
            return json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MinecraftVolumeError(
                MinecraftVolumeErrorCode.MALFORMED_RESPONSE,
                "bridge",
                str(exc),
            ) from exc


@dataclass(frozen=True, slots=True)
class MinecraftVolumeCapture:
    SCHEMA: ClassVar[str] = "MinecraftVolumeCapture@1"

    scan_bytes: bytes
    scan_sha256: str
    bounds: MinecraftVolumeBounds
    dimension: str
    known_count: int
    unknown_count: int


class MinecraftVolumeAdapter:
    """Validates every requested cell before one workspace-bound scan write."""

    def __init__(self, *, max_cells: int = 32_768) -> None:
        if type(max_cells) is not int or not 1 <= max_cells <= 100_000:
            raise ValueError("max_cells must be between 1 and 100000")
        self._max_cells = max_cells

    def capture(
        self,
        *,
        source_artifact: ArtifactRef,
        base_state: StateRef,
        workspace: WorkspaceRef,
        bounds: MinecraftVolumeBounds,
        transport: MinecraftVolumeTransport,
    ) -> MinecraftVolumeCapture:
        if workspace.base != base_state:
            raise MinecraftVolumeError(
                MinecraftVolumeErrorCode.BASE_STATE_MISMATCH,
                "workspace.base",
                "workspace was forked from another state",
            )
        if bounds.volume > self._max_cells:
            raise MinecraftVolumeError(
                MinecraftVolumeErrorCode.OVERSIZED,
                "bounds",
                f"volume {bounds.volume} exceeds {self._max_cells}",
            )
        response = transport.scan_volume(bounds)
        parsed = _validate_response(response, bounds)
        payload = {
            "schema": "VoxelScan@1",
            "source_artifact_sha256": source_artifact.sha256,
            "base_state": {
                "run_id": base_state.run_id,
                "version": base_state.version,
            },
            "workspace_id": workspace.workspace_id,
            "bounds": bounds.to_dict(),
            "default_kind": "unknown",
            "runs": _compress_runs(parsed["known"]),
            "cells": [],
            "provider_observation": {
                "schema": "MinecraftVolumeScan@1",
                "dimension": parsed["dimension"],
                "complete": parsed["unknown_count"] == 0,
                "known_count": len(parsed["known"]),
                "unknown_count": parsed["unknown_count"],
            },
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return MinecraftVolumeCapture(
            scan_bytes=encoded,
            scan_sha256=hashlib.sha256(encoded).hexdigest(),
            bounds=bounds,
            dimension=parsed["dimension"],
            known_count=len(parsed["known"]),
            unknown_count=parsed["unknown_count"],
        )


def _validate_response(
    value: object,
    bounds: MinecraftVolumeBounds,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _malformed("response", "must be an object")
    expected = {
        "schema",
        "dimension",
        "bounds",
        "volume",
        "complete",
        "knownCount",
        "unknownCount",
        "cells",
    }
    if set(value) != expected or value.get("schema") != "MinecraftVolumeScan@1":
        raise _malformed("response", "schema or fields drifted")
    if value.get("bounds") != bounds.to_dict():
        raise MinecraftVolumeError(
            MinecraftVolumeErrorCode.BOUNDS_MISMATCH,
            "bounds",
            "provider did not repeat the requested bounds exactly",
        )
    if value.get("volume") != bounds.volume:
        raise MinecraftVolumeError(
            MinecraftVolumeErrorCode.COUNT_MISMATCH,
            "volume",
            "provider volume does not match the requested bounds",
        )
    dimension = value.get("dimension")
    if not isinstance(dimension, str) or not dimension or len(dimension) > 200:
        raise _malformed("dimension", "must be a bounded non-empty string")
    complete = value.get("complete")
    known_count = value.get("knownCount")
    unknown_count = value.get("unknownCount")
    if type(complete) is not bool:
        raise _malformed("complete", "must be bool")
    if any(type(item) is not int or item < 0 for item in (known_count, unknown_count)):
        raise _malformed("counts", "must be non-negative integers")
    if known_count + unknown_count != bounds.volume:
        raise MinecraftVolumeError(
            MinecraftVolumeErrorCode.COUNT_MISMATCH,
            "counts",
            "known and unknown counts do not cover the requested volume",
        )
    if complete != (unknown_count == 0):
        raise MinecraftVolumeError(
            MinecraftVolumeErrorCode.COUNT_MISMATCH,
            "complete",
            "complete must be true exactly when unknownCount is zero",
        )
    cells = value.get("cells")
    if not isinstance(cells, list):
        raise _malformed("cells", "must be an array")
    seen: set[Coordinate] = set()
    known: dict[Coordinate, str] = {}
    actual_unknown = 0
    for index, item in enumerate(cells):
        if not isinstance(item, dict) or set(item) != {
            "x",
            "y",
            "z",
            "kind",
            "blockId",
            "state",
        }:
            raise _malformed(f"cells[{index}]", "fields drifted")
        coordinate = item.get("x"), item.get("y"), item.get("z")
        if any(type(component) is not int for component in coordinate):
            raise _invalid_cell(index, "coordinates must be integers")
        if coordinate in seen:
            raise MinecraftVolumeError(
                MinecraftVolumeErrorCode.DUPLICATE_CELL,
                f"cells[{index}]",
                f"duplicate coordinate {coordinate}",
            )
        seen.add(coordinate)
        if any(
            value < low or value > high
            for value, low, high in zip(
                coordinate,
                bounds.minimum,
                bounds.maximum,
                strict=True,
            )
        ):
            raise _invalid_cell(index, "coordinate lies outside requested bounds")
        kind = item.get("kind")
        if kind not in CELL_KINDS:
            raise _invalid_cell(index, f"unknown kind {kind!r}")
        for field in ("blockId", "state"):
            text = item.get(field)
            if not isinstance(text, str) or len(text) > 1000:
                raise _invalid_cell(index, f"{field} must be a bounded string")
        if kind == "unknown":
            actual_unknown += 1
        else:
            known[coordinate] = kind
    if len(seen) != bounds.volume:
        raise MinecraftVolumeError(
            MinecraftVolumeErrorCode.MISSING_CELL,
            "cells",
            f"provider returned {len(seen)} of {bounds.volume} coordinates",
        )
    if len(known) != known_count or actual_unknown != unknown_count:
        raise MinecraftVolumeError(
            MinecraftVolumeErrorCode.COUNT_MISMATCH,
            "cells",
            "declared counts do not match cell kinds",
        )
    return {
        "dimension": dimension,
        "known": known,
        "unknown_count": unknown_count,
    }


def _compress_runs(cells: dict[Coordinate, str]) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    current_kind: str | None = None
    start: Coordinate | None = None
    end: Coordinate | None = None
    for coordinate, kind in sorted(cells.items()):
        contiguous = (
            end is not None
            and coordinate[0] == end[0]
            and coordinate[1] == end[1]
            and coordinate[2] == end[2] + 1
            and kind == current_kind
        )
        if not contiguous:
            if start is not None and end is not None and current_kind is not None:
                runs.append(
                    {
                        "kind": current_kind,
                        "from": list(start),
                        "to": list(end),
                    }
                )
            start = coordinate
            current_kind = kind
        end = coordinate
    if start is not None and end is not None and current_kind is not None:
        runs.append(
            {
                "kind": current_kind,
                "from": list(start),
                "to": list(end),
            }
        )
    return runs


def _malformed(field: str, message: str) -> MinecraftVolumeError:
    return MinecraftVolumeError(
        MinecraftVolumeErrorCode.MALFORMED_RESPONSE,
        field,
        message,
    )


def _invalid_cell(index: int, message: str) -> MinecraftVolumeError:
    return MinecraftVolumeError(
        MinecraftVolumeErrorCode.INVALID_CELL,
        f"cells[{index}]",
        message,
    )
