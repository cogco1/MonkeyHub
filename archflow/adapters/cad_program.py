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

Semantics travel natively — no plugins, no sidecar files. Every physical
object is emitted with its object id as the CAD object name, placed on a
per-component layer, and tagged with key-value user text carrying exactly
the binding ids, component id, commitment refs, evidence refs, and
producer op the compiled program states. Component repetition becomes
native block instancing (one definition, N transforms), mirroring the
family identity the program already owns. The script reads its own
semantics back from the document and prints them beside the measures so
the receipt can verify the round trip against the program alone.

Some forms are not recoverable from the geometry they leave behind: a
bounding box holds the same hull for a wedge rising along its run, one
rising across it and one rising the other way, and a hollow drum and a
solid one share a box entirely. Those producers therefore state their own
defining numbers on the operation, in ``GeometryOperation.statements``,
and every object of such an operation carries each statement as user text
beside its identity: the key verbatim under ``archflow:``, the value
verbatim. The translator keeps no table of which producer states what and
formats nothing — a statement is already the text it will be written as,
so no geometry is measured or recomputed to produce a string, and an
object whose operation states nothing carries nothing extra. What it does
own is the identity namespace: a statement may not take a key the export
already writes (``producer_op``, ``object_ref``, ``operation_ref``,
``bindings``, ``component``, ``material``, ``commitments``, ``evidence``,
``inspection_witness``), and one that tries fails ``CadTranslationError``
rather than overwriting an object's identity.

The producers on the spine state these today — keys and formats exactly:

===========================  ==========================================
``archflow:wedge_low``       metres above the row's base datum
``archflow:wedge_high``      metres above the base datum, above ``low``
``archflow:wedge_axis``      ``along`` | ``across``
``archflow:wedge_sense``     ``+x`` | ``-x`` | ``+z`` | ``-z`` — the
                             direction the top rises in, anchored to the
                             kernel plan axes rather than to the row's
                             reference order
``archflow:shell_thickness`` metres of wall
``archflow:shell_kind``      ``cylinder`` | ``dome``
===========================  ==========================================

Metres are canonical decimal text (shortest round-trip repr: ``0.5``,
``2.0`` — no locale, no thousands separator), enumerated values their bare
literal; the producer that states them writes them that way. An older
export that predates the strings is not repaired here — the re-index keeps
such a row AMBIGUOUS and names what is missing.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

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

_ROOT_LAYER = "archflow"

# The ``archflow:*`` user-text keys the export itself writes: an object's
# identity, its layer semantics and its inspection role. They are the one
# namespace an operation's statements may not enter — a statement is free
# text the run declared, and no declaration may overwrite what identifies
# the object it travels on.
_RESERVED_USER_TEXT: frozenset[str] = frozenset(
    {
        "bindings",
        "commitments",
        "component",
        "evidence",
        "inspection_witness",
        "material",
        "object_ref",
        "operation_ref",
        "producer_op",
    }
)


class CadTranslationError(ValueError):
    """The program contains a construct the translator cannot express."""


@dataclass(frozen=True, slots=True)
class CadTranslation:
    script: str
    physical_object_ids: tuple[str, ...]
    losses: tuple[dict, ...]
    layer_colors: tuple[tuple[str, tuple[int, int, int]], ...]



def lift_to_base_level(points, params: Mapping[str, object], op_id: str):
    """Apply an optional datum-bound ``base_level`` to profile points.

    ``base_level`` (P090, M096) is the elevation the profile's lowest
    point must sit on. Elevation is the program's Y axis. The profile
    keeps its shape; only its elevation derives from the datum, so a
    dependent object never restates the level it sits on.
    """

    if "base_level" not in params:
        if "base_offset" in params:
            raise CadTranslationError(
                f"{op_id}: base_offset without a datum-bound base_level restates an elevation"
            )
        return [tuple(float(v) for v in p) for p in points]
    raw = params["base_level"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise CadTranslationError(f"{op_id}: base_level must be a finite number")
    # P092: an element's own vertical dimension above its storey (a sill
    # height, a frame seat) rides on the datum instead of restating it.
    offset = params.get("base_offset", 0.0)
    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset):
        raise CadTranslationError(f"{op_id}: base_offset must be a finite number")
    lifted = [tuple(float(v) for v in p) for p in points]
    if not lifted:
        return lifted
    shift = float(raw) + float(offset) - min(p[1] for p in lifted)
    return [(p[0], p[1] + shift, p[2]) for p in lifted]

