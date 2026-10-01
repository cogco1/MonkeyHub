"""Composing a display ``.3dm`` through rhino3dm, with no CAD host.

A composed model is an imported model with a run's native objects patched in.
``patch_composed_three_dm`` replaces one program's changed native objects.
``rewrite_composed_materials`` then gives the whole model the materials the
run declares, whatever its geometry did: every material the model carried is
cleared and each object wears what its component declares, or nothing
(#580). ``verify_composed_three_dm`` reads the final bytes back against the
model they were composed from, the native exports and those declarations.
"""

from __future__ import annotations

import base64
import math
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from archflow.contracts.canonical import canonical_digest
from archflow.state.geometry_program import CompiledGeometryProgram, delivered_object_ids
from monkeycad.backends.occt.export import _preview_materials
from monkeycad.backends.occt.preview import PreviewMaterial
from monkeycad.execution import _UNIT_TO_RHINO
from monkeycad.patch import CadPatchError, select_patch_operations
from monkeycad.program import (
    UNDECLARED_MATERIAL,
    _material_color,
    _resolved_layer_colors,
    declared_material,
    expected_object_semantics,
)

# The two labels an export writes about an object's material (``monkeycad.program``):
# the material it declares, or that it declares none.
MATERIAL_KEY = "archflow:material"
MATERIAL_STATUS_KEY = "archflow:material_status"
_MATERIAL_LABELS = frozenset({MATERIAL_KEY, MATERIAL_STATUS_KEY})
# The user string the preview's native material carries, and the tolerance its readback compares transparency within.
_MATERIAL_ID_KEY = "archflow:material_id"
_TRANSPARENCY_TOLERANCE = 1.0e-6
_NO_RENDER_MATERIAL = uuid.UUID(int=0)


@dataclass(frozen=True)
class ComposedPatch:
    """One program's native replacement, as ``patch_composed_three_dm`` takes it.

    ``prior_program`` is the program the composed model's native objects come
    from, ``program`` the one the run compiled, ``replacement_3dm`` the run's
    native export of it.
    """

    prior_program: CompiledGeometryProgram
    program: CompiledGeometryProgram
    replacement_3dm: bytes


