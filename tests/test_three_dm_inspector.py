from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.adapters.three_dm_inspector import (
    ThreeDmInspection,
    ThreeDmInspectionError,
    ThreeDmInspectionErrorCode,
    _rgba,
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
        self.assertEqual(summary["schema"], "ThreeDmInspectionSummary@1")
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
