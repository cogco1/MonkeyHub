"""Opening solver (P092): window and door types filling hosted voids.

A type carries only its own proportions (frame width and depth, how far
the frame stands proud of the exterior face, glazing or leaf thickness
and offsets). Where the opening is, how large, and on which storey come
from the ``HostedVoid`` the wall solver granted — the type never carries
a project coordinate. Every member binds ``base_level`` to the storey
datum and rides its own seat height as ``base_offset``; the wall's cut
result is the assembly's HOST_CUT member, so the compiler sees the void,
the frame and the infill as one hosted assembly at ENVELOPE maturity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from archflow.capabilities.wall_solver import HostedVoid, OpeningKind
from archflow.project.refs import require_identifier
from archflow.state.geometry_program import (
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    DatumBinding,
    DetailMaturity,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    HostedAssembly,
    LengthUnit,
)

_M = LengthUnit.METER
INTERFACE_INSIDE_OUTSIDE = "interface:inside-to-outside"


class OpeningSolverError(ValueError):
    """Typed failure of the opening type contracts."""


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpeningSolverError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise OpeningSolverError(f"{field} must be finite")
    return number


def _positive(value: object, field: str) -> float:
    number = _finite(value, field)
    if number <= 0.0:
        raise OpeningSolverError(f"{field} must be positive")
    return number


def _non_negative(value: object, field: str) -> float:
    number = _finite(value, field)
    if number < 0.0:
        raise OpeningSolverError(f"{field} must not be negative")
    return number


@dataclass(frozen=True, slots=True)
class WindowType:
    """Proportions of one window family; no position, no level, no size."""

    type_id: str
    frame_width: float
    frame_depth: float
    frame_projection: float
    glazing_thickness: float
    glazing_offset: float

    SCHEMA = "WindowType@1"

    def __post_init__(self) -> None:
        require_identifier(self.type_id, "window type_id")
        for field in ("frame_width", "frame_depth", "glazing_thickness"):
            object.__setattr__(self, field, _positive(getattr(self, field), f"{self.type_id} {field}"))
        object.__setattr__(self, "frame_projection", _finite(self.frame_projection, f"{self.type_id} frame_projection"))
        object.__setattr__(self, "glazing_offset", _finite(self.glazing_offset, f"{self.type_id} glazing_offset"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "type_id": self.type_id, "frame_width": self.frame_width,
            "frame_depth": self.frame_depth, "frame_projection": self.frame_projection,
            "glazing_thickness": self.glazing_thickness, "glazing_offset": self.glazing_offset,
        }


@dataclass(frozen=True, slots=True)
class DoorType:
    """Proportions of one door family: frame, leaves and their clearances."""

    type_id: str
    frame_width: float
    frame_depth: float
    frame_projection: float
    leaf_thickness: float
    leaf_offset: float
    leaf_count: int
    leaf_gap: float
    clearance_bottom: float
    clearance_top: float

    SCHEMA = "DoorType@1"

    def __post_init__(self) -> None:
        require_identifier(self.type_id, "door type_id")
        for field in ("frame_width", "frame_depth", "leaf_thickness"):
            object.__setattr__(self, field, _positive(getattr(self, field), f"{self.type_id} {field}"))
        for field in ("leaf_gap", "clearance_bottom", "clearance_top"):
            object.__setattr__(self, field, _non_negative(getattr(self, field), f"{self.type_id} {field}"))
        object.__setattr__(self, "frame_projection", _finite(self.frame_projection, f"{self.type_id} frame_projection"))
        object.__setattr__(self, "leaf_offset", _finite(self.leaf_offset, f"{self.type_id} leaf_offset"))
        if isinstance(self.leaf_count, bool) or not isinstance(self.leaf_count, int) or self.leaf_count not in (1, 2):
            raise OpeningSolverError(f"{self.type_id} leaf_count must be 1 or 2")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "type_id": self.type_id, "frame_width": self.frame_width,
            "frame_depth": self.frame_depth, "frame_projection": self.frame_projection,
            "leaf_thickness": self.leaf_thickness, "leaf_offset": self.leaf_offset,
            "leaf_count": self.leaf_count, "leaf_gap": self.leaf_gap,
            "clearance_bottom": self.clearance_bottom, "clearance_top": self.clearance_top,
        }


@dataclass(frozen=True, slots=True)
class OpeningSolution:
    opening_id: str
    operations: tuple[GeometryOperation, ...]
    datum_bindings: tuple[DatumBinding, ...]
    assembly: HostedAssembly

    @property
    def object_ids(self) -> tuple[str, ...]:
        return tuple(sorted(o for op in self.operations for o in op.output_object_ids))


def _box(void: HostedVoid, op_id: str, binding_id: str, along0: float, along1: float,
         across0: float, across1: float, seat: float, height: float) -> GeometryOperation:
    if along1 <= along0 or across1 <= across0 or height <= 0.0:
        raise OpeningSolverError(f"{op_id}: degenerate member (opening too small for its type)")
    profile = void.wall.plan_rectangle(along0, along1, across0, across1)
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.EXTRUSION,
        output_object_ids=(f"obj-{op_id}",),
        input_object_ids=(),
        frame_id=void.wall.frame_id,
        parameters=(
            GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=round(seat, 9), unit=_M),
            GeometryParameter.create(name="profile", kind=GeometryParameterKind.POINTS3,
                                     value=[[round(float(v), 9) for v in p] for p in profile], unit=_M),
            GeometryParameter.create(name="vector", kind=GeometryParameterKind.VECTOR3, value=[0.0, round(height, 9), 0.0], unit=_M),
        ),
        semantic_binding_ids=(binding_id,),
    )


def _arrayed(void: HostedVoid, members: tuple[GeometryOperation, ...], binding_id: str) -> tuple[GeometryOperation, ...]:
    """P099: one member definition, ``void.count`` placements along the wall."""

    if void.count <= 1:
        return ()
    step = void.step_vector
    return tuple(
        GeometryOperation(
            op_id=f"{member.op_id}-array",
            kind=GeometryOperationKind.ARRAY,
            output_object_ids=(f"{member.output_object_ids[0]}-array",),
            input_object_ids=(member.output_object_ids[0],),
            frame_id=void.wall.frame_id,
            parameters=(
                GeometryParameter.create(name="count", kind=GeometryParameterKind.INTEGER, value=void.count),
                GeometryParameter.create(name="step", kind=GeometryParameterKind.VECTOR3,
                                         value=[round(v, 9) for v in step], unit=_M),
            ),
            semantic_binding_ids=(binding_id,),
        )
        for member in members
    )


def _placed(member: GeometryOperation, void: HostedVoid) -> str:
    """The object id the assembly names: the array when placed N times."""

    base = member.output_object_ids[0]
    return f"{base}-array" if void.count > 1 else base


def _bind(op: GeometryOperation, datum_id: str) -> DatumBinding:
    return DatumBinding(binding_id=f"bind-{op.op_id}", datum_id=datum_id, op_id=op.op_id, parameter_name="base_level")


def _assembly(void: HostedVoid, kind: AssemblyKind, members: dict[AssemblyRole, tuple[str, ...]], binding_id: str, interface_ref: str) -> HostedAssembly:
    members[AssemblyRole.HOST_CUT] = (void.aperture_object_id,)
    return HostedAssembly(
        assembly_id=f"{void.opening_id}-assembly",
        kind=kind,
        host_object_id=void.host_object_id,
        host_socket_id=f"void-{void.opening_id}",
        members=tuple(AssemblyMember(role, ids) for role, ids in sorted(members.items(), key=lambda item: item[0].value)),
        interface_refs=(interface_ref,),
        semantic_binding_ids=(binding_id,),
        maturity=DetailMaturity.ENVELOPE,
    )


def solve_window(void: HostedVoid, window: WindowType, *, binding_id: str, interface_ref: str = INTERFACE_INSIDE_OUTSIDE) -> OpeningSolution:
    """Four frame members on the void's edge and one pane inside them.

    ``interface_ref`` names the spatial relation the opening serves (a
    connection's relationship ref in the selected spatial option).
    """

    if not isinstance(void, HostedVoid) or not isinstance(window, WindowType):
        raise OpeningSolverError("solve_window needs a HostedVoid and a WindowType")
    if void.kind is not OpeningKind.WINDOW:
        raise OpeningSolverError(f"void {void.opening_id} was requested as a {void.kind.value}, not a window")
    require_identifier(binding_id, "binding_id")
    fw = window.frame_width
    if 2.0 * fw >= void.width or 2.0 * fw >= void.height:
        raise OpeningSolverError(
            f"window type {window.type_id} frame {fw} does not fit void {void.opening_id} "
            f"({void.width} x {void.height})"
        )
    a0, a1, sill, head = void.along0, void.along1, void.sill, void.head
    f0, f1 = -window.frame_projection, -window.frame_projection + window.frame_depth
    oid = void.opening_id
    frame = (
        _box(void, f"frame-{oid}-bottom", binding_id, a0, a1, f0, f1, sill, fw),
        _box(void, f"frame-{oid}-left", binding_id, a0, a0 + fw, f0, f1, sill, head - sill),
        _box(void, f"frame-{oid}-right", binding_id, a1 - fw, a1, f0, f1, sill, head - sill),
        _box(void, f"frame-{oid}-top", binding_id, a0, a1, f0, f1, head - fw, fw),
    )
    pane = _box(void, f"glazing-{oid}", binding_id, a0 + fw, a1 - fw,
                window.glazing_offset, window.glazing_offset + window.glazing_thickness,
                sill + fw, head - sill - 2.0 * fw)
    members = (*frame, pane)
    operations = tuple(sorted(members + _arrayed(void, members, binding_id), key=lambda o: o.op_id))
    datum = void.wall.base_level_datum_id
    assembly = _assembly(void, AssemblyKind.WINDOW, {
        AssemblyRole.FRAME: tuple(sorted(_placed(o, void) for o in frame)),
        AssemblyRole.GLAZING: (_placed(pane, void),),
    }, binding_id, interface_ref)
    return OpeningSolution(
        opening_id=oid, operations=operations,
        datum_bindings=tuple(sorted((_bind(o, datum) for o in members), key=lambda b: b.binding_id)),
        assembly=assembly,
    )


def solve_door(void: HostedVoid, door: DoorType, *, binding_id: str, interface_ref: str = INTERFACE_INSIDE_OUTSIDE) -> OpeningSolution:
    """Jambs and head on the void's edge; one or two leaves with clearances."""

    if not isinstance(void, HostedVoid) or not isinstance(door, DoorType):
        raise OpeningSolverError("solve_door needs a HostedVoid and a DoorType")
    if void.kind is not OpeningKind.DOOR:
        raise OpeningSolverError(f"void {void.opening_id} was requested as a {void.kind.value}, not a door")
    require_identifier(binding_id, "binding_id")
    fw = door.frame_width
    clear = fw + door.clearance_bottom + door.clearance_top
    if 2.0 * fw + (door.leaf_count - 1) * 2.0 * door.leaf_gap >= void.width or clear >= void.height:
        raise OpeningSolverError(
            f"door type {door.type_id} does not fit void {void.opening_id} ({void.width} x {void.height})"
        )
    a0, a1, sill, head = void.along0, void.along1, void.sill, void.head
    f0, f1 = -door.frame_projection, -door.frame_projection + door.frame_depth
    oid = void.opening_id
    frame = (
        _box(void, f"door-frame-{oid}-left", binding_id, a0, a0 + fw, f0, f1, sill, head - sill),
        _box(void, f"door-frame-{oid}-right", binding_id, a1 - fw, a1, f0, f1, sill, head - sill),
        _box(void, f"door-frame-{oid}-top", binding_id, a0, a1, f0, f1, head - fw, fw),
    )
    l0, l1 = door.leaf_offset, door.leaf_offset + door.leaf_thickness
    leaf_seat = sill + door.clearance_bottom
    leaf_height = (head - fw - door.clearance_top) - leaf_seat
    if door.leaf_count == 1:
        spans = ((a0 + fw, a1 - fw),)
    else:
        centre = (a0 + a1) / 2.0
        spans = ((a0 + fw, centre - door.leaf_gap), (centre + door.leaf_gap, a1 - fw))
    leaves = tuple(
        _box(void, f"door-leaf-{oid}-{index}", binding_id, s0, s1, l0, l1, leaf_seat, leaf_height)
        for index, (s0, s1) in enumerate(spans)
    )
    members = (*frame, *leaves)
    operations = tuple(sorted(members + _arrayed(void, members, binding_id), key=lambda o: o.op_id))
    datum = void.wall.base_level_datum_id
    assembly = _assembly(void, AssemblyKind.DOOR, {
        AssemblyRole.FRAME: tuple(sorted(_placed(o, void) for o in frame)),
        AssemblyRole.LEAF: tuple(sorted(_placed(o, void) for o in leaves)),
    }, binding_id, interface_ref)
    return OpeningSolution(
        opening_id=oid, operations=operations,
        datum_bindings=tuple(sorted((_bind(o, datum) for o in members), key=lambda b: b.binding_id)),
        assembly=assembly,
    )


def solve_openings(voids: tuple[HostedVoid, ...], types: dict[str, WindowType | DoorType], *, binding_ids: dict[str, str],
                   interface_refs: dict[str, str] | None = None) -> tuple[OpeningSolution, ...]:
    """Fill every hosted void with the type its opening id maps to."""

    solutions = []
    for void in voids:
        kind = types.get(void.opening_id)
        if kind is None:
            raise OpeningSolverError(f"void {void.opening_id} has no type to fill it")
        binding = binding_ids.get(void.opening_id)
        if binding is None:
            raise OpeningSolverError(f"void {void.opening_id} has no semantic binding")
        ref = (interface_refs or {}).get(void.opening_id, INTERFACE_INSIDE_OUTSIDE)
        if isinstance(kind, WindowType):
            solutions.append(solve_window(void, kind, binding_id=binding, interface_ref=ref))
        elif isinstance(kind, DoorType):
            solutions.append(solve_door(void, kind, binding_id=binding, interface_ref=ref))
        else:
            raise OpeningSolverError(f"void {void.opening_id}: unknown type {type(kind).__name__}")
    return tuple(sorted(solutions, key=lambda s: s.opening_id))
