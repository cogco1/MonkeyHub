from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from archflow.adapters.three_dm_inspector import (
    ThreeDmInspection,
    ThreeDmInspectionError,
    ThreeDmInspectionErrorCode,
    _aggregate_bbox,
    _points_bbox,
    _rgba,
    _visible_geometry_points,
    inspect_three_dm,
)

try:
    import rhino3dm
except ImportError:  # pragma: no cover - optional dependency
    rhino3dm = None


class ThreeDmInspectorTests(unittest.TestCase):
    def test_missing_file_fails_closed_with_named_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "missing.3dm"

            with self.assertRaises(ThreeDmInspectionError) as raised:
                inspect_three_dm(source)

        self.assertIs(
            raised.exception.code,
            ThreeDmInspectionErrorCode.FILE_NOT_FOUND,
        )

    def test_directory_is_rejected_as_non_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(ThreeDmInspectionError) as raised:
                inspect_three_dm(Path(temporary_directory))

        self.assertIs(
            raised.exception.code,
            ThreeDmInspectionErrorCode.PATH_NOT_FILE,
        )

    def test_missing_optional_dependency_is_typed_and_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "model.3dm"
            source.write_bytes(b"file bytes are read before dependency loading")
            with patch(
                "archflow.adapters.three_dm_inspector.importlib.import_module",
                side_effect=ModuleNotFoundError("rhino3dm"),
            ):
                with self.assertRaises(ThreeDmInspectionError) as raised:
                    inspect_three_dm(source)

        error = raised.exception
        self.assertIs(
            error.code,
            ThreeDmInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
        )
        self.assertIn("pip install rhino3dm", error.message)
        self.assertEqual(
            error.to_dict()["code"],
            "three_dm_inspection.dependency_unavailable",
        )

    @unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
    def test_damaged_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "damaged.3dm"
            source.write_bytes(b"not an openNURBS archive")

            with self.assertRaises(ThreeDmInspectionError) as raised:
                inspect_three_dm(source)

        self.assertIs(
            raised.exception.code,
            ThreeDmInspectionErrorCode.INVALID_FILE,
        )

    @unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
    def test_valid_model_produces_stable_complete_json_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "model.3dm"
            self._write_model(source)
            expected_bytes = source.read_bytes()

            first = inspect_three_dm(source)
            second = inspect_three_dm(source)

        self.assertEqual(first, second)
        self.assertIsInstance(first, ThreeDmInspection)
        summary = first.to_dict()
        json.dumps(summary, allow_nan=False, sort_keys=True)
        self.assertEqual(summary["schema"], "ThreeDmInspectionSummary@4")
        self.assertEqual(
            summary["file_sha256"],
            hashlib.sha256(expected_bytes).hexdigest(),
        )
        self.assertEqual(summary["file_bytes"], len(expected_bytes))
        self.assertEqual(summary["three_dm_version"], 8)
        self.assertEqual(summary["archive_version"], 80)
        self.assertEqual(summary["units"], {"name": "Meters", "code": 4})
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["rhino_process_started"])

        self.assertEqual(summary["object_count"], 4)
        self.assertEqual(summary["top_level_object_count"], 3)
        self.assertEqual(summary["instance_definition_member_count"], 1)
        self.assertEqual(summary["object_counts_by_type"]["Point"], 2)
        self.assertEqual(
            summary["object_counts_by_type"]["InstanceReference"],
            1,
        )
        self.assertEqual(
            [item["total"] for item in summary["object_counts_by_layer"]],
            [2, 2],
        )
        self.assertEqual(
            [item["color_rgba"] for item in summary["layers"]],
            [[10, 20, 30, 255], [40, 50, 60, 128]],
        )

        self.assertEqual(len(summary["instance_definitions"]), 1)
        definition = summary["instance_definitions"][0]
        self.assertEqual(definition["name"], "Marker")
        self.assertEqual(definition["object_count"], 1)
        self.assertEqual(definition["reference_count"], 1)
        self.assertEqual(len(summary["instance_references"]), 1)
        self.assertEqual(
            summary["instance_references"][0]["definition_id"],
            definition["id"],
        )

        self.assertEqual(
            summary["document_user_strings"],
            [
                {"key": "project", "value": "archflow"},
                {"key": "status", "value": "inspection"},
            ],
        )
        self.assertEqual(len(summary["object_user_strings"]), 1)
        self.assertEqual(
            summary["object_user_strings"][0]["name"],
            "axis-witness",
        )
        self.assertEqual(
            summary["object_user_strings"][0]["layer_path"],
            "Main",
        )
        self.assertEqual(
            summary["object_user_strings"][0]["attributes"],
            [{"key": "discipline", "value": "architecture"}],
        )
        self.assertEqual(
            summary["object_user_strings"][0]["geometry"],
            [{"key": "role", "value": "survey-point"}],
        )
        self.assertEqual(
            summary["aggregate_bbox"],
            {
                "min": [-1.0, 0.0, 0.0],
                "max": [11.0, 4.0, 6.0],
            },
        )
        self.assertEqual(summary["bbox_contributing_geometry_count"], 3)
        self.assertEqual(len(summary["visible_bounds_witnesses"]), 3)
        self.assertEqual(len(summary["named_object_bboxes"]), 1)
        self.assertEqual(
            summary["named_object_bboxes"][0]["name"],
            "axis-witness",
        )
        self.assertEqual(
            summary["named_object_bboxes"][0]["layer_path"],
            "Main",
        )
        self.assertEqual(
            summary["named_object_bboxes"][0]["bbox"],
            {"min": [-1.0, 2.0, 3.0], "max": [-1.0, 2.0, 3.0]},
        )
        self.assertEqual(summary["materials"], [])
        self.assertEqual(summary["render_materials"], [])
        self.assertEqual(len(summary["object_material_bindings"]), 4)
        self.assertEqual(len(summary["object_geometry_analysis"]), 4)

    @unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
    def test_native_material_table_and_object_attachment_are_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "native-material.3dm"
            model = rhino3dm.File3dm()
            layer = rhino3dm.Layer()
            layer.Name = "Material"
            layer_index = model.Layers.Add(layer)

            material = rhino3dm.Material()
            material.Name = "archflow-material:stucco"
            material.DiffuseColor = (210, 205, 190, 255)
            material.Transparency = 0.35
            material.SetUserString("archflow:material_id", "stucco")
            material.ToPhysicallyBased()
            material_index = model.Materials.Add(material)

            attributes = rhino3dm.ObjectAttributes()
            attributes.LayerIndex = layer_index
            attributes.Name = "material-point"
            attributes.MaterialIndex = material_index
            attributes.MaterialSource = (
                rhino3dm.ObjectMaterialSource.MaterialFromObject
            )
            model.Objects.AddPoint(rhino3dm.Point3d(1, 2, 3), attributes)
            self.assertTrue(model.Write(str(source), 8))

            summary = inspect_three_dm(source).to_dict()

        self.assertEqual(len(summary["materials"]), 1)
        saved_material = summary["materials"][0]
        self.assertEqual(saved_material["index"], 0)
        self.assertEqual(saved_material["name"], "archflow-material:stucco")
        self.assertEqual(
            saved_material["diffuse_color_rgba"],
            [210, 205, 190, 255],
        )
        self.assertTrue(saved_material["physically_based"])
        # the native openNURBS transparency the file actually stores, not a default
        self.assertEqual(saved_material["transparency"], 0.35)
        self.assertEqual(
            saved_material["user_strings"],
            [{"key": "archflow:material_id", "value": "stucco"}],
        )
        self.assertEqual(summary["render_materials"], [])
        self.assertEqual(len(summary["object_material_bindings"]), 1)
        binding = summary["object_material_bindings"][0]
        self.assertEqual(binding["name"], "material-point")
        self.assertEqual(binding["material_source"], "MaterialFromObject")
        self.assertEqual(binding["material_source_code"], 1)
        self.assertEqual(binding["material_index"], 0)
        self.assertEqual(binding["material_name"], "archflow-material:stucco")
        self.assertEqual(binding["archflow_material_id"], "stucco")
        self.assertEqual(binding["material_transparency"], 0.35)
        self.assertIsNone(binding["render_material_instance_id"])

    @unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
    def test_every_object_user_string_is_read_back_whatever_its_key(self) -> None:
        """The producer parameters an export writes come back verbatim, keys the reader has never seen included.

        The inspector reads the whole user-string table off the object, not a
        known subset, so the wedge and shell strings the CAD side started
        writing need no change here - and neither will the next producer's.
        """

        written = {
            "archflow:component": "roof-abutments",
            "archflow:producer_op": "abutment-north",
            "archflow:wedge_low": "0.5",
            "archflow:wedge_high": "2.5",
            "archflow:wedge_axis": "along",
            "archflow:wedge_sense": "+x",
            "archflow:shell_thickness": "0.6",
            "archflow:shell_kind": "cylinder",
            "archflow:not_a_key_this_reader_knows": "carried anyway",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "stated-parameters.3dm"
            model = rhino3dm.File3dm()
            layer = rhino3dm.Layer()
            layer.Name = "Main"
            layer_index = model.Layers.Add(layer)
            attributes = rhino3dm.ObjectAttributes()
            attributes.LayerIndex = layer_index
            attributes.Name = "obj-abutment-north"
            for key, value in written.items():
                attributes.SetUserString(key, value)
            model.Objects.AddPoint(rhino3dm.Point3d(0, 0, 0), attributes)
            self.assertTrue(model.Write(str(source), 8))

            summary = inspect_three_dm(source).to_dict()

        self.assertEqual(len(summary["object_user_strings"]), 1)
        row = summary["object_user_strings"][0]
        self.assertEqual(row["name"], "obj-abutment-north")
        self.assertEqual(
            {item["key"]: item["value"] for item in row["attributes"]},
            written,
        )

    def test_brep_bounds_use_retained_face_mesh_vertices(self) -> None:
        vertices = tuple(
            SimpleNamespace(X=x, Y=y, Z=z)
            for x, y, z in (
                (2, 2, 2),
                (8, 2, 2),
                (8, 8, 8),
                (2, 8, 8),
            )
        )
        mesh = SimpleNamespace(IsValid=True, Vertices=vertices, Faces=(1,))
        face = SimpleNamespace(GetMesh=lambda mesh_type: mesh)
        geometry = SimpleNamespace(Faces=(face, face))
        module = SimpleNamespace(MeshType=SimpleNamespace(Render=1))
        points, witness = _visible_geometry_points(
            geometry,
            "Brep",
            module,
            object_id="trimmed-brep",
        )
        self.assertEqual(
            _points_bbox(points),
            {"min": [2.0, 2.0, 2.0], "max": [8.0, 8.0, 8.0]},
        )
        self.assertEqual(
            witness["source"],
            "brep_face_render_mesh_vertices",
        )
        self.assertEqual(witness["mesh_face_count"], 2)
        self.assertEqual(witness["mesh_vertex_count"], 8)

    def test_brep_without_retained_face_mesh_fails_closed(self) -> None:
        geometry = SimpleNamespace(
            Faces=(SimpleNamespace(GetMesh=lambda mesh_type: None),)
        )
        module = SimpleNamespace(MeshType=SimpleNamespace(Render=1))
        with self.assertRaises(ThreeDmInspectionError) as raised:
            _visible_geometry_points(
                geometry,
                "Brep",
                module,
                object_id="missing-witness",
            )

        self.assertIs(
            raised.exception.code,
            ThreeDmInspectionErrorCode.VISIBLE_BOUNDS_UNAVAILABLE,
        )
        self.assertIn("no retained render mesh", raised.exception.message)

    @unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
    def test_hidden_explicit_mesh_witness_is_used_but_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "explicit-witness.3dm"
            model = rhino3dm.File3dm()
            brep = rhino3dm.Brep.CreateFromBoundingBox(
                rhino3dm.BoundingBox(
                    rhino3dm.Point3d(0, 0, 0),
                    rhino3dm.Point3d(10, 10, 10),
                )
            )
            attributes = rhino3dm.ObjectAttributes()
            attributes.Name = "explicit-brep"
            source_id = model.Objects.AddBrep(brep, attributes)
            face_count = len(tuple(brep.Faces))
            for index in range(face_count):
                mesh = rhino3dm.Mesh()
                for point in (
                    (2, 2, 2),
                    (8, 2, 2),
                    (8, 8, 8),
                    (2, 8, 8),
                ):
                    mesh.Vertices.Add(*point)
                mesh.Faces.AddFace(0, 1, 2, 3)
                witness = rhino3dm.ObjectAttributes()
                witness.Name = (
                    f"__archflow_visible_bounds__:{str(source_id).lower()}:"
                    f"{index:04d}"
                )
                witness.Visible = False
                witness.SetUserString(
                    "archflow:visible_bounds_witness_for",
                    str(source_id).lower(),
                )
                witness.SetUserString(
                    "archflow:visible_bounds_witness_index",
                    str(index),
                )
                witness.SetUserString(
                    "archflow:visible_bounds_witness_count",
                    str(face_count),
                )
                model.Objects.AddMesh(mesh, witness)
            self.assertTrue(model.Write(str(source), 8))

            summary = inspect_three_dm(source).to_dict()

        self.assertEqual(summary["object_count"], 1)
        self.assertEqual(summary["object_counts_by_type"], {"Brep": 1})
        self.assertEqual(
            summary["aggregate_bbox"],
            {"min": [2.0, 2.0, 2.0], "max": [8.0, 8.0, 8.0]},
        )
        self.assertEqual(
            summary["named_object_bboxes"][0]["bbox_source"],
            "explicit_trimmed_render_mesh_witnesses",
        )

    def test_instance_definition_brep_mesh_vertices_are_transformed(self) -> None:
        vertices = tuple(
            SimpleNamespace(X=x, Y=y, Z=z)
            for x, y, z in ((0, 0, 0), (2, 1, 1), (0, 1, 1))
        )
        mesh = SimpleNamespace(IsValid=True, Vertices=vertices, Faces=(1,))
        member_geometry = SimpleNamespace(
            ObjectType="Brep",
            Faces=(SimpleNamespace(GetMesh=lambda mesh_type: mesh),),
        )
        member = SimpleNamespace(
            Geometry=member_geometry,
            Attributes=SimpleNamespace(Id="member-id"),
        )
        transform = SimpleNamespace(
            M00=1,
            M01=0,
            M02=0,
            M03=10,
            M10=0,
            M11=1,
            M12=0,
            M13=20,
            M20=0,
            M21=0,
            M22=1,
            M23=30,
            M30=0,
            M31=0,
            M32=0,
            M33=1,
        )
        reference = SimpleNamespace(
            Geometry=SimpleNamespace(
                ObjectType="InstanceReference",
                ParentIdefId="definition-id",
                Xform=transform,
            )
        )
        module = SimpleNamespace(MeshType=SimpleNamespace(Render=1))
        aggregate, count = _aggregate_bbox(
            [
                {
                    "is_instance_definition_object": False,
                    "source": reference,
                }
            ],
            {"member-id": member},
            {"definition-id": ("member-id",)},
            module,
        )
        self.assertEqual(count, 1)
        self.assertEqual(
            aggregate,
            {"min": [10.0, 20.0, 30.0], "max": [12.0, 21.0, 31.0]},
        )

    def test_layer_rgba_is_strictly_validated(self) -> None:
        self.assertEqual(
            _rgba((0, 127, 255, 64), "layer color"),
            [0, 127, 255, 64],
        )
        for invalid in (
            (0, 0, 0),
            [0, 0, 0, 255],
            (True, 0, 0, 255),
            (0.0, 0, 0, 255),
            (-1, 0, 0, 255),
            (0, 0, 0, 256),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    _rgba(invalid, "layer color")

    def _write_model(self, source: Path) -> None:
        model = rhino3dm.File3dm()
        model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
        model.Strings["status"] = "inspection"
        model.Strings["project"] = "archflow"

        main = rhino3dm.Layer()
        main.Name = "Main"
        main.Color = (10, 20, 30, 255)
        main_index = model.Layers.Add(main)
        secondary = rhino3dm.Layer()
        secondary.Name = "Secondary"
        secondary.Color = (40, 50, 60, 128)
        secondary_index = model.Layers.Add(secondary)

        point_attributes = rhino3dm.ObjectAttributes()
        point_attributes.LayerIndex = main_index
        point_attributes.Name = "axis-witness"
        point_attributes.SetUserString("discipline", "architecture")
        point = rhino3dm.Point(rhino3dm.Point3d(-1, 2, 3))
        point.SetUserString("role", "survey-point")
        model.Objects.Add(point, point_attributes)

        line_attributes = rhino3dm.ObjectAttributes()
        line_attributes.LayerIndex = secondary_index
        model.Objects.AddLine(
            rhino3dm.Point3d(0, 0, 0),
            rhino3dm.Point3d(2, 4, 6),
            line_attributes,
        )

        definition_attributes = rhino3dm.ObjectAttributes()
        definition_attributes.LayerIndex = main_index
        definition_index = model.InstanceDefinitions.Add(
            "Marker",
            "one point marker",
            "",
            "",
            rhino3dm.Point3d(0, 0, 0),
            (rhino3dm.Point(rhino3dm.Point3d(1, 1, 1)),),
            (definition_attributes,),
        )
        definition = model.InstanceDefinitions[definition_index]
        reference = rhino3dm.InstanceReference(
            definition.Id,
            rhino3dm.Transform.Translation(10, 0, 0),
        )
        reference_attributes = rhino3dm.ObjectAttributes()
        reference_attributes.LayerIndex = secondary_index
        model.Objects.AddInstanceObject(reference, reference_attributes)

        self.assertTrue(model.Write(str(source), 8))


if __name__ == "__main__":
    unittest.main()
