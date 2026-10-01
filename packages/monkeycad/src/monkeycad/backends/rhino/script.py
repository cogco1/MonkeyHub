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

What each object carries, including every operation's statements, is
``monkeycad.program.expected_object_semantics``; this module writes it.
The same module writes the rest of the script a Rhino export runs: the
patch prelude that carries a prior document's kept objects into a fresh one
(P103), and the wrapper that saves the document with its render meshes and
writes the completion marker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from archflow.state.geometry_program import (
    ANALYTIC_OPERATION_KINDS,
    delivered_object_ids,
    lift_to_base_level,
    operation_parameters,
    revolve_parameters,
)
from monkeycad.execution import _UNIT_TO_RHINO, _positive_finite
from monkeycad.patch import CadPatchError, PatchSelection
from monkeycad.program import CadTranslationError, _resolved_layer_colors, _rgb, expected_object_semantics


# Windows refuses an ordinary absolute path once it passes about 260
# characters, and a run's export workspace - project, run id, stage, seat,
# attempt - is often longer than that. The extended-length form names the same
# file, and which call uses it was measured on a real Rhino 8 host rather than
# generalized: the host's Python (whose interpreter does not honour this
# machine's long-path setting) and Rhino's readers, File3dm.Read and
# FileStp.Read, take it; RhinoDoc.WriteFile was seen to refuse it and keeps the
# ordinary absolute name, and the File3dm archive write - which real exports
# write through on the ordinary name - keeps it too, its acceptance of the
# prefix never having been tested separately. Nothing persisted changes:
# receipts keep the ordinary relative identities they always had.
#
# The prefix is spelled with ``chr(92)`` because this text is emitted into
# another Python file, where a literal backslash would be escaped twice.
LONG_PATH_HELPER_SOURCE: tuple[str, ...] = (
    "import os as _os",
    "def _long(_path):",
    "    _text = _os.fspath(_path)",
    "    if _os.name != 'nt':",
    "        return Path(_text)",
    "    _extended = chr(92) * 2 + '?' + chr(92)",
    "    _text = _os.path.abspath(_text)",
    "    if _text.startswith(_extended):",
    "        return Path(_text)",
    "    if _text.startswith(chr(92) * 2):",
    "        return Path(_extended + 'UNC' + _text[1:])",
    "    return Path(_extended + _text)",
    "",
)


@dataclass(frozen=True, slots=True)
class CadTranslation:
    script: str
    physical_object_ids: tuple[str, ...]
    losses: tuple[dict, ...]
    layer_colors: tuple[tuple[str, tuple[int, int, int]], ...]


