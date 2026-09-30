"""Replacing changed native objects inside a composed display ``.3dm``, through rhino3dm; no CAD host."""

from __future__ import annotations

import base64
import math

from archflow.state.geometry_program import CompiledGeometryProgram, delivered_object_ids
from monkeycad.execution import _UNIT_TO_RHINO
from monkeycad.patch import CadPatchError, select_patch_operations


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
    A replacement without a native material keeps the existing object's native
    material, or a new object's unambiguous component material. Explicit donor
    materials take precedence; inherited materials retain their PBR and textures.
    New native objects must use built-in linetypes: rhino3dm's custom-linetype
    table wrappers cannot safely be released on the supported Windows runtime.
    """

    import rhino3dm

    selection = select_patch_operations(program, prior_program)

    def read(data: bytes, label: str):
        if not isinstance(data, bytes) or not data:
            raise CadPatchError(f"{label} must contain 3DM bytes")
        try:
            model = rhino3dm.File3dm.FromByteArray(data)
        except Exception as exc:
            raise CadPatchError(f"{label} is not a readable 3DM") from exc
        if model is None:
            raise CadPatchError(f"{label} is not a readable 3DM")
        return model

    base = read(base_3dm, "composed base")
    donor = read(replacement_3dm, "native replacement")
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

    source_materials = {}
    component_materials: dict[str, set[int]] = {}
    for name in prior_names:
        attributes = base_objects[name][0].Attributes
        index = native_material_index(base, attributes)
        if index is None:
            continue
        source_materials[name] = index
        component = attributes.GetUserString("archflow:component")
        if component:
            component_materials.setdefault(component, set()).add(index)

    inherited_materials = {}
    for item in replacements:
        attributes = item.Attributes
        if native_material_index(donor, attributes) is not None:
            continue
        index = source_materials.get(attributes.Name)
        if index is None:
            component = attributes.GetUserString("archflow:component")
            candidates = component_materials.get(component, set())
            if len(candidates) == 1:
                index = next(iter(candidates))
        if index is not None:
            inherited_materials[attributes.Name] = index

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
        inherited = inherited_materials.get(attributes.Name)
        if inherited is not None:
            attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            attributes.MaterialIndex = inherited
            material = base.Materials.FindIndex(inherited)
            logical = material.GetUserString("archflow:material_id") or material.GetUserString("archflow:material")
            if logical:
                attributes.SetUserString("archflow:material", logical)
        elif donor_material is not None:
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
    reopened = read(encoded, "composed result")
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
