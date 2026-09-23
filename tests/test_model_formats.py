"""Generated mesh fixtures; native file reopens and numerical interchange checks."""
import base64
import struct
import unittest
from unittest.mock import patch
from archflow.adapters.model_formats import GLB, ThreeDM, Mesh, Scene, convert, ConversionError

def fixture():
    return Scene([Mesh("Offset triangle", [(1, 2, 3), (2, 2, 3), (1, 3, 3)], [(0, 1, 2)], "Structure")], "Meters", [])


class FormatTests(unittest.TestCase):
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
        from archflow.adapters.model_formats import FORMATS, unsupported
        routes = [{"sourceFormat": a, "targetFormat": b, "status": "unsupported" if unsupported(a,b) else "bounded-mesh"}
                  for a in FORMATS for b in FORMATS if a != b]
        self.assertEqual(len(routes), 12)
        blocked = [r for r in routes if r["status"] == "unsupported"]
        self.assertEqual(len(blocked), 10)
        for route in blocked:
            with self.assertRaisesRegex(ConversionError, "unavailable"):
                convert(b"anything", route["sourceFormat"], route["targetFormat"])
