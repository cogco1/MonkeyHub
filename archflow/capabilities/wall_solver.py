"""Wall element solver (P092): a wall by location line, thickness, height
and storey datum, hosting openings the compiler cuts.

The wall is one solid; every opening is a void request the wall hosts
(a through-cut tool consumed by one boolean difference) and re-publishes
as a ``HostedVoid`` for the opening solver to fill. Each void is also
materialized as an aperture volume — the intersection of the wall and
its tool — which is the HOST_CUT member of the opening's assembly: the
kernel's opening evidence (never the residual wall), exported hidden. Levels never appear
as numbers in the operations: the wall and every tool bind ``base_level``
to the storey datum, and an opening's sill rides on it as ``base_offset``.

Zero typology constants: every dimension is an input. Exclusions (from
portico or roof obligations) are refused at solve time, so an opening
behind a pediment is unrepresentable rather than repaired afterwards.
Coordinates: program frame, Y up; plan is (X, Z). The reference line
is the exterior face; thickness grows toward ``normal = (dz, -dx)``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from archflow.project.refs import require_identifier
from archflow.state.geometry_program import (
    DatumBinding,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    LengthUnit,
)
from archflow.contracts.fields import (
    number,
    positive,
)

Bounds = tuple[tuple[float, float, float], tuple[float, float, float]]
_M = LengthUnit.METER


class WallSolverError(ValueError):
    """Typed failure of the wall element contracts."""


def _finite(value: object, field: str) -> float:
    """The owned finite-number rule, typed for this module's callers."""

    try:
        return number(value, field)
    except ValueError as exc:
        raise WallSolverError(str(exc)) from exc


def _positive(value: object, field: str) -> float:
    """The owned positive-number rule, typed for this module's callers."""

    try:
        return positive(value, field)
    except ValueError as exc:
        raise WallSolverError(str(exc)) from exc


def _plan(value: object, field: str) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise WallSolverError(f"{field} must be a plan (X, Z) pair")
    return (_finite(value[0], field), _finite(value[1], field))


def _points(name: str, points) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.POINTS3,
        value=[[round(float(v), 9) for v in p] for p in points],
        unit=_M,
    )


def _vector(v) -> GeometryParameter:
    return GeometryParameter.create(
        name="vector",
        kind=GeometryParameterKind.VECTOR3,
        value=[round(float(x), 9) for x in v],
        unit=_M,
    )


def _number(name: str, value: float) -> GeometryParameter:
    return GeometryParameter.create(
        name=name, kind=GeometryParameterKind.NUMBER, value=round(float(value), 9), unit=_M
    )


@dataclass(frozen=True, slots=True)
class WallElement:
    """One wall: reference line, thickness, height above its storey datum."""

    wall_id: str
    origin: tuple[float, float]
    direction: tuple[float, float]
    length: float
    thickness: float
    height: float
    base_level_datum_id: str
    frame_id: str
    binding_id: str

    SCHEMA = "WallElement@1"

    def __post_init__(self) -> None:
        require_identifier(self.wall_id, "wall_id")
        require_identifier(self.base_level_datum_id, "base_level_datum_id")
        require_identifier(self.frame_id, "frame_id")
        require_identifier(self.binding_id, "binding_id")
        object.__setattr__(self, "origin", _plan(self.origin, "wall origin"))
        dx, dz = _plan(self.direction, "wall direction")
        norm = math.hypot(dx, dz)
        if norm == 0.0:
            raise WallSolverError("wall direction must be nonzero")
        object.__setattr__(self, "direction", (dx / norm, dz / norm))
        object.__setattr__(self, "length", _positive(self.length, "wall length"))
        object.__setattr__(self, "thickness", _positive(self.thickness, "wall thickness"))
        object.__setattr__(self, "height", _positive(self.height, "wall height"))

    @property
    def normal(self) -> tuple[float, float]:
        dx, dz = self.direction
        return (dz, -dx)

    def plan_point(self, along: float, across: float) -> tuple[float, float]:
        """Plan (X, Z) at ``along`` the line and ``across`` toward the normal."""

        dx, dz = self.direction
        nx, nz = self.normal
        return (
            self.origin[0] + dx * along + nx * across,
            self.origin[1] + dz * along + nz * across,
        )

    def plan_rectangle(self, along0: float, along1: float, across0: float, across1: float):
        """Closed plan polygon (Y = 0) for an extrusion profile."""

        corners = (
            self.plan_point(along0, across0),
            self.plan_point(along1, across0),
            self.plan_point(along1, across1),
            self.plan_point(along0, across1),
        )
        return [(x, 0.0, z) for x, z in corners]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "wall_id": self.wall_id,
            "origin": list(self.origin),
            "direction": list(self.direction),
            "length": self.length,
            "thickness": self.thickness,
            "height": self.height,
            "base_level_datum_id": self.base_level_datum_id,
            "frame_id": self.frame_id,
            "binding_id": self.binding_id,
        }