def patch_composed_three_dm(
    base_3dm: bytes,
    *,
    prior_program: CompiledGeometryProgram,
    program: CompiledGeometryProgram,
    replacement_3dm: bytes,
) -> bytes:
    """Replace changed native objects inside an existing composed display model.

    The caller verifies the source binding and retains the returned bytes. This
    function neither writes files nor executes CAD. Patch selection comes from
    the existing compiled-program diff; imported objects and unchanged native
    objects stay in the original File3dm, including their attributes, cached
    meshes, instance definitions and document tables. Existing imported geometry
    is not revalidated as newly generated geometry.

    ``replacement_3dm`` is the native export of ``program``. Only its selected
    physical objects are imported. Its mesh/Brep objects are supported; a changed
    native block instance is refused, never silently stripped of its definition.
    Preserved block instances in the base need no reconstruction. This is a
    display-model composition, not an exact STEP export of the imported assets.
    Rebuilt logical objects retain their source GUIDs. New objects cannot take
    any source object's identity, including an object retired by this patch.
    A replacement arrives with its donor's material binding; what every object
    finally wears is decided afterwards, from the run's declared semantics, by
    ``rewrite_composed_materials``. Nothing is inherited from the base's
    materials. An empty selection returns ``base_3dm`` itself: its geometry is
    what the program says, and its materials are still rewritten.
    New native objects must use built-in linetypes: rhino3dm's custom-linetype
    table wrappers cannot safely be released on the supported Windows runtime.
    """

    import rhino3dm

    selection = select_patch_operations(program, prior_program)
    base = _read(rhino3dm, base_3dm, "composed base")
    donor = _read(rhino3dm, replacement_3dm, "native replacement")
    unit = getattr(rhino3dm.UnitSystem, _UNIT_TO_RHINO[program.proposal.length_unit.value][0])
    if donor.Settings.ModelUnitSystem != unit:
        raise CadPatchError("native replacement must use the program's length unit")
    base_unit = base.Settings.ModelUnitSystem
    unit_scale = rhino3dm.UnitSystem.UnitScale(unit, base_unit)
    if base_unit.name in {"None", "Unset", "CustomUnits"} or not math.isfinite(unit_scale) or unit_scale <= 0:
        raise CadPatchError("composed base must declare a convertible model length unit")
    scale_transform = rhino3dm.Transform.Scale(rhino3dm.Point3d(0, 0, 0), unit_scale)

    def physical(model):
        by_name = {}
        for item in model.Objects:
            if not item.Attributes.IsInstanceDefinitionObject:
                by_name.setdefault(item.Attributes.Name, []).append(item)
        return by_name

    def require_native_identity(objects, names, label):
        for name in sorted(names):
            matches = objects[name]
            if len(matches) != 1:
                raise CadPatchError(f"{label} has ambiguous native object {name}: {len(matches)} top-level objects")
            if matches[0].Attributes.GetUserString("archflow:object_ref") != f"cad-object:{name}":
                raise CadPatchError(f"{label} native object has a missing or mismatched object_ref: {name}")

    base_objects = physical(base)
    donor_objects = physical(donor)
    prior_names = set(delivered_object_ids(prior_program.proposal))
    new_names = set(delivered_object_ids(program.proposal))
    missing = prior_names - base_objects.keys()
    if missing:
        raise CadPatchError(f"composed base is missing native objects: {sorted(missing)}")
    require_native_identity(base_objects, prior_names, "composed base")
    collisions = (new_names - prior_names) & base_objects.keys()
    if collisions:
        raise CadPatchError(f"new native names collide with preserved objects: {sorted(collisions)}")
    replacement_names = new_names - set(selection.kept_object_ids)
    missing = replacement_names - donor_objects.keys()
    if missing:
        raise CadPatchError(f"native replacement is missing patch objects: {sorted(missing)}")
    require_native_identity(donor_objects, replacement_names, "native replacement")
    replacements = [item for name in sorted(replacement_names) for item in donor_objects[name]]
    source_ids = {item.Attributes.Id for item in base.Objects}
    replacement_ids = {}
    for item in replacements:
        name = item.Attributes.Name
        if name in prior_names:
            replacement_ids[name] = base_objects[name][0].Attributes.Id
        else:
            if item.Attributes.Id in source_ids:
                raise CadPatchError(f"new native object GUID collides with source identity: {name}")
            replacement_ids[name] = item.Attributes.Id
        if isinstance(item.Geometry, rhino3dm.InstanceReference):
            raise CadPatchError(f"native replacement block needs its definition: {item.Attributes.Name}")
        if not item.Geometry.IsValid:
            raise CadPatchError(f"native replacement geometry is invalid: {item.Attributes.Name}")
    if selection.empty:
        return base_3dm

    def native_material_index(model, attributes):
        if attributes.MaterialSource == rhino3dm.ObjectMaterialSource.MaterialFromObject:
            index = attributes.MaterialIndex
        elif attributes.MaterialSource == rhino3dm.ObjectMaterialSource.MaterialFromLayer:
            layer = model.Layers.FindIndex(attributes.LayerIndex)
            index = -1 if layer is None else layer.RenderMaterialIndex
        else:
            return None
        return index if index >= 0 and model.Materials.FindIndex(index) is not None else None

    # Table indices belong to a document. Copy only tables the replacement uses;
    # never change an existing base layer or material while adding native objects.
    materials: dict[int, int] = {}
    groups: dict[int, int] = {}
    layers: dict[int, int] = {}
    donor_layers = {layer.Index: layer for layer in donor.Layers}
    donor_layer_ids = {layer.Id: layer.Index for layer in donor.Layers}
    base_layer_paths = {layer.FullPath: layer.Index for layer in base.Layers}
    donor_layer_paths = {layer.Index: layer.FullPath for layer in donor.Layers}

    def copy_material(index: int) -> int:
        if index < 0:
            return index
        if index not in materials:
            source = donor.Materials.FindIndex(index)
            if source is None:
                raise CadPatchError(f"replacement material is missing: {index}")
            materials[index] = base.Materials.Add(source)
        return materials[index]

    def copy_linetype(index: int) -> int:
        if index < 0:
            return index
        raise CadPatchError("custom replacement linetypes require a native CAD export")

    def copy_layer(index: int) -> int:
        if index not in layers:
            source = donor_layers.get(index)
            if source is None:
                raise CadPatchError(f"replacement layer is missing: {index}")
            path = donor_layer_paths[index]
            if path in base_layer_paths:
                layers[index] = base_layer_paths[path]
            else:
                parent = donor_layer_ids.get(source.ParentLayerId)
                if parent is not None:
                    source.ParentLayerId = base.Layers.FindIndex(copy_layer(parent)).Id
                source.RenderMaterialIndex = copy_material(source.RenderMaterialIndex)
                source.LinetypeIndex = copy_linetype(source.LinetypeIndex)
                layers[index] = base.Layers.Add(source)
                base_layer_paths[path] = layers[index]
        return layers[index]

    def copy_group(index: int) -> int:
        if index not in groups:
            source = donor.Groups.FindIndex(index)
            if source is None:
                raise CadPatchError(f"replacement group is missing: {index}")
            base.Groups.Add(source)
            groups[index] = max(item.Index for item in base.Groups)
        return groups[index]

    deleted = {
        item.Attributes.Id
        for name in selection.delete_object_names
        for item in base_objects.get(name, ())
    }
    preserved = {item.Attributes.Id for item in base.Objects} - deleted
    for object_id in deleted:
        base.Objects.Delete(object_id)
    imported = set()
    for item in replacements:
        attributes = item.Attributes
        attributes.Id = replacement_ids[attributes.Name]
        donor_material = native_material_index(donor, attributes)
        attributes.LayerIndex = copy_layer(attributes.LayerIndex)
        if donor_material is not None:
            attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            attributes.MaterialIndex = copy_material(donor_material)
        else:
            attributes.MaterialIndex = copy_material(attributes.MaterialIndex)
        attributes.LinetypeIndex = copy_linetype(attributes.LinetypeIndex)
        old_groups = attributes.GetGroupList2()
        attributes.RemoveFromAllGroups()
        for group in old_groups:
            attributes.AddToGroup(copy_group(group))
        geometry = item.Geometry.Duplicate()
        if unit_scale != 1.0 and not geometry.Transform(scale_transform):
            raise CadPatchError(f"native replacement could not be converted to the base unit: {attributes.Name}")
        inserted_id = base.Objects.Add(geometry, attributes)
        if inserted_id != replacement_ids[attributes.Name]:
            raise CadPatchError(f"native replacement did not retain its expected GUID: {attributes.Name}")
        imported.add(inserted_id)

    encoded = base64.b64decode(base.Encode())
    reopened = _read(rhino3dm, encoded, "composed result")
    if {item.Attributes.Id for item in reopened.Objects} != preserved | imported:
        raise CadPatchError("composed result did not preserve its objects")
    result_objects = physical(reopened)
    if any(name not in result_objects for name in new_names) or any(
        name in result_objects for name in prior_names - new_names
    ):
        raise CadPatchError("composed result does not contain the native patch outputs")
    for name, expected_id in replacement_ids.items():
        if len(result_objects[name]) != 1 or result_objects[name][0].Attributes.Id != expected_id:
            raise CadPatchError(f"composed result changed native object identity: {name}")
    return encoded