def _is_axis_aligned_box(profile, vector) -> bool:
    """True for a rectangular, axis-aligned, level profile extruded along Y."""

    if len(profile) != 4 or len(vector) != 3:
        return False
    if float(vector[0]) != 0.0 or float(vector[2]) != 0.0 or float(vector[1]) == 0.0:
        return False
    ys = {round(float(p[1]), 12) for p in profile}
    xs = sorted({round(float(p[0]), 12) for p in profile})
    zs = sorted({round(float(p[2]), 12) for p in profile})
    if len(ys) != 1 or len(xs) != 2 or len(zs) != 2:
        return False
    corners = {(x, z) for x in xs for z in zs}
    return {(round(float(p[0]), 12), round(float(p[2]), 12)) for p in profile} == corners


def _params(operation) -> dict[str, object]:
    decoded = {}
    for parameter in operation.parameters:
        decoded[parameter.name] = json.loads(parameter.value_json)
    return decoded


_LAYER_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-.]{0,63}$")


def _component_layer(
    components: tuple[str, ...],
    layer_by_component: Mapping[str, str] | None,
) -> str:
    """The layer an object's components put it on.

    Without a scheme: the historical ``archflow::<components>`` path. With a
    caller-supplied scheme (P108 numbered categories, e.g. ``20_STRUCTURE``):
    ``<category>::<components>`` — the category is the parent layer, the
    component keeps its own child layer, so identity survives the renumbering.
    The kernel never invents a category: an unmapped component stays on the
    historical path, visibly, rather than being guessed into a bucket.
    """

    if not components:
        return _ROOT_LAYER
    joined = "+".join(components)
    if layer_by_component is not None:
        categories = sorted(
            {
                layer_by_component[component]
                for component in components
                if component in layer_by_component
            }
        )
        if len(categories) == 1:
            category = categories[0]
            if not _LAYER_SEGMENT.match(category):
                raise ValueError(
                    f"layer category {category!r} is not a valid layer name"
                )
            return f"{category}::{joined}"
    return f"{_ROOT_LAYER}::{joined}"


def _physical_ids(proposal) -> tuple[str, ...]:
    consumed: set[str] = set()
    for op in proposal.operations:
        consumed.update(op.input_object_ids)
    return tuple(
        sorted(
            object_id
            for op in proposal.operations
            for object_id in op.output_object_ids
            if object_id not in consumed
            and (
                op.kind.value != "curve"
                or bool(_params(op).get("retain_for_inspection", False))
            )
        )
    )


