from __future__ import annotations

import copy
import unittest

from tools import run_parthenon_reconstruction as stage3
from tools import run_parthenon_stage4_reconstruction as stage4
from tools.parthenon_stage4_inner_colonnade import (
    DELTA_SCHEMA,
    VALIDATION_SCHEMA,
    compile_inner_colonnade_delta,
    validate_inner_colonnade_operations,
)


def _current_stage4_operations() -> tuple[dict[str, object], ...]:
    sources = {
        decision.decision_ref: (f"evidence:{decision.decision_ref.split(':')[-1]}",)
        for decision in stage3.DECISIONS
    }
    predecessor = stage3.build_stage_operations(3, sources)
    operations, _ = stage4.compile_stage4_operations(
        predecessor,
        column_source_refs=("evidence:penrose-interior-columns",),
        entablature_source_refs=("evidence:perseus-entablature",),
        opening_source_refs=("evidence:bsa-east-windows",),
        visual_manifest_ref="evidence:research-005-selected-regions",
    )
    return operations


class ParthenonStage4InnerColonnadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.current = _current_stage4_operations()
        cls.full, cls.lineage = compile_inner_colonnade_delta(
            cls.current,
            textual_evidence_refs=(
                "evidence:ysma-doric-morphology",
                "evidence:stevens-interior-supports",
                "evidence:bsa-east-windows-2025",
            ),
            selected_visual_refs=(
                "evidence:manifest#candidate=roi_ysma_12_11_center_fluting",
                "evidence:manifest#candidate=roi_ysma_11_6_far_wall",
            ),
        )

    def test_current_successor_exposes_direct_stack_and_window_projection_failures(self) -> None:
        receipt = validate_inner_colonnade_operations(self.current)

        self.assertFalse(receipt["passed"])
        diagnostic = receipt["checks"]["inherited_schematic_diagnostic"]
        self.assertEqual(46, diagnostic["legacy_naos_shaft_count"])
        self.assertEqual(23, diagnostic["legacy_direct_shaft_contact_count"])
        self.assertEqual(2, diagnostic["legacy_window_projection_obstruction_count"])

    def test_compiler_replaces_complete_scope_and_is_deterministic(self) -> None:
        reversed_full, reversed_lineage = compile_inner_colonnade_delta(
            tuple(reversed(self.current)),
            textual_evidence_refs=(
                "evidence:bsa-east-windows-2025",
                "evidence:stevens-interior-supports",
                "evidence:ysma-doric-morphology",
            ),
            selected_visual_refs=(
                "evidence:manifest#candidate=roi_ysma_11_6_far_wall",
                "evidence:manifest#candidate=roi_ysma_12_11_center_fluting",
            ),
        )

        self.assertEqual(DELTA_SCHEMA, self.lineage["schema"])
        self.assertEqual(509, self.lineage["predecessor_operation_count"])
        self.assertEqual(650, self.lineage["current_operation_count"])
        self.assertEqual(195, self.lineage["delta_operation_count"])
        self.assertEqual(141, self.lineage["added_operation_count"])
        self.assertEqual(46, self.lineage["shaft_replacement_count"])
        self.assertEqual(138, self.lineage["capital_part_count"])
        self.assertEqual(3, self.lineage["intertier_architrave_count"])
        self.assertEqual(8, self.lineage["window_piece_replacement_count"])
        self.assertEqual(48, self.lineage["folded_stage3_root_operation_count"])
        self.assertEqual(self.full, reversed_full)
        self.assertEqual(self.lineage, reversed_lineage)

    def test_transitive_ancestry_folds_to_exact_stage3_roots(self) -> None:
        ancestors = self.lineage["transitive_ancestor_map"]

        self.assertEqual(
            ["cella-wall-east-left"],
            ancestors["cella-wall-east-left-window-lintel"][
                "exact_stage3_root_operation_ids"
            ],
        )
        self.assertEqual(
            ["naos-north-00-lower"],
            ancestors["naos-north-00-lower-neck"][
                "exact_stage3_root_operation_ids"
            ],
        )
        north_roots = ancestors["naos-u-architrave-north"][
            "exact_stage3_root_operation_ids"
        ]
        self.assertEqual(20, len(north_roots))
        self.assertIn("naos-north-09-upper", north_roots)
        self.assertEqual(
            set(self.lineage["delta_operation_ids"]),
            set(ancestors),
        )

    def test_corrected_geometry_has_real_twenty_flutes_and_typed_pair_chain(self) -> None:
        receipt = validate_inner_colonnade_operations(self.full)

        self.assertEqual(VALIDATION_SCHEMA, receipt["schema"])
        self.assertTrue(receipt["passed"], receipt["failures"])
        flutes = receipt["checks"]["geometric_twenty_flute_shafts"]
        stacks = receipt["checks"]["u_colonnade_topology"]
        pairs = receipt["checks"]["scoped_inner_pair_relations"]
        self.assertEqual(46, flutes["valid_geometric_flute_count"])
        self.assertFalse(flutes["metadata_only_allowed"])
        self.assertEqual(23, stacks["valid_stack_count"])
        self.assertEqual(0, stacks["direct_shaft_contact_count"])
        self.assertEqual({"north": 10, "south": 10, "west": 3}, stacks["row_counts"])
        self.assertEqual(187, pairs["bounded_operation_denominator"])
        self.assertEqual(0, pairs["positive_volume_overlap_count"])
        self.assertEqual(0, pairs["dangling_typed_contact_count"])

    def test_window_centres_are_derived_mirrored_true_wall_voids(self) -> None:
        receipt = validate_inner_colonnade_operations(self.full)
        window = receipt["checks"]["east_window_relation"]
        window_operations = [
            item
            for item in self.full
            if isinstance(item.get("parameters"), dict)
            and "window_clear" in item["parameters"]
        ]
        centers = {
            str(item["parameters"]["window_side"]): float(
                item["parameters"]["window_clear"]["center_x"]
            )
            for item in window_operations
        }

        self.assertTrue(receipt["passed"])
        self.assertAlmostEqual(7.6872765, window["derived_center_magnitude_m"])
        self.assertAlmostEqual(abs(centers["left"]), centers["right"])
        self.assertEqual(0, window["projection_obstruction_count"])
        self.assertEqual(0, window["host_void_derivation_failure_count"])
        self.assertTrue(
            all(
                item["parameters"]["void_contract"]["realization"]
                == "decomposed-host-wall-void-not-surface-patch"
                for item in window_operations
            )
        )

    def test_metadata_only_flutes_and_missing_architrave_fail_closed(self) -> None:
        metadata_only = copy.deepcopy(self.full)
        shaft = next(item for item in metadata_only if item.get("kind") == "doric_shaft")
        shaft["parameters"]["geometry_contract"]["actual_flute_grooves"] = False
        flute_receipt = validate_inner_colonnade_operations(metadata_only)

        missing_architrave = tuple(
            item
            for item in copy.deepcopy(self.full)
            if item["operation_id"] != "naos-u-architrave-north"
        )
        stack_receipt = validate_inner_colonnade_operations(missing_architrave)

        self.assertFalse(flute_receipt["passed"])
        self.assertEqual(
            45,
            flute_receipt["checks"]["geometric_twenty_flute_shafts"][
                "valid_geometric_flute_count"
            ],
        )
        self.assertFalse(stack_receipt["passed"])
        self.assertGreater(
            stack_receipt["checks"]["scoped_inner_pair_relations"][
                "dangling_typed_contact_count"
            ],
            0,
        )

    def test_window_ionic_lineage_and_branch_mutations_fail_closed(self) -> None:
        shifted_window = copy.deepcopy(self.full)
        for item in shifted_window:
            parameters = item.get("parameters")
            if isinstance(parameters, dict) and parameters.get("window_side") == "right":
                parameters["window_clear"]["center_x"] = 6.25
        shifted_receipt = validate_inner_colonnade_operations(shifted_window)

        moved_ionic = copy.deepcopy(self.full)
        ionic = next(
            item for item in moved_ionic if item["operation_id"] == "west-room-ionic-0-0"
        )
        ionic["parameters"]["center"][0] = -2.9
        ionic_receipt = validate_inner_colonnade_operations(moved_ionic)

        leaking = copy.deepcopy(self.full)
        scoped = next(
            item for item in leaking if item.get("refinement_scope") is not None
        )
        scoped["parameters"]["surface_content"] = "Nero-readable-inscription"
        branch_receipt = validate_inner_colonnade_operations(leaking)

        no_lineage = copy.deepcopy(self.full)
        scoped = next(item for item in no_lineage if item.get("refinement_scope") is not None)
        scoped.pop("replaces_operation_id", None)
        scoped.pop("refines_operation_id", None)
        lineage_receipt = validate_inner_colonnade_operations(no_lineage)

        self.assertFalse(shifted_receipt["passed"])
        self.assertGreater(
            shifted_receipt["checks"]["east_window_relation"][
                "host_void_derivation_failure_count"
            ],
            0,
        )
        self.assertFalse(ionic_receipt["passed"])
        self.assertGreater(
            ionic_receipt["checks"]["west_room_ionic_boundary"]["failure_count"],
            0,
        )
        self.assertFalse(branch_receipt["passed"])
        self.assertEqual(1, branch_receipt["checks"]["branch_exclusion"]["leakage_count"])
        self.assertFalse(lineage_receipt["passed"])
        self.assertEqual(
            1,
            lineage_receipt["checks"]["scoped_lineage_evidence"][
                "lineage_failure_count"
            ],
        )


if __name__ == "__main__":
    unittest.main()