class OpeningKind(StrEnum):
    WINDOW = "window"
    DOOR = "door"


@dataclass(frozen=True, slots=True)
class OpeningRequest:
    """A void an opening asks its wall to host: position, size, sill, head."""

    opening_id: str
    kind: OpeningKind
    along: float
    width: float
    sill: float
    head: float
    binding_id: str
    count: int = 1
    step: float = 0.0

    SCHEMA = "OpeningRequest@1"

    def __post_init__(self) -> None:
        require_identifier(self.opening_id, "opening_id")
        if not isinstance(self.kind, OpeningKind):
            raise WallSolverError("opening kind must be OpeningKind")
        require_identifier(self.binding_id, "opening binding_id")
        object.__setattr__(self, "along", _finite(self.along, f"opening {self.opening_id} along"))
        object.__setattr__(self, "width", _positive(self.width, f"opening {self.opening_id} width"))
        object.__setattr__(self, "sill", _finite(self.sill, f"opening {self.opening_id} sill"))
        object.__setattr__(self, "head", _finite(self.head, f"opening {self.opening_id} head"))
        if self.sill < 0.0:
            raise WallSolverError(f"opening {self.opening_id} sill below the wall base")
        if self.head <= self.sill:
            raise WallSolverError(f"opening {self.opening_id} head must be above its sill")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise WallSolverError(f"opening {self.opening_id} count must be a positive integer")
        object.__setattr__(self, "step", _finite(self.step, f"opening {self.opening_id} step"))
        if self.count > 1 and self.step < self.width:
            raise WallSolverError(f"opening {self.opening_id} instances overlap: step {self.step} < width {self.width}")

    @property
    def along0(self) -> float:
        """Start of the first instance."""

        return self.along - self.width / 2.0

    @property
    def along1(self) -> float:
        """End of the first instance."""

        return self.along + self.width / 2.0

    def instances(self) -> tuple[tuple[float, float], ...]:
        """(along0, along1) of every placement, first to last."""

        return tuple(
            (self.along0 + index * self.step, self.along1 + index * self.step)
            for index in range(self.count)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "opening_id": self.opening_id,
            "kind": self.kind.value,
            "along": self.along,
            "width": self.width,
            "sill": self.sill,
            "head": self.head,
            "binding_id": self.binding_id,
            "count": self.count,
            "step": self.step,
        }


@dataclass(frozen=True, slots=True)
class HostedVoid:
    """What the wall grants an opening: its aperture in wall coordinates.

    ``aperture_object_id`` is the wall-∩-tool volume (the assembly's
    HOST_CUT member); ``cut_object_id`` the residual wall it leaves.
    """

    opening_id: str
    kind: OpeningKind
    wall: WallElement
    host_object_id: str
    cut_object_id: str
    tool_object_id: str
    aperture_object_id: str
    along0: float
    along1: float
    sill: float
    head: float
    count: int = 1
    step: float = 0.0
    aperture_object_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.aperture_object_ids:
            object.__setattr__(self, "aperture_object_ids", (self.aperture_object_id,))

    @property
    def width(self) -> float:
        return self.along1 - self.along0

    @property
    def step_vector(self) -> tuple[float, float, float]:
        """Program-frame vector between consecutive placements."""

        dx, dz = self.wall.direction
        return (dx * self.step, 0.0, dz * self.step)

    @property
    def height(self) -> float:
        return self.head - self.sill

    def to_dict(self) -> dict[str, object]:
        return {
            "opening_id": self.opening_id,
            "kind": self.kind.value,
            "wall_id": self.wall.wall_id,
            "host_object_id": self.host_object_id,
            "cut_object_id": self.cut_object_id,
            "tool_object_id": self.tool_object_id,
            "aperture_object_id": self.aperture_object_id,
            "aperture_object_ids": list(self.aperture_object_ids),
            "along0": self.along0,
            "along1": self.along1,
            "sill": self.sill,
            "head": self.head,
            "count": self.count,
            "step": self.step,
        }


