"""Reference-reading element producers (P100 / P102) — construction by relation.

Every producer here takes an entity's *references* and *parameters*, never a
coordinate it computed itself: plan positions come from
``resolve_plan`` (grids, hosts), elevations stay symbolic as datum bindings
the compiler resolves (P090 / M096 / P098), and every interface between two
elements is one shared datum published by the supporting element and bound
by the supported one. There is no unexplained epsilon: an overlap exists
only as a declared ``engagement`` parameter with a depth, recorded on the
produced relation so the verifier checks the declared interval instead of
reporting a clash.

The portico vertical slice — column → capital → entablature → pediment —
is the reference case: the column array publishes ``<id>-top``; the
capital binds it and publishes ``<id>-top``; the entablature binds the
capital top; the pediment binds the entablature top. Change the column
height and the whole chain recompiles from the datums; nothing is patched.

These producers are the canonical set; the runner's private producers are
the parallel abstraction they retire once the runner reads references.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from archflow.capabilities.reference_resolver import (
    HostLine,
    ReferenceContext,
    ReferenceError,
    parse_reference,
    resolve_elevation,
    resolve_plan,
)
from archflow.capabilities.wall_solver import OpeningKind, OpeningRequest, WallElement, solve_wall
from archflow.project.refs import require_identifier
from archflow.state.geometry_program import (
    DatumBinding,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)

_M = LengthUnit.METER
FRAME_ID = "building-local"


class ElementProducerError(ValueError):
    """Typed failure of a reference-reading producer."""


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ElementProducerError(f"{label} must be a finite number")
    return float(value)


def _positive(value: object, label: str) -> float:
    number = _finite(value, label)
    if number <= 0.0:
        raise ElementProducerError(f"{label} must be positive")
    return number


@dataclass(frozen=True, slots=True)
class ElementRow:
    """One element as the state record carries it: id, component, producer, references, parameters."""

    element_id: str
    component_id: str
    producer: str
    references: Mapping[str, Any]
    params: Mapping[str, Any]
    basis_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.element_id, "element_id")
        require_identifier(self.component_id, "component_id")
        require_identifier(self.producer, "producer")
        object.__setattr__(self, "references", dict(self.references))
        object.__setattr__(self, "params", dict(self.params))

    @property
    def binding_id(self) -> str:
        return f"binding-{self.component_id}"


@dataclass(frozen=True, slots=True)
class ProducedRelation:
    """A relation the producer materialized by construction, with what it declared."""

    relation_id: str
    kind: str
    subject: str
    object: str
    datum_id: str | None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {"relation_id": self.relation_id, "kind": self.kind, "subject": self.subject, "object": self.object, "datum_id": self.datum_id, "parameters": dict(self.parameters)}


@dataclass(frozen=True, slots=True)
class ProducedElement:
    operations: tuple[GeometryOperation, ...]
    bindings: tuple[DatumBinding, ...]
    datums: tuple[InterfaceDatum, ...] = ()
    relations: tuple[ProducedRelation, ...] = ()
    host_line: HostLine | None = None


@dataclass
class ProductionContext:
    """What a producer may read: references (grids, levels, hosts), datums published so far, frame."""

    references: ReferenceContext
    published: dict[str, InterfaceDatum]
    frame_id: str = FRAME_ID

    def datum_value(self, datum_id: str) -> float:
        item = self.published.get(datum_id)
        if item is not None:
            import json as _json

            return float(_json.loads(item.value_json))
        levels = self.references.levels
        if levels is not None:
            for level in levels.levels:
                if level.level_id == datum_id:
                    return level.elevation
        raise ElementProducerError(f"unknown datum {datum_id!r}")


# ---------------------------------------------------------------- operation helpers (no coordinates invented here)
def _points(name: str, pts) -> GeometryParameter:
    return GeometryParameter.create(name=name, kind=GeometryParameterKind.POINTS3, value=[[round(float(c), 9) for c in p] for p in pts], unit=_M)


def _extrusion(op_id: str, profile, height: float, binding_id: str, frame_id: str, base_offset: float | None = None) -> GeometryOperation:
    params = [_points("profile", profile), GeometryParameter.create(name="vector", kind=GeometryParameterKind.VECTOR3, value=[0.0, round(height, 9), 0.0], unit=_M)]
    if base_offset is not None and base_offset != 0.0:
        params.insert(0, GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=round(base_offset, 9), unit=_M))
    return GeometryOperation(op_id=op_id, kind=GeometryOperationKind.EXTRUSION, output_object_ids=(f"obj-{op_id}",), input_object_ids=(), frame_id=frame_id,
                             parameters=tuple(params), semantic_binding_ids=(binding_id,))


def _bind(op_id: str, datum_id: str) -> DatumBinding:
    return DatumBinding(binding_id=f"bind-{op_id}", datum_id=datum_id, op_id=op_id, parameter_name="base_level")


def _level_datum(datum_id: str, published_by: str, value: float, basis: tuple[str, ...]) -> InterfaceDatum:
    return InterfaceDatum.create(datum_id=datum_id, kind=InterfaceDatumKind.LEVEL, published_by=published_by, value=round(value, 9), unit=_M, basis_refs=basis)


def _rect(cx: float, cz: float, half_w: float, half_d: float):
    return [(cx - half_w, 0.0, cz - half_d), (cx + half_w, 0.0, cz - half_d), (cx + half_w, 0.0, cz + half_d), (cx - half_w, 0.0, cz + half_d)]


def _circle(cx: float, cz: float, r: float, n: int):
    return [(cx + r * math.cos(2 * math.pi * i / n), 0.0, cz + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _base(row: ElementRow, context: ProductionContext) -> tuple[str, float]:
    """The element's base as (datum id, offset); a level, or a datum another element published."""

    base = row.references.get("base")
    if base is None:
        raise ElementProducerError(f"{row.element_id}: base reference required")
    if isinstance(base, Mapping) and "datum" in base:
        datum_id = str(base["datum"])
        if datum_id not in context.published and context.references.levels is not None and datum_id not in context.references.level_ids():
            raise ElementProducerError(f"{row.element_id}: base datum {datum_id!r} is not published yet (order the supporting element first)")
        return datum_id, _finite(base.get("offset", 0.0), f"{row.element_id} base offset")
    return resolve_elevation(parse_reference(base), context.references)


