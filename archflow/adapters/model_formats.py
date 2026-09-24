"""Bounded format adapters. No project paths, persistence, or CAD authority."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import math
import struct

FORMATS = ("3dm", "skp", "glb", "dwg")
VERSION = "monkeyhub-mesh/1"


class ConversionError(ValueError):
    pass


@dataclass
class Mesh:
    name: str
    vertices: list
    triangles: list
    layer: str = "Default"


@dataclass
class Scene:
    meshes: list[Mesh]
    units: str
    warnings: list[str]

    def metrics(self):
        points = [p for m in self.meshes for p in m.vertices]
        if not points or not any(m.triangles for m in self.meshes):
            raise ConversionError("The model has no supported triangle geometry.")
        if any(not math.isfinite(v) for p in points for v in p):
            raise ConversionError("Geometry contains non-finite coordinates.")
        for mesh in self.meshes:
            if any(len(t) != 3 or any(i < 0 or i >= len(mesh.vertices) for i in t) for t in mesh.triangles):
                raise ConversionError("Invalid triangle indices.")
        return {"objectCount": len(self.meshes), "layerCount": len({m.layer for m in self.meshes}),
                "triangleCount": sum(len(m.triangles) for m in self.meshes),
                "boundsMetersZUp": [[min(p[i] for p in points) for i in range(3)],
                                    [max(p[i] for p in points) for i in range(3)]]}


class ThreeDM:
    def __init__(self):
        try:
            import rhino3dm
        except ImportError as exc:
            raise ConversionError("3DM conversion requires the optional rhino3dm runtime.") from exc
        self.r = rhino3dm
        self.version = "rhino3dm/" + rhino3dm.__version__

    def read(self, data):
        r = self.r
        if not data.startswith(b"3D Geometry File Format"):
            raise ConversionError("Invalid 3DM signature.")
        model = r.File3dm.FromByteArray(data)
        if model is None:
            raise ConversionError("3DM reader rejected the file.")
        units = model.Settings.ModelUnitSystem
        if units in (getattr(r.UnitSystem, "None"), r.UnitSystem.Unset, r.UnitSystem.CustomUnits):
            raise ConversionError("3DM units are unspecified; set model units before conversion.")
        scale = r.UnitSystem.UnitScale(units, r.UnitSystem.Meters)
        meshes, warnings = [], []
        for item in model.Objects:
            geometry = item.Geometry
            if isinstance(geometry, r.Mesh):
                pieces = [geometry]
            elif isinstance(geometry, r.Brep):
                pieces = [face.GetMesh(r.MeshType.Render) for face in geometry.Faces]
                if not pieces or any(p is None for p in pieces):
                    raise ConversionError("A BREP has no saved render mesh; a real tessellation runtime is required.")
                warnings.append("Exact BREP geometry is approximated by its saved render mesh.")
            else:
                raise ConversionError("Unsupported 3DM entity: " + type(geometry).__name__ + ". No entities were silently dropped.")
            vertices, triangles = [], []
            for piece in pieces:
                offset = len(vertices)
                vertices.extend([(v.X * scale, v.Y * scale, v.Z * scale) for v in piece.Vertices])
                for a, b, c, d in piece.Faces:
                    triangles.append((a + offset, b + offset, c + offset))
                    if c != d:
                        triangles.append((a + offset, c + offset, d + offset))
            layer = model.Layers.FindIndex(item.Attributes.LayerIndex)
            meshes.append(Mesh(item.Attributes.Name or "Mesh", vertices, triangles, layer.Name if layer else "Default"))
        warnings.append("Materials, textures, custom normals, CAD metadata and layer hierarchy are not preserved; layers are flattened into the default layer in GLB.")
        scene = Scene(meshes, str(units), list(dict.fromkeys(warnings)))
        scene.metrics()
        return scene

    def write(self, scene):
        r = self.r
        model = r.File3dm()
        model.Settings.ModelUnitSystem = r.UnitSystem.Meters
        layers = {}
        for item in scene.meshes:
            if item.layer not in layers:
                layer = r.Layer()
                layer.Name = item.layer
                layers[item.layer] = model.Layers.Add(layer)
            mesh = r.Mesh()
            for p in item.vertices:
                mesh.Vertices.Add(*p)
            for t in item.triangles:
                mesh.Faces.AddFace(*t)
            attr = r.ObjectAttributes()
            attr.Name, attr.LayerIndex = item.name, layers[item.layer]
            model.Objects.AddMesh(mesh, attr)
        return base64.b64decode(model.Encode())


class GLB:
    version = VERSION + "-glb2"

    def read(self, data):
        if len(data) < 20 or struct.unpack_from("<4sII", data) != (b"glTF", 2, len(data)):
            raise ConversionError("Invalid GLB 2 signature or length.")
        chunks, cursor = [], 12
        while cursor < len(data):
            if cursor + 8 > len(data):
                raise ConversionError("Invalid GLB chunk header.")
            size, kind = struct.unpack_from("<I4s", data, cursor)
            cursor += 8
            if size % 4 or cursor + size > len(data):
                raise ConversionError("Invalid GLB chunk.")
            chunks.append((kind, data[cursor:cursor + size]))
            cursor += size
        if len(chunks) != 2 or chunks[0][0] != b"JSON" or chunks[1][0] != b"BIN\x00":
            raise ConversionError("Only self-contained JSON/BIN GLB files are supported.")
        doc, binary = json.loads(chunks[0][1]), chunks[1][1]
        if doc.get("asset", {}).get("version") != "2.0" or doc.get("extensionsRequired") or doc.get("animations") or doc.get("skins"):
            raise ConversionError("GLB extensions, animation or skinning require another adapter.")

        def integer(value, field, minimum=0, maximum=None):
            if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
                raise ConversionError("Invalid GLB " + field + ".")
            return value

        def reference(collection, index):
            values = doc[collection]
            if not isinstance(values, list):
                raise ConversionError("Invalid GLB " + collection + " array.")
            return values[integer(index, collection + " index", maximum=len(values)-1)]

        buffers = doc.get("buffers", [])
        if not isinstance(buffers, list) or len(buffers) != 1 or "uri" in buffers[0]:
            raise ConversionError("External or invalid GLB buffers are unsupported.")
        buffer_length = integer(buffers[0]["byteLength"], "buffer byteLength", 1, len(binary))
        if len(binary) - buffer_length > 3:
            raise ConversionError("Invalid GLB buffer padding.")

        def accessor(index, position=False):
            a = reference("accessors", index)
            expected = "VEC3" if position else "SCALAR"
            types = {5126: ("f", 4), 5125: ("I", 4), 5123: ("H", 2), 5121: ("B", 1)}
            component = integer(a["componentType"], "accessor componentType")
            if a.get("sparse") or a.get("normalized") or a["type"] != expected or component not in types:
                raise ConversionError("Unsupported GLB accessor.")
            if position != (component == 5126):
                raise ConversionError("Positions must be floats and indices unsigned integers.")
            view = reference("bufferViews", a["bufferView"])
            reference("buffers", view.get("buffer", 0))
            fmt, component_width = types[component]
            width = component_width * (3 if position else 1)
            stride = integer(view.get("byteStride", width), "bufferView byteStride", width, 252)
            view_offset = integer(view.get("byteOffset", 0), "bufferView byteOffset", maximum=buffer_length)
            view_length = integer(view["byteLength"], "bufferView byteLength", 1, buffer_length-view_offset)
            offset = integer(a.get("byteOffset", 0), "accessor byteOffset", maximum=view_length)
            count = integer(a["count"], "accessor count", 1)
            start, end = view_offset + offset, view_offset + view_length
            if (offset % component_width or start % component_width
                    or ("byteStride" in view and stride % 4)):
                raise ConversionError("Invalid GLB accessor alignment.")
            if start + (count - 1) * stride + width > end:
                raise ConversionError("GLB accessor exceeds its buffer.")
            return [struct.unpack_from("<" + fmt * (3 if position else 1), binary, start + i * stride) for i in range(count)]

        meshes = []
        def visit(index, parents):
            node = reference("nodes", index)
            if index in parents:
                raise ConversionError("GLB node hierarchy contains a cycle.")
            # Fail closed until transform composition is supplied by a verified adapter.
            if any(key in node for key in ("matrix", "rotation", "translation", "scale", "weights", "skin")):
                raise ConversionError("Transformed or deformed GLB nodes are not supported by this adapter; bake transforms first.")
            if "mesh" in node:
                for primitive in reference("meshes", node["mesh"])["primitives"]:
                    if integer(primitive.get("mode", 4), "primitive mode") != 4 or primitive.get("targets") or primitive.get("extensions"):
                        raise ConversionError("Only uncompressed triangle primitives are supported.")
                    vertices = [(x, -z, y) for x, y, z in accessor(primitive["attributes"]["POSITION"], True)]
                    indices = [v[0] for v in accessor(primitive["indices"])] if "indices" in primitive else list(range(len(vertices)))
                    if len(indices) % 3:
                        raise ConversionError("Incomplete GLB triangle.")
                    meshes.append(Mesh(node.get("name", "Mesh"), vertices, [tuple(indices[i:i+3]) for i in range(0, len(indices), 3)]))
            for child in node.get("children", []):
                visit(child, parents + [index])
        for root in reference("scenes", doc.get("scene", 0)).get("nodes", []):
            visit(root, [])
        scene = Scene(meshes, "Meters", ["Mesh geometry only: no CAD solids reconstructed. Materials, textures and normals are omitted; hierarchy and instances are flattened."])
        scene.metrics()
        return scene

    def write(self, scene):
        binary = bytearray()
        doc = {"asset": {"version": "2.0", "generator": self.version}, "scene": 0,
               "scenes": [{"nodes": list(range(len(scene.meshes)))}], "nodes": [], "meshes": [], "accessors": [], "bufferViews": []}
        for n, mesh in enumerate(scene.meshes):
            points = [(x, z, -y) for x, y, z in mesh.vertices]
            for values, fmt, typ, component in ((points, "fff", "VEC3", 5126),
                    ([(i,) for t in mesh.triangles for i in t], "I", "SCALAR", 5125)):
                offset = len(binary)
                for row in values:
                    binary.extend(struct.pack("<" + fmt, *row))
                doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(binary)-offset})
                a = {"bufferView": len(doc["bufferViews"])-1, "componentType": component, "count": len(values), "type": typ}
                if typ == "VEC3":
                    a.update(min=[min(p[i] for p in points) for i in range(3)], max=[max(p[i] for p in points) for i in range(3)])
                doc["accessors"].append(a)
            doc["meshes"].append({"primitives": [{"attributes": {"POSITION": 2*n}, "indices": 2*n+1}]})
            doc["nodes"].append({"mesh": n, "name": mesh.name})
        doc["buffers"] = [{"byteLength": len(binary)}]
        encoded = json.dumps(doc, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 4)
        return struct.pack("<4sII", b"glTF", 2, 28+len(encoded)+len(binary)) + struct.pack("<I4s", len(encoded), b"JSON") + encoded + struct.pack("<I4s", len(binary), b"BIN\x00") + binary


def encode_mesh(data, source, target):
    """Produce mesh bytes; the provider's separate validate step must approve them."""
    adapters = {"3dm": ThreeDM, "glb": GLB}
    reader, writer = adapters[source](), adapters[target]()
    scene = reader.read(data)
    output = data if source == target else writer.write(scene)
    return output, {"converter": [reader.version, writer.version],
                    "warnings": [] if source == target else scene.warnings,
                    "converted": source != target}


def validate_mesh(data, source, target, output):
    """Reopen both original and output; never validate metrics supplied by a writer."""
    adapters = {"3dm": ThreeDM, "glb": GLB}
    original, reopened = adapters[source]().read(data), adapters[target]().read(output)
    before, after = original.metrics(), reopened.metrics()
    if before["objectCount"] != after["objectCount"] or before["triangleCount"] != after["triangleCount"]:
        raise ConversionError("Output validation changed mesh or triangle counts.")
    for a, b in zip(sum(before["boundsMetersZUp"], []), sum(after["boundsMetersZUp"], [])):
        if not math.isclose(a, b, rel_tol=2e-6, abs_tol=1e-6):
            raise ConversionError("Output validation changed model scale or placement.")
    return {"sourceUnits": original.units, "outputUnits": reopened.units,
            "coordinateSystem": "meters/Z-up (validation)",
            "sourceMetrics": before, "outputMetrics": after}


def convert(data, source, target, *, providers=None):
    """Existing public entry, now routed through configured runtime providers."""
    from .model_providers import ConversionCoordinator
    return ConversionCoordinator(providers).convert(data, source, target)
