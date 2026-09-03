"""Semantic references and their one resolver (P100).

Elements are placed by reference, not by coordinate:

* plan references — ``GridRef(axis)``, ``GridIntersection(a, b)``,
  ``AxisPoint(axis, along)``, ``HostAlong(host, along)`` — resolve here into
  plan coordinates (X, Z) from the project grids or a host's reference line;
* elevation references — ``LevelRef(level)`` and ``OffsetFrom(LevelRef, d)``
  — are *not* resolved here: they stay symbolic as ``base_level`` /
  ``base_offset`` and the geometry compiler resolves them from the datum
  record (P090 / M096 / P098). One resolver for plan, one for elevation;
  no producer computes either on its own.

The runner's producers read references directly (P089 / P102); no coordinate
pack exists any more — the lowering adapter was retired on 2026-09-02 once
both projects' records reproduced their runner-002 programs exactly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from archflow.project.refs import require_identifier
from archflow.state.derivation import EvaluatedDerivations, substitute
from archflow.state.geometry_program import GeometryProgramError, ProjectGridAxis, ProjectGrids, ProjectLevels
from archflow.contracts.fields import (
    number,
)

Plan = tuple[float, float]


class ReferenceError(ValueError):
    """Typed failure of reference resolution."""


def _finite(value: object, field: str) -> float:
    """The owned finite-number rule, typed for this module's callers."""

    try:
        return number(value, field)
    except ValueError as exc:
        raise ReferenceError(str(exc)) from exc


# ---------------------------------------------------------------- reference types
@dataclass(frozen=True, slots=True)
class GridRef:
    axis: str

    def __post_init__(self) -> None:
        require_identifier(self.axis, "grid axis")


@dataclass(frozen=True, slots=True)
class GridIntersection:
    axis_a: str
    axis_b: str

    def __post_init__(self) -> None:
        require_identifier(self.axis_a, "grid axis")
        require_identifier(self.axis_b, "grid axis")
        if self.axis_a == self.axis_b:
            raise ReferenceError("a grid intersection needs two different axes")


@dataclass(frozen=True, slots=True)
class AxisPoint:
    """A point on a grid axis at a signed distance ``along`` from its origin."""

    axis: str
    along: float

    def __post_init__(self) -> None:
        require_identifier(self.axis, "grid axis")
        object.__setattr__(self, "along", _finite(self.along, "along"))


@dataclass(frozen=True, slots=True)
class HostAlong:
    """A point along a host element's reference line at ``along`` metres."""

    host: str
    along: float
    across: float = 0.0

    def __post_init__(self) -> None:
        require_identifier(self.host, "host")
        object.__setattr__(self, "along", _finite(self.along, "along"))
        object.__setattr__(self, "across", _finite(self.across, "across"))


@dataclass(frozen=True, slots=True)
class LevelRef:
    level: str

    def __post_init__(self) -> None:
        require_identifier(self.level, "level")


@dataclass(frozen=True, slots=True)
class OffsetFrom:
    level: str
    offset: float

    def __post_init__(self) -> None:
        require_identifier(self.level, "level")
        object.__setattr__(self, "offset", _finite(self.offset, "offset"))


PlanReference = GridRef | GridIntersection | AxisPoint | HostAlong
ElevationReference = LevelRef | OffsetFrom


def parse_reference(value: object) -> PlanReference | ElevationReference:
    """Read a reference from its JSON form ``{"grid": ...}`` etc."""

    if isinstance(value, (GridRef, GridIntersection, AxisPoint, HostAlong, LevelRef, OffsetFrom)):
        return value
    if not isinstance(value, Mapping) or len(value) != 1:
        raise ReferenceError(f"a reference is a one-key object, got {value!r}")
    (kind, payload), = value.items()
    if kind == "grid":
        if isinstance(payload, (list, tuple)) and len(payload) == 2:
            return GridIntersection(str(payload[0]), str(payload[1]))
        return GridRef(str(payload))
    if kind == "axis_point":
        return AxisPoint(str(payload["axis"]), payload["along"])
    if kind == "host":
        return HostAlong(str(payload["element"]), payload["along"], payload.get("across", 0.0))
    if kind == "level":
        return LevelRef(str(payload))
    if kind == "offset_from":
        return OffsetFrom(str(payload["level"]), payload["offset"])
    raise ReferenceError(f"unknown reference kind {kind!r}")