def _script_header(
    program,
    semantics: Mapping[str, dict],
    *,
    provenance: Mapping[str, str] | None,
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> tuple[list[str], tuple[tuple[str, tuple[int, int, int]], ...]]:
    """The opening every emitted script shares: layers, materials, provenance.

    ``objects`` and ``counts`` are what the body fills in - one entry per
    physical object - and the tail below reads them. Both the program script
    and the STEP-import script are the same document apart from the body, so
    a document written either way carries the same semantics and is verified
    against the same denominator.
    """

    lines: list[str] = [
        "import json",
        "import math",
        "import Rhino",
        "import rhinoscriptsyntax as rs",
        "from pathlib import Path",
        *LONG_PATH_HELPER_SOURCE,
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
    native_colors = {}
    layer_color_map = dict(layer_colors)
    for object_id in delivered_object_ids(program.proposal):
        row = semantics["objects"][object_id]
        material = row["user_text"].get("archflow:material")
        if material:
            native_colors.setdefault(material, _rgb(
                (material_colors or {}).get(material, layer_color_map[row["layer"]]),
                f"material color for {material}",
            ))
    lines.extend([
        f"_material_colors = {native_colors!r}",
        "_native_materials = dict(globals().get('_patch_native_materials', {}))",
        "def _assign_native_material(_g, _meta):",
        "    _material_name = _meta.get('user_text', {}).get('archflow:material')",
        "    if not _material_name: return",
        "    _material_index = _native_materials.get(_material_name)",
        "    if _material_index is None:",
        "        rs.ObjectMaterialIndex(_g, -1)",
        "        _material_index = rs.AddMaterialToObject(_g)",
        "        if _material_index is None or _material_index < 0: raise Exception('material failed: ' + _material_name)",
        "        rs.MaterialName(_material_index, _material_name)",
        "        _native_materials[_material_name] = _material_index",
        "    else:",
        "        rs.ObjectMaterialIndex(_g, _material_index)",
        "    rs.MaterialColor(_material_index, _material_colors[_material_name])",
        "    _material = Rhino.RhinoDoc.ActiveDoc.Materials[_material_index]",
        "    if _material.IsPhysicallyBased:",
        "        _rgb = _material_colors[_material_name]",
        "        _alpha = _material.PhysicallyBased.BaseColor.A",
        "        _material.PhysicallyBased.BaseColor = Rhino.Display.Color4f(_rgb[0] / 255.0, _rgb[1] / 255.0, _rgb[2] / 255.0, _alpha)",
        "        _material.CommitChanges()",
        "    rs.ObjectMaterialSource(_g, 1)",
    ])
    for key, value in sorted((provenance or {}).items()):
        lines.append(
            f"rs.SetDocumentUserText({f'archflow:{key}'!r}, {value!r})"
        )
    lines.append("")
    return lines, layer_colors


def _script_tail(
    semantics: Mapping[str, dict], physical: Sequence[str], *, work_materials: bool = False
) -> list[str]:
    """The closing every emitted script shares: semantics, measures, report.

    Whatever the body built or imported, every physical object is named,
    put on its layer, given its user text and measured here, and the
    document reports the same two lines the Python side reads back.

    Order matters and is the point of this one loop. Assigning a material in
    Rhino replaces an object's attributes - ``AddMaterialToObject`` modifies
    the whole attribute set - so every material an object is going to wear is
    applied first: the program's own, then, with ``work_materials``, the
    material this delivery was exported with. Only then are the name, layer,
    user text and visibility written, and they are what the readback counts.
    An export that put a material on afterwards left two objects with no user
    strings at all and failed on its semantic witnesses.
    """

    return [
        "",
        f"_physical = {list(physical)!r}",
        "for _oid, _guids in objects.items():",
        "    if _oid not in _physical:",
        "        rs.DeleteObjects(_guids)",
        "_semantic_table = json.loads("
        + repr(json.dumps(semantics["objects"], sort_keys=True))
        + ")",
        "for _kept_name, _kept_guids in globals().get('_patch_kept_objects', {}).items():",
        "    for _kept_guid in _kept_guids: _assign_native_material(_kept_guid, _semantic_table[_kept_name])",
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
        "        _assign_native_material(_g, _meta)",
        *((
            "        _declared = _work_materials.get(_oid)",
            "        if _declared is not None: _assign_work_material(_g, _declared)",
        ) if work_materials else ()),
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


def translate_step_import_to_rhino_python(
    program,
    *,
    step_file_by_object: Mapping[str, str],
    step_sha256_by_object: Mapping[str, str] | None = None,
    object_materials: Mapping[str, Mapping[str, object]] | None = None,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
    layer_by_component: Mapping[str, str] | None = None,
    material_by_part: Mapping[str, Mapping[str, str | None]] | None = None,
) -> CadTranslation:
    """Emit the script that imports one already exact STEP per physical object.

    The geometry is not rebuilt: each file holds the one named shape the
    program's own STEP export wrote, and the script reads it with Rhino's
    STEP reader, then hands whatever objects that one read produced to
    ``_register`` under the object id of the file it came from. A named
    shape that arrives as several Breps stays one semantic object with
    several objects under it; the shared tail measures and counts them, and
    the ordinary readback compares that against the program's denominator.

    Identity comes from which file was read, never from import order, a
    bounding box or a name the reader may or may not set: Rhino's STEP
    reader leaves ``Name`` empty, which is exactly why the files are read
    one at a time.
    """

    semantics = expected_object_semantics(
        program,
        material_by_component=material_by_component,
        material_by_part=material_by_part,
        layer_by_component=layer_by_component,
    )
    physical = delivered_object_ids(program.proposal)
    missing = [object_id for object_id in physical if object_id not in step_file_by_object]
    if missing:
        raise CadTranslationError(
            "the STEP export has no file for physical object(s): " + ", ".join(sorted(missing))
        )
    unknown = sorted(set(step_file_by_object) - set(physical))
    if unknown:
        raise CadTranslationError(
            "these files name objects the program does not deliver: " + ", ".join(unknown)
        )
    lines, layer_colors = _script_header(
        program,
        semantics,
        provenance=provenance,
        material_by_component=material_by_component,
        material_colors=material_colors,
    )
    digests = dict(step_sha256_by_object or {})
    if object_materials:
        unknown_materials = sorted(set(object_materials) - set(physical))
        if unknown_materials:
            raise CadTranslationError(
                "materials name objects the program does not deliver: " + ", ".join(unknown_materials)
            )
        lines.extend([
            # The material each object wears in this program's own delivery -
            # the same names, colours and transparency the mesh preview of this
            # model carries, so a work model does not arrive plain white or with
            # its glass opaque. A material already in the document under that
            # name is reused rather than added a second time.
            "_work_materials = json.loads("
            + repr(json.dumps({key: dict(value) for key, value in sorted(object_materials.items())}, sort_keys=True))
            + ")",
            # Two objects share a material only when the material they were
            # delivered with is the same one: the same name with a different
            # colour or transparency - a frame shaded per layer, say - is a
            # different material and gets its own.
            "_work_material_index = {}",
            "def _assign_work_material(_g, _declared):",
            "    _name = _declared['name']",
            "    _key = json.dumps(_declared, sort_keys=True)",
            "    _index = _work_material_index.get(_key)",
            "    if _index is None:",
            "        rs.ObjectMaterialIndex(_g, -1)",
            "        _index = rs.AddMaterialToObject(_g)",
            "        if _index is None or _index < 0: raise Exception('material failed: ' + _name)",
            "        rs.MaterialName(_index, _name)",
            "        _work_material_index[_key] = _index",
            "    else:",
            "        rs.ObjectMaterialIndex(_g, _index)",
            "    rs.MaterialColor(_index, tuple(_declared['diffuse']))",
            "    _material = Rhino.RhinoDoc.ActiveDoc.Materials[_index]",
            "    _material.Transparency = _declared.get('transparency', 0.0)",
            "    if _material.IsPhysicallyBased:",
            "        _rgb = _declared['diffuse']",
            "        _material.PhysicallyBased.BaseColor = Rhino.Display.Color4f(_rgb[0] / 255.0, _rgb[1] / 255.0, _rgb[2] / 255.0, 1.0)",
            "        _material.PhysicallyBased.Opacity = 1.0 - _declared.get('transparency', 0.0)",
            "    _material.CommitChanges()",
            "    rs.ObjectMaterialSource(_g, 1)",
            "",
        ])
    lines.extend([
        "import hashlib",
        "_import_options = Rhino.FileIO.FileStpReadOptions()",
        "def _import_one(object_id, file_name, file_sha256):",
        "    _document = Rhino.RhinoDoc.ActiveDoc",
        "    _path = _long(_script_directory / file_name)",
        "    if not _path.is_file(): raise Exception('import source missing: ' + file_name)",
        # The bytes are checked here, in the host, right before they are read:
        # the file the script imports is the one the plan measured, or nothing
        # is imported from it.
        "    _actual = hashlib.sha256(_path.read_bytes()).hexdigest()",
        "    if file_sha256 and _actual != file_sha256:",
        "        raise Exception('import source changed: ' + file_name + ' is ' + _actual)",
        # Each file is read into a document of its own, and only the geometry
        # it produced is brought over. A STEP read carries its own layer table
        # with it - a stray empty layer literally named ``archflow::portico``
        # beside the real child of that name - and the delivered document is
        # the program's, so that table never enters it. The layer, name, user
        # text and material every object ends up with are the ones the shared
        # tail assigns from the program's own semantics.
        "    _source = Rhino.RhinoDoc.CreateHeadless(None)",
        "    try:",
        # Units first, before anything is read: a headless document starts in
        # millimetres, and reading a metre STEP into it would arrive a
        # thousand times too large. ``False`` sets the unit without scaling,
        # so the geometry keeps the one conversion this export already made.
        "        _source.AdjustModelUnitSystem(_document.ModelUnitSystem, False)",
        "        if not Rhino.FileIO.FileStp.Read(str(_path), _source, _import_options):",
        "            raise Exception('STEP import failed: ' + file_name)",
        "        _added = []",
        "        for _object in list(_source.Objects):",
        "            _geometry = _object.Geometry",
        "            if _geometry is None: continue",
        "            _attributes = Rhino.DocObjects.ObjectAttributes()",
        "            if isinstance(_geometry, Rhino.Geometry.Brep):",
        "                _guid = _document.Objects.AddBrep(_geometry, _attributes)",
        "            elif isinstance(_geometry, Rhino.Geometry.Extrusion):",
        "                _guid = _document.Objects.AddExtrusion(_geometry, _attributes)",
        "            else:",
        "                _guid = _document.Objects.Add(_geometry, _attributes)",
        "            if str(_guid) == '00000000-0000-0000-0000-000000000000':",
        "                raise Exception('imported geometry could not be added: ' + file_name)",
        "            _added.append(str(_guid))",
        "    finally:",
        # The temporary document is this script's own; the architect's active
        # document is never one of these.
        "        _source.Dispose()",
        # One named shape can arrive as several B-reps; they all belong to this
        # one object, and the count is what actually came over.
        "    if not _added: raise Exception('STEP import added no object: ' + file_name)",
        "    _register(object_id, _added)",
        "    counts[object_id] = len(_added)",
        "",
    ])
    for object_id in physical:
        lines.append(
            f"_import_one({object_id!r}, {step_file_by_object[object_id]!r}, "
            f"{digests.get(object_id, '')!r})"
        )
    # The tail applies this delivery's own material right after the program's,
    # and before any metadata is written: the retained material still has the
    # last word on what the object wears, and the name, layer and user text
    # written after it survive.
    lines.extend(_script_tail(semantics, physical, work_materials=bool(object_materials)))
    return CadTranslation(
        script="\n".join(lines),
        physical_object_ids=tuple(physical),
        losses=(),
        layer_colors=layer_colors,
    )


def translate_to_rhino_python(
    program,
    *,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
    operation_subset: Iterable[str] | None = None,
    layer_by_component: Mapping[str, str] | None = None,
    material_by_part: Mapping[str, Mapping[str, str | None]] | None = None,
) -> CadTranslation:
    """Emit one deterministic, semantics-carrying rhinoscriptsyntax script.

    With ``operation_subset`` (P103 patch) only those operations are
    emitted, in program order, and only their physical outputs are named
    and measured; the rest of the document is the patch base's business.

    With a material assignment, component layers take the material's display
    color and objects carry both a native rendering material and the matching
    ``archflow:material`` user text.
    """

    proposal = program.proposal
    operations = {op.op_id: op for op in proposal.operations}
    order = list(program.operation_order)
    physical = delivered_object_ids(proposal)
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
        material_by_part=material_by_part,
        layer_by_component=layer_by_component,
    )
    losses: list[dict] = []
    lines, layer_colors = _script_header(
        program,
        semantics,
        provenance=provenance,
        material_by_component=material_by_component,
        material_colors=material_colors,
    )
    for op_id in order:
        operation = operations[op_id]
        kind = operation.kind.value
        if kind not in ANALYTIC_OPERATION_KINDS:
            losses.append(
                {
                    "code": "cad.unsupported_operation",
                    "op_id": op_id,
                    "kind": kind,
                }
            )
            continue
        params = operation_parameters(operation)
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
            points = lift_to_base_level(params["points"], params, op_id)
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
            a0, a1, r0, r1 = revolve_parameters(params, op_id)
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
        elif kind == "planar_surface":
            profile = lift_to_base_level(params["profile"], params, op_id)
            pts = ", ".join(f"({p[0]},{p[2]},{p[1]})" for p in profile)
            lines.extend([
                f"_crv = rs.AddPolyline([{pts}])",
                "_srf = rs.AddPlanarSrf(_crv)",
                "if not _srf or len(_srf) != 1: raise RuntimeError('planar_surface did not produce one face')",
                f"_register({out!r}, _srf)",
                "rs.DeleteObject(_crv)",
            ])
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
    lines.extend(_script_tail(semantics, physical))
    return CadTranslation(
        script="\n".join(lines),
        physical_object_ids=physical,
        losses=tuple(losses),
        layer_colors=layer_colors,
    )


_WITNESS_PREFIX = "__archflow_visible_bounds__"


def build_patch_prelude(selection: PatchSelection, *, prior_model_path: Path, semantics: Mapping[str, Mapping[str, Any]]) -> str:
    """Rhino-side prelude: carry the prior document's kept objects into a fresh one.

    Referenced materials are copied with their textures and remapped; layers
    are recreated by full path, then every instance definition the
    prior file holds is rebuilt from its own member geometry so that block
    families (P099 typed instances) survive the carry. Kept objects are
    re-added by geometry and duplicated attributes, instance references
    against their rebuilt definition, and each is re-stamped with the *new*
    program's semantics for that name (layer, user text, visibility):
    geometry is kept, identity is refreshed. Witness meshes and every name
    the selection deletes are skipped. The carried *names* are compared with
    the selection, so a stale base fails typed inside Rhino rather than
    reading back short.
    """

    prior = str(Path(prior_model_path).resolve())
    if any(character in prior for character in "\x00\r\n'"):
        raise CadPatchError("prior model path contains unsafe characters")
    delete_literal = repr(sorted(selection.delete_object_names))
    missing = [object_id for object_id in selection.kept_object_ids if object_id not in semantics]
    if missing:
        raise CadPatchError(f"kept objects have no semantics in the new program: {missing}")
    kept_semantics = {object_id: dict(semantics[object_id]) for object_id in selection.kept_object_ids}
    semantics_literal = repr(json.dumps(kept_semantics, sort_keys=True))
    expected_names_literal = repr(json.dumps(sorted(selection.kept_object_ids)))
    return "\n".join(
        (
            "import System",
            f"_patch_base_path = Path({prior!r})",
            "_patch_base = Rhino.FileIO.File3dm.Read(str(_patch_base_path))",
            "if _patch_base is None: raise Exception('patch base unreadable: ' + str(_patch_base_path))",
            "_existing = rs.AllObjects() or []",
            "if _existing: rs.DeleteObjects(_existing)",
            f"_patch_delete = set({delete_literal})",
            f"_patch_semantics = json.loads({semantics_literal})",
            f"_patch_expected_names = set(json.loads({expected_names_literal}))",
            f"_patch_witness_prefix = {_WITNESS_PREFIX!r}",
            "_patch_material_indices = {_layer.RenderMaterialIndex for _layer in _patch_base.Layers if _layer.RenderMaterialIndex >= 0}",
            "for _item in _patch_base.Objects:",
            "    if (_item.Attributes.Name or '').startswith(_patch_witness_prefix) or _item.Attributes.Name in _patch_delete: continue",
            "    if _item.Attributes.MaterialIndex >= 0: _patch_material_indices.add(_item.Attributes.MaterialIndex)",
            "_patch_materials, _patch_native_materials = {}, {}",
            "for _base_index in sorted(_patch_material_indices):",
            "    _material = _patch_base.Materials.FindIndex(_base_index)",
            "    if _material is None: raise Exception('patch base material missing: ' + str(_base_index))",
            "    _new_index = Rhino.RhinoDoc.ActiveDoc.Materials.Add(_material)",
            "    if _new_index < 0: raise Exception('patch base material could not be copied: ' + str(_base_index))",
            "    _patch_materials[_base_index] = _new_index",
            "    if _material.Name: _patch_native_materials[_material.Name] = _new_index",
            "    _logical_material = _material.GetUserString('archflow:material_id') or _material.GetUserString('archflow:material')",
            "    if _logical_material: _patch_native_materials[_logical_material] = _new_index",
            "_patch_base_layers = {_layer.Index: _layer for _layer in _patch_base.Layers}",
            "_patch_base_by_id = {str(_layer.Id): _layer for _layer in _patch_base.Layers}",
            "def _patch_full_path(_layer):",
            "    _names = [_layer.Name]",
            "    _parent = _patch_base_by_id.get(str(_layer.ParentLayerId))",
            "    while _parent is not None:",
            "        _names.insert(0, _parent.Name)",
            "        _parent = _patch_base_by_id.get(str(_parent.ParentLayerId))",
            "    return '::'.join(_names)",
            "_patch_layers = {}",
            "for _base_index in sorted(_patch_base_layers):",
            "    _base_layer = _patch_base_layers[_base_index]",
            "    _full = _patch_full_path(_base_layer)",
            "    rs.AddLayer(_full, (_base_layer.Color.R, _base_layer.Color.G, _base_layer.Color.B))",
            "    _doc_index = Rhino.RhinoDoc.ActiveDoc.Layers.FindByFullPath(_full, -1)",
            "    if _doc_index < 0: raise Exception('patch base layer could not be recreated: ' + _full)",
            "    _patch_layers[_base_index] = _doc_index",
            "    if _base_layer.RenderMaterialIndex >= 0:",
            "        _doc_layer = Rhino.RhinoDoc.ActiveDoc.Layers[_doc_index]",
            "        _doc_layer.RenderMaterialIndex = _patch_materials[_base_layer.RenderMaterialIndex]",
            "        _doc_layer.CommitChanges()",
            # ---- instance definitions: rebuilt from their own members, so block families survive the carry
            "_patch_base_objects = {str(_item.Attributes.ObjectId): _item for _item in _patch_base.Objects}",
            "_patch_definition_index = {}",
            "_patch_definition_members = set()",
            "for _definition in _patch_base.InstanceDefinitions:",
            "    _member_ids = [str(_value) for _value in (_definition.GetObjectIds() or [])]",
            "    _member_geometry, _member_attributes = [], []",
            "    for _member_id in _member_ids:",
            "        _member = _patch_base_objects.get(_member_id)",
            "        if _member is None: raise Exception('instance definition member missing from the patch base: ' + _member_id)",
            "        _patch_definition_members.add(_member_id)",
            "        _member_attribute = _member.Attributes.Duplicate()",
            "        _member_attribute.LayerIndex = _patch_layers[_member.Attributes.LayerIndex]",
            "        _member_attribute.MaterialIndex = _patch_materials.get(_member.Attributes.MaterialIndex, -1)",
            "        _member_geometry.append(_member.Geometry)",
            "        _member_attributes.append(_member_attribute)",
            "    if not _member_geometry: raise Exception('instance definition has no members: ' + _definition.Name)",
            "    _new_index = Rhino.RhinoDoc.ActiveDoc.InstanceDefinitions.Add(_definition.Name, _definition.Description, Rhino.Geometry.Point3d.Origin, _member_geometry, _member_attributes)",
            "    if _new_index < 0: raise Exception('instance definition could not be recreated: ' + _definition.Name)",
            "    _patch_definition_index[str(_definition.Id)] = _new_index",
            # ---- kept objects, instance references against their rebuilt definition
            "_patch_carried = {}",
            "_patch_kept_objects = {}",
            "for _base_object in _patch_base.Objects:",
            "    _base_name = _base_object.Attributes.Name or ''",
            "    if _base_name.startswith(_patch_witness_prefix) or _base_name in _patch_delete: continue",
            "    if str(_base_object.Attributes.ObjectId) in _patch_definition_members: continue",
            "    _base_attributes = _base_object.Attributes.Duplicate()",
            "    _base_attributes.LayerIndex = _patch_layers[_base_object.Attributes.LayerIndex]",
            "    _base_attributes.MaterialIndex = _patch_materials.get(_base_object.Attributes.MaterialIndex, -1)",
            "    _base_geometry = _base_object.Geometry",
            "    if isinstance(_base_geometry, Rhino.Geometry.InstanceReferenceGeometry):",
            "        _definition_key = str(_base_geometry.ParentIdefId)",
            "        if _definition_key not in _patch_definition_index: raise Exception('instance reference has no carried definition: ' + _base_name)",
            "        _kept_guid = Rhino.RhinoDoc.ActiveDoc.Objects.AddInstanceObject(_patch_definition_index[_definition_key], _base_geometry.Xform, _base_attributes)",
            "    else:",
            "        _kept_guid = Rhino.RhinoDoc.ActiveDoc.Objects.Add(_base_geometry, _base_attributes)",
            "    if _kept_guid == System.Guid.Empty: raise Exception('patch base object could not be re-added: ' + _base_name)",
            "    _kept_meta = _patch_semantics.get(_base_name)",
            "    if _kept_meta is None: raise Exception('patch base object is not in the kept set: ' + _base_name)",
            "    if _kept_meta.get('layer'): rs.ObjectLayer(_kept_guid, _kept_meta['layer'])",
            "    for _old_key in (rs.GetUserText(_kept_guid) or []):",
            "        rs.SetUserText(_kept_guid, _old_key, None)",
            "    for _key in sorted(_kept_meta.get('user_text', {})):",
            "        rs.SetUserText(_kept_guid, _key, _kept_meta['user_text'][_key])",
            "    if _kept_meta.get('visible') is False: rs.HideObject(_kept_guid)",
            "    else: rs.ShowObject(_kept_guid)",
            "    _patch_carried[_base_name] = _patch_carried.get(_base_name, 0) + 1",
            "    _patch_kept_objects.setdefault(_base_name, []).append(_kept_guid)",
            "if set(_patch_carried) != _patch_expected_names:",
            "    raise Exception('patch base carried the wrong object names: missing %s, extra %s' % (sorted(_patch_expected_names - set(_patch_carried)), sorted(set(_patch_carried) - _patch_expected_names)))",
        )
    )


def _saved_geometry_check(source_measures: Mapping[str, Mapping[str, object]] | None) -> tuple[str, ...]:
    """The lines that compare the saved document with the shapes it came from.

    Only an import-based export has shapes to compare against, and this is the
    one place where the host can say what the saved geometry actually is: the
    kernel's solid count, face count, closure and volume are checked against
    the objects in the file that was just written, through RhinoCommon's own
    mass properties, before the completion marker exists. A healed-away
    opening or a solid that arrived as a surface fails the export here rather
    than being reported as an exact work model.
    """

    if not source_measures:
        return ()
    return (
        "_source_measures = json.loads("
        + repr(json.dumps({key: dict(value) for key, value in sorted(source_measures.items())}, sort_keys=True))
        + ")",
        "_saved_by_name = {}",
        "for _saved in _final_archive.Objects:",
        "    _saved_by_name.setdefault(_saved.Attributes.Name or '', []).append(_saved.Geometry)",
        "for _oid in sorted(_source_measures):",
        "    _expected = _source_measures[_oid]",
        "    _geometries = _saved_by_name.get(_oid) or []",
        "    if not _geometries: raise Exception('saved work model has no object named ' + _oid)",
        "    _solids = 0",
        "    _faces = 0",
        "    _volume = 0.0",
        "    for _geometry in _geometries:",
        "        if isinstance(_geometry, Rhino.Geometry.Extrusion): _geometry = _geometry.ToBrep(False)",
        "        if not isinstance(_geometry, Rhino.Geometry.Brep):",
        "            raise Exception(_oid + ': saved geometry is ' + type(_geometry).__name__ + ', not a B-rep')",
        "        _faces += _geometry.Faces.Count",
        "        if _geometry.IsSolid: _solids += 1",
        "        _mass = Rhino.Geometry.VolumeMassProperties.Compute(_geometry)",
        "        if _mass is not None: _volume += _mass.Volume",
        "    if _solids != _expected['solid_count']:",
        "        raise Exception(_oid + ': saved solids ' + str(_solids) + ' != ' + str(_expected['solid_count']))",
        "    if _faces != _expected['face_count']:",
        "        raise Exception(_oid + ': saved faces ' + str(_faces) + ' != ' + str(_expected['face_count']))",
        "    if _expected['closed'] and _solids < 1:",
        "        raise Exception(_oid + ': the exported shape is closed; the saved object is not')",
        "    if _expected.get('volume') is not None:",
        "        _allowed = max(abs(_expected['volume']) * 1e-6, 1e-9)",
        "        if abs(_volume - _expected['volume']) > _allowed:",
        "            raise Exception(_oid + ': saved volume ' + str(_volume) + ' != ' + str(_expected['volume']))",
    )


def _export_script(
    translated_script: str,
    *,
    artifact_name: str,
    completion_marker_name: str,
    completion_token: str,
    length_unit: str,
    readback_tolerance: float,
    patch_prelude: str | None = None,
    source_measures: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    unit_enum = _UNIT_TO_RHINO[length_unit][0]
    mesh_tolerance = _positive_finite(
        readback_tolerance,
        "readback_tolerance",
    ) / 4.0
    mesh_tolerance_literal = format(mesh_tolerance, ".17g")
    success_payload = _completion_marker_payload(
        artifact_relative_path=artifact_name,
        completion_token=completion_token,
        status="succeeded",
    )
    opening = (
        patch_prelude.rstrip("\n")
        if patch_prelude
        else "_existing = rs.AllObjects() or []\nif _existing: rs.DeleteObjects(_existing)"
    )
    body = "\n".join(
        (
            f"Rhino.RhinoDoc.ActiveDoc.AdjustModelUnitSystem(Rhino.UnitSystem.{unit_enum}, False)",
            opening,
            "rs.EnableRedraw(False)",
            translated_script.rstrip("\n"),
            "rs.EnableRedraw(True)",
            "_mesh_type = Rhino.Geometry.MeshType.Render",
            "_mesh_parameters = Rhino.Geometry.MeshingParameters(Rhino.Geometry.MeshingParameters.QualityRenderMesh)",
            "_mesh_parameters.DoublePrecision = True",
            f"_mesh_parameters.Tolerance = {mesh_tolerance_literal}",
            f"_mesh_parameters.MinimumTolerance = {mesh_tolerance_literal}",
            "_archive_meshes = {}",
            "_mesh_objects = {}",
            "for _active_guid in (rs.AllObjects() or []):",
            "    _active_object = Rhino.RhinoDoc.ActiveDoc.Objects.FindId(_active_guid)",
            "    if _active_object is not None:",
            "        _mesh_objects[str(_active_object.Id)] = _active_object",
            "for _instance_definition in Rhino.RhinoDoc.ActiveDoc.InstanceDefinitions:",
            "    if _instance_definition.IsDeleted or _instance_definition.IsReference:",
            "        continue",
            "    for _definition_object in _instance_definition.GetObjects():",
            "        _mesh_objects[str(_definition_object.Id)] = _definition_object",
            "for _rhino_object in sorted(_mesh_objects.values(), key=lambda _item: str(_item.Id)):",
            "    _geometry = _rhino_object.Geometry",
            "    if not isinstance(_geometry, (Rhino.Geometry.Brep, Rhino.Geometry.Extrusion)):",
            "        continue",
            "    _rhino_object.CreateMeshes(_mesh_type, _mesh_parameters, True)",
            "    _retained_meshes = _rhino_object.GetMeshes(_mesh_type)",
            "    _expected_meshes = _geometry.Faces.Count if isinstance(_geometry, Rhino.Geometry.Brep) else 1",
            "    if _retained_meshes is None or len(_retained_meshes) != _expected_meshes:",
            "        raise Exception('retained render-mesh face denominator mismatch')",
            "    _mesh_copies = []",
            "    for _retained_mesh in _retained_meshes:",
            "        if _retained_mesh is None or not _retained_mesh.IsValid or _retained_mesh.Vertices.Count == 0 or _retained_mesh.Faces.Count == 0:",
            "            raise Exception('retained render mesh is missing or invalid')",
            "        _mesh_copies.append(_retained_mesh.DuplicateMesh())",
            "    _archive_meshes[str(_rhino_object.Id)] = _mesh_copies",
            "if not _archive_meshes:",
            "    raise Exception('no meshable document geometry was enumerated')",
            f"_artifact_name = {artifact_name!r}",
            "_output_path = (_script_directory / _artifact_name).resolve()",
            "if _output_path.parent != _script_directory:",
            "    raise Exception('output escaped script workspace')",
            "if _long(_output_path).exists(): raise Exception('output exists')",
            "_raw_path = (_script_directory / (Path(_artifact_name).stem + '.archflow-raw.3dm')).resolve()",
            "if _raw_path.parent != _script_directory or _long(_raw_path).exists():",
            "    raise Exception('raw output path is invalid or occupied')",
            "_write_options = Rhino.FileIO.FileWriteOptions()",
            "_write_options.SuppressDialogBoxes = True",
            "_write_options.IncludeRenderMeshes = True",
            "if not Rhino.RhinoDoc.ActiveDoc.WriteFile(str(_raw_path), _write_options):",
            "    raise Exception('raw 3dm save failed')",
            "_archive = Rhino.FileIO.File3dm.Read(str(_long(_raw_path)))",
            "if _archive is None:",
            "    raise Exception('raw 3dm readback failed inside Rhino')",
            "_archive_sources = {str(_item.Attributes.ObjectId): _item for _item in _archive.Objects}",
            "_initial_archive_object_count = _archive.Objects.Count",
            "_witness_mesh_count = sum(len(_items) for _items in _archive_meshes.values())",
            "for _source_id in sorted(_archive_meshes):",
            "    _source_object = _archive_sources.get(_source_id)",
            "    if _source_object is None:",
            "        raise Exception('archive retained-mesh source identity is missing')",
            "    _saved_meshes = _archive_meshes[_source_id]",
            "    _source_geometry = _source_object.Geometry",
            "    _expected_saved_meshes = _source_geometry.Faces.Count if isinstance(_source_geometry, Rhino.Geometry.Brep) else 1",
            "    if len(_saved_meshes) != _expected_saved_meshes:",
            "        raise Exception('archive retained-mesh source denominator mismatch')",
            "    for _mesh_index, _saved_mesh in enumerate(_saved_meshes):",
            "        _witness_attributes = Rhino.DocObjects.ObjectAttributes()",
            "        _witness_attributes.Name = '__archflow_visible_bounds__:' + _source_id + ':' + format(_mesh_index, '04d')",
            "        _witness_attributes.LayerIndex = _source_object.Attributes.LayerIndex",
            "        _witness_attributes.Visible = False",
            "        _witness_attributes.SetUserString('archflow:visible_bounds_witness_for', _source_id)",
            "        _witness_attributes.SetUserString('archflow:visible_bounds_witness_index', str(_mesh_index))",
            "        _witness_attributes.SetUserString('archflow:visible_bounds_witness_count', str(len(_saved_meshes)))",
            "        _witness_id = _archive.Objects.AddMesh(_saved_mesh, _witness_attributes)",
            "        if str(_witness_id) == '00000000-0000-0000-0000-000000000000':",
            "            raise Exception('failed to add explicit visible-bounds witness mesh')",
            "if _archive.Objects.Count != _initial_archive_object_count + _witness_mesh_count:",
            "    raise Exception('explicit witness mesh archive count mismatch before save')",
            "_archive_options = Rhino.FileIO.File3dmWriteOptions()",
            "_archive_options.Version = 8",
            "_archive_options.SaveRenderMeshes = True",
            "_archive_options.SaveUserData = True",
            "if not _archive.Write(str(_output_path), _archive_options):",
            "    raise Exception('final 3dm save failed')",
            "_final_archive = Rhino.FileIO.File3dm.Read(str(_long(_output_path)))",
            "if _final_archive is None or _final_archive.Objects.Count != _archive.Objects.Count:",
            "    raise Exception('explicit witness mesh archive count mismatch after save')",
            *_saved_geometry_check(source_measures),
            "_long(_raw_path).unlink()",
        )
    )
    indented_body = "\n".join(
        ("    " + line if line else "") for line in body.splitlines()
    )
    return "\n".join(
        (
            "#! python 3",
            "import json",
            "from pathlib import Path",
            "import Rhino",
            "import rhinoscriptsyntax as rs",
            *LONG_PATH_HELPER_SOURCE,
            "_script_directory = Path(__file__).resolve().parent",
            f"_marker_path = _script_directory / {completion_marker_name!r}",
            f"_completion_token = {completion_token!r}",
            "def _write_completion_marker(_payload):",
            "    _text = json.dumps(_payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))",
            # The marker is the one file a failure still has to write, and it
            # sits deepest in the workspace: like every other file call in this
            # script it is made through the extended-length name.
            "    with _long(_marker_path).open('x', encoding='utf-8', newline='\\n') as _stream:",
            "        _stream.write(_text)",
            "try:",
            indented_body,
            "except Exception as _error:",
            "    try:",
            "        _write_completion_marker({",
            "            'schema': 'RhinoCadCompletionMarker@1',",
            f"            'artifact_relative_path': {artifact_name!r},",
            "            'completion_token': _completion_token,",
            "            'status': 'failed',",
            "            'error_type': type(_error).__name__[:128],",
            "            'error_detail': str(_error)[:1000],",
            "        })",
            "    finally:",
            "        raise",
            "else:",
            f"    _write_completion_marker({success_payload!r})",
            "",
        )
    )


def _completion_marker_payload(
    *,
    artifact_relative_path: str,
    completion_token: str,
    status: str,
) -> dict[str, str]:
    return {
        "schema": "RhinoCadCompletionMarker@1",
        "artifact_relative_path": artifact_relative_path,
        "completion_token": completion_token,
        "status": status,
    }
