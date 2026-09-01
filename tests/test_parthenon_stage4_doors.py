from __future__ import annotations

import copy
import unittest

from tools import parthenon_stage4_doors as doors
from tools import parthenon_stage4_inner_colonnade as inner_colonnade
from tools import run_parthenon_reconstruction as stage3
from tools import run_parthenon_stage4_reconstruction as stage4


TEXT_REFS = ("evidence:dooge-door", "evidence:ascsa-door")
VISUAL_REFS = (
    "evidence:selected-visual-regions#candidate=roi_ysma_11_6_central_doorway",
)
HUMAN_AUTHORIZATION_REF = (
    "decision:human-authorized-stage4-principal-door-closed-double-leaf"
)


def _stage4_operations() -> tuple[dict[str, object], ...]:
    sources = {
        decision.decision_ref: (
            f"evidence:{decision.decision_ref.split(':')[-1]}",
        )
        for decision in stage3.DECISIONS
    }
    predecessor = stage3.build_stage_operations(3, sources)
    operations, _ = stage4.compile_stage4_operations(
        predecessor,
        column_source_refs=("evidence:penrose-column",),
        entablature_source_refs=("evidence:perseus-entablature",),
        opening_source_refs=("evidence:bsa-windows",),
        visual_manifest_ref="evidence:selected-visual-regions",
    )
    return operations


def _corrected() -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    dict[str, object],
]:
    current = _stage4_operations()
    delta, receipt = doors.compile_door_assembly_delta(
        current,
        TEXT_REFS,
        VISUAL_REFS,
        resolution=doors.DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF,
        human_authorization_ref=HUMAN_AUTHORIZATION_REF,
    )
    return (
        doors.apply_door_assembly_delta(current, delta, receipt),
        delta,
        receipt,
    )


def _validate_authorized(
    full: tuple[dict[str, object], ...] | list[dict[str, object]],
    receipt: dict[str, object],
) -> dict[str, object]:
    return doors.validate_door_assembly_operations(
        full,
        resolution=doors.DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF,
        resolution_receipt=receipt,
    )


