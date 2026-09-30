"""Generated mesh fixtures; native file reopens and numerical interchange checks."""
import base64
import json
import struct
import unittest
from uuid import UUID
from unittest.mock import patch
from archflow.adapters.model_formats import GLB, ThreeDM, Mesh, Scene, convert, ConversionError

def fixture():
    return Scene([Mesh("Offset triangle", [(1, 2, 3), (2, 2, 3), (1, 3, 3)], [(0, 1, 2)], "Structure")], "Meters", [])


def changed_glb(changes, binary=None):
    source = GLB().write(fixture())
    json_size = struct.unpack_from("<I", source, 12)[0]
    document = json.loads(source[20:20 + json_size])
    if binary is None:
        binary = source[28 + json_size:]
    for path, value in changes:
        parent = document
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary))
            + struct.pack("<I4s", len(encoded), b"JSON") + encoded
            + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


CUBE = [(x, y, z) for x in (0, 2) for y in (0, 2) for z in (0, 2)]
# Outward counter-clockwise quads of the cube above, meters/Z-up.
CUBE_QUADS = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
CUBE_TRIANGLES = [t for a, b, c, d in CUBE_QUADS for t in ((a, b, c), (a, c, d))]


def authored_glb(vertices, triangles, normals=None, colors=None, material=None):
    """A GLB with the optional attributes the adapter's own writer never emits."""
    binary, views, accessors = bytearray(), [], []

    def add(rows, fmt, kind, component, **extra):
        offset = len(binary)
        for row in rows:
            binary.extend(struct.pack("<" + fmt, *row))
        binary.extend(b"\0" * (-len(binary) % 4))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(rows) * struct.calcsize("<" + fmt)})
        accessors.append({"bufferView": len(views) - 1, "componentType": component, "count": len(rows), "type": kind, **extra})
        return len(accessors) - 1

    # Test geometry is written meters/Z-up; glTF is Y-up.
    attributes = {"POSITION": add([(x, z, -y) for x, y, z in vertices], "fff", "VEC3", 5126)}
    if normals is not None:
        attributes["NORMAL"] = add([(x, z, -y) for x, y, z in normals], "fff", "VEC3", 5126)
    if colors is not None:
        attributes["COLOR_0"] = add(colors, "BBBB", "VEC4", 5121, normalized=True)
    primitive = {"attributes": attributes, "indices": add([(i,) for t in triangles for i in t], "I", "SCALAR", 5125)}
    document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                "nodes": [{"mesh": 0, "name": "Authored"}], "meshes": [{"primitives": [primitive]}],
                "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(binary)}]}
    if material is not None:
        primitive["material"] = 0
        document["materials"] = [material]
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary))
            + struct.pack("<I4s", len(encoded), b"JSON") + encoded
            + struct.pack("<I4s", len(binary), b"BIN\x00") + bytes(binary))


def reopened_mesh(output):
    """The converted 3DM as a viewer opens it: file bytes read back by rhino3dm."""
    import rhino3dm as r
    model = r.File3dm.FromByteArray(output)
    item = model.Objects[0]
    mesh = item.Geometry
    vertices = [(v.X, v.Y, v.Z) for v in mesh.Vertices]
    normals = [(mesh.Normals[i].X, mesh.Normals[i].Y, mesh.Normals[i].Z) for i in range(len(mesh.Normals))]
    faces = [tuple(face[:3]) for face in mesh.Faces]
    return model, item, vertices, normals, faces


def cross(u, v):
    return (u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0])


def dot(u, v):
    return sum(a * b for a, b in zip(u, v))


