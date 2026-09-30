"""SKP reader behavior, including real SDK round trips when installed locally."""
import ctypes as c
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from archflow.adapters.local_cad_discovery import Discovery, discover_local_cad
from archflow.adapters.model_formats import ConversionError, ThreeDM
from archflow.adapters import sketchup_reader as reader


class SketchUpReaderBoundaryTests(unittest.TestCase):
    def test_new_install_directory_is_detected_without_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "SketchUp/SketchUp 2025/SketchUp/SketchUp.exe"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not executable")
            with patch("subprocess.Popen", side_effect=AssertionError("must not launch")):
                result = discover_local_cad(system="Windows", program_roots=[temporary], registry_candidates=[])
            self.assertEqual([row.executable for row in result.installations], [path.resolve()])

    def test_no_sdk_returns_runtime_unavailable(self):
        with patch.object(reader, "discover_local_cad", return_value=Discovery("Windows", ())):
            with self.assertRaisesRegex(ConversionError, "runtime unavailable"):
                reader.read_skp(b"source")

    def test_incomplete_sdk_and_empty_input_fail_explicitly(self):
        with self.assertRaisesRegex(ConversionError, "SDK entry SUInitialize"):
            reader._API(object())
        with self.assertRaisesRegex(ConversionError, "model bytes"):
            reader.read_skp(b"")

    def test_homogeneous_scale_and_bad_transform(self):
        transform = list(reader._IDENTITY)
        transform[15] = .5
        self.assertEqual(reader._point_meters(reader._Point(10, 20, 30), transform), (.508, 1.016, 1.524))
        transform[15] = 0
        with self.assertRaisesRegex(ConversionError, "invalid instance transform"):
            reader._point_meters(reader._Point(), transform)


