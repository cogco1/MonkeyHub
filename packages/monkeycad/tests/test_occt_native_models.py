"""Native 3DM bytes feed the existing hidden-line and material-section owner."""

from __future__ import annotations

import base64
import math
import subprocess
import sys
import textwrap
import unittest
import uuid
from unittest.mock import patch

import rhino3dm as rhino

from archflow.adapters import occt_backend as backend


def _model(unit=rhino.UnitSystem.Meters):
    model = rhino.File3dm()
    model.Settings.ModelUnitSystem = unit
    layer = rhino.Layer()
    layer.Name = "Model"
    model.Layers.Add(layer)
    return model


def _bytes(model):
    return base64.b64decode(model.Encode())


def _box(x=0.0):
    return rhino.Brep.CreateFromBox(rhino.Box(rhino.BoundingBox(x, 0, 0, x + 2, 3, 4)))


def _rect(points):
    return rhino.PolylineCurve([rhino.Point3d(*point) for point in points])


def _ring():
    outer = _rect([(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (0, 0, 0)])
    inner = _rect([(1, 1, 0), (1, 3, 0), (3, 3, 0), (3, 1, 0), (1, 1, 0)])
    extrusion = rhino.Extrusion.Create(outer, 3, True)
    assert extrusion.AddInnerProfile(inner)
    return extrusion


def _mesh_box():
    mesh = rhino.Mesh()
    for point in [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 3, 0),
                  (0, 0, 4), (2, 0, 4), (2, 3, 4), (0, 3, 4)]:
        mesh.Vertices.Add(*point)
    for a, b, c, d in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                       (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
        mesh.Faces.AddFace(a, b, c)
        mesh.Faces.AddFace(a, c, d)
    return mesh


def _project(entries):
    return backend.project_occt_lines(entries, object_ids=[entry.name for entry in entries],
                                      origin=(0, -10, 0), right=(1, 0, 0), up=(0, 0, 1),
                                      linear_deflection=0.001)


def _section(entries, z=1):
    return backend.section_occt_regions(entries, object_ids=[entry.name for entry in entries],
                                        origin=(0, 0, z), right=(1, 0, 0), up=(0, 1, 0),
                                        linear_deflection=0.001)


@unittest.skipUnless(backend.occt_available(), "cadquery-ocp is unavailable")
class NativeThreeDmTests(unittest.TestCase):
    def test_exact_brep_preserves_guid_z_up_and_units_without_host_or_file_writes(self):
        for rhino_unit, unit in [(rhino.UnitSystem.Meters, "meter"), (rhino.UnitSystem.Millimeters, "millimeter"),
                                 (rhino.UnitSystem.Inches, "inch"), (rhino.UnitSystem.Feet, "foot")]:
            with self.subTest(unit=unit):
                model = _model(rhino_unit)
                guid = model.Objects.AddBrep(_box())
                data = _bytes(model)
                with patch("subprocess.Popen", side_effect=AssertionError("no CAD host")), \
                     patch("builtins.open", side_effect=AssertionError("no files")):
                    entries, units = backend.read_three_dm(data)
                self.assertEqual(units, unit)
                self.assertEqual([entry.name for entry in entries], [str(guid)])
                self.assertEqual(entries[0].geometry_quality, "exact")
                measure = backend.measure_shape(entries[0].shape)
                self.assertEqual(measure.bbox_min, (0, 0, 0))
                self.assertEqual(measure.bbox_max, (2, 3, 4))
                self.assertAlmostEqual(measure.volume, 24)
                lines = _project(entries)
                self.assertEqual({line.kind for line in lines}, {"visible", "hidden"})
                self.assertEqual(len(_section(entries)), 1)

    def test_mesh_coplanar_triangles_do_not_emit_diagonals_and_can_be_cut(self):
        model = _model()
        model.Objects.AddMesh(_mesh_box())
        entries, _ = backend.read_three_dm(_bytes(model))
        self.assertEqual(entries[0].geometry_quality, "faceted")
        self.assertEqual(backend.measure_shape(entries[0].shape).face_count, 6)
        lines = _project(entries)
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(len({point[0] for point in line.points}) == 1 or
                            len({point[1] for point in line.points}) == 1)
        self.assertEqual(len(_section(entries)[0].loops), 1)

    def test_disconnected_solids_in_one_native_mesh_keep_one_id_and_separate_sections(self):
        model = _model()
        mesh, second = _mesh_box(), _mesh_box()
        second.Translate(rhino.Vector3d(6, 0, 0))
        mesh.Append(second)
        self.assertTrue(mesh.IsClosed)
        object_id = str(model.Objects.AddMesh(mesh))
        entries, _ = backend.read_three_dm(_bytes(model))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, object_id)
        self.assertEqual(entries[0].geometry_quality, "faceted")
        measure = backend.measure_shape(entries[0].shape)
        self.assertEqual(measure.solid_count, 2)
        self.assertEqual(measure.face_count, 12)
        self.assertAlmostEqual(measure.volume, 48)
        regions = _section(entries)
        self.assertEqual(len(regions), 2)
        self.assertEqual({region.object_id for region in regions}, {object_id})
        self.assertTrue(all(len(region.loops) == 1 for region in regions))
        lines = _project(entries)
        self.assertEqual({line.kind for line in lines}, {"visible", "hidden"})
        self.assertEqual({line.object_id for line in lines}, {object_id})
        self.assertEqual({point[0] for line in lines for point in line.points}, {0, 2, 6, 8})

    def test_overlapping_or_nested_native_mesh_shells_are_refused(self):
        for contained in (False, True):
            with self.subTest(contained=contained):
                model = _model()
                mesh, other = _mesh_box(), _mesh_box()
                if contained:
                    other.Scale(.5)
                other.Translate(rhino.Vector3d(.5, .5, .5))
                mesh.Append(other)
                self.assertTrue(mesh.IsClosed)
                model.Objects.AddMesh(mesh)
                with self.assertRaisesRegex(backend.OcctCapabilityError, "overlapping or contained"):
                    backend.read_three_dm(_bytes(model))

    def test_saved_brep_mesh_still_requires_supported_cavity_semantics(self):
        mesh, other = _mesh_box(), _mesh_box()
        other.Scale(.5)
        other.Translate(rhino.Vector3d(.5, .5, .5))
        mesh.Append(other)
        with self.assertRaisesRegex(backend.OcctCapabilityError, "one closed manifold shell"):
            # BRep/Extrusion saved meshes carry explicit source closure; only
            # original native Mesh objects may use the new compound path.
            backend._native_mesh_shape(backend._occt(), mesh, "brep", closed=True)

    def test_planar_brep_trim_hole_survives_projection_and_cut(self):
        model = _model()
        model.Objects.AddBrep(_ring().ToBrep(True))
        entries, _ = backend.read_three_dm(_bytes(model))
        self.assertAlmostEqual(backend.measure_shape(entries[0].shape).volume, 36)
        self.assertEqual(len(_section(entries)[0].loops), 2)
        plan = backend.project_occt_lines(entries, object_ids=[entries[0].name],
                                         origin=(0, 0, 10), right=(1, 0, 0), up=(0, 1, 0),
                                         linear_deflection=0.001)
        visible_points = {point for line in plan if line.kind == "visible" for point in line.points}
        self.assertTrue({(1, 1), (3, 3)} <= visible_points)

    def test_native_extrusion_and_rational_curve_keep_exact_geometry(self):
        model = _model()
        model.Objects.AddExtrusion(_ring())
        model.Objects.AddExtrusion(rhino.Extrusion.Create(rhino.Circle(rhino.Point3d(10, 0, 0), 2).ToNurbsCurve(), 3, True))
        # Ellipse exercises homogeneous rational NURBS transfer, not circular arcs.
        ellipse = rhino.Circle(rhino.Point3d(0, 0, 0), 1).ToNurbsCurve()
        scale = rhino.Transform.Identity()
        scale.M00, scale.M11 = 5, 2
        self.assertTrue(ellipse.Transform(scale))
        model.Objects.AddCurve(ellipse)
        entries, _ = backend.read_three_dm(_bytes(model))
        self.assertEqual([entry.geometry_quality for entry in entries], ["exact"] * 3)
        self.assertEqual(len(_section(entries[:1])[0].loops), 2)
        self.assertAlmostEqual(backend.measure_shape(entries[1].shape).volume, math.pi * 12)
        self.assertEqual(_section(entries[2:]), ())
        plan = backend.project_occt_lines(entries[2:], object_ids=[entries[2].name],
                                         origin=(0, 0, 10), right=(1, 0, 0), up=(0, 1, 0),
                                         linear_deflection=0.001)
        points = [point for line in plan for point in line.points]
        self.assertGreater(len(points), 20)
        for x, y in points:
            self.assertAlmostEqual(x*x / 25 + y*y / 4, 1, places=7)

    def test_nested_block_instances_have_unique_paths_and_composed_transforms(self):
        model = _model()
        first = model.InstanceDefinitions.Add("inner", "", "", "", rhino.Point3d(0, 0, 0),
                                              (_box(),), (rhino.ObjectAttributes(),))
        inner = model.InstanceDefinitions.FindIndex(first)
        reference = rhino.InstanceReference(inner.Id, rhino.Transform.Translation(10, 0, 0))
        second = model.InstanceDefinitions.Add("outer", "", "", "", rhino.Point3d(0, 0, 0),
                                               (reference,), (rhino.ObjectAttributes(),))
        outer = model.InstanceDefinitions.FindIndex(second)
        rotation = rhino.Transform.Rotation(math.pi / 2, rhino.Vector3d(0, 0, 1), rhino.Point3d(0, 0, 0))
        roots = [model.Objects.AddInstanceObject(rhino.InstanceReference(
                    outer.Id, rhino.Transform.Multiply(rhino.Transform.Translation(0, y, 0), rotation)))
                 for y in (20, 40)]
        entries, _ = backend.read_three_dm(_bytes(model))
        self.assertEqual(len(entries), 2)
        self.assertEqual(len({entry.name for entry in entries}), 2)
        for root, entry, y in zip(roots, entries, (20, 40)):
            self.assertTrue(entry.name.startswith(str(root) + "/"))
            self.assertEqual(len(entry.name.split("/")), 3)
            measure = backend.measure_shape(entry.shape)
            self.assertAlmostEqual(measure.bbox_min[0], -3, places=5)
            self.assertAlmostEqual(measure.bbox_min[1], y + 10, places=5)
        self.assertTrue(_project(entries))
        self.assertEqual(len(_section(entries)), 2)

    def test_hidden_objects_layers_and_parent_layers_are_not_drawn(self):
        model = _model()
        hidden = rhino.ObjectAttributes()
        hidden.Visible = False
        model.Objects.AddSphere(rhino.Sphere(rhino.Point3d(0, 0, 0), 1), hidden)
        parent = rhino.Layer()
        parent.Name, parent.Visible = "Hidden", False
        parent_index = model.Layers.Add(parent)
        child = rhino.Layer()
        child.Name, child.ParentLayerId = "Child", model.Layers.FindIndex(parent_index).Id
        child_index = model.Layers.Add(child)
        for layer_index in (parent_index, child_index):
            attributes = rhino.ObjectAttributes()
            attributes.LayerIndex = layer_index
            model.Objects.AddSphere(rhino.Sphere(rhino.Point3d(0, 0, 0), 1), attributes)
        visible_id = model.Objects.AddBrep(_box())
        entries, _ = backend.read_three_dm(_bytes(model))
        self.assertEqual([entry.name for entry in entries], [str(visible_id)])

    def test_nonplanar_brep_without_mesh_is_refused_instead_of_bounding_box(self):
        model = _model()
        model.Objects.AddBrep(_box())
        guid = model.Objects.AddBrep(rhino.Sphere(rhino.Point3d(5, 0, 0), 2).ToBrep())
        with self.assertRaisesRegex(backend.OcctCapabilityError, str(guid) + ".*non-planar BRep"):
            backend.read_three_dm(_bytes(model))

    def test_saved_nonplanar_brep_mesh_is_explicitly_approximate(self):
        # rhino3dm 8.32 SetMesh transfers the same mesh pointer into the BRep;
        # destroying both Python owners double-frees it. Isolate only fixture
        # creation and exit before native finalizers; the reader starts no host.
        source = textwrap.dedent("""
            import base64, os, sys, rhino3dm as r
            model = r.File3dm()
            model.Settings.ModelUnitSystem = r.UnitSystem.Meters
            sphere = r.Sphere(r.Point3d(0, 0, 0), 1).ToBrep()
            mesh = r.Mesh()
            for point in [(1,0,0),(0,1,0),(-1,0,0),(0,-1,0),(0,0,1),(0,0,-1)]:
                mesh.Vertices.Add(*point)
            for face in [(0,1,4),(1,2,4),(2,3,4),(3,0,4),(1,0,5),(2,1,5),(3,2,5),(0,3,5)]:
                mesh.Faces.AddFace(*face)
            assert sphere.Faces[0].SetMesh(mesh, r.MeshType.Render)
            model.Objects.AddBrep(sphere)
            sys.stdout.buffer.write(base64.b64decode(model.Encode()))
            sys.stdout.buffer.flush()
            os._exit(0)
        """)
        data = subprocess.run([sys.executable, "-c", source], capture_output=True, check=True, timeout=30).stdout
        entries, _ = backend.read_three_dm(data)
        self.assertEqual(entries[0].geometry_quality, "mesh_approximation")
        self.assertEqual(backend.measure_shape(entries[0].shape).face_count, 8)
        self.assertTrue(_project(entries))
        self.assertTrue(_section(entries, .25))

    def test_bad_empty_unknown_units_and_unsupported_visible_objects_fail(self):
        for data in (b"", b"not a 3DM", _bytes(_model())):
            with self.subTest(data=data[:12]), self.assertRaises(backend.OcctBackendError):
                backend.read_three_dm(data)
        model = _model(getattr(rhino.UnitSystem, "None"))
        model.Objects.AddBrep(_box())
        with self.assertRaisesRegex(backend.OcctCapabilityError, "unsupported length unit"):
            backend.read_three_dm(_bytes(model))
        model = _model()
        model.Objects.AddBrep(_box())
        model.Objects.AddPoint(rhino.Point3d(0, 0, 0))
        with self.assertRaisesRegex(backend.OcctCapabilityError, "unsupported visible 3DM geometry"):
            backend.read_three_dm(_bytes(model))

    def test_missing_block_definition_fails_explicitly(self):
        model = _model()
        model.Objects.AddInstanceObject(rhino.InstanceReference(uuid.uuid4(), rhino.Transform.Identity()))
        with self.assertRaisesRegex(backend.OcctBuildError, "missing definition"):
            backend.read_three_dm(_bytes(model))

    def test_cyclic_block_definition_fails_explicitly(self):
        model = _model()
        index = model.InstanceDefinitions.Add("cycle", "", "", "", rhino.Point3d(0, 0, 0),
                                             (_box(),), (rhino.ObjectAttributes(),))
        definition = model.InstanceDefinitions.FindIndex(index)
        member_id = definition.GetObjectIds()[0]
        attributes = model.Objects.FindId(member_id).Attributes
        model.Objects.Delete(member_id)
        attributes.Id = member_id
        model.Objects.AddInstanceObject(rhino.InstanceReference(definition.Id, rhino.Transform.Identity()), attributes)
        model.Objects.AddInstanceObject(rhino.InstanceReference(definition.Id, rhino.Transform.Identity()))
        with self.assertRaisesRegex(backend.OcctBuildError, "block cycle"):
            backend.read_three_dm(_bytes(model))

    def test_nonuniform_mirrored_instance_preserves_shape_and_sections(self):
        model = _model()
        index = model.InstanceDefinitions.Add("scaled", "", "", "", rhino.Point3d(0, 0, 0),
                                             (_box(),), (rhino.ObjectAttributes(),))
        transform = rhino.Transform.Identity()
        transform.M00, transform.M11, transform.M22 = -2, 3, 4
        transform.M03 = 10
        model.Objects.AddInstanceObject(rhino.InstanceReference(model.InstanceDefinitions.FindIndex(index).Id, transform))
        entries, _ = backend.read_three_dm(_bytes(model))
        measure = backend.measure_shape(entries[0].shape)
        self.assertAlmostEqual(measure.volume, 576, places=5)
        for actual, expected in zip(measure.bbox_min + measure.bbox_max, (6, 0, 0, 10, 9, 16)):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertTrue(_section(entries))

    def test_open_mesh_and_finite_planar_surface_have_no_material_fill(self):
        model = _model()
        mesh = rhino.Mesh()
        for point in [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)]:
            mesh.Vertices.Add(*point)
        mesh.Faces.AddFace(0, 1, 2, 3)
        model.Objects.AddMesh(mesh)
        surface = rhino.PlaneSurface(rhino.Plane.WorldXY(), rhino.Interval(0, 4), rhino.Interval(0, 5))
        model.Objects.AddSurface(surface)
        entries, _ = backend.read_three_dm(_bytes(model))
        self.assertEqual(len(entries), 2)
        self.assertEqual(_section(entries), ())
        self.assertTrue(_project(entries))


if __name__ == "__main__":
    unittest.main()
