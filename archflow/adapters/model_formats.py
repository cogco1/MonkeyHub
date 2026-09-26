"""Bounded format adapters. No project paths, persistence, or CAD authority."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import base64
import json
import math
import struct
from uuid import UUID

FORMATS = ("3dm", "skp", "glb", "dwg")
VERSION = "monkeyhub-mesh/3"
# Display colour of a mesh whose source carried no material: a light neutral
# grey, not the black a new 3DM layer and object would otherwise draw with.
FALLBACK_MATERIAL = "MonkeyHub neutral"
FALLBACK_COLOR = (200, 200, 200, 255)


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class Material:
    name: str
    color: tuple  # sRGB 0-255 red, green, blue, alpha
    transparency: float = 0.0


@dataclass
class Mesh:
    name: str
    vertices: list
    triangles: list
    layer: str = "Default"
    source_object_id: str | None = None
    # Carried only when the source had them; the 3DM writer reports what it
    # kept, computed or defaulted instead of inventing them here.
    normals: list | None = None
    colors: list | None = None  # sRGB 0-255 red, green, blue per vertex
    material: Material | None = None
    texcoords: list | None = None
    base_color: tuple | None = None
    roughness: float = 0.65
    metallic: float = 0.0


@dataclass
class Scene:
    meshes: list[Mesh]
    units: str
    warnings: list[str]
    display: dict = field(default_factory=dict)

    def metrics(self):
        points = [p for m in self.meshes for p in m.vertices]
        if not points or not any(m.triangles for m in self.meshes):
            raise ConversionError("The model has no supported triangle geometry.")
        if any(not math.isfinite(v) for p in points for v in p):
            raise ConversionError("Geometry contains non-finite coordinates.")
        for mesh in self.meshes:
            if any(len(t) != 3 or any(i < 0 or i >= len(mesh.vertices) for i in t) for t in mesh.triangles):
                raise ConversionError("Invalid triangle indices.")
            for name, values, width in (("normals", mesh.normals, 3), ("UV", mesh.texcoords, 2)):
                if values is not None and (len(values) != len(mesh.vertices) or any(
                        len(v) != width or any(not math.isfinite(c) for c in v) for v in values)):
                    raise ConversionError("Invalid mesh " + name + ".")
            if mesh.normals is not None and any(sum(c*c for c in n) < 1e-20 for n in mesh.normals):
                raise ConversionError("Mesh normals must be nonzero.")
            if mesh.base_color is not None and (len(mesh.base_color) != 4 or any(
                    not math.isfinite(c) or not 0 <= c <= 1 for c in mesh.base_color)):
                raise ConversionError("Invalid base color.")
            if any(not math.isfinite(c) or not 0 <= c <= 1 for c in (mesh.roughness, mesh.metallic)):
                raise ConversionError("Invalid PBR material factors.")
        return {"objectCount": len(self.meshes), "layerCount": len({m.layer for m in self.meshes}),
                "triangleCount": sum(len(m.triangles) for m in self.meshes),
                "boundsMetersZUp": [[min(p[i] for p in points) for i in range(3)],
                                    [max(p[i] for p in points) for i in range(3)]]}


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])


def _face_normal(vertices, triangle):
    a, b, c = (vertices[i] for i in triangle)
    return _cross([b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)])


def _unit(vector):
    length = math.sqrt(sum(v * v for v in vector))
    return tuple(v / length for v in vector) if math.isfinite(length) and length > 1e-12 else None


def orient_triangles(vertices, triangles):
    """Consistent winding on each edge-connected patch; closed patches face outward.

    Coincident vertices are one topological vertex, so split UV/normal seams
    stay connected. An open patch keeps the winding most of its source faces
    had; a non-manifold or non-orientable patch keeps its source winding.
    Returns (triangles, reversed triangle count, closed patch count).
    """
    ids = {}
    key = [ids.setdefault(tuple(p), len(ids)) for p in vertices]
    edges, faces = {}, {}
    for n, triangle in enumerate(triangles):
        a, b, c = (key[i] for i in triangle)
        if a == b or b == c or a == c:
            continue
        faces[n] = []
        for u, v in ((a, b), (b, c), (c, a)):
            edge = (min(u, v), max(u, v))
            edges.setdefault(edge, []).append((n, u < v))
            faces[n].append(edge)
    flip, oriented, reversed_count, closed = {}, list(triangles), 0, 0
    for start in faces:
        if start in flip:
            continue
        flip[start], patch, queue, manifold, orientable = False, [start], [start], True, True
        while queue:
            n = queue.pop()
            for edge in faces[n]:
                users = edges[edge]
                if len(users) != 2:
                    manifold = False
                    continue
                (p, forward_p), (q, forward_q) = users
                m, forward_m, forward_n = (q, forward_q, forward_p) if p == n else (p, forward_p, forward_q)
                # Neighbours traverse a shared edge in opposite directions.
                wanted = flip[n] ^ (forward_n == forward_m)
                if m not in flip:
                    flip[m] = wanted
                    patch.append(m)
                    queue.append(m)
                elif flip[m] != wanted:
                    orientable = False
        if not orientable:
            continue
        if manifold:
            closed += 1
            volume = sum(sum(x * y for x, y in zip(vertices[t[0]], _cross(vertices[t[1]], vertices[t[2]])))
                         for t in ((oriented[n][::-1] if flip[n] else oriented[n]) for n in patch))
            invert = volume < 0
        else:
            invert = 2 * sum(flip[n] for n in patch) > len(patch)
        for n in patch:
            if flip[n] != invert:
                oriented[n] = tuple(oriented[n][::-1])
                reversed_count += 1
    return oriented, reversed_count, closed


def vertex_normals(vertices, triangles):
    """Area-weighted normals of the faces sharing each vertex index.

    Vertices split at a seam keep a hard edge; shared vertices shade smooth.
    A vertex no face uses gets +Z so the 3DM normal list stays complete.
    """
    sums = [[0.0, 0.0, 0.0] for _ in vertices]
    for triangle in triangles:
        normal = _face_normal(vertices, triangle)
        for index in triangle:
            for axis in range(3):
                sums[index][axis] += normal[axis]
    return [_unit(total) or (0.0, 0.0, 1.0) for total in sums]


def _kept_normals(mesh, triangles):
    """Source normals when complete, finite and agreeing with the winding they shade."""
    if not mesh.normals or len(mesh.normals) != len(mesh.vertices):
        return None
    normals = [_unit(n) if len(n) == 3 else None for n in mesh.normals]
    if any(n is None for n in normals):
        return None
    faces = [(t, _face_normal(mesh.vertices, t)) for t in triangles]
    faces = [(t, f) for t, f in faces if _unit(f) is not None]
    agree = sum(sum(f[axis] * sum(normals[i][axis] for i in t) for axis in range(3)) > 0 for t, f in faces)
    return normals if 2 * agree >= len(faces) else None


def display_mesh(mesh):
    """Winding, normals and material a shaded viewer needs; returns the mesh and what was done."""
    if mesh.material is None and mesh.base_color is not None:
        srgb = lambda c: round(255 * (12.92*c if c <= .0031308 else 1.055*c**(1/2.4)-.055))
        mesh = replace(mesh, material=Material(mesh.name + " material",
                       tuple(srgb(c) for c in mesh.base_color[:3]) + (round(255*mesh.base_color[3]),),
                       1-mesh.base_color[3]))
    triangles, reversed_count, closed = orient_triangles(mesh.vertices, mesh.triangles)
    normals = _kept_normals(mesh, triangles)
    record = {"normals": "kept" if normals else "computed" if not mesh.normals else "recomputed",
              "reversedTriangles": reversed_count, "closedShells": closed,
              "material": "kept" if mesh.material else "defaulted",
              "vertexColors": "kept" if mesh.colors else "absent"}
    return replace(mesh, triangles=triangles, normals=normals or vertex_normals(mesh.vertices, triangles),
                   material=mesh.material or Material(FALLBACK_MATERIAL, FALLBACK_COLOR)), record


def display_scene(scene):
    """Every mesh prepared by display_mesh, with a summary and plain-language notes."""
    meshes, records = zip(*(display_mesh(m) for m in scene.meshes)) if scene.meshes else ((), ())
    count = lambda key, value: sum(r[key] == value for r in records)
    summary = {"normals": {state: count("normals", state) for state in ("kept", "computed", "recomputed")},
               "winding": {"reversedTriangles": sum(r["reversedTriangles"] for r in records),
                           "closedShells": sum(r["closedShells"] for r in records)},
               "material": {"kept": count("material", "kept"), "defaulted": count("material", "defaulted"),
                            "fallback": {"name": FALLBACK_MATERIAL, "color": list(FALLBACK_COLOR)}},
               "vertexColors": {"kept": count("vertexColors", "kept")}}
    notes = []
    if summary["normals"]["kept"]:
        notes.append(f"Source vertex normals kept on {summary['normals']['kept']} mesh(es).")
    if summary["normals"]["computed"]:
        notes.append(f"Vertex normals computed from the triangles of {summary['normals']['computed']} mesh(es) that had none.")
    if summary["normals"]["recomputed"]:
        notes.append(f"Source normals of {summary['normals']['recomputed']} mesh(es) were incomplete, invalid or contradicted the face winding and were recomputed.")
    if summary["winding"]["reversedTriangles"]:
        notes.append(f"{summary['winding']['reversedTriangles']} triangle(s) were reversed for consistent winding; closed shells face outward.")
    if summary["material"]["kept"]:
        notes.append(f"Source base colour materials kept on {summary['material']['kept']} mesh(es).")
    if summary["material"]["defaulted"]:
        notes.append(f"{summary['material']['defaulted']} mesh(es) had no material and received the neutral '{FALLBACK_MATERIAL}' display material.")
    if summary["vertexColors"]["kept"]:
        notes.append(f"Vertex colours kept on {summary['vertexColors']['kept']} mesh(es).")
    return Scene(list(meshes), scene.units, list(dict.fromkeys(scene.warnings + notes)), summary)


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
            vertices, triangles, normals, colors, texcoords = [], [], [], [], []
            for piece in pieces:
                offset = len(vertices)
                vertices.extend([(v.X * scale, v.Y * scale, v.Z * scale) for v in piece.Vertices])
                for a, b, c, d in piece.Faces:
                    triangles.append((a + offset, b + offset, c + offset))
                    if c != d:
                        triangles.append((a + offset, c + offset, d + offset))
                texcoords.extend([(v.X, v.Y) for v in piece.TextureCoordinates])
                # Per-vertex lists count only when every piece carries a complete one.
                count = len(piece.Vertices)
                normals = None if normals is None or len(piece.Normals) != count else normals + [
                    (n.X, n.Y, n.Z) for n in map(piece.Normals.__getitem__, range(count))]
                colors = None if colors is None or piece.VertexColors.Count != count else colors + [
                    tuple(piece.VertexColors[i][:3]) for i in range(count)]
            layer = model.Layers.FindIndex(item.Attributes.LayerIndex)
            native_material = self._material(model, item.Attributes, layer)
            index = item.Attributes.MaterialIndex if item.Attributes.MaterialSource == r.ObjectMaterialSource.MaterialFromObject else (layer.RenderMaterialIndex if layer else -1)
            native = model.Materials[index] if native_material and 0 <= index < len(model.Materials) else None
            pbr = native.PhysicallyBased if native else None
            linear = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
            rgba = tuple(linear(c/255) for c in native_material.color[:3]) + (1-native_material.transparency,) if native_material else None
            meshes.append(Mesh(item.Attributes.Name or "Mesh", vertices, triangles, layer.Name if layer else "Default",
                               str(item.Attributes.Id), normals, colors, native_material,
                               texcoords=texcoords if len(texcoords)==len(vertices) else None, base_color=rgba,
                               roughness=pbr.Roughness if pbr and pbr.Supported else .65,
                               metallic=pbr.Metallic if pbr and pbr.Supported else 0.0))
        warnings.append("Texture images, CAD metadata and layer hierarchy are not preserved; layers are flattened in GLB.")
        scene = Scene(meshes, str(units), list(dict.fromkeys(warnings)))
        scene.metrics()
        return scene

    def _material(self, model, attributes, layer):
        r = self.r
        if attributes.MaterialSource == r.ObjectMaterialSource.MaterialFromObject:
            index = attributes.MaterialIndex
        elif attributes.MaterialSource == r.ObjectMaterialSource.MaterialFromLayer and layer is not None:
            index = layer.RenderMaterialIndex
        else:
            return None
        if not 0 <= index < len(model.Materials):
            return None
        native = model.Materials[index]
        return Material(native.Name or "Material", tuple(native.DiffuseColor), native.Transparency)

    def write(self, scene):
        """Shaded-display-ready 3DM; the scene's warnings and display record say what was done."""
        r = self.r
        scene.metrics()
        prepared = display_scene(scene)
        scene.warnings[:], scene.display = prepared.warnings, prepared.display
        model = r.File3dm()
        model.Settings.ModelUnitSystem = r.UnitSystem.Meters
        layers, materials = {}, {}
        object_ids = set()
        for item in prepared.meshes:
            if item.layer not in layers:
                layer = r.Layer()
                layer.Name = item.layer
                # A new layer draws black; objects below carry their own colour.
                layer.Color = FALLBACK_COLOR
                layers[item.layer] = model.Layers.Add(layer)
            material_key = (item.material, item.roughness, item.metallic)
            if material_key not in materials:
                material = r.Material()
                material.Name = item.material.name
                material.DiffuseColor = item.material.color
                material.Transparency = item.material.transparency
                material.ToPhysicallyBased()
                material.PhysicallyBased.Roughness = item.roughness
                material.PhysicallyBased.Metallic = item.metallic
                materials[material_key] = model.Materials.Add(material)
            mesh = r.Mesh()
            for p in item.vertices:
                mesh.Vertices.Add(*p)
            for t in item.triangles:
                mesh.Faces.AddFace(*t)
            for n in item.normals:
                mesh.Normals.Add(*n)
            for uv in item.texcoords or ():
                mesh.TextureCoordinates.__add__(*uv)
            for c in item.colors or ():
                mesh.VertexColors.Add(*c[:3])
            attr = r.ObjectAttributes()
            attr.Name, attr.LayerIndex = item.name, layers[item.layer]
            attr.MaterialSource = r.ObjectMaterialSource.MaterialFromObject
            attr.MaterialIndex = materials[material_key]
            attr.ColorSource = r.ObjectColorSource.ColorFromObject
            attr.ObjectColor = item.material.color
            if item.source_object_id is not None:
                try:
                    object_id = UUID(item.source_object_id)
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ConversionError("The source object ID must be a valid UUID.") from exc
                if not object_id.int or object_id in object_ids:
                    raise ConversionError("Source object IDs must be nonzero and unique within a 3DM.")
                object_ids.add(object_id)
                attr.Id = object_id
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

        def accessor(index, kind="index"):
            a = reference("accessors", index)
            types = {5126: ("f", 4), 5125: ("I", 4), 5123: ("H", 2), 5121: ("B", 1)}
            component = integer(a["componentType"], "accessor componentType")
            # glTF colours may be floats or normalized unsigned bytes/shorts.
            shapes = ("VEC3", "VEC4") if kind == "color" else ("VEC3",) if kind in ("position", "normal") else ("VEC2",) if kind == "uv" else ("SCALAR",)
            normalized = kind == "color" and component in (5121, 5123)
            if a.get("sparse") or bool(a.get("normalized")) != normalized or a["type"] not in shapes or component not in types:
                raise ConversionError("Unsupported GLB accessor.")
            if kind != "color" and (kind != "index") != (component == 5126):
                raise ConversionError("Positions and normals must be floats and indices unsigned integers.")
            if kind == "color" and component == 5125:
                raise ConversionError("Unsupported GLB accessor.")
            components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
            view = reference("bufferViews", a["bufferView"])
            reference("buffers", view.get("buffer", 0))
            fmt, component_width = types[component]
            width = component_width * components
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
            rows = [struct.unpack_from("<" + fmt * components, binary, start + i * stride) for i in range(count)]
            return [tuple(v / (255 if component == 5121 else 65535) for v in row) for row in rows] if normalized else rows

        def srgb(linear):
            # glTF colours are linear; 3DM stores 8-bit sRGB.
            value = min(max(float(linear), 0.0), 1.0)
            return round(255 * (12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055))

        def material(primitive):
            if "material" not in primitive:
                return None
            source = reference("materials", primitive["material"])
            factor = source.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])
            if (not isinstance(factor, list) or len(factor) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in factor)):
                raise ConversionError("Invalid GLB material baseColorFactor.")
            if any("Texture" in key for key in source) or "baseColorTexture" in source.get("pbrMetallicRoughness", {}):
                warnings.append("Material textures are not transferred; the material's base colour factor is kept.")
            alpha = min(max(float(factor[3]), 0.0), 1.0)
            return Material(str(source.get("name") or "Material " + str(primitive["material"])),
                            tuple(srgb(v) for v in factor[:3]) + (255,),
                            1.0 - alpha if source.get("alphaMode") == "BLEND" else 0.0)

        meshes, warnings = [], ["Mesh geometry only: no CAD solids reconstructed. Texture images and PBR maps are not transferred; hierarchy and instances are flattened."]
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
                    attributes = primitive["attributes"]
                    vertices = [(x, -z, y) for x, y, z in accessor(attributes["POSITION"], "position")]
                    indices = [v[0] for v in accessor(primitive["indices"])] if "indices" in primitive else list(range(len(vertices)))
                    if len(indices) % 3:
                        raise ConversionError("Incomplete GLB triangle.")
                    # The same Y-up to Z-up rotation as the positions; validity is judged by the writer.
                    normals = [(x, -z, y) for x, y, z in accessor(attributes["NORMAL"], "normal")] if "NORMAL" in attributes else None
                    colors = [tuple(srgb(v) for v in row[:3]) for row in accessor(attributes["COLOR_0"], "color")] if "COLOR_0" in attributes else None
                    if any(values is not None and len(values) != len(vertices) for values in (normals, colors)):
                        raise ConversionError("GLB vertex attributes must match the position count.")
                    texcoords = accessor(attributes["TEXCOORD_0"], "uv") if "TEXCOORD_0" in attributes else None
                    pbr = reference("materials", primitive["material"]).get("pbrMetallicRoughness", {}) if "material" in primitive else {}
                    source_material = material(primitive)
                    meshes.append(Mesh(node.get("name") or reference("meshes", node["mesh"]).get("name") or "Mesh",
                                       vertices, [tuple(indices[i:i+3]) for i in range(0, len(indices), 3)],
                                       normals=normals, colors=colors, material=source_material,
                                       texcoords=texcoords, base_color=tuple(pbr.get("baseColorFactor", [1,1,1,1])) if source_material else None,
                                       roughness=pbr.get("roughnessFactor", .65), metallic=pbr.get("metallicFactor", 0.0)))
            for child in node.get("children", []):
                visit(child, parents + [index])
        for root in reference("scenes", doc.get("scene", 0)).get("nodes", []):
            visit(root, [])
        scene = Scene(meshes, "Meters", list(dict.fromkeys(warnings)))
        scene.metrics()
        return scene

    def write(self, scene):
        binary = bytearray()
        doc = {"asset": {"version": "2.0", "generator": self.version}, "scene": 0,
               "scenes": [{"nodes": list(range(len(scene.meshes)))}], "nodes": [], "meshes": [], "accessors": [], "bufferViews": []}
        for n, mesh in enumerate(scene.meshes):
            if mesh.base_color is None and mesh.material is not None:
                linear = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                mesh = replace(mesh, base_color=tuple(linear(c/255) for c in mesh.material.color[:3]) + (1-mesh.material.transparency,))
            position_index = len(doc["accessors"])
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
            primitive = {"attributes": {"POSITION": position_index}, "indices": position_index+1}
            for name, values, width in (("NORMAL", [(x,z,-y) for x,y,z in mesh.normals] if mesh.normals else None, 3),
                                        ("TEXCOORD_0", mesh.texcoords, 2)):
                if values is None:
                    continue
                offset = len(binary)
                for row in values:
                    binary.extend(struct.pack("<"+"f"*width,*row))
                doc["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":len(binary)-offset})
                primitive["attributes"][name] = len(doc["accessors"])
                doc["accessors"].append({"bufferView":len(doc["bufferViews"])-1,"componentType":5126,"count":len(values),"type":"VEC"+str(width)})
            if mesh.base_color is not None:
                materials = doc.setdefault("materials",[])
                primitive["material"] = len(materials)
                materials.append({"pbrMetallicRoughness":{"baseColorFactor":mesh.base_color,"roughnessFactor":mesh.roughness,"metallicFactor":mesh.metallic}})
            doc["meshes"].append({"primitives": [primitive]})
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
    details = {"converter": [reader.version, writer.version],
               "warnings": [] if source == target else scene.warnings,
               "converted": source != target}
    if scene.display and source != target:
        details["display"] = scene.display
    return output, details


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
    if target == "3dm" and source != target:
        # A shaded viewer draws a mesh without normals or material dark.
        for mesh in reopened.meshes:
            if not mesh.normals or any(_unit(n) is None for n in mesh.normals):
                raise ConversionError("Output validation found a mesh without complete vertex normals.")
            if mesh.material is None:
                raise ConversionError("Output validation found a mesh without a display material.")
    return {"sourceUnits": original.units, "outputUnits": reopened.units,
            "coordinateSystem": "meters/Z-up (validation)",
            "sourceMetrics": before, "outputMetrics": after}


def convert(data, source, target, *, providers=None):
    """Existing public entry, now routed through configured runtime providers."""
    from .model_providers import ConversionCoordinator
    return ConversionCoordinator(providers).convert(data, source, target)