class ParthenonStage4DoorTests(unittest.TestCase):
    def test_current_door_dependency_defect_is_quantified(self) -> None:
        current = _stage4_operations()
        diagnosis = doors.measure_door_dependency_state(current)

        self.assertFalse(diagnosis["passed"])
        self.assertEqual(2, len(diagnosis["metrics"]))
        for metric in diagnosis["metrics"]:
            self.assertAlmostEqual(4.92, metric["opening_width_m"])
            self.assertAlmostEqual(9.84, metric["opening_height_m"])
            self.assertAlmostEqual(4.20, metric["leaf_width_m"])
            self.assertAlmostEqual(7.00, metric["leaf_height_m"])
            self.assertAlmostEqual(0.36, metric["left_gap_m"])
            self.assertAlmostEqual(0.36, metric["right_gap_m"])
            self.assertAlmostEqual(2.84, metric["top_gap_m"])
            self.assertFalse(metric["shared_aperture_contract"])
        self.assertFalse(doors.validate_door_assembly_operations(current)["passed"])

    def test_default_resolution_parks_unknown_door_geometry_and_blocks_stage(self) -> None:
        current = _stage4_operations()
        delta, receipt = doors.compile_door_assembly_delta(
            current,
            TEXT_REFS,
            VISUAL_REFS,
        )
        parked = doors.apply_door_assembly_delta(current, delta, receipt)

        self.assertEqual((), delta)
        self.assertEqual("PARK_DOOR_LEAVES", receipt["resolution"])
        self.assertEqual("PARKED_BLOCKING_HUMAN_DECISION", receipt["resolution_status"])
        self.assertIsNone(receipt["human_authorization_ref"])
        self.assertTrue(receipt["blocking"])
        self.assertFalse(receipt["authorization_gate_satisfied"])
        self.assertFalse(receipt["stage_gate_satisfied"])
        self.assertTrue(receipt["parked_reason"])
        self.assertEqual(
            {"cella-door-east", "cella-door-west"},
            set(receipt["superseded_operation_ids"]),
        )
        self.assertFalse(
            any(
                item["component_id"] in {"door-east", "door-west"}
                for item in parked
            )
        )
        self.assertFalse(
            any(
                str(item["operation_id"]).startswith("principal-door-")
                for item in parked
            )
        )
        for item in receipt["transitive_lineage"]:
            self.assertEqual("PARKED_WITH_REASON", item["disposition"])
            self.assertTrue(item["blocking"])
            self.assertIsNone(item["final_operation_id"])
            self.assertTrue(item["parked_reason"])
        result = doors.validate_door_assembly_operations(
            parked,
            resolution_receipt=receipt,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["policy_compliant"], result["failures"])
        self.assertTrue(result["blocking"])
        self.assertFalse(result["stage_gate_satisfied"])
        self.assertAlmostEqual(4.92, result["metrics"]["east"]["opening_width_m"])
        self.assertAlmostEqual(4.92, result["metrics"]["west"]["opening_width_m"])

    def test_closed_double_leaf_requires_typed_human_authorization(self) -> None:
        current = _stage4_operations()
        for authorization in (None, "", "decision:assistant-invented-door"):
            with self.subTest(authorization=authorization):
                with self.assertRaises(doors.ParthenonDoorAssemblyError):
                    doors.compile_door_assembly_delta(
                        current,
                        TEXT_REFS,
                        VISUAL_REFS,
                        resolution=(
                            doors.DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF
                        ),
                        human_authorization_ref=authorization,
                    )

    def test_delta_builds_one_host_local_contract_and_transitive_lineage(self) -> None:
        full, delta, receipt = _corrected()

        self.assertEqual(18, len(delta))
        self.assertEqual(8, len(receipt["superseded_operation_ids"]))
        self.assertEqual(18, len(receipt["transitive_lineage"]))
        self.assertEqual(
            HUMAN_AUTHORIZATION_REF,
            receipt["human_authorization_ref"],
        )
        self.assertTrue(receipt["authorization_gate_satisfied"])
        self.assertFalse(receipt["stage_gate_satisfied"])
        self.assertEqual(
            ["cella-door-east", "cella-door-west"],
            receipt["exact_stage3_door_root_operation_ids"],
        )
        self.assertEqual(2, len(receipt["door_assembly_ancestry"]))
        for ancestry in receipt["door_assembly_ancestry"]:
            self.assertEqual(6, len(ancestry["final_assembly_operation_ids"]))
            self.assertEqual(6, len(ancestry["root_to_final_paths"]))
            self.assertTrue(
                all(
                    path[0] == ancestry["exact_stage3_door_root_operation_id"]
                    for path in ancestry["root_to_final_paths"]
                )
            )
        roots = {
            item["exact_stage3_root_operation_id"]
            for item in receipt["transitive_lineage"]
        }
        self.assertTrue(
            {
                "cella-door-east",
                "cella-door-west",
                "cella-wall-east-left",
                "cella-wall-east-right",
                "cella-wall-east-lintel",
                "cella-wall-west-left",
                "cella-wall-west-right",
                "cella-wall-west-lintel",
            }.issubset(roots)
        )
        validation = _validate_authorized(full, receipt)
        self.assertTrue(validation["passed"], validation["failures"])
        self.assertEqual(HUMAN_AUTHORIZATION_REF, validation["human_authorization_ref"])

    def test_authorized_door_preserves_shared_east_window_host_dependencies(self) -> None:
        architectural = _stage4_operations()
        inner, _ = inner_colonnade.compile_inner_colonnade_delta(
            architectural,
            textual_evidence_refs=("evidence:bsa-east-window",),
            selected_visual_refs=("evidence:selected-inner-colonnade",),
        )
        delta, receipt = doors.compile_door_assembly_delta(
            inner,
            TEXT_REFS,
            VISUAL_REFS,
            resolution=doors.DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF,
            human_authorization_ref=HUMAN_AUTHORIZATION_REF,
        )
        corrected = doors.apply_door_assembly_delta(inner, delta, receipt)

        door_validation = _validate_authorized(corrected, receipt)
        inner_validation = inner_colonnade.validate_inner_colonnade_operations(
            corrected
        )
        self.assertTrue(door_validation["passed"], door_validation["failures"])
        self.assertTrue(inner_validation["passed"], inner_validation["failures"])

        by_id = {str(item["operation_id"]): item for item in corrected}
        for operation_id in (
            "cella-wall-east-left-window-inner-pier",
            "cella-wall-east-right-window-outer-pier",
        ):
            operation = by_id[operation_id]
            self.assertEqual(
                "decomposes_host_around_void",
                operation["host"]["relation"],
            )
            self.assertEqual(
                "bounds_empty_aperture",
                operation["contact_contract"]["relation"],
            )

    def test_corrected_leaf_coverage_uses_only_typed_construction_gaps(self) -> None:
        full, _, receipt = _corrected()
        validation = _validate_authorized(full, receipt)

        for side in ("east", "west"):
            metric = validation["metrics"][side]
            self.assertAlmostEqual(4.96, metric["rough_opening_width_m"])
            self.assertAlmostEqual(4.91, metric["clear_opening_width_m"])
            self.assertAlmostEqual(4.89, metric["leaf_envelope_width_m"])
            self.assertAlmostEqual(9.76, metric["leaf_height_m"])
            self.assertAlmostEqual(0.02, metric["meeting_gap_m"])
            self.assertTrue(
                all(
                    gap <= 0.0100001
                    for gap in metric["perimeter_gaps_m"].values()
                )
            )

    def test_shrunken_leaf_fails_clear_opening_coverage(self) -> None:
        full, _, receipt = _corrected()
        mutated = copy.deepcopy(list(full))
        leaf = next(
            item
            for item in mutated
            if item["operation_id"] == "principal-door-east-leaf-left"
        )
        leaf["parameters"]["origin"][0] += 0.20
        leaf["parameters"]["size"][0] -= 0.20

        result = _validate_authorized(mutated, receipt)

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["clear_opening_coverage"])

    def test_off_axis_contract_fails_even_when_all_members_share_it(self) -> None:
        full, _, receipt = _corrected()
        mutated = copy.deepcopy(list(full))
        for item in mutated:
            if item["component_id"] == "door-west":
                item["parameters"]["aperture_contract"]["axis_x_m"] = 0.10

        result = _validate_authorized(mutated, receipt)

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["coaxial_and_mirrored"])

    def test_leaf_wall_positive_volume_collision_fails(self) -> None:
        full, _, receipt = _corrected()
        mutated = copy.deepcopy(list(full))
        leaf = next(
            item
            for item in mutated
            if item["operation_id"] == "principal-door-east-leaf-left"
        )
        leaf["parameters"]["origin"][0] = -2.50
        leaf["parameters"]["size"][0] += 0.055

        result = _validate_authorized(mutated, receipt)

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["wall_noncollision"])

    def test_internal_partition_opening_is_forbidden(self) -> None:
        full, _, receipt = _corrected()
        mutated = copy.deepcopy(list(full))
        partition = next(
            item for item in mutated if item["operation_id"] == "cella-partition"
        )
        partition["operation_id"] = "cella-partition-left-of-internal-door"

        result = _validate_authorized(mutated, receipt)

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["no_internal_door"])

    def test_window_metadata_cannot_be_reclassified_as_a_door(self) -> None:
        full, _, receipt = _corrected()
        mutated = copy.deepcopy(list(full))
        leaf = next(
            item
            for item in mutated
            if item["operation_id"] == "principal-door-east-leaf-right"
        )
        leaf["parameters"]["window_clear"] = {
            "center_x": 6.25,
            "width": 2.5,
        }

        result = _validate_authorized(mutated, receipt)

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["window_role_separation"])

    def test_evidence_lineage_material_and_branch_leakage_fail_closed(self) -> None:
        full, _, receipt = _corrected()
        mutated = copy.deepcopy(list(full))
        leaf = next(
            item
            for item in mutated
            if item["operation_id"] == "principal-door-west-leaf-left"
        )
        leaf.pop("visual_region_refs")
        leaf["material_id"] = "nero-bronze-inscription"

        result = _validate_authorized(mutated, receipt)

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["evidence_and_lineage"])
        self.assertFalse(result["checks"]["material_roles"])
        self.assertFalse(result["checks"]["branch_scope"])

    def test_compiler_rejects_missing_text_or_selected_visual_evidence(self) -> None:
        current = _stage4_operations()
        with self.assertRaises(doors.ParthenonDoorAssemblyError):
            doors.compile_door_assembly_delta(current, (), VISUAL_REFS)
        with self.assertRaises(doors.ParthenonDoorAssemblyError):
            doors.compile_door_assembly_delta(current, TEXT_REFS, ())


if __name__ == "__main__":
    unittest.main()
