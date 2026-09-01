"""Material-only existing-3DM transition and readback contracts."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.adapters.three_dm_materialization import (
    ThreeDmMaterialSpec,
    ThreeDmMaterializationError,
    ThreeDmMaterializationStatus,
    execute_three_dm_materialization,
    prepare_three_dm_materialization,
    verify_three_dm_materialization_readback,
)

try:
    import rhino3dm
except ImportError:  # pragma: no cover - optional dependency
    rhino3dm = None


@unittest.skipIf(rhino3dm is None, "rhino3dm is not installed")
class ThreeDmMaterializationTests(unittest.TestCase):
    def test_existing_model_gets_native_pbr_materials_without_geometry_delta(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "source.3dm"
            object_ids, definition_member_id = self._write_source(source)
            source_bytes = source.read_bytes()
            source_sha256 = hashlib.sha256(source_bytes).hexdigest()

            plan = prepare_three_dm_materialization(
                source_model_path=source,
                source_sha256=source_sha256,
                speculative_workspace=workspace,
                artifact_name="materialized.3dm",
                materials=(
                    ThreeDmMaterialSpec(
                        "stone",
                        (170, 160, 145),
                        metallic=0.0,
                        roughness=0.72,
                    ),
                    ThreeDmMaterialSpec(
                        "stucco",
                        (220, 214, 198),
                        metallic=0.0,
                        roughness=0.86,
                    ),
                ),
                material_by_component={
                    "column": "stone",
                    "wall": "stucco",
                },
            )

            self.assertEqual(
                {
                    (object_ids["column"], "stone"),
                    (object_ids["wall"], "stucco"),
                },
                set(plan.object_material_assignments),
            )
            self.assertEqual(
                (definition_member_id,),
                plan.parent_material_object_ids,
            )
            self.assertNotIn(str(workspace), str(plan.to_dict()))

            receipt = execute_three_dm_materialization(plan)

            self.assertIs(
                receipt.status,
                ThreeDmMaterializationStatus.SUCCEEDED,
            )
            self.assertTrue(receipt.readback_verified)
            self.assertEqual((), receipt.failures)
            self.assertEqual(
                plan.source_geometry_sha256,
                receipt.output_geometry_sha256,
            )
            self.assertEqual(
                plan.source_identity_sha256,
                receipt.output_identity_sha256,
            )
            self.assertEqual(
                plan.source_bounds_sha256,
                receipt.output_bounds_sha256,
            )
            self.assertEqual(source_bytes, source.read_bytes())
            self.assertTrue(plan.output_path.is_file())

            output = inspect_three_dm(plan.output_path)
            materials = {row["name"]: row for row in output.materials}
            self.assertEqual(
                {
                    "archflow-material:stone",
                    "archflow-material:stucco",
                },
                set(materials),
            )
            self.assertTrue(
                materials["archflow-material:stone"]["physically_based"]
            )
            self.assertAlmostEqual(
                0.72,
                materials["archflow-material:stone"][
                    "physically_based_roughness"
                ],
                places=6,
            )
            bindings = {
                row["object_id"]: row
                for row in output.object_material_bindings
            }
            self.assertEqual(
                "archflow-material:stucco",
                bindings[object_ids["wall"]]["material_name"],
            )
            self.assertEqual(
                "archflow-material:stone",
                bindings[object_ids["column"]]["material_name"],
            )
            self.assertEqual(
                "MaterialFromParent",
                bindings[definition_member_id]["material_source"],
            )

    def test_exact_object_id_selector_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "source.3dm"
            object_ids, _ = self._write_source(source)
            plan = prepare_three_dm_materialization(
                source_model_path=source,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                speculative_workspace=workspace,
                artifact_name="materialized.3dm",
                materials=(ThreeDmMaterialSpec("stucco", (220, 214, 198)),),
                material_by_object_id={object_ids["wall"]: "stucco"},
            )

            receipt = execute_three_dm_materialization(plan)

        self.assertIs(receipt.status, ThreeDmMaterializationStatus.SUCCEEDED)
        self.assertEqual(
            ((object_ids["wall"], "stucco"),),
            plan.object_material_assignments,
        )

    def test_independent_readback_rejects_geometry_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "source.3dm"
            object_ids, _ = self._write_source(source)
            plan = prepare_three_dm_materialization(
                source_model_path=source,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                speculative_workspace=workspace,
                artifact_name="materialized.3dm",
                materials=(ThreeDmMaterialSpec("stucco", (220, 214, 198)),),
                material_by_object_id={object_ids["wall"]: "stucco"},
            )
            successful = execute_three_dm_materialization(plan)
            self.assertTrue(successful.readback_verified)
            source_inspection = inspect_three_dm(source)
            output_inspection = inspect_three_dm(plan.output_path)
            first = dict(output_inspection.object_geometry_sha256[0])
            first["geometry_sha256"] = "f" * 64
            tampered = replace(
                output_inspection,
                object_geometry_sha256=(
                    first,
                    *output_inspection.object_geometry_sha256[1:],
                ),
            )

            receipt = verify_three_dm_materialization_readback(
                plan,
                source_inspection,
                tampered,
            )

        self.assertIs(receipt.status, ThreeDmMaterializationStatus.FAILED)
        self.assertIn(
            "three_dm_materialization.geometry_changed",
            {failure["code"] for failure in receipt.failures},
        )

    def test_source_sha_and_selector_ambiguity_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "source.3dm"
            self._write_source(source)
            with self.assertRaisesRegex(
                ThreeDmMaterializationError,
                "SHA-256",
            ):
                prepare_three_dm_materialization(
                    source_model_path=source,
                    source_sha256="0" * 64,
                    speculative_workspace=workspace,
                    artifact_name="materialized.3dm",
                    materials=(
                        ThreeDmMaterialSpec("stucco", (220, 214, 198)),
                    ),
                    material_by_component={"wall": "stucco"},
                )
            with self.assertRaisesRegex(
                ThreeDmMaterializationError,
                "exactly one",
            ):
                prepare_three_dm_materialization(
                    source_model_path=source,
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    speculative_workspace=workspace,
                    artifact_name="materialized.3dm",
                    materials=(
                        ThreeDmMaterialSpec("stucco", (220, 214, 198)),
                    ),
                    material_by_component={"wall": "stucco"},
                    material_by_object_id={},
                )

    def test_material_spec_rejects_non_physical_values(self) -> None:
        with self.assertRaisesRegex(
            ThreeDmMaterializationError,
            "between 0 and 1",
        ):
            ThreeDmMaterialSpec("metal", (100, 100, 100), roughness=1.2)

    def _write_source(self, source: Path) -> tuple[dict[str, str], str]:
        model = rhino3dm.File3dm()
        model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
        model.Strings["archflow:stage"] = "stage-4"

        layer = rhino3dm.Layer()
        layer.Name = "Architecture"
        layer_index = model.Layers.Add(layer)

        wall_attributes = rhino3dm.ObjectAttributes()
        wall_attributes.LayerIndex = layer_index
        wall_attributes.Name = "wall-object"
        wall_attributes.SetUserString("archflow:component", "wall")
        wall_attributes.SetUserString("archflow:producer_op", "wall-op")
        wall_id = model.Objects.AddPoint(
            rhino3dm.Point3d(1, 2, 3),
            wall_attributes,
        )

        member_attributes = rhino3dm.ObjectAttributes()
        member_attributes.LayerIndex = layer_index
        definition_index = model.InstanceDefinitions.Add(
            "archflow-family-column-op",
            "column family",
            "",
            "",
            rhino3dm.Point3d(0, 0, 0),
            (rhino3dm.Point(rhino3dm.Point3d(0, 0, 0)),),
            (member_attributes,),
        )
        definition = model.InstanceDefinitions[definition_index]
        definition_member_id = str(definition.GetObjectIds()[0]).lower()
        reference = rhino3dm.InstanceReference(
            definition.Id,
            rhino3dm.Transform.Translation(5, 0, 0),
        )
        column_attributes = rhino3dm.ObjectAttributes()
        column_attributes.LayerIndex = layer_index
        column_attributes.Name = "column-object"
        column_attributes.SetUserString("archflow:component", "column")
        column_attributes.SetUserString("archflow:producer_op", "column-op")
        column_id = model.Objects.AddInstanceObject(reference, column_attributes)

        self.assertTrue(model.Write(str(source), 8))
        return (
            {
                "wall": str(wall_id).lower(),
                "column": str(column_id).lower(),
            },
            definition_member_id,
        )


if __name__ == "__main__":
    unittest.main()