def rewrite_composed_materials(
    composed_3dm: bytes,
    *,
    programs: Sequence[CompiledGeometryProgram],
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> bytes:
    """Clear every material a composed model carries and give it what the run declares (#580).

    It runs on every compose, whatever the geometry did, so a change of
    material alone reaches the composed model. ``programs`` are the run's
    compiled programs, one per patched seat, and the two maps are the run's
    ``declared_materials``. Every top-level object ends in one of two states:

    * declared: bound (material from object) to the one native material of
      its declared name, in that material's one colour, with
      ``archflow:material`` and without ``archflow:material_status``;
    * undeclared: no material (material from layer, and no layer carries
      one), with ``archflow:material_status`` = ``undeclared`` and without
      ``archflow:material``.

    An object a program delivers (by name and ``archflow:object_ref``) wears
    what that run's own preview gives it, ``_preview_materials`` of the
    program with its assembly roles, and takes its label from
    ``expected_object_semantics``; one the program binds to no component is
    undeclared here too. Any other object, imported or left by an earlier
    export, wears what the components its ``archflow:component`` names
    declare, by the same ``declared_material`` and ``_material_color``, and
    one that names no component is undeclared. Nothing is inherited from the
    materials the model carried: layer render materials are cleared and
    block definition members wear their instance's material (from parent).

    GUIDs, geometry, names, layers and every other user string are left as
    they are. A declared material reuses a table entry that is exactly the
    preview's native material for it, else one is added. An entry that no
    object wears any more stays in the table unreferenced, so no other
    reference into the table is renumbered. A model that already wears its
    declarations comes back as the same bytes.
    """

    import rhino3dm

    model = _read(rhino3dm, composed_3dm, "composed model")
    targets = _material_targets(model, programs, material_by_component, material_colors)
    from_object = rhino3dm.ObjectMaterialSource.MaterialFromObject
    from_layer = rhino3dm.ObjectMaterialSource.MaterialFromLayer
    from_parent = rhino3dm.ObjectMaterialSource.MaterialFromParent
    entries: dict[PreviewMaterial, int] = {}
    changed = False

    def entry(material: PreviewMaterial) -> int:
        nonlocal changed
        if material not in entries:
            index = next((index for index, existing in enumerate(model.Materials)
                          if _is_native_material(rhino3dm, existing, material)), None)
            if index is None:
                index = model.Materials.Add(_native_material(rhino3dm, material))
                changed = True
            entries[material] = index
        return entries[material]

    for item in model.Objects:
        attributes = item.Attributes
        if attributes.IsInstanceDefinitionObject:
            binding, labels = (from_parent, -1), {}
        else:
            target = targets[attributes.Id]
            binding = (from_layer, -1) if target.material is None else (from_object, entry(target.material))
            labels = {
                MATERIAL_KEY: target.declared or "",
                MATERIAL_STATUS_KEY: "" if target.declared else UNDECLARED_MATERIAL,
            }
        if (attributes.MaterialSource, attributes.MaterialIndex) != binding:
            attributes.MaterialSource, attributes.MaterialIndex = binding
            changed = True
        for key, value in labels.items():
            # rhino3dm reads an absent key as "" and removes a key set to "".
            if attributes.GetUserString(key) != value:
                attributes.SetUserString(key, value)
                changed = True
    for layer in model.Layers:
        if layer.RenderMaterialIndex != -1:
            layer.RenderMaterialIndex = -1
            changed = True
    if not changed:
        return composed_3dm
    encoded = base64.b64decode(model.Encode())
    reopened = _read(rhino3dm, encoded, "rewritten composed model")
    if {item.Attributes.Id for item in reopened.Objects} != {item.Attributes.Id for item in model.Objects}:
        raise CadPatchError("rewriting the composed model's materials changed its objects")
    return encoded


def verify_composed_three_dm(
    composed_3dm: bytes,
    *,
    base_3dm: bytes,
    patches: Sequence[ComposedPatch],
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> None:
    """Read a final composed model back before it is retained; ``CadPatchError`` names what differs.

    Fresh reads of the final bytes, of ``base_3dm`` they were composed from
    and of each patch's native export. Identity and geometry are what the
    patches define, in order: an object no patch replaced has its GUID, name,
    layer, every user string but its two material labels, and its encoded
    geometry from the base; a rebuilt native object has the base's GUID and
    a new one the export's, with the export's name, layer, user strings and
    encoded geometry converted to the base's unit; block definition members
    are the base's. Geometry is compared by content, a digest of each
    object's encoded geometry, never by bounds. Materials are what
    ``rewrite_composed_materials`` states for these declarations: a declared
    object is bound to a table entry that is exactly its native material,
    an undeclared one wears none, its labels say which, no layer carries a
    render material and block members wear their instance's material.
    """

    import rhino3dm

    final = _read(rhino3dm, composed_3dm, "composed result")
    source = _read(rhino3dm, base_3dm, "composed base")
    failures: list[str] = []
    if final.Settings.ModelUnitSystem != source.Settings.ModelUnitSystem:
        failures.append("its length unit differs from its base's")
    expected = _object_rows(source, members=False)
    expected_members = _object_rows(source, members=True)
    for patch in patches:
        donor = _read(rhino3dm, patch.replacement_3dm, "native replacement")
        selection = select_patch_operations(patch.program, patch.prior_program)
        prior_names = set(delivered_object_ids(patch.prior_program.proposal))
        named: dict[str, list[Any]] = {}
        for object_id, row in expected.items():
            named.setdefault(row.name, []).append(object_id)
        for name in selection.delete_object_names:
            for object_id in named.get(name, ()):
                del expected[object_id]
        scale = rhino3dm.UnitSystem.UnitScale(donor.Settings.ModelUnitSystem, source.Settings.ModelUnitSystem)
        transform = None if scale == 1.0 else rhino3dm.Transform.Scale(rhino3dm.Point3d(0, 0, 0), scale)
        donor_layers = {layer.Index: layer.FullPath for layer in donor.Layers}
        exports: dict[str, list[Any]] = {}
        for item in donor.Objects:
            if not item.Attributes.IsInstanceDefinitionObject:
                exports.setdefault(item.Attributes.Name, []).append(item)
        for name in sorted(set(delivered_object_ids(patch.program.proposal)) - set(selection.kept_object_ids)):
            items = exports.get(name, [])
            if len(items) != 1:
                failures.append(f"the native export holds {len(items)} objects named {name}")
                continue
            rebuilt = named.get(name, []) if name in prior_names else []
            object_id = rebuilt[0] if len(rebuilt) == 1 else items[0].Attributes.Id
            expected[object_id] = _object_row(items[0], donor_layers, transform)
    _compare_rows(failures, "object", expected, _object_rows(final, members=False))
    _compare_rows(failures, "block member", expected_members, _object_rows(final, members=True))

    targets = _material_targets(final, tuple(patch.program for patch in patches), material_by_component, material_colors)
    from_object = rhino3dm.ObjectMaterialSource.MaterialFromObject
    from_layer = rhino3dm.ObjectMaterialSource.MaterialFromLayer
    from_parent = rhino3dm.ObjectMaterialSource.MaterialFromParent
    for layer in final.Layers:
        if layer.RenderMaterialIndex != -1:
            failures.append(f"layer {layer.FullPath} still carries a render material")
    for item in final.Objects:
        attributes = item.Attributes
        label = f"{attributes.Name or '(unnamed)'} {attributes.Id}"
        binding = (attributes.MaterialSource, attributes.MaterialIndex)
        if attributes.IsInstanceDefinitionObject:
            if binding != (from_parent, -1):
                failures.append(f"block member {label} does not wear its instance's material")
            continue
        target = targets[attributes.Id]
        if target.material is None:
            if binding != (from_layer, -1):
                failures.append(f"object {label} declares no material but wears one")
        else:
            material = final.Materials.FindIndex(attributes.MaterialIndex) if attributes.MaterialIndex >= 0 else None
            if (attributes.MaterialSource != from_object or material is None
                    or not _is_native_material(rhino3dm, material, target.material)):
                failures.append(f"object {label} does not wear material {target.material.name}")
        labels = (attributes.GetUserString(MATERIAL_KEY), attributes.GetUserString(MATERIAL_STATUS_KEY))
        wanted = (target.declared, "") if target.declared else ("", UNDECLARED_MATERIAL)
        if labels != wanted:
            failures.append(f"object {label} is labelled {labels} rather than {wanted}")
    if failures:
        shown = "; ".join(failures[:12])
        more = f" (and {len(failures) - 12} more)" if len(failures) > 12 else ""
        raise CadPatchError(f"the composed model does not read back as composed: {shown}{more}")


@dataclass(frozen=True)
class _Wears:
    """What one top-level object wears, and the material it declares (None: it declares none)."""

    material: PreviewMaterial | None
    declared: str | None


@dataclass(frozen=True)
class _ObjectRow:
    """One saved object as the readback compares it."""

    name: str
    layer: str | None
    # Every user string but the two material labels, which the rewrite owns.
    strings: tuple[tuple[str, str], ...]
    geometry: str


def _read(rhino3dm: Any, data: bytes, label: str) -> Any:
    if not isinstance(data, bytes) or not data:
        raise CadPatchError(f"{label} must contain 3DM bytes")
    try:
        model = rhino3dm.File3dm.FromByteArray(data)
    except Exception as exc:
        raise CadPatchError(f"{label} is not a readable 3DM") from exc
    if model is None:
        raise CadPatchError(f"{label} is not a readable 3DM")
    return model


def _material_targets(
    model: Any,
    programs: Sequence[CompiledGeometryProgram],
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> dict[Any, _Wears]:
    """What each top-level object of the model wears, by GUID."""

    native: dict[str, _Wears] = {}
    for program in programs:
        for name, wears in _native_wears(program, material_by_component, material_colors).items():
            if name in native:
                raise CadPatchError(f"two composed programs deliver native object {name}")
            native[name] = wears
    targets: dict[Any, _Wears] = {}
    found: dict[str, int] = {}
    for item in model.Objects:
        attributes = item.Attributes
        if attributes.IsInstanceDefinitionObject:
            continue
        name = attributes.Name
        if name in native and attributes.GetUserString("archflow:object_ref") == f"cad-object:{name}":
            found[name] = found.get(name, 0) + 1
            targets[attributes.Id] = native[name]
        else:
            targets[attributes.Id] = _declared_wears(
                attributes.GetUserString("archflow:component"), material_by_component, material_colors)
    wrong = sorted(name for name in native if found.get(name, 0) != 1)
    if wrong:
        raise CadPatchError(f"the composed model does not hold each native object exactly once: {wrong}")
    return targets


def _native_wears(
    program: CompiledGeometryProgram,
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> dict[str, _Wears]:
    """What the run's own preview gives each object the program delivers, and the material it declares."""

    semantics = expected_object_semantics(program, material_by_component=material_by_component)
    objects = semantics["objects"]
    layer_colors = dict(_resolved_layer_colors(
        {row["layer"] for row in objects.values()},
        material_by_component=material_by_component, material_colors=material_colors,
    ))
    worn = _preview_materials(program, physical=tuple(sorted(objects)), semantics=semantics,
                              layer_colors=layer_colors, material_colors=material_colors)
    return {name: _Wears(worn.get(name), row["user_text"].get(MATERIAL_KEY)) for name, row in objects.items()}


def _declared_wears(
    component_text: str | None,
    material_by_component: Mapping[str, str] | None,
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> _Wears:
    """What an object no program delivers wears: the material the components it names declare, or none."""

    components = tuple(part for part in (component_text or "").split("+") if part)
    declared = declared_material(components, material_by_component)
    if declared is None:
        return _Wears(None, None)
    color = _material_color(declared, material_colors)
    return _Wears(PreviewMaterial(name=declared, diffuse=tuple(int(channel) for channel in color)), declared)


def _native_material(rhino3dm: Any, material: PreviewMaterial) -> Any:
    """The table entry the preview writes for a material: its name, colour, transparency and ``archflow:material_id``."""

    native = rhino3dm.Material()
    native.Name = material.name
    red, green, blue = material.diffuse
    native.DiffuseColor = (int(red), int(green), int(blue), 255)
    native.Transparency = material.transparency
    native.SetUserString(_MATERIAL_ID_KEY, material.name)
    return native


def _is_native_material(rhino3dm: Any, existing: Any, material: PreviewMaterial) -> bool:
    """Whether a table entry is that native material and nothing more: no texture, render material or other look."""

    return (
        existing.Name == material.name
        and dict(existing.GetUserStrings() or ()) == {_MATERIAL_ID_KEY: material.name}
        and existing.RenderMaterialInstanceId == _NO_RENDER_MATERIAL
        and rhino3dm.Material.CompareAppearance(existing, _native_material(rhino3dm, material)) == 0
        and abs(float(existing.Transparency) - material.transparency) <= _TRANSPARENCY_TOLERANCE
    )


def _object_rows(model: Any, *, members: bool) -> dict[Any, _ObjectRow]:
    """The model's top-level objects, or its block definition members, by GUID."""

    layers = {layer.Index: layer.FullPath for layer in model.Layers}
    return {
        item.Attributes.Id: _object_row(item, layers, None)
        for item in model.Objects
        if bool(item.Attributes.IsInstanceDefinitionObject) == members
    }


def _object_row(item: Any, layers: Mapping[int, str], transform: Any) -> _ObjectRow:
    attributes = item.Attributes
    geometry = item.Geometry
    if transform is not None:
        geometry = geometry.Duplicate()
        if not geometry.Transform(transform):
            raise CadPatchError(f"native replacement could not be converted to the base unit: {attributes.Name}")
    strings = tuple(sorted(
        (str(key), str(value)) for key, value in (attributes.GetUserStrings() or ()) if key not in _MATERIAL_LABELS
    ))
    return _ObjectRow(attributes.Name, layers.get(attributes.LayerIndex), strings, canonical_digest(geometry.Encode()))


def _compare_rows(
    failures: list[str], what: str, expected: Mapping[Any, _ObjectRow], actual: Mapping[Any, _ObjectRow],
) -> None:
    missing = sorted(str(object_id) for object_id in expected.keys() - actual.keys())
    extra = sorted(str(object_id) for object_id in actual.keys() - expected.keys())
    if missing:
        failures.append(f"{len(missing)} {what}(s) missing: {missing[:5]}")
    if extra:
        failures.append(f"{len(extra)} unexpected {what}(s): {extra[:5]}")
    for object_id in sorted(expected.keys() & actual.keys(), key=str):
        want, got = expected[object_id], actual[object_id]
        label = f"{what} {got.name or '(unnamed)'} {object_id}"
        if got.geometry != want.geometry:
            failures.append(f"{label} geometry differs from its source")
        if (got.name, got.layer) != (want.name, want.layer):
            failures.append(f"{label} name or layer differs from its source")
        if got.strings != want.strings:
            failures.append(f"{label} user strings other than its material labels differ from its source")
