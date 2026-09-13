"""Blender-process mesh build and independent saved-scene readback.

Invoked with Blender's ``--python`` flag. This worker imports no ArchFlow
modules and receives already-derived Z-up geometry from its caller.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


READBACK_PREFIX = "ARCHFLOW_BLENDER_READBACK "
UNIT_SETTINGS = {
    "meter": ("METRIC", 1.0),
    "millimeter": ("METRIC", 0.001),
    "inch": ("IMPERIAL", 0.0254),
    "foot": ("IMPERIAL", 0.3048),
}


def build(plan_path: Path, model_path: Path) -> None:
    import bmesh
    import bpy

    if not plan_path.is_absolute() or not model_path.is_absolute():
        raise ValueError("plan and model must use explicit absolute paths")
    plan_path = plan_path.resolve(strict=True)
    workspace = model_path.parent.resolve(strict=True)
    if workspace != plan_path.parent or model_path.suffix.lower() != ".blend":
        raise ValueError("model must be a .blend file in the plan's workspace")
    model_path = workspace / model_path.name
    if model_path.exists() or model_path.is_symlink():
        raise FileExistsError(f"refusing to overwrite {model_path.name}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    unit_system, unit_scale = UNIT_SETTINGS[plan["length_unit"]]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene["archflow_binding"] = plan["binding_json"]
    scene["archflow_provenance"] = plan["provenance_json"]
    scene["archflow_length_unit"] = plan["length_unit"]
    scene.unit_settings.system = unit_system
    scene.unit_settings.scale_length = unit_scale
    collections = {}
    materials = {}
    for row in plan["objects"]:
        semantics = row["semantics"]
        mesh = bpy.data.meshes.new(row["object_id"])
        mesh.from_pydata(row["vertices"], [], row["faces"])
        mesh.update()
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            bm.to_mesh(mesh)
        finally:
            bm.free()
        mesh.update()
        obj = bpy.data.objects.new(row["object_id"], mesh)
        for key, value in semantics["user_text"].items():
            obj[key] = value
        obj["archflow_object_digest"] = row["object_digest"]
        layer = semantics["layer"]
        if layer not in collections:
            collection = bpy.data.collections.new(layer)
            collection["archflow_layer"] = layer
            scene.collection.children.link(collection)
            collections[layer] = collection
        collections[layer].objects.link(obj)
        visible = semantics.get("visible", True)
        obj.hide_viewport = not visible
        obj.hide_render = not visible
        material = row.get("material")
        if material is not None:
            material_key = (material["name"], tuple(material["color"]))
            if material_key not in materials:
                native = bpy.data.materials.new(material["name"])
                native.diffuse_color = material["color"]
                native.use_nodes = True
                shader = native.node_tree.nodes.get("Principled BSDF")
                shader.inputs["Base Color"].default_value = material["color"]
                shader.inputs["Alpha"].default_value = material["color"][3]
                materials[material_key] = native
            mesh.materials.append(materials[material_key])

    bpy.context.preferences.filepaths.save_version = 0
    result = bpy.ops.wm.save_as_mainfile(
        filepath=str(model_path), check_existing=False, relative_remap=False,
        copy=True, compress=False,
    )
    if "FINISHED" not in result or not model_path.is_file():
        raise RuntimeError("Blender did not save the requested model")


def _plain_property(value):
    """Keep scalar types and custom-property containers visible in JSON."""
    if hasattr(value, "to_dict"):
        return {key: _plain_property(item) for key, item in value.to_dict().items()}
    if hasattr(value, "to_list"):
        return [_plain_property(item) for item in value.to_list()]
    return value


def _read_object(obj) -> dict:
    import bmesh

    user_text = {key: _plain_property(obj[key]) for key in obj.keys() if key.startswith("archflow:")}
    reference = user_text.get("archflow:object_ref")
    object_id = reference.removeprefix("cad-object:") if isinstance(reference, str) and reference.startswith("cad-object:") else None
    layers = [collection.get("archflow_layer") for collection in obj.users_collection]
    row = {
        "object_id": object_id or None,
        "object_digest": obj.get("archflow_object_digest"),
        "display_name": obj.name,
        "type": obj.type,
        "vertices": [],
        "faces": [],
        "bounds": None,
        "closed": False,
        "volume": None,
        "layer": layers[0] if len(layers) == 1 else None,
        "user_text": user_text,
        "visible": not obj.hide_viewport and not obj.hide_render,
        "material": None,
    }
    if obj.type != "MESH":
        return row
    mesh = obj.data
    vertices = [list(obj.matrix_world @ vertex.co) for vertex in mesh.vertices]
    row["vertices"] = vertices
    row["faces"] = [list(face.vertices) for face in mesh.polygons]
    if vertices:
        row["bounds"] = {"min": [min(vertex[axis] for vertex in vertices) for axis in range(3)],
                         "max": [max(vertex[axis] for vertex in vertices) for axis in range(3)]}
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.transform(obj.matrix_world)
        bm.normal_update()
        row["closed"] = bool(bm.faces) and all(edge.is_manifold for edge in bm.edges)
        row["volume"] = abs(bm.calc_volume(signed=True))
    finally:
        bm.free()
    material = obj.active_material
    if material is not None:
        row["material"] = {"name": material.name, "color": list(material.diffuse_color)}
    return row


def inspect(model_path: Path) -> None:
    import bpy

    if not model_path.is_absolute() or not model_path.is_file():
        raise ValueError("inspection requires an existing absolute model path")
    bpy.ops.wm.open_mainfile(filepath=str(model_path), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    objects = [_read_object(obj) for obj in scene.objects]
    objects.sort(key=lambda row: (str(row["object_id"]), row["display_name"]))
    readback = {
        "blender_version": bpy.app.version_string,
        "binding_json": scene.get("archflow_binding"),
        "provenance_json": scene.get("archflow_provenance"),
        "length_unit": scene.get("archflow_length_unit"),
        "unit_scale": scene.unit_settings.scale_length,
        "objects": objects,
    }
    print(READBACK_PREFIX + json.dumps(readback), flush=True)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("plan", type=Path)
    build_parser.add_argument("model", type=Path)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("model", type=Path)
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.command == "build":
        build(args.plan, args.model)
    else:
        inspect(args.model)


if __name__ == "__main__":
    main()