# ---------------------------------------------------------------- resolution
@dataclass(frozen=True, slots=True)
class HostLine:
    """A host element's reference line in plan, as the wall solver defines it."""

    origin: Plan
    direction: Plan

    def point(self, along: float, across: float = 0.0) -> Plan:
        dx, dz = self.direction
        norm = math.hypot(dx, dz)
        dx, dz = dx / norm, dz / norm
        nx, nz = dz, -dx
        return (self.origin[0] + dx * along + nx * across, self.origin[1] + dz * along + nz * across)


class ReferenceContext:
    """Grids, levels and host lines a resolver reads; nothing else."""

    def __init__(self, *, grids: ProjectGrids | None, levels: ProjectLevels | None = None, hosts: Mapping[str, HostLine] | None = None) -> None:
        self.grids = grids
        self.levels = levels
        self.hosts = dict(hosts or {})

    def axis(self, axis_id: str) -> ProjectGridAxis:
        """An axis by role (the project's own lookup) or by id."""

        if self.grids is None:
            raise ReferenceError(f"no project grids: cannot resolve axis {axis_id!r}")
        try:
            return self.grids.axis(axis_id)
        except GeometryProgramError:
            by_id = {item.axis_id: item for item in self.grids.axes}
            if axis_id in by_id:
                return by_id[axis_id]
            raise ReferenceError(f"unknown grid axis {axis_id!r}") from None

    def level_ids(self) -> tuple[str, ...]:
        return self.levels.datum_ids if self.levels is not None else ()


def _line_of(axis: ProjectGridAxis) -> tuple[Plan, Plan]:
    ox, _, oz = axis.origin
    dx, _, dz = axis.direction
    norm = math.hypot(dx, dz)
    return (ox, oz), (dx / norm, dz / norm)


def resolve_plan(reference: PlanReference, context: ReferenceContext) -> Plan:
    """One plan coordinate for one plan reference; every producer calls this."""

    if isinstance(reference, GridIntersection):
        (o1, d1), (o2, d2) = _line_of(context.axis(reference.axis_a)), _line_of(context.axis(reference.axis_b))
        det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
        if abs(det) < 1e-12:
            raise ReferenceError(f"axes {reference.axis_a} and {reference.axis_b} are parallel")
        rx, rz = o2[0] - o1[0], o2[1] - o1[1]
        t = (rx * (-d2[1]) - rz * (-d2[0])) / det
        return (round(o1[0] + d1[0] * t, 9), round(o1[1] + d1[1] * t, 9))
    if isinstance(reference, AxisPoint):
        (o, d) = _line_of(context.axis(reference.axis))
        return (round(o[0] + d[0] * reference.along, 9), round(o[1] + d[1] * reference.along, 9))
    if isinstance(reference, GridRef):
        (o, _) = _line_of(context.axis(reference.axis))
        return (round(o[0], 9), round(o[1], 9))
    if isinstance(reference, HostAlong):
        host = context.hosts.get(reference.host)
        if host is None:
            raise ReferenceError(f"unknown host {reference.host!r}")
        x, z = host.point(reference.along, reference.across)
        return (round(x, 9), round(z, 9))
    raise ReferenceError(f"{reference!r} is not a plan reference")


def resolve_elevation(reference: ElevationReference, context: ReferenceContext) -> tuple[str, float]:
    """The symbolic form the compiler binds: (level datum id, offset). Never a number."""

    if isinstance(reference, LevelRef):
        level_id, offset = reference.level, 0.0
    elif isinstance(reference, OffsetFrom):
        level_id, offset = reference.level, reference.offset
    else:
        raise ReferenceError(f"{reference!r} is not an elevation reference")
    if context.levels is not None and level_id not in context.level_ids():
        raise ReferenceError(f"unknown level {level_id!r}")
    return level_id, offset


# ---------------------------------------------------------------- lowering (adapter, to be retired with reference-reading producers)
