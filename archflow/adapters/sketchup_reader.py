"""Read SKP face geometry with the local, standalone SketchUp C API.

No desktop session, project paths, or file writes. The caller retains the SKP;
this mesh scene is a derived representation, not a native editable conversion.
API reference: https://extensions.sketchup.com/developers/sketchup_c_api/sketchup/
"""
from __future__ import annotations

import ctypes as c
from contextlib import contextmanager
import math
import os
from pathlib import Path
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from .local_cad_discovery import discover_local_cad
from .model_formats import ConversionError, Mesh, Scene


_SDK_LOCK = Lock()  # SUInitialize / SUTerminate and all intervening calls are serial.
_IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)
_OBJECT_NAMESPACE = uuid5(NAMESPACE_URL, "archflow:sketchup:instance-path-and-tag")


class _Ref(c.Structure):
    _fields_ = [("ptr", c.c_void_p)]


class _Point(c.Structure):
    _fields_ = [("x", c.c_double), ("y", c.c_double), ("z", c.c_double)]


class _Transform(c.Structure):
    _fields_ = [("values", c.c_double * 16)]


def _multiply(parent, child):
    # SketchUp uses column-major homogeneous matrices; values[15] can be 1/scale.
    return tuple(sum(parent[k * 4 + row] * child[col * 4 + k] for k in range(4))
                 for col in range(4) for row in range(4))


def _point_meters(point, transform):
    coords = (point.x, point.y, point.z, 1.)
    values = [sum(transform[col * 4 + row] * coords[col] for col in range(4)) for row in range(4)]
    if not all(math.isfinite(v) for v in values) or abs(values[3]) < 1e-15:
        raise ConversionError("SKP contains an invalid instance transform.")
    return tuple(v / values[3] * 0.0254 for v in values[:3])


def _mirrored(transform):
    a, b, d = transform[0], transform[4], transform[8]
    e, f, g = transform[1], transform[5], transform[9]
    h, i, j = transform[2], transform[6], transform[10]
    return (a * (f * j - g * i) - b * (e * j - g * h) + d * (e * i - f * h)) * transform[15] < 0


def _sdk_location(sdk_path):
    if os.name != "nt":
        raise ConversionError("SKP reader runtime unavailable: this adapter requires the Windows SketchUp C API.")
    if sdk_path is not None:
        path = Path(sdk_path)
        if not path.is_absolute() or not path.is_file():
            raise ConversionError("SKP reader runtime unavailable: the configured SketchUpAPI.dll does not exist.")
        return path
    installations = sorted(discover_local_cad().installations,
                           key=lambda row: (row.version_hint or "", str(row.executable)), reverse=True)
    for row in installations:
        if row.product == "sketchup":
            path = row.executable.with_name("SketchUpAPI.dll")
            if path.is_file():
                return path
    raise ConversionError("SKP reader runtime unavailable: no local SketchUpAPI.dll was found.")