def _layer_color(component_key: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(component_key.encode("utf-8")).digest()
    return (
        60 + digest[0] % 160,
        60 + digest[1] % 160,
        60 + digest[2] % 160,
    )


def _rgb(value: object, field: str) -> tuple[int, int, int]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise CadTranslationError(f"{field} must be a three-channel tuple")
    channels: list[int] = []
    for channel in value:
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise CadTranslationError(f"{field} channels must be integers")
        if channel < 0 or channel > 255:
            raise CadTranslationError(
                f"{field} channels must be between 0 and 255"
            )
        channels.append(channel)
    return channels[0], channels[1], channels[2]


def _resolved_layer_colors(
    layer_paths: set[str],
    *,
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    """Resolve the one color table used by both the script and its contract.

    The root layer has no component or material assignment, so it always uses
    the same deterministic path-derived fallback as an explicit contract.
    """

    rows: list[tuple[str, tuple[int, int, int]]] = []
    for layer_path in sorted({_ROOT_LAYER, *layer_paths}):
        color = _layer_color(layer_path)
        if layer_path != _ROOT_LAYER:
            component = layer_path.split("::", 1)[1]
            material = (material_by_component or {}).get(component)
            if material is not None and material_colors is not None:
                color = material_colors.get(material, color)
        rows.append(
            (
                layer_path,
                _rgb(color, f"layer color for {layer_path}"),
            )
        )
    return tuple(rows)


def expected_object_semantics(
    program,
    *,
    material_by_component: Mapping[str, str] | None = None,
    layer_by_component: Mapping[str, str] | None = None,
) -> dict[str, dict]:
    """The semantics each physical object must carry in the CAD document.

    Derived from the program alone: producer op, binding ids, component
    id, commitment and evidence refs, the per-component layer path, and
    every one of the operation's own ``statements`` — what a saved solid
    cannot show about itself — written through verbatim as
    ``archflow:<key>``. A statement that names a reserved identity key
    fails ``CadTranslationError``. Objects the program leaves unbound stay
    on the root layer with no invented component. Families report the block
    definitions arrays must create with their instance multiplicities.
    """

    proposal = program.proposal
    bindings = {
        binding.binding_id: binding
        for binding in getattr(proposal, "semantic_bindings", ())
    }
    objects: dict[str, dict] = {}
    families: dict[str, int] = {}
    for operation in proposal.operations:
        kind = operation.kind.value
        parameters = _params(operation)
        for object_id in operation.output_object_ids:
            binding_ids = tuple(
                sorted(getattr(operation, "semantic_binding_ids", ()) or ())
            )
            components = sorted(
                {
                    bindings[item].component_id
                    for item in binding_ids
                    if item in bindings
                }
            )
            commitments = sorted(
                {
                    ref
                    for item in binding_ids
                    if item in bindings
                    for ref in bindings[item].commitment_refs
                }
            )
            evidence = sorted(
                {
                    ref
                    for item in binding_ids
                    if item in bindings
                    for ref in bindings[item].evidence_refs
                }
            )
            layer = _component_layer(components, layer_by_component)
            user_text = {
                "archflow:producer_op": operation.op_id,
                "archflow:object_ref": f"cad-object:{object_id}",
                "archflow:operation_ref": f"cad-operation:{operation.op_id}",
            }
            if binding_ids:
                user_text["archflow:bindings"] = ",".join(binding_ids)
            if components:
                user_text["archflow:component"] = "+".join(components)
                materials = sorted(
                    {
                        (material_by_component or {}).get(component)
                        for component in components
                    }
                    - {None}
                )
                if materials:
                    user_text["archflow:material"] = ",".join(materials)
            if commitments:
                user_text["archflow:commitments"] = ",".join(commitments)
            if evidence:
                user_text["archflow:evidence"] = ",".join(evidence)
            for key, statement in sorted(operation.statements.items()):
                if key in _RESERVED_USER_TEXT:
                    raise CadTranslationError(
                        f"statement {key!r} on {operation.op_id} would "
                        "overwrite an identity string the export owns"
                    )
                user_text[f"archflow:{key}"] = statement
            objects[object_id] = {
                "name": object_id,
                "layer": layer,
                "user_text": user_text,
            }
            if bool(parameters.get("hidden_for_inspection", False)):
                objects[object_id]["visible"] = False
                user_text["archflow:inspection_witness"] = "hidden"
        if kind in ("array", "radial_array"):
            families[f"archflow-family-{operation.op_id}"] = int(
                parameters["count"]
            )
    physical = set(_physical_ids(proposal))
    return {
        "objects": {
            object_id: row
            for object_id, row in sorted(objects.items())
            if object_id in physical
        },
        "blocks": families,
    }


def translate_to_rhino_python(
    program,
    *,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
    operation_subset: Iterable[str] | None = None,
    layer_by_component: Mapping[str, str] | None = None,
) -> CadTranslation:
    """Emit one deterministic, semantics-carrying rhinoscriptsyntax script.

    With ``operation_subset`` (P103 patch) only those operations are
    emitted, in program order, and only their physical outputs are named
    and measured; the rest of the document is the patch base's business.

    With a material assignment, component layers take the material's
    display color and objects carry ``archflow:material`` user text —
    the assignment travels with the geometry, auditable in the file.
    """

    proposal = program.proposal
    operations = {op.op_id: op for op in proposal.operations}
    order = list(program.operation_order)
    physical = _physical_ids(proposal)
    if operation_subset is not None:
        subset = set(operation_subset)
        unknown = sorted(subset - set(order))
        if unknown:
            raise ValueError(f"operation_subset names unknown operations: {unknown}")
        order = [op_id for op_id in order if op_id in subset]
        emitted = {out for op_id in order for out in operations[op_id].output_object_ids}
        physical = tuple(object_id for object_id in physical if object_id in emitted)
    semantics = expected_object_semantics(
        program,
        material_by_component=material_by_component,
        layer_by_component=layer_by_component,
    )
    losses: list[dict] = []
    lines: list[str] = [
        "import json",
        "import math",
        "import rhinoscriptsyntax as rs",
        "objects = {}",
        "counts = {}",
        "",
        "def _register(object_id, guids):",
        "    if guids is None: raise Exception('build failed: ' + object_id)",
        "    if not isinstance(guids, list): guids = [guids]",
        "    objects[object_id] = guids",
        "",
    ]
    layer_colors = _resolved_layer_colors(
        {row["layer"] for row in semantics["objects"].values()},
        material_by_component=material_by_component,
        material_colors=material_colors,
    )
    for layer_path, color in layer_colors:
        lines.append(f"rs.AddLayer({layer_path!r}, {color!r})")
    for key, value in sorted((provenance or {}).items()):
        lines.append(
            f"rs.SetDocumentUserText({f'archflow:{key}'!r}, {value!r})"
        )
    lines.append("")
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
            if not bool(params.get("retain_for_inspection", False)):
                losses.append(
                    {
                        "code": "cad.curve_reference_only",
                        "op_id": op_id,
                        "kind": kind,
                    }
                )
                lines.append(f"objects[{out!r}] = []  # reference curve omitted")
                continue
            basis = params.get("basis", "polyline")
            points = params["points"]
            pts = ", ".join(
                f"({p[0]},{p[2]},{p[1]})" for p in points
            )
            if basis == "polyline":
                lines.append(f"_register({out!r}, rs.AddPolyline([{pts}]))")
            elif basis == "bezier":
                lines.append(f"_register({out!r}, rs.AddInterpCurve([{pts}], 3))")
            else:
                raise CadTranslationError(
                    f"curve {op_id} has unsupported basis {basis!r}"
                )
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
            profile = lift_to_base_level(params["profile"], params, op_id)
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
            profiles = lift_to_base_level(params["profiles"], params, op_id)
            size = int(params["profile_size"])
            cap_ends = bool(params.get("cap_ends", True))
            loft_type = params.get("loft_type", "normal")
            profile_basis = params.get("profile_basis", "polyline")
            if loft_type not in {"normal", "straight"}:
                raise CadTranslationError(
                    f"loft {op_id} has unsupported loft_type {loft_type!r}"
                )
            if profile_basis not in {"polyline", "interpolated"}:
                raise CadTranslationError(
                    f"loft {op_id} has unsupported profile_basis "
                    f"{profile_basis!r}"
                )
            if profile_basis == "interpolated" and size < 4:
                raise CadTranslationError(
                    f"loft {op_id} interpolated profiles require at least four points"
                )
            rings = [
                profiles[i : i + size]
                for i in range(0, len(profiles), size)
            ]
            lines.append("_rings = []")
            for ring in rings:
                pts = ", ".join(
                    f"({p[0]},{p[2]},{p[1]})" for p in [*ring, ring[0]]
                )
                if profile_basis == "interpolated":
                    lines.append(f"_rings.append(rs.AddInterpCurve([{pts}], 3))")
                else:
                    lines.append(f"_rings.append(rs.AddPolyline([{pts}]))")
            lines.append(
                "_srf = rs.AddLoftSrf(_rings)"
                if loft_type == "normal"
                else "_srf = rs.AddLoftSrf(_rings, loft_type=2)"
            )
            if cap_ends:
                lines.append("rs.CapPlanarHoles(_srf[0])")
            lines.extend(
                [
                    f"_register({out!r}, _srf)",
                    "rs.DeleteObjects(_rings)",
                ]
            )
        elif kind in (
            "boolean_union",
            "boolean_difference",
            "boolean_intersection",
        ):
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
                    "_res = rs.BooleanIntersection(_ins[0], "
                    + " + ".join(
                        f"_ins[{index}]" for index in range(1, len(ins))
                    )
                    + ", True)"
                )
            lines.append(f"_register({out!r}, _res)")
        elif kind == "array":
            count = int(params["count"])
            step = params["step"]
            block = f"archflow-family-{op_id}"
            lines.extend(
                [
                    f"_seed = objects[{ins[0]!r}]",
                    "for _g in _seed: rs.ObjectColorSource(_g, 3)",
                    f"rs.AddBlock(_seed, (0,0,0), {block!r}, False)",
                    "_guids = []",
                    f"for _i in range({count}):",
                    f"    _guids.append(rs.InsertBlock({block!r}, "
                    f"({step[0]}*_i, {step[2]}*_i, {step[1]}*_i)))",
                    f"counts[{out!r}] = {count} * len(_seed)",
                    f"_register({out!r}, _guids)",
                ]
            )
        elif kind == "radial_array":
            count = int(params["count"])
            center = params["center"]
            angle = float(params["angle_step_degrees"])
            start = float(params.get("start_angle_degrees", 0.0))
            block = f"archflow-family-{op_id}"
            center_pt = f"({center[0]},{center[2]},{center[1]})"
            lines.extend(
                [
                    f"_seed = objects[{ins[0]!r}]",
                    "for _g in _seed: rs.ObjectColorSource(_g, 3)",
                    f"rs.AddBlock(_seed, {center_pt}, {block!r}, False)",
                    "_guids = []",
                    f"for _i in range({count}):",
                    f"    _guids.append(rs.InsertBlock({block!r}, "
                    f"{center_pt}, (1,1,1), -({start} + _i * {angle}), "
                    f"(0,0,1)))",
                    f"counts[{out!r}] = {count} * len(_seed)",
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
            "_semantic_table = json.loads("
            + repr(json.dumps(semantics["objects"], sort_keys=True))
            + ")",
            "_semantics = {}",
            "measures = {}",
            "for _oid in _physical:",
            "    _guids = objects.get(_oid) or []",
            "    if not _guids:",
            "        measures[_oid] = None",
            "        _semantics[_oid] = None",
            "        continue",
            "    _meta = _semantic_table.get(_oid, {})",
            "    for _g in _guids:",
            "        rs.ObjectName(_g, _oid)",
            "        if _meta.get('layer'): rs.ObjectLayer(_g, _meta['layer'])",
            "        for _k in sorted(_meta.get('user_text', {})):",
            "            rs.SetUserText(_g, _k, _meta['user_text'][_k])",
            "        if _meta.get('visible') is False: rs.HideObject(_g)",
            "    _first = _guids[0]",
            "    _keys = rs.GetUserText(_first) or []",
            "    _semantics[_oid] = {",
            "        'name': rs.ObjectName(_first),",
            "        'layer': rs.ObjectLayer(_first),",
            "        'user_text': {_k: rs.GetUserText(_first, _k) for _k in _keys},",
            "    }",
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
            "        'brep_count': counts.get(_oid, len(_guids)),",
            "    }",
            "_blocks = {}",
            "for _bn in (rs.BlockNames() or []):",
            "    if _bn.startswith('archflow-family-'):",
            "        _blocks[_bn] = rs.BlockInstanceCount(_bn)",
            "print('CAD_MEASURES=' + json.dumps(measures))",
            "print('SEMANTICS=' + json.dumps("
            "{'objects': _semantics, 'blocks': _blocks}))",
        ]
    )
    return CadTranslation(
        script="\n".join(lines),
        physical_object_ids=physical,
        losses=tuple(losses),
        layer_colors=layer_colors,
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
    boxes: set[str] = set()   # objects known to be axis-aligned boxes
    for op_id in program.operation_order:
        operation = operations[op_id]
        kind = operation.kind.value
        if kind not in _SUPPORTED:
            continue
        params = _params(operation)
        out = operation.output_object_ids[0]
        ins = list(operation.input_object_ids)
        if kind == "curve":
            if not bool(params.get("retain_for_inspection", False)):
                continue
            points[out] = [tuple(point) for point in params["points"]]
            counts[out] = 1
        elif kind == "solid":
            o, s = params["origin"], params["size"]
            points[out] = [
                (o[0] + dx * s[0], o[1] + dy * s[1], o[2] + dz * s[2])
                for dx in (0, 1)
                for dy in (0, 1)
                for dz in (0, 1)
            ]
            counts[out] = 1
            boxes.add(out)
        elif kind == "extrusion":
            profile = lift_to_base_level(params["profile"], params, op_id)
            vector = params["vector"]
            points[out] = [tuple(p) for p in profile] + [
                (p[0] + vector[0], p[1] + vector[1], p[2] + vector[2])
                for p in profile
            ]
            counts[out] = 1
            if _is_axis_aligned_box(profile, vector):
                boxes.add(out)
        elif kind == "revolve":
            a0, a1 = params["axis_start"], params["axis_end"]
            axis = [float(a1[i]) - float(a0[i]) for i in range(3)]
            axis_length = math.sqrt(sum(value * value for value in axis))
            if not math.isfinite(axis_length) or axis_length <= 0.0:
                raise CadTranslationError(
                    f"revolve {op_id} requires a finite non-zero axis"
                )
            unit_axis = [value / axis_length for value in axis]
            pts = []
            for level, radius in (
                (a0, max(float(params["start_radius"]), 0.01)),
                (a1, max(float(params["end_radius"]), 0.01)),
            ):
                projected_radii = [
                    radius * math.sqrt(max(0.0, 1.0 - component * component))
                    for component in unit_axis
                ]
                pts.extend(
                    tuple(
                        float(level[axis_index]) + sign * projected_radii[axis_index]
                        for axis_index in range(3)
                    )
                    for sign in (-1.0, 1.0)
                )
            points[out] = pts
            counts[out] = 1
        elif kind == "loft":
            points[out] = [
                tuple(p)
                for p in lift_to_base_level(params["profiles"], params, op_id)
            ]
            counts[out] = 1
        elif kind == "boolean_union":
            points[out] = [p for i in ins for p in points[i]]
            counts[out] = 1
        elif kind == "boolean_difference":
            base = sorted(ins)[int(params.get("base_index", 0))]
            base_min, base_max = _point_bounds(points[base])
            for cutter in (object_id for object_id in ins if object_id != base):
                cutter_min, cutter_max = _point_bounds(points[cutter])
                disjoint = any(
                    cutter_max[axis] < base_min[axis]
                    or cutter_min[axis] > base_max[axis]
                    for axis in range(3)
                )
                internal = [
                    base_min[axis] < cutter_min[axis]
                    and cutter_max[axis] < base_max[axis]
                    for axis in range(3)
                ]
                # Any base keeps its extrema under a cutter strictly inside
                # it on every axis. A box base also keeps them under a
                # cutter strictly inside on at least one axis: no face of
                # a box can be removed whole by such a cutter, so a
                # through-cut opening (P092) leaves the wall's bounds.
                # Other shapes may hold an extremum at a single point the
                # cutter reaches, so they fail closed.
                strictly_internal = all(internal) or (
                    base in boxes and any(internal)
                )
                if not disjoint and not strictly_internal:
                    raise CadTranslationError(
                        "boolean difference bounds are not analytically "
                        f"determined for {op_id}: cutter {cutter} can alter "
                        "a base extremum"
                    )
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


def _point_bounds(
    values: list[tuple[float, float, float]],
) -> tuple[list[float], list[float]]:
    if not values:
        raise CadTranslationError("analytic bounds require at least one point")
    return (
        [min(point[axis] for point in values) for axis in range(3)],
        [max(point[axis] for point in values) for axis in range(3)],
    )
