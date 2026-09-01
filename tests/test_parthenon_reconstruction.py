from __future__ import annotations

import json
import copy
import tempfile
import unittest
from pathlib import Path

import rhino3dm

from archflow.capabilities.spatial_validation import (
    SpatialCheckKind,
    SpatialValidationStatus,
)

from tools import run_parthenon_reconstruction as P


class ParthenonReconstructionTests(unittest.TestCase):
    def test_peristyle_closes_as_eight_by_seventeen_without_duplicates(self):
        centers = P.peristyle_centers()

        self.assertEqual(46, len(centers))
        self.assertEqual(46, len({(item["x"], item["z"]) for item in centers}))
        self.assertLessEqual(P.peristyle_axial_symmetry_deviation(centers), 1e-6)
        self.assertEqual(8, sum(item["side"] == "west" for item in centers))
        self.assertEqual(8, sum(item["side"] == "east" for item in centers))
        self.assertEqual(15, sum(item["side"] == "south" for item in centers))
        self.assertEqual(15, sum(item["side"] == "north" for item in centers))

    def test_each_stage_only_consumes_decisions_available_at_that_stage(self):
        sources = {
            spec.decision_ref: (f"project:evidence/{spec.decision_ref.split(':', 1)[1]}",)
            for spec in P.DECISIONS
        }
        stage_by_decision = {spec.decision_ref: spec.stage for spec in P.DECISIONS}

        for stage in range(4):
            with self.subTest(stage=stage):
                operations = P.build_stage_operations(stage, sources)
                used = {
                    decision_ref
                    for operation in operations
                    for decision_ref in operation["decision_refs"]
                }
                self.assertTrue(all(stage_by_decision[item] <= stage for item in used))

    def test_architectural_ir_compiles_to_rhino_world_z_up(self):
        sources = {
            spec.decision_ref: (f"project:evidence/{spec.decision_ref.split(':', 1)[1]}",)
            for spec in P.DECISIONS
        }
        operations = P.build_stage_operations(3, sources)
        by_id = {operation["operation_id"]: operation for operation in operations}

        step = by_id["crepidoma-step-0"]["parameters"]
        self.assertEqual(0.0, step["origin"][2])
        self.assertEqual(P.STEP_RISE, step["size"][2])
        self.assertGreater(step["size"][1], step["size"][0])
        column = by_id["peristyle-west-00"]["parameters"]
        self.assertEqual(P.BASE_Y, column["center"][2])
        self.assertGreater(abs(column["center"][1]), abs(column["center"][0]))
        roof = by_id["main-gabled-roof"]["parameters"]
        self.assertIn("eave_z", roof)
        self.assertIn("ridge_z", roof)
        self.assertNotIn("eave_y", roof)

    def test_stage_two_has_two_axial_doors_a_solid_partition_and_clear_columns(self):
        sources = {
            spec.decision_ref: (
                f"project:evidence/{spec.decision_ref.split(':', 1)[1]}",
            )
            for spec in P.DECISIONS
        }
        operations = P.build_stage_operations(2, sources)
        by_id = {operation["operation_id"]: operation for operation in operations}

        self.assertIn("cella-door-east", by_id)
        self.assertIn("cella-door-west", by_id)
        self.assertEqual("door-east", by_id["cella-door-east"]["component_id"])
        self.assertEqual("door-west", by_id["cella-door-west"]["component_id"])
        self.assertNotIn("cella-wall-east", by_id)
        self.assertNotIn("cella-wall-west", by_id)
        self.assertIn("cella-wall-east-lintel", by_id)
        self.assertIn("cella-wall-west-lintel", by_id)
        partition = by_id["cella-partition"]["parameters"]
        self.assertAlmostEqual(P.CELLA_OUTER_WIDTH, partition["size"][0])

        centers = P._u_colonnade_centers()
        self.assertEqual(P.INTERIOR_TIER_COLUMN_COUNT, len(centers))
        self.assertGreater(
            min(longitudinal for _, _, longitudinal in centers)
            - P.INTERIOR_COLUMN_DIAMETER / 2,
            P.PARTITION_EAST_FACE,
        )

    def test_stage_three_west_room_supports_form_symmetric_two_by_two(self):
        sources = {
            spec.decision_ref: (
                f"project:evidence/{spec.decision_ref.split(':', 1)[1]}",
            )
            for spec in P.DECISIONS
        }
        operations = P.build_stage_operations(3, sources)
        west = tuple(
            operation
            for operation in operations
            if str(operation["operation_id"]).startswith("west-room-ionic-")
        )

        self.assertEqual(4, len(west))
        centers = {tuple(item["parameters"]["center"][:2]) for item in west}
        self.assertEqual(4, len(centers))
        self.assertEqual(
            {-P.WEST_ROOM_COLUMN_HALF_SPAN_X, P.WEST_ROOM_COLUMN_HALF_SPAN_X},
            {item[0] for item in centers},
        )
        self.assertEqual(
            {
                P.WEST_ROOM_CENTER - P.WEST_ROOM_COLUMN_HALF_SPAN_Y,
                P.WEST_ROOM_CENTER + P.WEST_ROOM_COLUMN_HALF_SPAN_Y,
            },
            {item[1] for item in centers},
        )

    def test_actual_3dm_missing_east_door_fails_closed(self):
        sources = {
            spec.decision_ref: (
                f"project:evidence/{spec.decision_ref.split(':', 1)[1]}",
            )
            for spec in P.DECISIONS
        }
        operations = tuple(
            item
            for item in P.build_stage_operations(3, sources)
            if item["operation_id"] != "cella-door-east"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-east-door.3dm"
            P._create_three_dm(
                path,
                operations=operations,
                program_digest="0" * 64,
                stage=3,
            )
            receipt = P.validate_stage_spatial_model(path, 3)

        self.assertIs(receipt.status, SpatialValidationStatus.FAILED)
        coverage_failures = tuple(
            item
            for item in receipt.failed_checks
            if item.kind is SpatialCheckKind.REQUIRED_COMPONENT_COVERAGE
        )
        self.assertTrue(
            any(
                item.subject_refs == ("component:door-east",)
                for item in coverage_failures
            )
        )

    def test_actual_3dm_column_through_partition_fails_closed(self):
        sources = {
            spec.decision_ref: (
                f"project:evidence/{spec.decision_ref.split(':', 1)[1]}",
            )
            for spec in P.DECISIONS
        }
        operations = list(copy.deepcopy(P.build_stage_operations(3, sources)))
        column = next(
            item
            for item in operations
            if item["operation_id"] == "naos-south-00-lower"
        )
        column["parameters"]["center"][1] = (
            P.PARTITION_WEST_FACE + P.PARTITION_EAST_FACE
        ) / 2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "column-through-partition.3dm"
            P._create_three_dm(
                path,
                operations=tuple(operations),
                program_digest="1" * 64,
                stage=3,
            )
            receipt = P.validate_stage_spatial_model(path, 3)

        self.assertIs(receipt.status, SpatialValidationStatus.FAILED)
        self.assertTrue(
            any(
                item.kind is SpatialCheckKind.FORBIDDEN_INTERSECTION_VOLUME
                for item in receipt.failed_checks
            )
        )

    def test_offline_four_stage_run_is_typed_metered_and_hold_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / P.PROJECT_ID
            result = P.run_project(
                root,
                captured_at="2026-08-29T17:00:00Z",
                offline=True,
            )

            rows = result["stage_rows"]
            self.assertEqual([0, 1, 2, 3], [row["stage"] for row in rows])
            self.assertEqual(["COMPLETE"] * 4, [row["stage_pack_status"] for row in rows])
            self.assertEqual(
                ["PASSED"] * 4,
                [row["spatial_validation_status"] for row in rows],
            )
            self.assertTrue(all(row["model_units"] == "Meters" for row in rows))
            self.assertTrue(all(row["review_disposition"] == "HOLD" for row in rows))
            self.assertFalse(any(row["accepted_archive_created"] for row in rows))

            final_model = Path(result["final_model_path"])
            self.assertEqual("parthenon-candidate.3dm", final_model.name)
            self.assertTrue(final_model.is_file())
            model = rhino3dm.File3dm.Read(str(final_model))
            self.assertIsNotNone(model)
            assert model is not None
            self.assertGreater(len(model.Objects), 250)
            self.assertEqual(rhino3dm.UnitSystem.Meters, model.Settings.ModelUnitSystem)
            self.assertEqual("Z", dict(model.Strings)["archflow:up_axis"])
            boxes = [item.Geometry.GetBoundingBox() for item in model.Objects]
            minimum = (
                min(box.Min.X for box in boxes),
                min(box.Min.Y for box in boxes),
                min(box.Min.Z for box in boxes),
            )
            maximum = (
                max(box.Max.X for box in boxes),
                max(box.Max.Y for box in boxes),
                max(box.Max.Z for box in boxes),
            )
            spans = tuple(high - low for low, high in zip(minimum, maximum, strict=True))
            self.assertAlmostEqual(0.0, minimum[2], places=6)
            self.assertGreater(spans[1], spans[0])
            self.assertGreater(spans[0], spans[2])
            exterior = next(
                item
                for item in model.Objects
                if item.Attributes.Name == "peristyle-west-00"
            ).Geometry.GetBoundingBox()
            self.assertGreater(
                exterior.Max.Z - exterior.Min.Z,
                exterior.Max.Y - exterior.Min.Y,
            )

            usage = result["usage"]
            self.assertEqual(0, usage["project_provider_usage"]["external_model_calls"])
            self.assertEqual(0, usage["project_provider_usage"]["total_tokens"])
            self.assertFalse(usage["codex_app_usage"]["telemetry_complete"])
            self.assertFalse(usage["token_estimation_from_bytes"])

            progress_ref = result["progress_ref"]
            progress_path = root / progress_ref.relative_path
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(P.BRANCH_ID, progress["selected_branch_id"])
            self.assertEqual("HOLD", progress["disposition"])
            self.assertFalse(progress["canonical_write_authority"])

    def test_axis_fix_is_a_successor_run_and_never_overwrites_predecessor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / P.PROJECT_ID
            first = P.run_project(
                root,
                captured_at="2026-08-29T17:00:00Z",
                offline=True,
            )
            corrected = P.run_project(
                root,
                captured_at="2026-08-29T18:00:00Z",
                offline=True,
                run_id="reconstruction-002",
                research_run_id="research-002",
                predecessor_run_id="reconstruction-001",
                reuse_predecessor_evidence=True,
            )

            self.assertEqual("successor-run", corrected["usage"]["project_resume_mode"])
            self.assertEqual(0, corrected["usage"]["web_rag"]["http_calls"])
            self.assertEqual(
                len(P.SOURCES),
                corrected["usage"]["web_rag"]["reused_source_count"],
            )
            self.assertEqual(
                "reconstruction-001",
                corrected["progress_payload"]["predecessor_run_id"],
            )
            self.assertEqual(
                first["progress_ref"].sha256,
                corrected["progress_payload"]["predecessor_progress_ref"]["sha256"],
            )
            self.assertTrue(
                (root / "runs" / "reconstruction-001" / "run.json").is_file()
            )
            self.assertTrue(
                (root / "runs" / "reconstruction-002" / "run.json").is_file()
            )
            self.assertNotEqual(
                first["progress_ref"].sha256,
                corrected["progress_ref"].sha256,
            )


if __name__ == "__main__":
    unittest.main()
