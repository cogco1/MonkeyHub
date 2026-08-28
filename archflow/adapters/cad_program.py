"""Translate a compiled neutral geometry program into CAD build steps.

The translator is deterministic and owns no design authority: it maps the
exact compiled operations (solids, revolves, extrusions, lofts, booleans,
linear and radial arrays) onto a self-contained ``rhinoscriptsyntax``
build script. Execution happens through an external CAD adapter whose
identity the caller retains; the script prints one JSON line of per-object
measures (bounding box and closed-volume where available) that an
equivalence receipt then compares against the sandbox realization within
explicit tolerances. Unsupported constructs become typed losses, never
silent drops.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

_SUPPORTED = {
    "solid",
    "revolve",
    "extrusion",
    "loft",
    "boolean_union",
    "boolean_difference",
    "boolean_intersection",
    "array",
    "radial_array",
    "transform",
    "curve",
}


class CadTranslationError(ValueError):
    """The program contains a construct the translator cannot express."""


@dataclass(frozen=True, slots=True)
class CadTranslation:
    script: str
    physical_object_ids: tuple[str, ...]
    losses: tuple[dict, ...]


def _params(operation) -> dict[str, object]:
    decoded = {}
    for parameter in operation.parameters:
        decoded[parameter.name] = json.loads(parameter.value_json)
    return decoded


def translate_to_rhino_python(program) -> CadTranslation:
    """Emit one deterministic rhinoscriptsyntax build script."""

    proposal = program.proposal
    operations = {op.op_id: op for op in proposal.operations}
    order = list(program.operation_order)
    consumed: set[str] = set()
    for op in proposal.operations:
        consumed.update(op.input_object_ids)
    physical = tuple(
        sorted(
            object_id
            for op in proposal.operations
            for object_id in op.output_object_ids
            if object_id not in consumed
            and op.kind.value != "curve"
        )
    )
    losses: list[dict] = []
    lines: list[str] = [
        "import json",
        "import math",
        "import rhinoscriptsyntax as rs",
        "objects = {}",
        "",
        "def _register(object_id, guids):",
        "    if guids is None: raise Exception('build failed: ' + object_id)",
        "    if not isinstance(guids, list): guids = [guids]",
        "    objects[object_id] = guids",
        "",
    ]
    for op_id in order:
        operation = operations[op_id]
        kind = operation.kind.value
        if kind not in _SUPPORTED:
            losses.append(
                {
                    "code": "cad.unsupported_operation",
                    "op_id": op_id,
                    "kind": kind,
                }
            )
            continue
        params = _params(operation)
        out = operation.output_object_ids[0]
        ins = list(operation.input_object_ids)
        if kind == "curve":
            losses.append(
                {
                    "code": "cad.curve_reference_only",
                    "op_id": op_id,
                    "kind": kind,
                }
            )
            lines.append(f"objects[{out!r}] = []  # reference curve omitted")
            continue
        if kind == "solid":
            o, s = params["origin"], params["size"]
            corners = (
                f"[({o[0]},{o[2]},{o[1]}), ({o[0]+s[0]},{o[2]},{o[1]}), "
                f"({o[0]+s[0]},{o[2]+s[2]},{o[1]}), ({o[0]},{o[2]+s[2]},{o[1]}), "
                f"({o[0]},{o[2]},{o[1]+s[1]}), ({o[0]+s[0]},{o[2]},{o[1]+s[1]}), "
                f"({o[0]+s[0]},{o[2]+s[2]},{o[1]+s[1]}), ({o[0]},{o[2]+s[2]},{o[1]+s[1]})]"
            )
            lines.append(f"_register({out!r}, rs.AddBox({corners}))")
        elif kind == "revolve":
            a0, a1 = params["axis_start"], params["axis_end"]
            r0 = max(float(params["start_radius"]), 0.01)
            r1 = max(float(params["end_radius"]), 0.01)
            lines.extend(
                [
                    f"_c0 = rs.AddCircle(rs.PlaneFromNormal(({a0[0]},{a0[2]},{a0[1]}), "
                    f"({a1[0]-a0[0]},{a1[2]-a0[2]},{a1[1]-a0[1]})), {r0})",
                    f"_c1 = rs.AddCircle(rs.PlaneFromNormal(({a1[0]},{a1[2]},{a1[1]}), "
                    f"({a1[0]-a0[0]},{a1[2]-a0[2]},{a1[1]-a0[1]})), {r1})",
                    "_srf = rs.AddLoftSrf([_c0, _c1])",
                    "rs.CapPlanarHoles(_srf[0])",
                    f"_register({out!r}, _srf)",
                    "rs.DeleteObjects([_c0, _c1])",
                ]
            )
        elif kind == "extrusion":
            profile = params["profile"]
            vector = params["vector"]
            pts = ", ".join(
                f"({p[0]},{p[2]},{p[1]})" for p in [*profile, profile[0]]
            )
            lines.extend(
                [
                    f"_crv = rs.AddPolyline([{pts}])",
                    f"_ext = rs.ExtrudeCurveStraight(_crv, (0,0,0), "
                    f"({vector[0]},{vector[2]},{vector[1]}))",
                    "rs.CapPlanarHoles(_ext)",
                    f"_register({out!r}, _ext)",
                    "rs.DeleteObject(_crv)",
                ]
            )
        elif kind == "loft":
            profiles = params["profiles"]
            size = int(params["profile_size"])
            rings = [
                profiles[i : i + size]
                for i in range(0, len(profiles), size)
            ]
            lines.append("_rings = []")
            for ring in rings:
                pts = ", ".join(
                    f"({p[0]},{p[2]},{p[1]})" for p in [*ring, ring[0]]
                )
                lines.append(f"_rings.append(rs.AddPolyline([{pts}]))")
            lines.extend(
                [
                    "_srf = rs.AddLoftSrf(_rings)",
                    "rs.CapPlanarHoles(_srf[0])",
                    f"_register({out!r}, _srf)",
                    "rs.DeleteObjects(_rings)",
                ]
            )
        elif kind in (
            "boolean_union",
            "boolean_difference",
            "boolean_intersection",
        ):
            copies = (
                "["
                + ", ".join(
                    f"rs.CopyObject(g) for g in objects[{i!r}]"
                    for i in ins
                )
                + "]"
            )
            lines.append(
                "_ins = ["
                + ", ".join(
                    f"[rs.CopyObject(_g) for _g in objects[{i!r}]]"
                    for i in ins
                )
                + "]"
            )
            if kind == "boolean_difference":
                base = int(params.get("base_index", 0))
                ordered = sorted(ins)
                base_id = ordered[base]
                base_pos = ins.index(base_id)
                others = [
                    f"_ins[{index}]"
                    for index in range(len(ins))
                    if index != base_pos
                ]
                lines.append(
                    f"_res = rs.BooleanDifference(_ins[{base_pos}], "
                    f"{' + '.join(others)}, True)"
                )
            elif kind == "boolean_union":
                lines.append(
                    "_res = rs.BooleanUnion("
                    + " + ".join(
                        f"_ins[{index}]" for index in range(len(ins))
                    )
                    + ")"
                )
            else:
                lines.append(
                    f"_res = rs.BooleanIntersection(_ins[0], "
                    + " + ".join(
                        f"_ins[{index}]" for index in range(1, len(ins))
                    )
                    + ", True)"
                )
            lines.append(f"_register({out!r}, _res)")
        elif kind == "array":
            count = int(params["count"])
            step = params["step"]
            lines.extend(
                [
                    f"_guids = [rs.CopyObject(_g) for _g in objects[{ins[0]!r}]]",
                    f"for _i in range(1, {count}):",
                    f"    _guids += [rs.CopyObject(_g, "
                    f"({step[0]}*_i, {step[2]}*_i, {step[1]}*_i)) "
                    f"for _g in objects[{ins[0]!r}]]",
                    f"_register({out!r}, _guids)",
                ]
            )
        elif kind == "radial_array":
            count = int(params["count"])
            center = params["center"]
            angle = float(params["angle_step_degrees"])
            start = float(params.get("start_angle_degrees", 0.0))
            lines.extend(
                [
                    f"_guids = []",
                    f"for _i in range({count}):",
                    f"    _a = {start} + _i * {angle}",
                    f"    for _g in objects[{ins[0]!r}]:",
                    f"        _c = rs.CopyObject(_g)",
                    f"        rs.RotateObject(_c, "
                    f"({center[0]},{center[2]},{center[1]}), -_a)",
                    f"        _guids.append(_c)",
                    f"_register({out!r}, _guids)",
                ]
            )
        elif kind == "transform":
            losses.append(
                {
                    "code": "cad.transform_copy_only",
                    "op_id": op_id,
                    "kind": kind,
                }
            )
            lines.append(
                f"_register({out!r}, "
                f"[rs.CopyObject(_g) for _g in objects[{ins[0]!r}]])"
            )
    lines.extend(
        [
            "",
            f"_physical = {list(physical)!r}",
            "for _oid, _guids in objects.items():",
            "    if _oid not in _physical:",
            "        rs.DeleteObjects(_guids)",
            "measures = {}",
            "for _oid in _physical:",
            "    _guids = objects.get(_oid) or []",
            "    if not _guids:",
            "        measures[_oid] = None",
            "        continue",
            "    _bb = rs.BoundingBox(_guids)",
            "    _vol = 0.0",
            "    for _g in _guids:",
            "        try:",
            "            _v = rs.SurfaceVolume(_g)",
            "            if _v: _vol += _v[0]",
            "        except Exception:",
            "            pass",
            "    measures[_oid] = {",
            "        'bbox_min': [_bb[0].X, _bb[0].Z, _bb[0].Y],",
            "        'bbox_max': [_bb[6].X, _bb[6].Z, _bb[6].Y],",
            "        'volume': _vol,",
            "        'brep_count': len(_guids),",
            "    }",
            "print('CAD_MEASURES=' + json.dumps(measures))",
        ]
    )
    return CadTranslation(
        script="\n".join(lines),
        physical_object_ids=physical,
        losses=tuple(losses),
    )


def _rotate_about_vertical(point, center, degrees):
    theta = math.radians(degrees)
    c, s = math.cos(theta), math.sin(theta)
    x, z = point[0] - center[0], point[2] - center[2]
    return (
        center[0] + x * c + z * s,
        point[1],
        center[2] + z * c - x * s,
    )


def expected_object_bounds(program) -> dict[str, dict]:
    """Analytic per-object bounds the CAD realization must reproduce.

    The replay tracks each object's extreme points through the same
    operation semantics the translator emits, so an equivalence receipt
    can compare CAD-measured bounding boxes against program-derived ones
    without trusting either side's renderer.
    """

    proposal = program.proposal
    operations = {op.op_id: op for op in proposal.operations}
    points: dict[str, list] = {}
    counts: dict[str, int] = {}
    for op_id in program.operation_order:
        operation = operations[op_id]
        kind = operation.kind.value
        if kind not in _SUPPORTED or kind == "curve":
            continue
        params = _params(operation)
        out = operation.output_object_ids[0]
        ins = list(operation.input_object_ids)
        if kind == "solid":
            o, s = params["origin"], params["size"]
            points[out] = [
                (o[0] + dx * s[0], o[1] + dy * s[1], o[2] + dz * s[2])
                for dx in (0, 1)
                for dy in (0, 1)
                for dz in (0, 1)
            ]
            counts[out] = 1
        elif kind == "extrusion":
            profile, vector = params["profile"], params["vector"]
            points[out] = [tuple(p) for p in profile] + [
                (p[0] + vector[0], p[1] + vector[1], p[2] + vector[2])
                for p in profile
            ]
            counts[out] = 1
        elif kind == "revolve":
            a0, a1 = params["axis_start"], params["axis_end"]
            pts = []
            for level, radius in (
                (a0, max(float(params["start_radius"]), 0.01)),
                (a1, max(float(params["end_radius"]), 0.01)),
            ):
                pts.extend(
                    [
                        (level[0] - radius, level[1], level[2] - radius),
                        (level[0] + radius, level[1], level[2] + radius),
                    ]
                )
            points[out] = pts
            counts[out] = 1
        elif kind == "loft":
            points[out] = [tuple(p) for p in params["profiles"]]
            counts[out] = 1
        elif kind == "boolean_union":
            points[out] = [p for i in ins for p in points[i]]
            counts[out] = 1
        elif kind == "boolean_difference":
            base = sorted(ins)[int(params.get("base_index", 0))]
            points[out] = list(points[base])
            counts[out] = 1
        elif kind == "boolean_intersection":
            lo = [max(min(c[axis] for c in points[i]) for i in ins)
                  for axis in range(3)]
            hi = [min(max(c[axis] for c in points[i]) for i in ins)
                  for axis in range(3)]
            points[out] = [
                (lo[0], lo[1], lo[2]),
                (hi[0], hi[1], hi[2]),
            ]
            counts[out] = 1
        elif kind == "array":
            count, step = int(params["count"]), params["step"]
            points[out] = [
                (p[0] + step[0] * i, p[1] + step[1] * i, p[2] + step[2] * i)
                for i in range(count)
                for p in points[ins[0]]
            ]
            counts[out] = count * counts[ins[0]]
        elif kind == "radial_array":
            count = int(params["count"])
            center = params["center"]
            angle = float(params["angle_step_degrees"])
            start = float(params.get("start_angle_degrees", 0.0))
            points[out] = [
                _rotate_about_vertical(p, center, start + i * angle)
                for i in range(count)
                for p in points[ins[0]]
            ]
            counts[out] = count * counts[ins[0]]
        elif kind == "transform":
            points[out] = list(points[ins[0]])
            counts[out] = counts[ins[0]]
    consumed: set[str] = set()
    for op in proposal.operations:
        consumed.update(op.input_object_ids)
    bounds: dict[str, dict] = {}
    for object_id, pts in points.items():
        if object_id in consumed:
            continue
        bounds[object_id] = {
            "bbox_min": [min(p[axis] for p in pts) for axis in range(3)],
            "bbox_max": [max(p[axis] for p in pts) for axis in range(3)],
            "brep_count": counts[object_id],
        }
    return bounds