@dataclass(frozen=True, slots=True)
class WallSolution:
    wall: WallElement
    operations: tuple[GeometryOperation, ...]
    datum_bindings: tuple[DatumBinding, ...]
    voids: tuple[HostedVoid, ...]
    host_object_id: str
    cut_object_id: str | None

    @property
    def realized_object_id(self) -> str:
        """The object the wall is realized as: the cut result, else the solid."""

        return self.cut_object_id or self.host_object_id


def _void_bounds(wall: WallElement, opening: OpeningRequest, base_elevation: float, margin: float, along0: float | None = None, along1: float | None = None) -> Bounds:
    a0 = opening.along0 if along0 is None else along0
    a1 = opening.along1 if along1 is None else along1
    corners = wall.plan_rectangle(a0, a1, -margin, wall.thickness + margin)
    xs = [c[0] for c in corners]
    zs = [c[2] for c in corners]
    return (
        (min(xs), base_elevation + opening.sill, min(zs)),
        (max(xs), base_elevation + opening.head, max(zs)),
    )


# Contact is not intersection: a void whose sill sits on a landing's top shares a face with it.
# The stage-5 convention counts an embed up to 25 mm as contact; only a deeper mutual
# penetration on every axis is an overlap.
CONTACT_TOLERANCE_M = 0.025


def _overlaps(a: Bounds, b: Bounds, tolerance: float = CONTACT_TOLERANCE_M) -> bool:
    return all(a[0][i] + tolerance < b[1][i] and b[0][i] + tolerance < a[1][i] for i in range(3))