def _height(row: ElementRow, context: ProductionContext, base_datum: str) -> float:
    params = row.params
    if "height" in params:
        return _positive(params["height"], f"{row.element_id} height")
    top = row.references.get("top")
    if top is None:
        raise ElementProducerError(f"{row.element_id}: height or top reference required")
    if isinstance(top, Mapping) and "datum" in top:
        top_id = str(top["datum"])
    else:
        top_id, offset = resolve_elevation(parse_reference(top), context.references)
        if offset:
            raise ElementProducerError(f"{row.element_id}: a top reference cannot carry an offset")
    height = round(context.datum_value(top_id) - context.datum_value(base_datum), 9)
    if height <= 0.0:
        raise ElementProducerError(f"{row.element_id}: top {top_id!r} is not above base {base_datum!r}")
    return height


def _engagement(row: ElementRow, label: str) -> float:
    """A declared embed depth; zero unless the row says ENGAGEMENT."""

    engagement = row.params.get("engagement")
    if engagement is None:
        return 0.0
    if not isinstance(engagement, Mapping) or "depth" not in engagement:
        raise ElementProducerError(f"{row.element_id}: engagement must declare a depth")
    return _finite(engagement["depth"], f"{row.element_id} engagement depth {label}")


# ---------------------------------------------------------------- producers
def produce_column_array(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """N shafts on grid axes (or evenly along a line), base on a datum, publishing ``<id>-top``."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    height = _height(row, context, base_datum)
    radius = _positive(p["radius"], f"{row.element_id} radius")
    segments = int(p.get("segments", 24))
    axes = row.references.get("axes")
    facade = row.references.get("facade")
    if axes and facade:
        centres = [resolve_plan(parse_reference({"grid": [str(a), str(facade)]}), context.references) for a in axes]
    else:
        origin = resolve_plan(parse_reference(row.references["at"]), context.references)
        d = row.references.get("direction")
        if d is None:
            raise ElementProducerError(f"{row.element_id}: a line placement needs a direction axis")
        axis = context.references.axis(str(d))
        dx, _, dz = axis.direction
        norm = math.hypot(dx, dz)
        dx, dz = dx / norm, dz / norm
        count, spacing = int(p["count"]), _positive(p["spacing"], f"{row.element_id} spacing")
        centres = [(origin[0] + dx * (k - (count - 1) / 2.0) * spacing, origin[1] + dz * (k - (count - 1) / 2.0) * spacing) for k in range(count)]
    ops, bindings = [], []
    for k, (cx, cz) in enumerate(centres):
        op_id = f"{row.element_id}-{k}"
        ops.append(_extrusion(op_id, _circle(cx, cz, radius, segments), height, row.binding_id, context.frame_id, base_offset))
        bindings.append(_bind(op_id, base_datum))
    top_value = context.datum_value(base_datum) + base_offset + height
    top = _level_datum(f"{row.element_id}-top", f"obj-{row.element_id}-0", top_value, row.basis_refs)
    context.published[top.datum_id] = top
    return ProducedElement(tuple(ops), tuple(bindings), (top,), (ProducedRelation(f"{row.element_id}-stands-on", "support", base_datum, row.element_id, base_datum),), None)


def produce_capitals(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """One abacus per column, bound to the column top; an embed only as a declared engagement."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    depth = _engagement(row, "into column")
    height = _positive(p["height"], f"{row.element_id} height")
    half = _positive(p["half_extent"], f"{row.element_id} half_extent")
    columns = str(row.references["columns"])
    centres = context.references.hosts.get(columns)
    axes, facade = row.references.get("axes"), row.references.get("facade")
    points = [resolve_plan(parse_reference({"grid": [str(a), str(facade)]}), context.references) for a in axes] if axes and facade else None
    if points is None:
        raise ElementProducerError(f"{row.element_id}: capitals are placed on the columns' axes (give axes + facade)")
    ops, bindings = [], []
    for k, (cx, cz) in enumerate(points):
        op_id = f"{row.element_id}-{k}"
        ops.append(_extrusion(op_id, _rect(cx, cz, half, half), height + depth, row.binding_id, context.frame_id, base_offset - depth))
        bindings.append(_bind(op_id, base_datum))
    top_value = context.datum_value(base_datum) + base_offset + height
    top = _level_datum(f"{row.element_id}-top", f"obj-{row.element_id}-0", top_value, row.basis_refs)
    context.published[top.datum_id] = top
    relation = ProducedRelation(f"{columns}-support-{row.element_id}", "support", columns, row.element_id, base_datum, {"engagement_depth": depth} if depth else {})
    return ProducedElement(tuple(ops), tuple(bindings), (top,), (relation,), None)


def produce_beam(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A rectangular beam between two plan references, bound to a support datum (entablature, lintel)."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    height = _height(row, context, base_datum)
    start = resolve_plan(parse_reference(row.references["from"]), context.references)
    end = resolve_plan(parse_reference(row.references["to"]), context.references)
    depth = _positive(p["depth"], f"{row.element_id} depth")
    overhang = _finite(p.get("end_overhang", 0.0), f"{row.element_id} end_overhang")
    dx, dz = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dz)
    if length <= 0.0:
        raise ElementProducerError(f"{row.element_id}: zero-length beam")
    ux, uz = dx / length, dz / length
    nx, nz = uz, -ux
    a = (start[0] - ux * overhang, start[1] - uz * overhang)
    b = (end[0] + ux * overhang, end[1] + uz * overhang)
    h = depth / 2.0
    profile = [(a[0] - nx * h, 0.0, a[1] - nz * h), (b[0] - nx * h, 0.0, b[1] - nz * h), (b[0] + nx * h, 0.0, b[1] + nz * h), (a[0] + nx * h, 0.0, a[1] + nz * h)]
    engage = _engagement(row, "into support")
    op = _extrusion(row.element_id, profile, height + engage, row.binding_id, context.frame_id, base_offset - engage)
    top = _level_datum(f"{row.element_id}-top", f"obj-{row.element_id}", context.datum_value(base_datum) + base_offset + height, row.basis_refs)
    context.published[top.datum_id] = top
    support = str(row.references.get("support", base_datum))
    relation = ProducedRelation(f"{support}-support-{row.element_id}", "support", support, row.element_id, base_datum, {"engagement_depth": engage} if engage else {})
    return ProducedElement((op,), (_bind(row.element_id, base_datum),), (top,), (relation,), HostLine(start, (ux, uz)))


def produce_pediment(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A triangular tympanum on the entablature top, spanning two plan references, rising by a parameter."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    start = resolve_plan(parse_reference(row.references["from"]), context.references)
    end = resolve_plan(parse_reference(row.references["to"]), context.references)
    rise = _positive(p["rise"], f"{row.element_id} rise")
    thickness = _positive(p["thickness"], f"{row.element_id} thickness")
    dx, dz = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dz)
    ux, uz = dx / length, dz / length
    nx, nz = uz, -ux
    apex = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    # a vertical triangular profile lofted through the thickness: as two triangles (front and back faces)
    front = [(start[0], 0.0, start[1]), (end[0], 0.0, end[1]), (apex[0], rise, apex[1])]
    back = [(x + nx * thickness, y, z + nz * thickness) for x, y, z in front]
    op = GeometryOperation(op_id=row.element_id, kind=GeometryOperationKind.LOFT, output_object_ids=(f"obj-{row.element_id}",), input_object_ids=(), frame_id=context.frame_id, parameters=(
        GeometryParameter.create(name="cap_ends", kind=GeometryParameterKind.BOOLEAN, value=True),
        GeometryParameter.create(name="loft_type", kind=GeometryParameterKind.TEXT, value="straight"),
        GeometryParameter.create(name="profile_basis", kind=GeometryParameterKind.TEXT, value="polyline"),
        GeometryParameter.create(name="profile_size", kind=GeometryParameterKind.INTEGER, value=3),
        _points("profiles", front + back),
    ) + ((GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=round(base_offset, 9), unit=_M),) if base_offset else ()),
        semantic_binding_ids=(row.binding_id,))
    support = str(row.references.get("support", base_datum))
    return ProducedElement((op,), (_bind(row.element_id, base_datum),), (), (ProducedRelation(f"{support}-support-{row.element_id}", "support", support, row.element_id, base_datum),), None)


