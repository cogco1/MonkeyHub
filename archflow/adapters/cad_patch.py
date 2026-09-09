"""P103: incremental Rhino patch — select what a program change actually touches.

A patch rebuilds only the operations whose outputs changed (by object
digest or analytic bounds), plus every operation connected to them through
input edges (consumers that would read a rebuilt object, producers of the
inputs a rebuilt operation consumes). Everything else is carried over from
the prior saved document by name, block-instance families included: an
array's instance definition is rebuilt from the prior file's definition
members and its references are re-added against it (P099 typed instances). The full rebuild stays the oracle: a
patch must read back to the same denominator as a rebuild of the same
program, and the plan carries the selection so the receipt says which path
ran.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from archflow.adapters.cad_program import _physical_ids, expected_object_bounds, expected_object_semantics

_WITNESS_PREFIX = "__archflow_visible_bounds__"


class CadPatchError(ValueError):
    """A patch cannot be selected or expressed; the caller rebuilds instead."""


@dataclass(frozen=True)
class PatchSelection:
    """Which operations a patch rebuilds and which prior objects it removes."""

    prior_program_digest: str
    program_digest: str
    changed_object_ids: tuple[str, ...]
    added_object_ids: tuple[str, ...]
    retired_object_ids: tuple[str, ...]
    rebuilt_op_ids: tuple[str, ...]
    delete_object_names: tuple[str, ...]
    kept_object_ids: tuple[str, ...]
    reasons: Mapping[str, str] = field(default_factory=dict)

    SCHEMA = "RhinoPatchSelection@1"

    @property
    def empty(self) -> bool:
        return not (self.changed_object_ids or self.added_object_ids or self.retired_object_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "prior_program_digest": self.prior_program_digest,
            "program_digest": self.program_digest,
            "changed_object_ids": list(self.changed_object_ids),
            "added_object_ids": list(self.added_object_ids),
            "retired_object_ids": list(self.retired_object_ids),
            "rebuilt_op_ids": list(self.rebuilt_op_ids),
            "delete_object_names": list(self.delete_object_names),
            "kept_object_ids": list(self.kept_object_ids),
            "reasons": dict(sorted(self.reasons.items())),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _bounds_differ(a: Mapping[str, Any], b: Mapping[str, Any], tolerance: float = 1e-9) -> bool:
    for key in ("bbox_min", "bbox_max"):
        for x, y in zip(a[key], b[key]):
            if abs(float(x) - float(y)) > tolerance:
                return True
    return int(a.get("brep_count", 1)) != int(b.get("brep_count", 1))


def structural_digests(program) -> dict[str, str]:
    """Per-object digests of what the CAD script actually emits for the object.

    The compiler's object digest also folds in the semantic binding's whole
    object list and its evidence refs, so adding one element (or re-recording
    the same geometry under a new state record) would mark every object
    changed. This digest takes the operation itself, its frame and the
    structural digests of its inputs — nothing else. Semantics (layer, user
    text, visibility) are re-stamped on kept objects by the patch prelude,
    so they never decide a rebuild.
    """

    frames = dict(program.frame_digests)
    ops = {op.op_id: op for op in program.proposal.operations}
    producer = {out: op.op_id for op in ops.values() for out in op.output_object_ids}
    digests: dict[str, str] = {}
    for op_id in program.operation_order:
        op = ops[op_id]
        payload = op.to_dict()
        payload.pop("semantic_binding_ids", None)
        inputs = []
        for inp in op.input_object_ids:
            if inp in producer and inp not in digests:
                raise CadPatchError(f"operation order does not produce {inp} before {op_id}")
            inputs.append(digests.get(inp, inp))
        for out in op.output_object_ids:
            row = {"op": payload, "frame": frames.get(op.frame_id), "inputs": inputs, "output": out}
            digests[out] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return digests


def select_patch_operations(program, prior_program) -> PatchSelection:
    """Diff two compiled programs into a closed set of operations to rebuild.

    An object counts as changed when its structural digest differs *or* its
    analytic bounds differ (a datum value can move an object whose operation
    text is unchanged). The rebuilt set is closed under input edges in both
    directions, so every object a rebuilt operation reads is rebuilt too and
    nothing in the script refers to an object the prior document holds.
    """

    if program.proposal.length_unit != prior_program.proposal.length_unit:
        raise CadPatchError("patch across a unit change is not expressible; rebuild")
    new_digest = structural_digests(program)
    old_digest = structural_digests(prior_program)
    new_bounds = expected_object_bounds(program)
    old_bounds = expected_object_bounds(prior_program)
    reasons: dict[str, str] = {}
    changed: list[str] = []
    for object_id in sorted(new_digest):
        if object_id not in old_digest:
            continue
        if new_digest[object_id] != old_digest[object_id]:
            changed.append(object_id)
            reasons[object_id] = "digest"
        elif object_id in new_bounds and object_id in old_bounds and _bounds_differ(new_bounds[object_id], old_bounds[object_id]):
            changed.append(object_id)
            reasons[object_id] = "bounds"
    added = sorted(o for o in new_digest if o not in old_digest)
    retired = sorted(o for o in old_digest if o not in new_digest)
    for object_id in added:
        reasons[object_id] = "added"
    for object_id in retired:
        reasons[object_id] = "retired"

    ops = {op.op_id: op for op in program.proposal.operations}
    producer: dict[str, str] = {}
    consumers: dict[str, set[str]] = {}
    for op in ops.values():
        for out in op.output_object_ids:
            producer[out] = op.op_id
        for inp in op.input_object_ids:
            consumers.setdefault(inp, set()).add(op.op_id)
    seed = {producer[o] for o in changed + added if o in producer}
    closed = set(seed)
    frontier = list(seed)
    while frontier:
        op = ops[frontier.pop()]
        linked: set[str] = set()
        for out in op.output_object_ids:
            linked |= consumers.get(out, set())
        for inp in op.input_object_ids:
            if inp in producer:
                linked.add(producer[inp])
        for op_id in linked:
            if op_id not in closed:
                closed.add(op_id)
                frontier.append(op_id)
    rebuilt = tuple(op_id for op_id in program.operation_order if op_id in closed)

    prior_physical = set(_physical_ids(prior_program.proposal))
    rebuilt_outputs = {out for op_id in rebuilt for out in ops[op_id].output_object_ids}
    delete_names = tuple(sorted((rebuilt_outputs | set(retired)) & prior_physical))
    kept = tuple(sorted(prior_physical - set(delete_names)))

    new_physical = set(_physical_ids(program.proposal))
    realized = set(kept) | (rebuilt_outputs & new_physical)
    if realized != new_physical:
        missing = sorted(new_physical - realized)
        extra = sorted(realized - new_physical)
        raise CadPatchError(f"patch does not realize the program: missing {missing}, extra {extra}")
    for object_id in kept:
        if new_digest.get(object_id) != old_digest.get(object_id):
            raise CadPatchError(f"kept object {object_id} has a different digest in the new program")
    return PatchSelection(
        prior_program_digest=prior_program.program_digest,
        program_digest=program.program_digest,
        changed_object_ids=tuple(changed),
        added_object_ids=tuple(added),
        retired_object_ids=tuple(retired),
        rebuilt_op_ids=rebuilt,
        delete_object_names=delete_names,
        kept_object_ids=kept,
        reasons=reasons,
    )


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