class _API:
    def __init__(self, library):
        self.library = library
        refp, sizep = c.POINTER(_Ref), c.POINTER(c.c_size_t)
        signatures = {
            "SUInitialize": ([], None), "SUTerminate": ([], None),
            "SUGetAPIVersion": ([sizep, sizep], None),
            "SUModelCreateFromBufferWithStatus": ([refp, c.c_void_p, c.c_size_t, c.POINTER(c.c_int)], c.c_int),
            "SUModelRelease": ([refp], c.c_int), "SUModelGetEntities": ([_Ref, refp], c.c_int),
            "SUEntityGetPersistentID": ([_Ref, c.POINTER(c.c_int64)], c.c_int),
            "SUDrawingElementGetHidden": ([_Ref, c.POINTER(c.c_bool)], c.c_int),
            "SUDrawingElementGetLayer": ([_Ref, refp], c.c_int),
            "SULayerGetVisibility": ([_Ref, c.POINTER(c.c_bool)], c.c_int),
            "SULayerGetName": ([_Ref, refp], c.c_int),
            "SULayerToEntity": ([_Ref], _Ref),
            "SUStringCreate": ([refp], c.c_int), "SUStringRelease": ([refp], c.c_int),
            "SUStringGetUTF8Length": ([_Ref, sizep], c.c_int),
            "SUStringGetUTF8": ([_Ref, c.c_size_t, c.c_void_p, sizep], c.c_int),
            "SUComponentInstanceGetDefinition": ([_Ref, refp], c.c_int),
            "SUComponentInstanceGetName": ([_Ref, refp], c.c_int),
            "SUComponentDefinitionGetName": ([_Ref, refp], c.c_int),
            "SUComponentInstanceGetTransform": ([_Ref, c.POINTER(_Transform)], c.c_int),
            "SUComponentDefinitionGetEntities": ([_Ref, refp], c.c_int),
            "SUGroupToComponentInstance": ([_Ref], _Ref),
            "SUInstancePathCreate": ([refp], c.c_int), "SUInstancePathRelease": ([refp], c.c_int),
            "SUInstancePathPushInstance": ([_Ref, _Ref], c.c_int),
            "SUInstancePathSetLeaf": ([_Ref, _Ref], c.c_int),
            "SUModelIsDrawingElementVisible": ([_Ref, _Ref, c.POINTER(c.c_bool)], c.c_int),
            "SUMeshHelperCreate": ([refp, _Ref], c.c_int), "SUMeshHelperRelease": ([refp], c.c_int),
            "SUMeshHelperGetNumVertices": ([_Ref, sizep], c.c_int),
            "SUMeshHelperGetNumTriangles": ([_Ref, sizep], c.c_int),
            "SUMeshHelperGetVertices": ([_Ref, c.c_size_t, c.POINTER(_Point), sizep], c.c_int),
            "SUMeshHelperGetVertexIndices": ([_Ref, c.c_size_t, sizep, sizep], c.c_int),
        }
        for kind in ("Face", "ComponentInstance"):
            for target in ("Entity", "DrawingElement"):
                signatures[f"SU{kind}To{target}"] = ([_Ref], _Ref)
        for kind in ("Faces", "Groups", "Instances"):
            signatures[f"SUEntitiesGetNum{kind}"] = ([_Ref, sizep], c.c_int)
            signatures[f"SUEntitiesGet{kind}"] = ([_Ref, c.c_size_t, refp, sizep], c.c_int)
        for kind in ("Images", "Texts", "Dimensions", "SectionPlanes", "Polyline3ds"):
            signatures[f"SUEntitiesGetNum{kind}"] = ([_Ref, sizep], c.c_int)
        signatures["SUEntitiesGetNumEdges"] = ([_Ref, c.c_bool, sizep], c.c_int)
        for name, (arguments, result) in signatures.items():
            try:
                function = getattr(library, name)
            except AttributeError as exc:
                raise ConversionError(f"SKP reader runtime unavailable: SDK entry {name} is missing.") from exc
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)

    def call(self, name, *args):
        result = getattr(self, name)(*args)
        if result != 0:
            raise ConversionError(f"SketchUp C API {name} failed (code {result}).")

    def output(self, name, kind, *args):
        value = kind()
        self.call(name, *args, c.byref(value))
        return value

    def string(self, name, item):
        value = self.output("SUStringCreate", _Ref)
        try:
            self.call(name, item, c.byref(value))
            length = self.output("SUStringGetUTF8Length", c.c_size_t, value).value
            buffer = c.create_string_buffer(length + 1)
            self.output("SUStringGetUTF8", c.c_size_t, value, len(buffer), buffer)
            return buffer.value.decode("utf-8")
        finally:
            self.call("SUStringRelease", c.byref(value))

    def entities(self, container, kind):
        count = self.output("SUEntitiesGetNum" + kind, c.c_size_t, container).value
        if not count:
            return ()
        values = (_Ref * count)()
        actual = self.output("SUEntitiesGet" + kind, c.c_size_t, container, count, values).value
        if actual != count:
            raise ConversionError("SKP entity count changed while reading the model.")
        return values