def produce_wall(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A wall between two plan references hosting openings placed along it (P092 solver)."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    if base_offset:
        raise ElementProducerError(f"{row.element_id}: a wall stands on a level, not on an offset")
    height = _height(row, context, base_datum)
    line = row.references["line"]
    start = resolve_plan(parse_reference(line["from"]), context.references)
    end = resolve_plan(parse_reference(line["to"]), context.references)
    dx, dz = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dz)
    direction = (dx / length, dz / length)
    origin = start
    inward = line.get("inward")
    if inward is not None:
        nx, nz = direction[1], -direction[0]
        if nx * float(inward[0]) + nz * float(inward[1]) < 0:
            origin, direction = end, (-direction[0], -direction[1])
    wall = WallElement(row.element_id, origin, direction, length, _positive(p["thickness"], f"{row.element_id} thickness"), height, base_datum, context.frame_id, row.binding_id)
    context.references.hosts[row.element_id] = HostLine(origin, direction)
    openings = []
    for o in p.get("openings", ()):
        along = o["along"] if "along" in o else None
        if along is None:
            ref = parse_reference(o["at"])
            x, z = resolve_plan(ref, context.references)
            along = (x - origin[0]) * direction[0] + (z - origin[1]) * direction[1]
        sill = o["sill"] if not isinstance(o["sill"], Mapping) else _rel(o["sill"], base_datum, context)
        head = o["head"] if not isinstance(o["head"], Mapping) else _rel(o["head"], base_datum, context)
        openings.append(OpeningRequest(o["opening_id"], OpeningKind(o["kind"]), round(along, 9), o["width"], round(sill, 9), round(head, 9), f"binding-{o.get('component_id', row.component_id)}",
                                       int(o.get("count", 1)), float(o.get("step", 0.0))))
    solution = solve_wall(wall, tuple(openings))
    relations = tuple(ProducedRelation(f"{row.element_id}-hosts-{v.opening_id}", "hosts_void", row.element_id, v.opening_id, None) for v in solution.voids)
    return ProducedElement(solution.operations, solution.datum_bindings, (), relations, HostLine(origin, direction))


def _rel(reference: Mapping[str, Any], base_datum: str, context: ProductionContext) -> float:
    level_id, offset = resolve_elevation(parse_reference(reference), context.references)
    return context.datum_value(level_id) - context.datum_value(base_datum) + offset


PRODUCERS: dict[str, Callable[[ElementRow, ProductionContext], ProducedElement]] = {
    "column-array": produce_column_array, "capitals": produce_capitals, "beam": produce_beam, "pediment": produce_pediment, "wall": produce_wall,
}


def produce_rows(rows: tuple[ElementRow, ...], context: ProductionContext) -> tuple[ProducedElement, ...]:
    """Produce rows in the given order (supports before supported); a missing datum fails typed."""

    out = []
    for row in rows:
        producer = PRODUCERS.get(row.producer)
        if producer is None:
            raise ElementProducerError(f"{row.element_id}: unknown producer {row.producer!r}")
        try:
            out.append(producer(row, context))
        except ReferenceError as exc:
            raise ElementProducerError(f"{row.element_id}: {exc}") from exc
    return tuple(out)
