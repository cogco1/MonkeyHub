"""Blender-process mesh build and independent saved-scene readback.

Invoked with Blender's ``--python`` flag. This worker imports no ArchFlow
modules and receives already-derived Z-up geometry from its caller.
"""

from __future__ import annotations

import argparse
import json
import math
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
        if "projection" not in plan:
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

    if "projection" in plan:
        scene["archflow_projection"] = json.dumps(plan["projection"], sort_keys=True, separators=(",", ":"))
        _presentation(scene, plan["projection"]["presentation"])
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


def _presentation(scene, settings):
    import bpy
    from mathutils import Vector

    points = [obj.matrix_world @ vertex.co for obj in scene.objects if obj.type == "MESH" for vertex in obj.data.vertices]
    low = Vector([min(p[i] for p in points) for i in range(3)])
    high = Vector([max(p[i] for p in points) for i in range(3)])
    target = (low + high) / 2
    extent = max(high - low)
    collection = bpy.data.collections.new("Presentation")
    collection["projection_presentation"] = True
    scene.collection.children.link(collection)
    camera_data = bpy.data.cameras.new(settings["camera_id"])
    camera = bpy.data.objects.new("Projection camera", camera_data)
    camera["projection_camera_id"] = settings["camera_id"]
    collection.objects.link(camera)
    azimuth, elevation = math.radians(settings["azimuth"]), math.radians(settings["elevation"])
    direction = Vector((math.cos(azimuth) * math.cos(elevation), math.sin(azimuth) * math.cos(elevation), math.sin(elevation)))
    camera.location = target + direction * extent * 3
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = extent * 1.8
    camera_data.clip_end = extent * 20
    camera_data.clip_start = extent * 0.001
    scene.camera = camera
    for name, position, power in (("key", (2, -3, 4), 350), ("fill", (-3, -1, 2), 200)):
        data = bpy.data.lights.new(name, "AREA")
        obj = bpy.data.objects.new("Projection " + name, data)
        obj["projection_light_id"] = name
        collection.objects.link(obj)
        obj.location = target + Vector(position) * extent
        obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()
        data.energy = power * extent * extent
        data.shape = "DISK"
        data.size = extent * 3
    world = bpy.data.worlds.new("Projection world")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.16, 0.16, 0.16, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.5
    scene.render.engine = settings["engine"]
    scene.cycles.device = settings["device"]
    scene.cycles.samples = settings["samples"]
    scene.cycles.seed = settings["seed"]
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.render.resolution_x = scene.render.resolution_y = settings["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = settings["view_transform"]
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene["projection_visual_state"] = json.dumps(_visual_state(scene), sort_keys=True)


def _visual_state(scene):
    camera = scene.camera
    def vector(value):
        return [float(v) for v in value]
    lights = sorted((obj for obj in scene.objects if obj.type == "LIGHT"), key=lambda obj: obj.get("projection_light_id", ""))
    materials = sorted((obj for obj in scene.objects if obj.type == "MESH"), key=lambda obj: obj.get("archflow:object_ref", ""))
    return {"camera_id": camera.get("projection_camera_id"), "camera_position": vector(camera.location),
            "camera_rotation": vector(camera.rotation_euler), "camera_type": camera.data.type,
            "ortho_scale": camera.data.ortho_scale, "clip": [camera.data.clip_start, camera.data.clip_end],
            "lights": [{"id": o.get("projection_light_id"), "position": vector(o.location),
                        "rotation": vector(o.rotation_euler), "energy": o.data.energy, "size": o.data.size,
                        "type": o.data.type, "shape": o.data.shape} for o in lights],
            "materials": [{"object_id": o.get("archflow:object_ref"),
                           "base_color": vector(o.active_material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)} for o in materials],
            "engine": scene.render.engine, "device": scene.cycles.device,
            "samples": scene.cycles.samples, "seed": scene.cycles.seed,
            "denoising": scene.cycles.use_denoising, "adaptive_sampling": scene.cycles.use_adaptive_sampling,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage],
            "format": scene.render.image_settings.file_format, "color_mode": scene.render.image_settings.color_mode,
            "view_transform": scene.view_settings.view_transform, "exposure": scene.view_settings.exposure,
            "gamma": scene.view_settings.gamma,
            "world_color": vector(scene.world.node_tree.nodes["Background"].inputs[0].default_value),
            "world_strength": scene.world.node_tree.nodes["Background"].inputs[1].default_value}


def inspect(model_path: Path, render_path: Path | None = None) -> None:
    import bpy

    if not model_path.is_absolute() or not model_path.is_file():
        raise ValueError("inspection requires an existing absolute model path")
    bpy.ops.wm.open_mainfile(filepath=str(model_path), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    projection_json = scene.get("archflow_projection")
    objects = [_read_object(obj) for obj in scene.objects
               if not projection_json or obj.type not in ("CAMERA", "LIGHT")]
    objects.sort(key=lambda row: (str(row["object_id"]), row["display_name"]))
    readback = {
        "blender_version": bpy.app.version_string,
        "binding_json": scene.get("archflow_binding"),
        "provenance_json": scene.get("archflow_provenance"),
        "length_unit": scene.get("archflow_length_unit"),
        "unit_scale": scene.unit_settings.scale_length,
        "objects": objects,
    }
    if projection_json:
        settings = json.loads(projection_json)["presentation"]
        visual_state = _visual_state(scene)
        readback.update(projection_json=projection_json, visual_state=visual_state,
                        presentation_verified=(visual_state == json.loads(scene["projection_visual_state"])
                            and visual_state["camera_id"] == settings["camera_id"]
                            and visual_state["engine"] == settings["engine"]
                            and visual_state["samples"] == settings["samples"]
                            and visual_state["resolution"] == [settings["resolution"], settings["resolution"], 100]))
    if render_path is not None:
        if not projection_json or not readback.get("presentation_verified"):
            raise ValueError("render requires verified projection presentation")
        if not render_path.is_absolute() or render_path.parent != model_path.parent or render_path.exists():
            raise ValueError("render requires a new path beside the scene")
        scene.render.filepath = str(render_path)
        bpy.ops.render.render(write_still=True)
    print(READBACK_PREFIX + json.dumps(readback), flush=True)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("plan", type=Path)
    build_parser.add_argument("model", type=Path)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("model", type=Path)
    render_parser = commands.add_parser("render")
    render_parser.add_argument("model", type=Path)
    render_parser.add_argument("image", type=Path)
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.command == "build":
        build(args.plan, args.model)
    elif args.command == "render":
        inspect(args.model, args.image)
    else:
        inspect(args.model)


if __name__ == "__main__":
    main()