class FormatTests(unittest.TestCase):
    def assert_outward(self, vertices, normals, faces):
        centre = [sum(p[i] for p in vertices) / len(vertices) for i in range(3)]
        self.assertEqual(len(normals), len(vertices))
        for point, normal in zip(vertices, normals):
            self.assertAlmostEqual(dot(normal, normal), 1, places=5)
            self.assertGreater(dot(normal, [point[i] - centre[i] for i in range(3)]), 0)
        for a, b, c in faces:
            pa, pb, pc = vertices[a], vertices[b], vertices[c]
            face = cross([pb[i] - pa[i] for i in range(3)], [pc[i] - pa[i] for i in range(3)])
            middle = [(pa[i] + pb[i] + pc[i]) / 3 - centre[i] for i in range(3)]
            self.assertGreater(dot(face, middle), 0, (a, b, c))

    def test_glb_without_normals_or_material_reopens_shaded_and_not_black(self):
        import rhino3dm as r
        # Two faces wound inward: the closed shell must come back outward throughout.
        triangles = list(CUBE_TRIANGLES)
        triangles[0], triangles[7] = triangles[0][::-1], triangles[7][::-1]
        output, info = convert(authored_glb(CUBE, triangles), "glb", "3dm")
        model, item, vertices, normals, faces = reopened_mesh(output)
        self.assert_outward(vertices, normals, faces)
        material = model.Materials[item.Attributes.MaterialIndex]
        self.assertEqual(item.Attributes.MaterialSource, r.ObjectMaterialSource.MaterialFromObject)
        for colour in (material.DiffuseColor, item.Attributes.DrawColor(model), model.Layers[0].Color):
            self.assertGreater(sum(colour[:3]), 3 * 128, colour)
        self.assertEqual(info["display"]["normals"], {"kept": 0, "computed": 1, "recomputed": 0})
        self.assertEqual(info["display"]["winding"], {"reversedTriangles": 2, "closedShells": 1})
        self.assertEqual(info["display"]["material"]["defaulted"], 1)
        self.assertIn("vertex-normals-and-display-material", info["outputValidation"]["checks"])
        notes = " ".join(info["warnings"])
        for phrase in ("normals computed", "2 triangle(s) were reversed", "neutral"):
            self.assertIn(phrase, notes)
        self.assertNotIn("normals are omitted", notes)
        self.assertIn("neutral display material", info["losses"]["materials"])

    def test_inward_closed_shell_is_turned_outward(self):
        output, info = convert(authored_glb(CUBE, [t[::-1] for t in CUBE_TRIANGLES]), "glb", "3dm")
        self.assert_outward(*reopened_mesh(output)[2:])
        self.assertEqual(info["display"]["winding"]["reversedTriangles"], 12)

    def test_open_surface_keeps_source_winding_and_split_seams_stay_connected(self):
        # A downward-facing open square whose two triangles share no vertex indices.
        vertices = [(0, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 0), (1, 1, 0), (1, 0, 0)]
        output, info = convert(authored_glb(vertices, [(0, 1, 2), (3, 4, 5)]), "glb", "3dm")
        _, _, _, normals, faces = reopened_mesh(output)
        self.assertEqual(faces, [(0, 1, 2), (3, 4, 5)])
        for normal in normals:
            self.assertAlmostEqual(normal[2], -1, places=6)
        self.assertEqual(info["display"]["winding"], {"reversedTriangles": 0, "closedShells": 0})

    def test_source_normals_vertex_colours_and_material_are_kept(self):
        import rhino3dm as r
        vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        tilted = (0.0, 0.6, 0.8)
        colors = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)]
        material = {"name": "Beak", "pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.2, 0.0, 1.0]}}
        output, info = convert(authored_glb(vertices, [(0, 1, 2)], [tilted] * 3, colors, material), "glb", "3dm")
        model, item, _, normals, _ = reopened_mesh(output)
        for normal in normals:
            for a, b in zip(normal, tilted):
                self.assertAlmostEqual(a, b, places=6)
        mesh = item.Geometry
        self.assertEqual([tuple(mesh.VertexColors[i][:3]) for i in range(3)], [c[:3] for c in colors])
        native = model.Materials[item.Attributes.MaterialIndex]
        self.assertEqual(native.Name, "Beak")
        # Linear glTF factors become 8-bit sRGB.
        self.assertEqual(tuple(native.DiffuseColor), (255, 124, 0, 255))
        self.assertEqual(item.Attributes.Name, "Authored")
        self.assertEqual(info["display"]["normals"]["kept"], 1)
        self.assertEqual(info["display"]["material"]["kept"], 1)
        self.assertEqual(info["display"]["vertexColors"]["kept"], 1)
        self.assertIn("Source vertex normals kept", " ".join(info["warnings"]))

    def test_source_normals_against_the_winding_are_recomputed(self):
        vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        output, info = convert(authored_glb(vertices, [(0, 1, 2)], [(0, 0, -1)] * 3), "glb", "3dm")
        for normal in reopened_mesh(output)[3]:
            self.assertAlmostEqual(normal[2], 1, places=6)
        self.assertEqual(info["display"]["normals"]["recomputed"], 1)

    def test_invalid_glb_vertex_attributes_are_refused(self):
        vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        with self.assertRaisesRegex(ConversionError, "position count"):
            GLB().read(authored_glb(vertices, [(0, 1, 2)], [(0, 0, 1)] * 2))
        with self.assertRaisesRegex(ConversionError, "baseColorFactor"):
            GLB().read(authored_glb(vertices, [(0, 1, 2)], material={"pbrMetallicRoughness": {"baseColorFactor": [1, 1]}}))

    def test_three_dm_preserves_only_explicit_source_object_ids(self):
        adapter = ThreeDM()
        scene = fixture()
        first = adapter.r.File3dm.FromByteArray(adapter.write(scene)).Objects[0].Attributes.Id
        second = adapter.r.File3dm.FromByteArray(adapter.write(scene)).Objects[0].Attributes.Id
        self.assertNotEqual(first, second)
        identity = "486a77cb-9c94-40ee-9b18-e5df263719bb"
        scene.meshes[0].source_object_id = identity
        for _ in range(2):
            data = adapter.write(scene)
            self.assertEqual(adapter.r.File3dm.FromByteArray(data).Objects[0].Attributes.Id, UUID(identity))
            self.assertEqual(adapter.read(data).meshes[0].source_object_id, identity)

    def test_three_dm_refuses_invalid_or_duplicate_source_ids(self):
        scene = fixture()
        for identity in ("not-a-guid", "00000000-0000-0000-0000-000000000000"):
            scene.meshes[0].source_object_id = identity
            with self.assertRaisesRegex(ConversionError, "source object ID|Source object IDs"):
                ThreeDM().write(scene)
        scene.meshes[0].source_object_id = "486a77cb-9c94-40ee-9b18-e5df263719bb"
        scene.meshes.append(scene.meshes[0])
        with self.assertRaisesRegex(ConversionError, "unique"):
            ThreeDM().write(scene)

    def test_glb_to_3dm_native_reopen_bounds_and_units(self):
        import rhino3dm as r
        output, info = convert(GLB().write(fixture()), "glb", "3dm")
        self.assertTrue(output.startswith(b"3D Geometry File Format"))
        model = r.File3dm.FromByteArray(output)
        self.assertEqual(model.Settings.ModelUnitSystem, r.UnitSystem.Meters)
        self.assertEqual(len(model.Objects), 1)
        p = model.Objects[0].Geometry.Vertices[0]
        self.assertEqual((p.X, p.Y, p.Z), (1, 2, 3))
        self.assertTrue(info["warnings"])
        # Closing and reopening the written file keeps the shaded display state.
        reopened = ThreeDM().read(base64.b64decode(model.Encode()))
        self.assertEqual(reopened.meshes[0].normals, [(0, 0, 1)] * 3)
        self.assertEqual(reopened.meshes[0].material.color, (200, 200, 200, 255))

    def test_3dm_millimeters_to_glb_signature_and_scale(self):
        import rhino3dm as r
        model = r.File3dm.FromByteArray(ThreeDM().write(fixture()))
        model.Settings.ModelUnitSystem = r.UnitSystem.Millimeters
        output, info = convert(base64.b64decode(model.Encode()), "3dm", "glb")
        self.assertEqual(struct.unpack_from("<4sII", output), (b"glTF", 2, len(output)))
        for a,b in zip(info["outputMetrics"]["boundsMetersZUp"][0], (.001,.002,.003)):
            self.assertAlmostEqual(a,b,places=8)
        self.assertEqual(info["outputMetrics"]["triangleCount"], 1)

    def test_same_format_returns_original_bytes(self):
        source = GLB().write(fixture())
        output, info = convert(source, "glb", "glb")
        self.assertEqual(output, source)
        self.assertFalse(info["converted"])
        self.assertEqual(info["warnings"], [])

    def test_glb_invalid_references_are_refused_before_same_format_delivery(self):
        from archflow.adapters.model_providers import InProcessMeshProvider
        paths = [
            ("scene",), ("scenes", 0, "nodes", 0), ("nodes", 0, "mesh"),
            ("meshes", 0, "primitives", 0, "attributes", "POSITION"),
            ("meshes", 0, "primitives", 0, "indices"),
            ("accessors", 0, "bufferView"), ("bufferViews", 0, "buffer"),
            ("nodes", 0, "children"),
        ]
        for path in paths:
            for value in (-1, False, 0.0, "0", 100):
                with self.subTest(path=path, value=value):
                    changed = [value] if path[-1] == "children" else value
                    with self.assertRaisesRegex(ConversionError, "Invalid GLB .* index"):
                        convert(changed_glb([(path, changed)]), "glb", "glb",
                                providers=(InProcessMeshProvider(),))

    def test_glb_numeric_fields_are_typed_and_bounded(self):
        from archflow.adapters.model_providers import InProcessMeshProvider
        cases = [
            (("buffers", 0, "byteLength"), (-1, False, 48.0, 0, 47, 49)),
            (("bufferViews", 0, "byteOffset"), (-1, False, 0.0, 48)),
            (("bufferViews", 0, "byteLength"), (-1, False, 36.0, 0, 49)),
            (("bufferViews", 0, "byteStride"), (-1, False, 12.0, 0, 8, 14, 256)),
            (("accessors", 0, "byteOffset"), (-1, False, 0.0, 36)),
            (("accessors", 0, "count"), (-1, True, 3.0, 0, 100)),
            (("accessors", 0, "componentType"), (-1, True, 5126.0)),
            (("meshes", 0, "primitives", 0, "mode"), (-1, True, 4.0)),
        ]
        for path, values in cases:
            for value in values:
                with self.subTest(path=path, value=value):
                    with self.assertRaises(ConversionError):
                        convert(changed_glb([(path, value)]), "glb", "glb",
                                providers=(InProcessMeshProvider(),))

    def test_glb_negative_offsets_cannot_cancel_each_other(self):
        changes = [(("bufferViews", 0, "byteOffset"), 4),
                   (("accessors", 0, "byteOffset"), -4)]
        with self.assertRaisesRegex(ConversionError, "accessor byteOffset"):
            GLB().read(changed_glb(changes))

    def test_glb_valid_bin_padding_remains_readable(self):
        source = GLB().write(fixture())
        json_size = struct.unpack_from("<I", source, 12)[0]
        # Three unsigned-byte indices follow the positions; the last byte is BIN padding.
        binary = source[28 + json_size:28 + json_size + 36] + bytes([0, 1, 2, 0])
        data = changed_glb([(("accessors", 1, "componentType"), 5121),
                            (("bufferViews", 1, "byteLength"), 3),
                            (("buffers", 0, "byteLength"), 39)], binary)
        output, info = convert(data, "glb", "glb")
        self.assertEqual(output, data)
        self.assertEqual(info["outputMetrics"]["triangleCount"], 1)

    def test_invalid_output_is_rejected(self):
        with patch.object(GLB, "write", return_value=b"not a GLB"):
            with self.assertRaisesRegex(ConversionError, "signature"):
                convert(ThreeDM().write(fixture()), "3dm", "glb")

    def test_output_scale_change_is_rejected(self):
        altered = fixture()
        altered.meshes[0].vertices[0] = (99,99,99)
        with patch.object(GLB, "write", return_value=GLB().write(altered)):
            with self.assertRaisesRegex(ConversionError, "scale|placement"):
                convert(ThreeDM().write(fixture()), "3dm", "glb")

    def test_curved_3dm_without_render_mesh_is_refused(self):
        import rhino3dm as r
        model = r.File3dm()
        model.Settings.ModelUnitSystem = r.UnitSystem.Meters
        model.Objects.AddBrep(r.Sphere(r.Point3d(0,0,0), 1).ToBrep(), r.ObjectAttributes())
        with self.assertRaisesRegex(ConversionError, "render mesh"):
            convert(base64.b64decode(model.Encode()), "3dm", "glb")

    def test_all_ten_unavailable_routes_are_explicit(self):
        from archflow.adapters.model_providers import ConversionCoordinator, NO_EXECUTOR
        routes = ConversionCoordinator().capabilities()
        self.assertEqual(len(routes), 12)
        blocked = [r for r in routes if not r["available"]]
        self.assertEqual(len(blocked), 10)
        for route in blocked:
            with self.assertRaisesRegex(ConversionError, NO_EXECUTOR):
                convert(b"anything", route["sourceFormat"], route["targetFormat"])
