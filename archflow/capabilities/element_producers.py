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

These producers are the canonical set. The runner's private producers
(prism, ring, loft, dome cap, declined, coordinate walls) were retired into
this module on 2026-09-02; the runner reads Element@1 rows and calls
``produce_rows`` in ``production_order``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping

from archflow.capabilities.reference_resolver import (
    HostLine,
    ReferenceContext,
    ReferenceError,
    parse_reference,
    resolve_elevation,
    resolve_plan,
)
from archflow.capabilities.opening_solver import DoorType, WindowType, solve_openings
from archflow.capabilities.wall_solver import OpeningKind, OpeningRequest, WallElement, WallSolverError, solve_wall
from archflow.project.refs import require_identifier
from archflow.state.geometry_program import (
    DatumBinding,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    HostedAssembly,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from archflow.contracts.fields import (
    number,
    positive,
)

_M = LengthUnit.METER
FRAME_ID = "building-local"


class ElementProducerError(ValueError):
    """Typed failure of a reference-reading producer."""


def producer_signatures() -> dict[str, dict[str, Any]]:
    """The semantic authoring contracts the Studio can query and execute.

    The first authoring consumer is a wall with hosted apertures. Other
    existing producers remain executable; they are not advertised as semantic
    creation tools until their authored parameter contract is exposed here.
    Values and placements belong to the project's record, never this table.
    """

    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties,
                "required": list(required), "additionalProperties": False}

    scalar = {"anyOf": [{"type": "number"}, {"type": "string", "pattern": r"^@[A-Za-z0-9_.:-]+$"}],
              "description": "A value in metres, or an explicit @parameter binding."}
    identifier = {"type": "string", "minLength": 1}
    grid_role = {**identifier, "description": "The existing GridAxis@1 fields.role, not its entity_id."}
    level_id = {**identifier, "description": "The existing Level@1 entity_id, not its role."}
    plan = {"anyOf": [
        obj({"grid": {"anyOf": [grid_role, {"type": "array", "items": grid_role,
                                                "minItems": 2, "maxItems": 2}]}}, ("grid",)),
        obj({"axis_point": obj({"axis": grid_role, "along": scalar}, ("axis", "along"))}, ("axis_point",)),
        obj({"host": obj({"element": identifier, "along": scalar, "across": scalar},
                         ("element", "along"))}, ("host",)),
    ]}
    elevation = {"anyOf": [
        obj({"level": level_id}, ("level",)),
        obj({"datum": identifier, "offset": scalar}, ("datum",)),
        obj({"offset_from": obj({"level": level_id, "offset": scalar}, ("level", "offset"))},
            ("offset_from",)),
    ]}
    # Hosted opening elevations use the level resolver; published datums are
    # supported by the wall's base/top resolver only.
    opening_elevation = {"anyOf": [elevation["anyOf"][0], elevation["anyOf"][2]]}
    opening = obj({
        "opening_id": identifier,
        "component_id": identifier,
        "kind": {"type": "string", "enum": ["door", "window"]},
        "shape": {"type": "string", "enum": ["rectangular", "semicircular_arch"],
                  "description": "The aperture profile. An unfilled doorway has no door leaf."},
        "along": scalar,
        "at": plan,
        "width": scalar,
        "sill": {"anyOf": [scalar, opening_elevation]},
        "head": {"anyOf": [scalar, opening_elevation]},
        "spring_height": {**scalar, "description":
            "For semicircular_arch only: springing above the wall base; head - spring_height = width / 2."},
        "count": {"anyOf": [{"type": "integer", "minimum": 1},
                              {"type": "string", "pattern": r"^@[A-Za-z0-9_.:-]+$"}]},
        "step": scalar,
        "type_id": {**identifier, "description": "Only an existing compatible rectangular window or door type; omit for an empty passage."},
        "interface_ref": identifier,
    }, ("opening_id", "kind", "width", "sill", "head"))
    return {"wall": {
        "producer": "wall",
        "label": "墙体与宿主开口",
        "description": (
            "A straight wall placed on existing grids or host references. Its hosted opening may be "
            "rectangular or semicircular. No opening type means an empty passage, with no frame or leaf. "
            "Types supply reusable defaults; instance params and references override named defaults. "
            "References and dimensions must come from the project or an explicit design proposal. "
            "A supporting wall ends at the supported slab's underside, not its walking surface."
        ),
        "parameters": obj({
            "height": {**scalar, "description": "Wall height; optional when references.top determines it."},
            "thickness": {**scalar, "description": "Positive wall thickness towards the line's inward normal."},
            "openings": {"type": "array", "items": opening},
        }),
        "references": obj({
            "base": {"anyOf": [obj({"level": level_id}, ("level",)), obj({"datum": identifier}, ("datum",))]},
            "top": elevation,
            "support": {**identifier, "description": "Existing support reference; bearing is declared by a named support relationship."},
            "line": obj({"from": plan, "to": plan,
                         "face": {"type": "string", "description": "The existing wall face label, when the record names one."},
                         "inward": {"type": "array", "items": {"type": "number"},
                                    "minItems": 2, "maxItems": 2}}, ("from", "to")),
        }),
        "requiredParameters": ["thickness"],
        "requiredReferences": ["base", "line"],
        "constraints": [
            "Provide either height or references.top; if both are stated they must agree.",
            "Each aperture needs along or at, and remains inside its wall's length and height.",
            "A semicircular aperture requires spring_height >= sill and head - spring_height = width / 2.",
            "Use @parameter bindings for dimensions that subsequent changes must share.",
            "Use existing relation kinds for support, host, adjacency or clearance; proximity does not prove support.",
        ],
    }}


