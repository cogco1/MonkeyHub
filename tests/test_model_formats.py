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


class FormatTests(unittest.TestCase):
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