def solve_wall(
    wall: WallElement,
    openings: tuple[OpeningRequest, ...] = (),
    *,
    exclusions: tuple[Bounds, ...] = (),
    base_elevation: float | None = None,
    cut_margin: float = 0.05,
) -> WallSolution:
    """Enumerate the wall solid, a tool and an aperture per opening, and the cut.

    ``cut_margin`` is how far every tool overshoots the wall faces so no
    boolean ever meets a coincident face (CAD hosts merge faces closer
    than their document tolerance; 1 cm was merged by Rhino, 5 cm is not).
    ``exclusions`` are realized bounds (program frame) the wall may not
    open into — portico roofs, pediments, abutments handed over by the
    structure seat. Checking them needs the storey's elevation, which is
    an evaluation input (``base_elevation``, the bound datum's value),
    never authored into an operation.
    """

    if not isinstance(wall, WallElement):
        raise WallSolverError("wall must be a WallElement")
    if not isinstance(openings, tuple) or any(not isinstance(o, OpeningRequest) for o in openings):
        raise WallSolverError("openings must be OpeningRequest items")
    margin = _positive(cut_margin, "cut_margin")
    if exclusions and base_elevation is None:
        raise WallSolverError("exclusions need the storey elevation to be evaluated")
    ids = [o.opening_id for o in openings]
    if len(set(ids)) != len(ids):
        raise WallSolverError("opening ids must be unique")
    for opening in openings:
        for a0, a1 in opening.instances():
            if a0 < 0.0 or a1 > wall.length:
                raise WallSolverError(f"opening {opening.opening_id} lies outside the wall length")
        if opening.head > wall.height:
            raise WallSolverError(f"opening {opening.opening_id} head is above the wall top")
    ordered = tuple(sorted(openings, key=lambda o: o.opening_id))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            for a0, a1 in a.instances():
                for b0, b1 in b.instances():
                    if a0 < b1 and b0 < a1 and a.sill < b.head and b.sill < a.head:
                        raise WallSolverError(f"openings {a.opening_id} and {b.opening_id} overlap")
    if exclusions:
        for opening in ordered:
            for placement, (a0, a1) in enumerate(opening.instances()):
                box = _void_bounds(wall, opening, float(base_elevation), margin, a0, a1)
                for index, exclusion in enumerate(exclusions):
                    if _overlaps(box, exclusion):
                        raise WallSolverError(
                            f"opening {opening.opening_id} placement {placement} intersects exclusion {index}: "
                            f"void {box} overlaps {exclusion}"
                        )

    host = f"obj-{wall.wall_id}"
    operations = [
        GeometryOperation(
            op_id=wall.wall_id,
            kind=GeometryOperationKind.EXTRUSION,
            output_object_ids=(host,),
            input_object_ids=(),
            frame_id=wall.frame_id,
            parameters=(
                _points("profile", wall.plan_rectangle(0.0, wall.length, 0.0, wall.thickness)),
                _vector((0.0, wall.height, 0.0)),
            ),
            semantic_binding_ids=(wall.binding_id,),
        )
    ]
    bindings = [
        DatumBinding(
            binding_id=f"bind-{wall.wall_id}",
            datum_id=wall.base_level_datum_id,
            op_id=wall.wall_id,
            parameter_name="base_level",
        )
    ]
    voids: list[HostedVoid] = []
    cut_id = f"{wall.wall_id}-cut" if ordered else None
    cut_object = f"obj-{cut_id}" if cut_id else None
    tool_objects = []
    for opening in ordered:
        # P099: one tool and one aperture per placement. Booleans consume
        # solids, never block instances, so the void geometry is repeated
        # here; the visible members of the opening are what get arrayed.
        placements = opening.instances()
        apertures = []
        first_tool = None
        for index, (a0, a1) in enumerate(placements):
            suffix = "" if opening.count == 1 else f"-{index}"
            op_id = f"{wall.wall_id}-void-{opening.opening_id}{suffix}"
            tool = f"obj-{op_id}"
            first_tool = first_tool or tool
            tool_objects.append(tool)
            operations.append(
                GeometryOperation(
                    op_id=op_id,
                    kind=GeometryOperationKind.EXTRUSION,
                    output_object_ids=(tool,),
                    input_object_ids=(),
                    frame_id=wall.frame_id,
                    parameters=(
                        # a tool that would share the wall's bottom or top face
                        # overshoots it by the margin: booleans never see coplanar faces
                        _number("base_offset", opening.sill if opening.sill > 0.0 else -margin),
                        _points("profile", wall.plan_rectangle(a0, a1, -margin, wall.thickness + margin)),
                        _vector((0.0, (opening.head - opening.sill) + (margin if opening.sill <= 0.0 else 0.0) + (margin if opening.head >= wall.height else 0.0), 0.0)),
                    ),
                    semantic_binding_ids=(opening.binding_id,),
                )
            )
            bindings.append(
                DatumBinding(
                    binding_id=f"bind-{op_id}",
                    datum_id=wall.base_level_datum_id,
                    op_id=op_id,
                    parameter_name="base_level",
                )
            )
            aperture_id = f"{wall.wall_id}-aperture-{opening.opening_id}{suffix}"
            aperture = f"obj-{aperture_id}"
            apertures.append(aperture)
            operations.append(
                GeometryOperation(
                    op_id=aperture_id,
                    kind=GeometryOperationKind.BOOLEAN_INTERSECTION,
                    output_object_ids=(aperture,),
                    input_object_ids=(host, tool),
                    frame_id=wall.frame_id,
                    parameters=(
                        GeometryParameter.create(
                            name="hidden_for_inspection", kind=GeometryParameterKind.BOOLEAN, value=True,
                        ),
                    ),
                    semantic_binding_ids=(opening.binding_id,),
                )
            )
        voids.append(
            HostedVoid(
                opening_id=opening.opening_id, kind=opening.kind, wall=wall,
                host_object_id=host, cut_object_id=cut_object or host, tool_object_id=first_tool,
                aperture_object_id=apertures[0], aperture_object_ids=tuple(apertures),
                along0=opening.along0, along1=opening.along1, sill=opening.sill, head=opening.head,
                count=opening.count, step=opening.step,
            )
        )
    if cut_id:
        inputs = (host, *tool_objects)
        operations.append(
            GeometryOperation(
                op_id=cut_id,
                kind=GeometryOperationKind.BOOLEAN_DIFFERENCE,
                output_object_ids=(cut_object,),
                input_object_ids=inputs,
                frame_id=wall.frame_id,
                parameters=(
                    GeometryParameter.create(
                        name="base_index", kind=GeometryParameterKind.INTEGER,
                        value=sorted(inputs).index(host),
                    ),
                ),
                semantic_binding_ids=(wall.binding_id,),
            )
        )
    return WallSolution(
        wall=wall,
        operations=tuple(sorted(operations, key=lambda o: o.op_id)),
        datum_bindings=tuple(sorted(bindings, key=lambda b: b.binding_id)),
        voids=tuple(voids),
        host_object_id=host,
        cut_object_id=cut_object,
    )