def _check_signature_value(value: Any, schema: Mapping[str, Any], field_name: str) -> None:
    """Check the small JSON-schema vocabulary returned by this owner's signatures."""

    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _check_signature_value(value, option, field_name)
                return
            except ElementProducerError:
                pass
        raise ElementProducerError(f"{field_name}: value does not match the producer signature")
    kind = schema.get("type")
    valid_type = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, (list, tuple)),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "string": isinstance(value, str),
    }.get(kind, False)
    if not valid_type:
        raise ElementProducerError(f"{field_name}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ElementProducerError(f"{field_name}: unsupported value {value!r}")
    if kind == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", ())) - value.keys()
        unknown = value.keys() - properties.keys() if schema.get("additionalProperties") is False else set()
        if missing or unknown:
            raise ElementProducerError(f"{field_name}: missing {sorted(missing)}, unknown {sorted(unknown)}")
        for key, item in value.items():
            if key in properties:
                _check_signature_value(item, properties[key], f"{field_name}.{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise ElementProducerError(f"{field_name}: incorrect number of items")
        for index, item in enumerate(value):
            _check_signature_value(item, schema["items"], f"{field_name}[{index}]")
    elif kind == "string":
        if len(value) < schema.get("minLength", 0):
            raise ElementProducerError(f"{field_name}: an empty name is not allowed")
        if "pattern" in schema:
            import re
            if re.fullmatch(schema["pattern"], value) is None:
                raise ElementProducerError(f"{field_name}: invalid parameter binding")
    elif "minimum" in schema and value < schema["minimum"]:
        raise ElementProducerError(f"{field_name}: value is below {schema['minimum']}")


def validate_element_contract(record, element_ids: tuple[str, ...]) -> None:
    """Check a semantic edit against the advertised producer and its real solver.

    This creates domain operations in memory only. Geometry, dimensions,
    reference resolution and opening rules are checked by their existing
    owners; no second solver or persistence path is introduced.
    """

    ids = set(element_ids)
    if not ids:
        return
    rows = element_rows_of(record)
    by_id = {row.element_id: row for row in rows}
    unknown = sorted(ids - by_id.keys())
    if unknown:
        raise ElementProducerError(f"semantic edit names unknown elements: {unknown}")
    signatures = producer_signatures()
    for entity_id in ids:
        row = by_id[entity_id]
        signature = signatures.get(row.producer)
        if signature is None:
            raise ElementProducerError(f"{entity_id}: semantic authoring is not available for {row.producer!r}")
        for key in signature["requiredParameters"]:
            if key not in row.params:
                raise ElementProducerError(f"{entity_id}: the wall needs {key} from its type or instance")
        for key in signature["requiredReferences"]:
            if key not in row.references:
                raise ElementProducerError(f"{entity_id}: the wall needs a {key} reference")
        # Existing rectangular fill types and exclusion policy retain their
        # solver's contract; semantic creation does not invent either one.
        _check_signature_value({k: v for k, v in row.params.items() if k not in {"types", "respect_exclusions"}},
                               signature["parameters"], f"{entity_id}.params")
        _check_signature_value(row.references, signature["references"], f"{entity_id}.references")
    from archflow.state.state_record import project_grids_of, project_levels_of

    context = ProductionContext(
        references=ReferenceContext(grids=project_grids_of(record), levels=project_levels_of(record)),
        published={},
    )
    # Existing rows establish host lines and top datums for the edited rows.
    # This is the same dependency order the runner uses, with no CAD execution.
    produce_rows(rows, context)


def _finite(value: object, field: str) -> float:
    """The owned finite-number rule, typed for this module's callers."""

    try:
        return number(value, field)
    except ValueError as exc:
        raise ElementProducerError(str(exc)) from exc


def _positive(value: object, field: str) -> float:
    """The owned positive-number rule, typed for this module's callers."""

    try:
        return positive(value, field)
    except ValueError as exc:
        raise ElementProducerError(str(exc)) from exc


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
    assemblies: tuple[HostedAssembly, ...] = ()


@dataclass
class ProductionContext:
    """What a producer may read: references (grids, levels, hosts), datums published so far, frame."""

    references: ReferenceContext
    published: dict[str, InterfaceDatum]
    frame_id: str = FRAME_ID
    exclusions: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = ()

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


def _stating(operation: GeometryOperation, **statements: str) -> GeometryOperation:
    """The same operation, with row facts the exported solid cannot show stated on it.

    A bounding box holds the same hull for a wedge rising along its run and
    one rising across it, and shows nothing of a shell's wall thickness. So
    the row's own numbers travel on the operation - not a measurement of the
    geometry, the value the producer read - and the CAD adapter writes them
    as ``archflow:*`` user strings the re-index can read back. They are
    statements, not parameters: they take no part in the geometry and no
    function contract names them.
    """

    return replace(operation, statements={**operation.statements, **statements})


def _metres(value: float) -> str:
    """A stated length as canonical decimal text: shortest round-trip repr, locale-free."""

    return repr(round(float(value), 9))


def _level_datum(datum_id: str, published_by: str, value: float, basis: tuple[str, ...]) -> InterfaceDatum:
    return InterfaceDatum.create(datum_id=datum_id, kind=InterfaceDatumKind.LEVEL, published_by=published_by, value=round(value, 9), unit=_M, basis_refs=basis)


def _rect(cx: float, cz: float, half_w: float, half_d: float):
    return [(cx - half_w, 0.0, cz - half_d), (cx + half_w, 0.0, cz - half_d), (cx + half_w, 0.0, cz + half_d), (cx - half_w, 0.0, cz + half_d)]


def _circle(cx: float, cz: float, r: float, n: int):
    return [(cx + r * math.cos(2 * math.pi * i / n), 0.0, cz + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _annulus(cx: float, cz: float, y: float, r_out: float, r_in: float, n: int):
    """One closed ring profile at one elevation: the outer circle, then the inner circle back."""

    outer = [(cx + r_out * math.cos(2 * math.pi * i / n), y, cz + r_out * math.sin(2 * math.pi * i / n)) for i in range(n)]
    inner = [(cx + r_in * math.cos(2 * math.pi * i / n), y, cz + r_in * math.sin(2 * math.pi * i / n)) for i in reversed(range(n))]
    return outer + inner


def _end_face(point: tuple[float, float], normal: tuple[float, float], half: float, y_minus: float, y_plus: float):
    """One end of a wedge: the base edge across the normal, then the top edge back, taller where the slope says."""

    nx, nz = normal
    return [(point[0] - nx * half, 0.0, point[1] - nz * half), (point[0] + nx * half, 0.0, point[1] + nz * half),
            (point[0] + nx * half, y_plus, point[1] + nz * half), (point[0] - nx * half, y_minus, point[1] - nz * half)]


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
    declared = _positive(params["height"], f"{row.element_id} height") if "height" in params else None
    top = row.references.get("top")
    if top is None:
        if declared is None:
            raise ElementProducerError(f"{row.element_id}: height or top reference required")
        return declared
    if isinstance(top, Mapping) and "datum" in top:
        top_id = str(top["datum"])
        offset = _finite(top.get("offset", 0.0), f"{row.element_id} top offset")
    else:
        top_id, offset = resolve_elevation(parse_reference(top), context.references)
    _, base_offset = _base(row, context)
    height = round(context.datum_value(top_id) + offset - context.datum_value(base_datum) - base_offset, 9)
    if height <= 0.0:
        raise ElementProducerError(f"{row.element_id}: top {top_id!r} is not above base {base_datum!r}")
    if declared is not None and not math.isclose(declared, height, rel_tol=0.0, abs_tol=0.001):
        raise ElementProducerError(f"{row.element_id}: height {declared} conflicts with the top reference height {height}")
    return declared if declared is not None else height


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
    return ProducedElement(tuple(ops), tuple(bindings), (top,), (ProducedRelation(f"{row.element_id}-stands-on", "support", base_datum, row.element_id, base_datum, _seat_parameters(base_offset)),), None)


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
        openings.append(OpeningRequest(o["opening_id"], OpeningKind(o["kind"]), round(_finite(along, f"{o['opening_id']} along"), 9), _finite(o["width"], f"{o['opening_id']} width"),
                                       round(_finite(sill, f"{o['opening_id']} sill"), 9), round(_finite(head, f"{o['opening_id']} head"), 9), f"binding-{o.get('component_id', row.component_id)}",
                                        int(o.get("count", 1)), _finite(o.get("step", 0.0), f"{o['opening_id']} step"),
                                        shape=str(o.get("shape", "rectangular")),
                                        spring_height=(None if o.get("spring_height") is None else
                                                       round(_finite(o["spring_height"], f"{o['opening_id']} spring_height"), 9))))
    exclusions = context.exclusions if p.get("respect_exclusions", True) else ()
    solution = solve_wall(wall, tuple(openings), exclusions=exclusions, base_elevation=context.datum_value(base_datum) if exclusions else None)
    ops, bindings, assemblies = list(solution.operations), list(solution.datum_bindings), []
    types: dict[str, Any] = {}
    for t in p.get("types", ()):
        fields = {k: v for k, v in t.items() if k not in ("schema", "kind")}
        types[t["type_id"]] = WindowType(**fields) if "glazing_thickness" in fields else DoorType(**fields)
    by_id = {o["opening_id"]: o for o in p.get("openings", ())}
    filled = tuple(v for v in solution.voids if by_id.get(v.opening_id, {}).get("type_id"))
    if filled:
        fills = solve_openings(filled, {v.opening_id: types[by_id[v.opening_id]["type_id"]] for v in filled},
                               binding_ids={v.opening_id: f"binding-{by_id[v.opening_id].get('component_id', row.component_id)}" for v in filled},
                               interface_refs={v.opening_id: by_id[v.opening_id]["interface_ref"] for v in filled if by_id[v.opening_id].get("interface_ref")})
        for fill in fills:
            ops.extend(fill.operations); bindings.extend(fill.datum_bindings); assemblies.append(fill.assembly)
    relations = tuple(ProducedRelation(f"{row.element_id}-hosts-{v.opening_id}", "hosts_void", row.element_id, v.opening_id, None) for v in solution.voids)
    return ProducedElement(tuple(ops), tuple(bindings), (), relations, HostLine(origin, direction), tuple(assemblies))


def _rel(reference: Mapping[str, Any], base_datum: str, context: ProductionContext) -> float:
    level_id, offset = resolve_elevation(parse_reference(reference), context.references)
    return context.datum_value(level_id) - context.datum_value(base_datum) + offset


def element_rows_of(record) -> tuple[ElementRow, ...]:
    """The record's Element@1 entities as producer rows, in production order.

    A row's explicit ``"@key"`` bindings are resolved to the record's evaluated
    parameter values by ``state_record.resolve_element_bindings`` (the one
    derivation engine); a numeric literal stays the literal the row stated. A
    binding that names no parameter, a bound derived value whose stored number
    disagrees with its expression, a cycle or an unknown name fails typed here,
    before any producer runs.
    """

    from archflow.state.state_record import StateRecordError, resolve_element_bindings

    try:
        resolved = resolve_element_bindings(record)
    except StateRecordError as exc:
        raise ElementProducerError(str(exc)) from exc
    rows = []
    for e in record.entities_of("Element@1"):
        fields = dict(resolved[e.entity_id])
        component_id = fields.get("component_id") or e.parent_id
        if not component_id:
            raise ElementProducerError(f"element {e.entity_id}: no component (field component_id or parent)")
        rows.append(ElementRow(e.entity_id, str(component_id), str(fields["producer"]), dict(fields.get("references", {})), dict(fields.get("params", {})), tuple(e.basis_refs)))
    return production_order(tuple(rows))


def _seat_parameters(base_offset: float) -> dict[str, float]:
    """What a base offset declares on the support relation: an embed (negative) or a rise (positive)."""

    if base_offset < 0.0:
        return {"engagement_depth": round(-base_offset, 9)}
    if base_offset > 0.0:
        return {"rise": round(base_offset, 9)}
    return {}


def _plan_point(row: ElementRow, context: ProductionContext, key: str = "at") -> tuple[float, float]:
    reference = row.references.get(key)
    if reference is None:
        raise ElementProducerError(f"{row.element_id}: plan reference {key!r} required")
    return resolve_plan(parse_reference(reference), context.references)


def produce_prism(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A straight extrusion of a declared profile, standing on a datum (an offset only as the base reference says)."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    profile = [(_finite(x, f"{row.element_id} profile x"), 0.0, _finite(z, f"{row.element_id} profile z")) for x, z in p["profile"]]
    height = _height(row, context, base_datum)
    op = _extrusion(row.element_id, profile, height, row.binding_id, context.frame_id, base_offset)
    top = _level_datum(f"{row.element_id}-top", f"obj-{row.element_id}", context.datum_value(base_datum) + base_offset + height, row.basis_refs)
    context.published[top.datum_id] = top  # a prism is what other elements sit on: it publishes its top like a beam does
    return ProducedElement((op,), (_bind(row.element_id, base_datum),), (top,), (ProducedRelation(f"{row.element_id}-stands-on", "support", base_datum, row.element_id, base_datum, _seat_parameters(base_offset)),), None)


def produce_ring(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """An annulus in ``pieces`` sectors around a plan reference (a round hall wall)."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    cx, cz = _plan_point(row, context)
    r_in, r_out = _positive(p["inner_radius"], f"{row.element_id} inner_radius"), _positive(p["outer_radius"], f"{row.element_id} outer_radius")
    pieces = int(p.get("pieces", 8))
    if r_out <= r_in:
        raise ElementProducerError(f"{row.element_id}: outer radius must exceed inner radius")
    height = _height(row, context, base_datum)
    ops, bindings = [], []
    for k in range(pieces):
        a0, a1 = 2 * math.pi * k / pieces, 2 * math.pi * (k + 1) / pieces
        arc = [a0 + (a1 - a0) * i / 6 for i in range(7)]
        outer = [(cx + r_out * math.cos(a), 0.0, cz + r_out * math.sin(a)) for a in arc]
        inner = [(cx + r_in * math.cos(a), 0.0, cz + r_in * math.sin(a)) for a in reversed(arc)]
        op_id = f"{row.element_id}-{k}"
        ops.append(_extrusion(op_id, outer + inner, height, row.binding_id, context.frame_id, base_offset))
        bindings.append(_bind(op_id, base_datum))
    return ProducedElement(tuple(ops), tuple(bindings), (), (ProducedRelation(f"{row.element_id}-stands-on", "support", base_datum, row.element_id, base_datum, _seat_parameters(base_offset)),), None)


def _loft(row: ElementRow, context: ProductionContext, profiles, size: int, base_datum: str, base_offset: float = 0.0,
          *, cap_ends: bool = True, profile_basis: str = "polyline", closed_profile: bool | None = None) -> ProducedElement:
    """One loft operation through closed polyline sections of ``size`` points each.

    Every producer that builds its own sections (stair, wedge, shell, dome
    cap) keeps the defaults: a capped polyline loft, one closed solid. Only
    ``produce_loft`` forwards what its row states, so a row cannot reach
    into another producer's closure.
    """

    stated = ((GeometryParameter.create(name="closed_profile", kind=GeometryParameterKind.BOOLEAN, value=closed_profile),) if closed_profile is not None else ())
    op = GeometryOperation(op_id=row.element_id, kind=GeometryOperationKind.LOFT, output_object_ids=(f"obj-{row.element_id}",), input_object_ids=(), frame_id=context.frame_id, parameters=((GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=round(base_offset, 9), unit=_M),) if base_offset else ()) + (
        GeometryParameter.create(name="cap_ends", kind=GeometryParameterKind.BOOLEAN, value=cap_ends),
    ) + stated + (
        GeometryParameter.create(name="loft_type", kind=GeometryParameterKind.TEXT, value=str(row.params.get("loft_type", "straight"))),
        GeometryParameter.create(name="profile_basis", kind=GeometryParameterKind.TEXT, value=profile_basis),
        GeometryParameter.create(name="profile_size", kind=GeometryParameterKind.INTEGER, value=size),
        _points("profiles", profiles),
    ),
        semantic_binding_ids=(row.binding_id,))
    return ProducedElement((op,), (_bind(row.element_id, base_datum),), (), (ProducedRelation(f"{row.element_id}-stands-on", "support", base_datum, row.element_id, base_datum, _seat_parameters(base_offset)),), None)


def _stated_bool(row: ElementRow, key: str, default: bool) -> bool:
    """A row's boolean, as a boolean: ``0``, ``"false"`` or ``None`` are refused, not coerced."""

    value = row.params.get(key, default)
    if not isinstance(value, bool):
        raise ElementProducerError(f"{row.element_id}: {key} must be true or false, not {value!r}")
    return value


def produce_loft(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A loft through declared section profiles (each point relative to the base datum).

    The CAD operation seats its lowest point at ``base_level + base_offset``.
    Derive that offset from the sections so their authored heights survive
    placement on the datum; the row does not state a second height offset.

    The row's ``cap_ends`` (default true) passes to the operation as stated:
    ``false`` delivers the lofted surface open at both end sections - a drum
    or a dome that the source gives as a surface with no thickness - rather
    than a solid with an invented thickness. ``profile_basis`` passes through
    when the IR names it (``polyline`` today; ``interpolated`` travels and is
    refused by the executor that cannot realize it) and fails here for any
    other value. ``closed_profile`` travels when the row states it; ``false``
    is refused, because every section is built as a closed polygon and an
    open section would be closed silently.
    """

    p = row.params
    base_datum, base_offset = _base(row, context)
    if base_offset:
        raise ElementProducerError(f"{row.element_id}: a loft's sections carry their own heights; no base offset")
    cap_ends = _stated_bool(row, "cap_ends", True)
    profile_basis = p.get("profile_basis", "polyline")
    if profile_basis not in ("polyline", "interpolated"):
        raise ElementProducerError(f"{row.element_id}: profile_basis must be 'polyline' or 'interpolated', not {profile_basis!r}")
    closed_profile = _stated_bool(row, "closed_profile", True) if "closed_profile" in p else None
    if closed_profile is False:
        raise ElementProducerError(f"{row.element_id}: an open section profile is not produced; every loft section is a closed polygon")
    profiles = [[_finite(c, f"{row.element_id} profile coordinate") for c in pt] for section in p["profiles"] for pt in section]
    section_base = min((pt[1] for pt in profiles), default=0.0)
    return _loft(row, context, profiles, int(p["profile_size"]), base_datum, section_base,
                 cap_ends=cap_ends, profile_basis=profile_basis, closed_profile=closed_profile)


def produce_dome_cap(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A spherical cap lofted through rings, from a plan reference and a base radius up to a level."""

    p = row.params
    base_datum, base_offset = _base(row, context)
    if base_offset:
        raise ElementProducerError(f"{row.element_id}: a dome stands on its drum's top datum; no base offset")
    cx, cz = _plan_point(row, context)
    a, h = _positive(p["base_radius"], f"{row.element_id} base_radius"), _height(row, context, base_datum)
    rings, top_fraction, n = int(p.get("rings", 5)), _finite(p.get("top_fraction", 0.95), f"{row.element_id} top_fraction"), int(p.get("segments", 24))
    sphere = (a * a + h * h) / (2 * h)
    profiles = []
    for i in range(rings):
        y = h * (i / (rings - 1)) * top_fraction if i < rings - 1 else h * top_fraction
        rr = math.sqrt(max(sphere * sphere - (sphere - h + y) ** 2, 0.0))
        profiles.append([(cx + rr * math.cos(2 * math.pi * j / n), y, cz + rr * math.sin(2 * math.pi * j / n)) for j in range(n)])
    return _loft(row, context, [pt for section in profiles for pt in section], n, base_datum)


def _stair_outline(length: float, count: int, going: float, rise: float) -> list[tuple[float, float]]:
    """One closed stepped side profile as (along, up) pairs: the floor line, then the treads back down.

    ``2 · count + 2`` vertices: the two ends of the floor line, then for each
    step from the top one down its nosing and the back of its tread. The
    intermediate nosings stand at ``k · going``; the far end stands on the
    ``to`` reference itself, so the flight ends exactly where the row says
    even when a declared going is a fraction of a millimetre off.
    """

    outline = [(0.0, 0.0), (length, 0.0)]
    for k in range(count, 0, -1):
        nosing = length if k == count else k * going
        outline.append((nosing, k * rise))
        outline.append(((k - 1) * going, k * rise))
    return outline


def produce_stair(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A straight flight of ``count`` steps between two plan references, as one closed solid.

    The flight is one stepped side profile — the floor line along the
    from→to line, then ``count`` treads of ``going`` and ``rise`` back down —
    lofted straight across ``width`` between its two sides and capped, so
    the export carries one object ``obj-<id>`` whose faces are the treads
    and risers themselves. Every step fills its whole rise. The flight
    publishes ``<id>-top`` at ``base + count · rise`` — the datum a landing,
    a podium or a stylobate above it binds, so changing the rise moves what
    the flight carries. It refuses a zero-length line, a count below one, and
    a declared ``going`` whose ``count · going`` misses the line length by
    more than a millimetre: a flight is measured by its references, never
    stretched to fit them. A declared ``top`` must match that rise total
    (including the base offset) within the same millimetre; the final
    tread's physical top is that same number by construction.

    A positive ``thickness`` used to mean separate slab treads, each only
    that thick, floating at its own rise. Separate treads are not one closed
    solid, and this producer derives no waist or flight slab from them, so
    that meaning is refused by name rather than reinterpreted. Retained
    programs compiled from the per-step path stay readable: the compiler
    and the adapters still realize plain extrusions.
    """

    p = row.params
    base_datum, base_offset = _base(row, context)
    start, end = _plan_point(row, context, "from"), _plan_point(row, context, "to")
    count = int(p["count"])
    if count < 1:
        raise ElementProducerError(f"{row.element_id}: a flight needs at least one step")
    rise = _positive(p["rise"], f"{row.element_id} rise")
    width = _positive(p["width"], f"{row.element_id} width")
    thickness = _finite(p.get("thickness", 0.0), f"{row.element_id} thickness")
    if thickness < 0.0:
        raise ElementProducerError(f"{row.element_id}: tread thickness must not be negative")
    if thickness > 0.0:
        raise ElementProducerError(
            f"{row.element_id}: tread thickness {round(thickness, 9)} m asks for separate slab treads, "
            "which are not one closed solid; a flight is produced as one stepped solid whose treads "
            "fill their rise, and no waist or slab thickness is derived from a tread thickness"
        )
    if "top" in row.references:
        top_ref = row.references["top"]
        if isinstance(top_ref, Mapping) and "datum" in top_ref:
            top_id = str(top_ref["datum"])
            top_offset = _finite(top_ref.get("offset", 0.0), f"{row.element_id} top offset")
        else:
            top_id, top_offset = resolve_elevation(parse_reference(top_ref), context.references)
        target_top = context.datum_value(top_id) + top_offset
        base_elevation = context.datum_value(base_datum) + base_offset
        flight_top = base_elevation + count * rise
        if abs(flight_top - target_top) > 1e-3:
            raise ElementProducerError(
                f"{row.element_id}: {count} rises of {round(rise, 9)} m from base "
                f"{base_datum!r} at {round(base_elevation, 9)} m reach {round(flight_top, 9)} m, "
                f"conflicting with declared top {top_id!r} at {round(target_top, 9)} m "
                "(tolerance 0.001 m); reconcile count/rise with the endpoint references"
            )
    dx, dz = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dz)
    if length <= 0.0:
        raise ElementProducerError(f"{row.element_id}: zero-length flight")
    ux, uz = dx / length, dz / length
    nx, nz = uz, -ux
    if "going" in p:
        going = _positive(p["going"], f"{row.element_id} going")
        if abs(count * going - length) > 1e-3:
            raise ElementProducerError(f"{row.element_id}: {count} steps of {going} m span {round(count * going, 6)} m, not the {round(length, 6)} m between the references")
    else:
        going = length / count
    half = width / 2.0
    outline = _stair_outline(length, count, going, rise)
    sides = [(start[0] + ux * along + sign * nx * half, up, start[1] + uz * along + sign * nz * half)
             for sign in (-1.0, 1.0) for along, up in outline]
    produced = _loft(row, context, sides, len(outline), base_datum, base_offset)
    top = _level_datum(f"{row.element_id}-top", f"obj-{row.element_id}", context.datum_value(base_datum) + base_offset + count * rise, row.basis_refs)
    context.published[top.datum_id] = top
    return ProducedElement(produced.operations, produced.bindings, (top,), produced.relations, None)


def _rise_sense(ux: float, uz: float, across: bool) -> str:
    """Which way a wedge's top rises, as a kernel plan axis and a sign: ``+x`` | ``-x`` | ``+z`` | ``-z``.

    Declared data, not measured geometry: it is read off the row's own
    ``from``→``to`` unit vector, but stated world-anchored so a reader does
    not have to know which end the row happened to name first. Along the
    run the low edge is at ``from``, so the rise is that unit vector;
    across it the high edge is on the +normal side, so the rise is the
    normal ``(uz, −ux)``. The dominant component names the string, and an
    exact tie (``|x| == |z|``, a run at 45°) picks x.
    """

    rx, rz = (uz, -ux) if across else (ux, uz)
    if abs(rx) >= abs(rz):
        return "+x" if rx > 0.0 else "-x"
    return "+z" if rz > 0.0 else "-z"


def produce_wedge(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A right prism with a sloped top: the box ``from``→``to``, ``depth`` across it, rising from ``low`` to ``high``.

    One loft through the two end faces, each a rectangle of four points, the
    far one taller, so the top is one plane and no coordinate is invented
    between them. ``slope_across`` tips that plane across the depth instead
    of along the length; ``low`` then sits on the −normal side and both end
    faces are the same rectangle. This is what carries a roof abutment or a
    roof sector — a pediment's sibling that is not a tympanum. It refuses a
    zero-length line, a ``low`` below the base datum, and a ``high`` that is
    not above ``low``: a wedge with a level top is a prism, and says so.

    The bounding box the export leaves behind holds the same hull whichever
    way the top rises, so the row's own numbers travel as statements on the
    operation: ``wedge_low`` and ``wedge_high`` in metres, ``wedge_axis``
    (``along`` | ``across``), and ``wedge_sense`` — the rise direction in the kernel
    plan, ``+x`` | ``-x`` | ``+z`` | ``-z``, computed by ``_rise_sense``
    from the declared line and ``slope_across`` alone. It is world-anchored
    rather than named against the row's reference order, so a re-index that
    re-derives its own ``from``/``to`` can still tell a wedge from its
    mirror image.
    """

    p = row.params
    base_datum, base_offset = _base(row, context)
    start, end = _plan_point(row, context, "from"), _plan_point(row, context, "to")
    depth = _positive(p["depth"], f"{row.element_id} depth")
    low, high = _finite(p["low"], f"{row.element_id} low"), _positive(p["high"], f"{row.element_id} high")
    if low < 0.0:
        raise ElementProducerError(f"{row.element_id}: low must stand on or above the base datum")
    if high <= low:
        raise ElementProducerError(f"{row.element_id}: high must rise above low")
    dx, dz = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dz)
    if length <= 0.0:
        raise ElementProducerError(f"{row.element_id}: zero-length wedge")
    ux, uz = dx / length, dz / length
    normal, half = (uz, -ux), depth / 2.0
    across = bool(p.get("slope_across", False))
    near = (low, high) if across else (low, low)
    far = (low, high) if across else (high, high)
    produced = _loft(row, context, _end_face(start, normal, half, *near) + _end_face(end, normal, half, *far), 4, base_datum, base_offset)
    # The solid keeps its own slope: low, high, the axis it tips on, and the direction it rises in.
    # This producer always seats low at the `from` end (along) or on the -normal side (across), so
    # the sense follows from the declared line; it is stated world-anchored, not measured back.
    stated = _stating(produced.operations[0], wedge_axis="across" if across else "along", wedge_high=_metres(high),
                      wedge_low=_metres(low), wedge_sense=_rise_sense(ux, uz, across))
    top = _level_datum(f"{row.element_id}-top", f"obj-{row.element_id}", context.datum_value(base_datum) + base_offset + high, row.basis_refs)
    context.published[top.datum_id] = top
    return ProducedElement((stated,), produced.bindings, (top,), produced.relations, None)


def produce_shell(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A hollow revolved shell around a plan reference: a cylinder wall, or a dome.

    ``cylinder`` extrudes one annulus — the outer circle of ``segments``
    points, then the inner circle back — up by ``height``. ``dome`` lofts
    ``rings`` such annuli up a hemi-ellipsoidal cap of ``height`` over
    ``outer_radius``, the inner surface being that same cap scaled to
    ``outer_radius − thickness``. Two approximations are stated rather than
    hidden: the wall therefore thins towards the crown instead of holding a
    constant normal thickness, and the top ring closes on a small annulus of
    outer radius ``thickness / 2`` rather than on a point, so the loft keeps
    one profile size. Both leave the published ``<id>-top`` at exactly
    ``base + height``. It refuses a thickness that is not inside the outer
    radius, fewer than three segments, and a dome of fewer than two rings.
    """

    p = row.params
    base_datum, base_offset = _base(row, context)
    cx, cz = _plan_point(row, context)
    outer = _positive(p["outer_radius"], f"{row.element_id} outer_radius")
    thickness = _positive(p["thickness"], f"{row.element_id} thickness")
    height = _positive(p["height"], f"{row.element_id} height")
    if thickness >= outer:
        raise ElementProducerError(f"{row.element_id}: thickness must be smaller than the outer radius")
    segments = int(p.get("segments", 24))
    if segments < 3:
        raise ElementProducerError(f"{row.element_id}: a shell needs at least three segments")
    kind = p.get("kind")
    if kind not in ("cylinder", "dome"):
        raise ElementProducerError(f"{row.element_id}: shell kind must be 'cylinder' or 'dome', not {kind!r}")
    if kind == "cylinder":
        op = _extrusion(row.element_id, _annulus(cx, cz, 0.0, outer, outer - thickness, segments), height, row.binding_id, context.frame_id, base_offset)
        operations, bindings = (op,), (_bind(row.element_id, base_datum),)
    else:
        rings = int(p.get("rings", 8))
        if rings < 2:
            raise ElementProducerError(f"{row.element_id}: a dome shell needs at least two rings")
        scale, profiles = (outer - thickness) / outer, []
        for i in range(rings):
            phi = (math.pi / 2.0) * (i / (rings - 1))
            r_out = max(outer * math.cos(phi), thickness / 2.0)
            profiles.extend(_annulus(cx, cz, height * math.sin(phi), r_out, r_out * scale, segments))
        lofted = _loft(row, context, profiles, 2 * segments, base_datum, base_offset)
        operations, bindings = lofted.operations, lofted.bindings
    # The wall is nowhere in the hull: a solid drum and a hollow one share a box. The shell states
    # its own thickness and which revolved form it is, so the export can say what the box cannot.
    operations = (_stating(operations[0], shell_kind=kind, shell_thickness=_metres(thickness)),)
    top = _level_datum(f"{row.element_id}-top", f"obj-{row.element_id}", context.datum_value(base_datum) + base_offset + height, row.basis_refs)
    context.published[top.datum_id] = top
    return ProducedElement(operations, bindings, (top,), (ProducedRelation(f"{row.element_id}-stands-on", "support", base_datum, row.element_id, base_datum, _seat_parameters(base_offset)),), None)


def produce_declined(row: ElementRow, context: ProductionContext) -> ProducedElement:
    """A typed declination: the component is owned, looked at, and left without geometry for a stated reason."""

    reason = row.params.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ElementProducerError(f"{row.element_id}: a declination needs a reason")
    return ProducedElement((), ())


PRODUCERS: dict[str, Callable[[ElementRow, ProductionContext], ProducedElement]] = {
    "column-array": produce_column_array, "capitals": produce_capitals, "beam": produce_beam, "pediment": produce_pediment, "wall": produce_wall,
    "prism": produce_prism, "ring": produce_ring, "loft": produce_loft, "dome-cap": produce_dome_cap,
    "stair": produce_stair, "wedge": produce_wedge, "shell": produce_shell, "declined": produce_declined,
}


def _mentions(value: Any, names: Mapping[str, str]) -> set[str]:
    """Element ids a reference value names, directly or through a published ``<id>-top`` datum."""

    found: set[str] = set()
    if isinstance(value, str):
        if value in names:
            found.add(names[value])
    elif isinstance(value, Mapping):
        for item in value.values():
            found |= _mentions(item, names)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _mentions(item, names)
    return found


def production_order(rows: tuple[ElementRow, ...]) -> tuple[ElementRow, ...]:
    """Supports before supported: an element that names another (or its published top) comes after it; ties keep the given order."""

    names = {r.element_id: r.element_id for r in rows}
    names.update({f"{r.element_id}-top": r.element_id for r in rows})
    deps = {r.element_id: _mentions(r.references, names) - {r.element_id} for r in rows}
    for r in rows:
        for opening in r.params.get("openings", ()) if isinstance(r.params.get("openings"), (list, tuple)) else ():
            deps[r.element_id] |= _mentions(opening.get("at"), names) - {r.element_id} if isinstance(opening, Mapping) else set()
    ordered: list[ElementRow] = []
    placed: set[str] = set()
    remaining = list(rows)
    while remaining:
        ready = [r for r in remaining if deps[r.element_id] <= placed]
        if not ready:
            cycle = sorted(r.element_id for r in remaining)
            raise ElementProducerError(f"element references form a cycle or name a missing element: {cycle}")
        for r in ready:
            ordered.append(r); placed.add(r.element_id)
        remaining = [r for r in remaining if r.element_id not in placed]
    return tuple(ordered)


def produce_rows(rows: tuple[ElementRow, ...], context: ProductionContext) -> tuple[ProducedElement, ...]:
    """Produce rows in the given order (supports before supported); a missing datum fails typed."""

    out = []
    for row in rows:
        producer = PRODUCERS.get(row.producer)
        if producer is None:
            raise ElementProducerError(f"{row.element_id}: unknown producer {row.producer!r}")
        try:
            out.append(producer(row, context))
        except (ReferenceError, WallSolverError) as exc:
            raise ElementProducerError(f"{row.element_id}: {exc}") from exc
    return tuple(out)