def _write_native_fixture(api, path, *, revision_path=None):
    """Two visible occurrences, a face hole, and hidden faces/tags/instance."""
    ref, refp = reader._Ref, c.POINTER(reader._Ref)
    signatures = {
        "SUModelCreate": [refp], "SUModelSaveToFile": [ref, c.c_char_p],
        "SUComponentDefinitionCreate": [refp],
        "SUModelAddComponentDefinitions": [ref, c.c_size_t, refp],
        "SUComponentDefinitionCreateInstance": [ref, refp],
        "SUComponentInstanceSetTransform": [ref, c.POINTER(reader._Transform)],
        "SUComponentInstanceSetName": [ref, c.c_char_p],
        "SUEntitiesAddInstance": [ref, ref, refp],
        "SUGroupCreate": [refp], "SUGroupGetEntities": [ref, refp],
        "SUEntitiesAddGroup": [ref, ref],
        "SULoopInputCreate": [refp], "SULoopInputAddVertexIndex": [ref, c.c_size_t],
        "SUFaceCreate": [refp, c.POINTER(reader._Point), refp],
        "SUFaceAddInnerLoop": [ref, c.POINTER(reader._Point), refp],
        "SUEntitiesAddFaces": [ref, c.c_size_t, refp],
        "SUDrawingElementSetHidden": [ref, c.c_bool], "SUDrawingElementSetLayer": [ref, ref],
        "SULayerCreate": [refp], "SULayerSetName": [ref, c.c_char_p],
        "SUModelAddLayers": [ref, c.c_size_t, refp], "SULayerSetVisibility": [ref, c.c_bool],
    }
    for name, args in signatures.items():
        function = getattr(api.library, name)
        function.argtypes, function.restype = args, c.c_int
        setattr(api, name, function)
    model = api.output("SUModelCreate", ref)
    try:
        root = api.output("SUModelGetEntities", ref, model)
        definition = api.output("SUComponentDefinitionCreate", ref)
        api.call("SUModelAddComponentDefinitions", model, 1, (ref * 1)(definition))
        entities = api.output("SUComponentDefinitionGetEntities", ref, definition)

        def loop():
            value = api.output("SULoopInputCreate", ref)
            for index in range(4):
                api.call("SULoopInputAddVertexIndex", value, index)
            return value

        def face(z):
            value, outline = ref(), loop()
            points = (reader._Point * 4)(reader._Point(0, 0, z), reader._Point(10, 0, z),
                                        reader._Point(10, 10, z), reader._Point(0, 10, z))
            api.call("SUFaceCreate", c.byref(value), points, c.byref(outline))
            api.call("SUEntitiesAddFaces", entities, 1, (ref * 1)(value))
            return value

        visible = face(0)
        hole = loop()
        points = (reader._Point * 4)(reader._Point(2, 2, 0), reader._Point(2, 4, 0),
                                    reader._Point(4, 4, 0), reader._Point(4, 2, 0))
        api.call("SUFaceAddInnerLoop", visible, points, c.byref(hole))
        hidden = face(1000)
        api.call("SUDrawingElementSetHidden", api.SUFaceToDrawingElement(hidden), True)

        def layer(name, visible):
            value = api.output("SULayerCreate", ref)
            api.call("SULayerSetName", value, name.encode())
            api.call("SUModelAddLayers", model, 1, (ref * 1)(value))
            api.call("SULayerSetVisibility", value, visible)
            return value

        hidden_tag = layer("Hidden geometry", False)
        tagged = face(2000)
        api.call("SUDrawingElementSetLayer", api.SUFaceToDrawingElement(tagged), hidden_tag)

        def instance(container, transform, name, hidden=False):
            value = api.output("SUComponentDefinitionCreateInstance", ref, definition)
            api.call("SUEntitiesAddInstance", container, value, None)
            api.call("SUComponentInstanceSetName", value, name.encode())
            api.call("SUComponentInstanceSetTransform", value, c.byref(reader._Transform((c.c_double * 16)(*transform))))
            api.call("SUDrawingElementSetHidden", api.SUComponentInstanceToDrawingElement(value), hidden)
            return value

        group = api.output("SUGroupCreate", ref)
        api.call("SUEntitiesAddGroup", root, group)
        group_instance = api.SUGroupToComponentInstance(group)
        rotation = (0., 1., 0., 0., -1., 0., 0., 0., 0., 0., 1., 0., 100., 200., 300., 1.)
        api.call("SUComponentInstanceSetTransform", group_instance, c.byref(reader._Transform((c.c_double * 16)(*rotation))))
        api.call("SUComponentInstanceSetName", group_instance, "Rotated group".encode())
        structure_tag = layer("Structure", True)
        api.call("SUDrawingElementSetLayer", api.SUComponentInstanceToDrawingElement(group_instance), structure_tag)
        child = api.output("SUGroupGetEntities", ref, group)
        scale = list(reader._IDENTITY)
        scale[0], scale[5] = -2, 3
        nested_instance = instance(child, scale, "Nested mirror")
        translation = list(reader._IDENTITY)
        translation[12] = -50
        second_instance = instance(root, translation, "Second occurrence")
        translation[14] = 9000
        instance(root, translation, "Hidden occurrence", hidden=True)
        api.call("SUModelSaveToFile", model, str(path).encode("utf-8"))
        if revision_path is not None:
            api.call("SUComponentInstanceSetName", group_instance, b"Renamed parent")
            api.call("SUComponentInstanceSetName", nested_instance, b"Renamed nested")
            api.call("SUComponentInstanceSetName", second_instance, b"Renamed second")
            api.call("SULayerSetName", structure_tag, b"Renamed structure tag")
            moved = list(reader._IDENTITY)
            moved[12], moved[13] = -45, 2
            api.call("SUComponentInstanceSetTransform", second_instance,
                     c.byref(reader._Transform((c.c_double * 16)(*moved))))
            api.call("SUModelSaveToFile", model, str(revision_path).encode("utf-8"))
    finally:
        api.call("SUModelRelease", c.byref(model))


class SketchUpNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.sdk_path = reader._sdk_location(None)
        except ConversionError as exc:
            raise unittest.SkipTest(str(exc))
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        fixture = Path(cls.temporary.name) / "嵌套 geometry.skp"
        revision = Path(cls.temporary.name) / "renamed-and-moved.skp"
        with reader._open_sdk(cls.sdk_path) as api:
            _write_native_fixture(api, fixture, revision_path=revision)
        cls.data = fixture.read_bytes()
        cls.revision_data = revision.read_bytes()

    def test_nested_mirror_hole_visibility_and_cold_three_dm_readback(self):
        with patch("subprocess.Popen", side_effect=AssertionError("must not launch")):
            scene = reader.read_skp(self.data, sdk_path=self.sdk_path)
        self.assertEqual(len(scene.meshes), 2)
        self.assertEqual(len({mesh.name for mesh in scene.meshes}), 2)
        nested = next(mesh for mesh in scene.meshes if "Nested mirror" in mesh.name)
        self.assertEqual(nested.layer, "Structure")
        for axis, limits in enumerate(((70, 100), (180, 200), (300, 300))):
            self.assertAlmostEqual(min(v[axis] for v in nested.vertices), limits[0] * .0254)
            self.assertAlmostEqual(max(v[axis] for v in nested.vertices), limits[1] * .0254)
        signed_area = 0.
        for a, b, d in nested.triangles:
            p, q, r = [nested.vertices[index] for index in (a, b, d)]
            signed_area += ((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])) / 2
        self.assertAlmostEqual(signed_area, 96 * 6 * .0254 ** 2)
        again = reader.read_skp(self.data, sdk_path=self.sdk_path)
        self.assertEqual([m.name for m in scene.meshes], [m.name for m in again.meshes])
        reopened = ThreeDM().read(ThreeDM().write(scene))
        self.assertEqual(reopened.metrics()["triangleCount"], scene.metrics()["triangleCount"])
        for expected, actual in zip(sum(scene.metrics()["boundsMetersZUp"], []), sum(reopened.metrics()["boundsMetersZUp"], [])):
            self.assertAlmostEqual(expected, actual, places=6)

    def test_invalid_skp_is_rejected_and_next_read_still_works(self):
        with self.assertRaisesRegex(ConversionError, "SUModelCreateFromBufferWithStatus failed"):
            reader.read_skp(b"not an SKP", sdk_path=self.sdk_path)
        self.assertEqual(len(reader.read_skp(self.data, sdk_path=self.sdk_path).meshes), 2)

    def test_persistent_instance_guids_survive_repeat_reads_renames_and_placement_edits(self):
        scenes = [reader.read_skp(data, sdk_path=self.sdk_path)
                  for data in (self.data, self.data, self.revision_data)]
        expected = {mesh.source_object_id for mesh in scenes[0].meshes}
        self.assertEqual(len(expected), 2)
        self.assertNotIn(None, expected)
        adapter = ThreeDM()
        for scene in scenes:
            self.assertEqual({mesh.source_object_id for mesh in scene.meshes}, expected)
            model = adapter.r.File3dm.FromByteArray(adapter.write(scene))
            self.assertEqual({str(item.Attributes.Id) for item in model.Objects}, expected)
        self.assertNotEqual([mesh.name for mesh in scenes[0].meshes], [mesh.name for mesh in scenes[2].meshes])
        self.assertNotEqual([mesh.layer for mesh in scenes[0].meshes], [mesh.layer for mesh in scenes[2].meshes])
        self.assertNotEqual(scenes[0].metrics()["boundsMetersZUp"], scenes[2].metrics()["boundsMetersZUp"])

    def test_missing_persistent_identity_is_refused(self):
        output = reader._API.output

        def without_id(api, name, kind, *args):
            return c.c_int64(0) if name == "SUEntityGetPersistentID" else output(api, name, kind, *args)

        with patch.object(reader._API, "output", without_id):
            with self.assertRaisesRegex(ConversionError, "no reliable persistent ID"):
                reader.read_skp(self.data, sdk_path=self.sdk_path)

    def test_bundled_template_without_source_writes(self):
        templates = self.sdk_path.parent / "resources/en-US/Templates"
        fixture = next(templates.glob("Temp01a*.skp"), None)
        if fixture is None:
            self.skipTest("SketchUp installation has no bundled Simple template")
        data = fixture.read_bytes()
        scene = reader.read_skp(data, sdk_path=self.sdk_path)
        self.assertGreater(scene.metrics()["triangleCount"], 0)
        self.assertEqual(data, fixture.read_bytes())
        self.assertTrue(all(mesh.name.startswith("skp:") for mesh in scene.meshes))


if __name__ == "__main__":
    unittest.main()