@contextmanager
def _open_sdk(sdk_path=None):
    path = _sdk_location(sdk_path)
    with _SDK_LOCK:
        try:
            directory = os.add_dll_directory(str(path.parent))
            with directory:
                api = _API(c.CDLL(str(path)))
                api.SUInitialize()
                try:
                    major, minor = c.c_size_t(), c.c_size_t()
                    api.SUGetAPIVersion(c.byref(major), c.byref(minor))
                    if major.value < 9:
                        raise ConversionError("SKP reader requires SketchUp C API 9.0 or newer.")
                    yield api
                finally:
                    api.SUTerminate()
        except OSError as exc:
            raise ConversionError("SKP reader runtime unavailable: SketchUpAPI.dll or a dependency could not be loaded.") from exc


def read_skp(data: bytes, *, sdk_path: str | Path | None = None) -> Scene:
    """Return visible SKP faces as meters/Z-up meshes; never open SketchUp.

Instance paths and tag ids name meshes uniquely. Nested affine transforms,
mirrors, face holes and model/tag visibility are evaluated by the native SDK.
Object UUIDs retain those persistent paths across reads and edits of the same
SKP model. Renaming or geometry edits preserve them; reparenting/recreating
instances or changing their effective tag changes their identity.
Materials, loose edges, annotations, section cuts and camera-facing behavior
are not reproduced. The original SKP must remain the source of truth.
"""
    if not isinstance(data, bytes) or not data:
        raise ConversionError("SKP input must contain model bytes.")
    with _open_sdk(sdk_path) as api:
        model, status = _Ref(), c.c_int()
        buffer = c.create_string_buffer(data)
        try:
            api.call("SUModelCreateFromBufferWithStatus", c.byref(model), buffer, len(data), c.byref(status))
            if status.value not in (0, 1):
                raise ConversionError("SketchUp returned an unknown model load status.")
            warnings = ["SKP faces are tessellated; materials, textures, native edge styling and camera-facing behavior are not transferred. Component instances are expanded into meshes."]
            if status.value == 1:
                warnings.append("This SKP was saved by a newer SketchUp version than the local SDK; newer model data may be omitted.")
            meshes, omitted, layers = [], set(), {}

            def persistent_id(entity):
                pid = api.output("SUEntityGetPersistentID", c.c_int64, entity).value
                if pid <= 0:
                    raise ConversionError("SKP instance/tag has no reliable persistent ID.")
                return pid

            def layer_info(element):
                layer = api.output("SUDrawingElementGetLayer", _Ref, element)
                if layer.ptr not in layers:
                    visible = api.output("SULayerGetVisibility", c.c_bool, layer).value
                    label = api.string("SULayerGetName", layer)
                    pid = persistent_id(api.SULayerToEntity(layer))
                    layers[layer.ptr] = (visible, label, pid)
                return layers[layer.ptr]

            def visible(element, entity, instances):
                if api.output("SUDrawingElementGetHidden", c.c_bool, element).value or not layer_info(element)[0]:
                    return False
                path = api.output("SUInstancePathCreate", _Ref)
                try:
                    for instance in instances:
                        api.call("SUInstancePathPushInstance", path, instance)
                    api.call("SUInstancePathSetLeaf", path, entity)
                    return api.output("SUModelIsDrawingElementVisible", c.c_bool, model, path).value
                finally:
                    api.call("SUInstancePathRelease", c.byref(path))

            def visit(entities, transform, instances=(), identifiers=(), labels=(), inherited=None, ancestors=()):
                if len(instances) > 128 or entities.ptr in ancestors:
                    raise ConversionError("SKP contains a cyclic or excessively deep component hierarchy.")
                for kind in ("Images", "Texts", "Dimensions", "SectionPlanes", "Polyline3ds"):
                    if api.output("SUEntitiesGetNum" + kind, c.c_size_t, entities).value:
                        omitted.add(kind)
                if api.output("SUEntitiesGetNumEdges", c.c_size_t, entities, True).value:
                    omitted.add("loose edges")
                buckets = {}
                for face in api.entities(entities, "Faces"):
                    element = api.SUFaceToDrawingElement(face)
                    if not visible(element, api.SUFaceToEntity(face), instances):
                        continue
                    _, label, layer_id = layer_info(element)
                    if inherited and label in ("Layer0", "Untagged"):
                        label, layer_id = inherited
                    if layer_id not in buckets:
                        path_name = "/".join(map(str, identifiers)) or "root"
                        display = " / ".join(labels)
                        identity = f"skp:{path_name}:tag:{layer_id}"
                        buckets[layer_id] = Mesh(identity + (f" {display}" if display else ""), [], [], label,
                                                str(uuid5(_OBJECT_NAMESPACE, identity)))
                    mesh = buckets[layer_id]
                    helper = _Ref()
                    api.call("SUMeshHelperCreate", c.byref(helper), face)
                    try:
                        count = api.output("SUMeshHelperGetNumVertices", c.c_size_t, helper).value
                        triangles = api.output("SUMeshHelperGetNumTriangles", c.c_size_t, helper).value
                        if not count or not triangles:
                            raise ConversionError("A visible SKP face could not be triangulated.")
                        vertices, indices = (_Point * count)(), (c.c_size_t * (triangles * 3))()
                        read_vertices = api.output("SUMeshHelperGetVertices", c.c_size_t, helper, count, vertices).value
                        read_indices = api.output("SUMeshHelperGetVertexIndices", c.c_size_t, helper, len(indices), indices).value
                        if read_vertices != count or read_indices != len(indices):
                            raise ConversionError("SketchUp returned incomplete face geometry.")
                        offset = len(mesh.vertices)
                        mesh.vertices.extend(_point_meters(point, transform) for point in vertices)
                        mirrored = _mirrored(transform)
                        for index in range(0, len(indices), 3):
                            a, b, d = (indices[index + k] + offset for k in range(3))
                            mesh.triangles.append((a, d, b) if mirrored else (a, b, d))
                    finally:
                        api.call("SUMeshHelperRelease", c.byref(helper))
                meshes.extend(buckets.values())
                children = list(api.entities(entities, "Instances"))
                children.extend(api.SUGroupToComponentInstance(group) for group in api.entities(entities, "Groups"))
                for instance in children:
                    element = api.SUComponentInstanceToDrawingElement(instance)
                    entity = api.SUComponentInstanceToEntity(instance)
                    if not visible(element, entity, instances):
                        continue
                    definition = api.output("SUComponentInstanceGetDefinition", _Ref, instance)
                    nested = api.output("SUComponentDefinitionGetEntities", _Ref, definition)
                    local = api.output("SUComponentInstanceGetTransform", _Transform, instance)
                    pid = persistent_id(entity)
                    name = api.string("SUComponentInstanceGetName", instance) or api.string("SUComponentDefinitionGetName", definition)
                    _, label, layer_id = layer_info(element)
                    tag = inherited if label in ("Layer0", "Untagged") else (label, layer_id)
                    visit(nested, _multiply(transform, local.values), instances + (instance,), identifiers + (pid,),
                          labels + ((name,) if name else ()), tag, ancestors + (entities.ptr,))

            visit(api.output("SUModelGetEntities", _Ref, model), _IDENTITY)
            if omitted:
                warnings.append("SKP mesh import omits: " + ", ".join(sorted(omitted)) + ". Section planes do not clip the imported geometry.")
            scene = Scene(meshes, "Meters", warnings)
            scene.metrics()
            return scene
        finally:
            if model.ptr:
                api.call("SUModelRelease", c.byref(model))
