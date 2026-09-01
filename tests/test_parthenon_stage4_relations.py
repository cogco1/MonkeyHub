from __future__ import annotations

import copy
import math
import unittest

from tools import run_parthenon_reconstruction as stage3
from tools import run_parthenon_stage4_reconstruction as stage4
from tools.parthenon_stage4_relations import validate_stage4_successor_relations


def _box(
    operation_id: str,
    component_id: str,
    origin: list[float],
    size: list[float],
    **extra_parameters: object,
) -> dict[str, object]:
    parameters: dict[str, object] = {"origin": origin, "size": size}
    parameters.update(extra_parameters)
    return {
        "operation_id": operation_id,
        "component_id": component_id,
        "kind": "box",
        "parameters": parameters,
    }


def _column(
    operation_id: str,
    *,
    component_id: str = "interior-colonnade",
    kind: str = "fluted_column",
    center: list[float],
    height: float,
    diameter: float,
) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "component_id": component_id,
        "kind": kind,
        "parameters": {
            "center": center,
            "height": height,
            "diameter": diameter,
        },
    }


def _typed_stack_contact_rules() -> list[dict[str, str]]:
    return [
        {
            "lower_component_id": "interior-colonnade",
            "lower_kind": "fluted_column",
            "upper_component_id": "interior-colonnade",
            "upper_kind": "column_capital",
            "relation": "supports",
        },
        {
            "lower_component_id": "interior-colonnade",
            "lower_kind": "column_capital",
            "upper_component_id": "interior-colonnade",
            "upper_kind": "fluted_column",
            "relation": "supports",
        },
    ]


def _relation_contract(
    first_operation_id: str,
    second_operation_id: str,
    *,
    relation: str,
    intersection_policy: str,
    max_overlap_m3: float | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "first_operation_id": first_operation_id,
        "second_operation_id": second_operation_id,
        "relation": relation,
        "intersection_policy": intersection_policy,
        "evidence_ref": "decision:test-exact-relation",
    }
    if max_overlap_m3 is not None:
        result["max_overlap_m3"] = max_overlap_m3
    return result


def _corrected_exact_contracts() -> list[dict[str, object]]:
    return [
        _relation_contract(
            "semantic-inner-tier-a",
            "semantic-inner-bearing",
            relation="supports",
            intersection_policy="touch_only",
        ),
        _relation_contract(
            "semantic-inner-bearing",
            "semantic-inner-tier-b",
            relation="supports",
            intersection_policy="touch_only",
        ),
    ]


def _corrected_successor() -> list[dict[str, object]]:
    # The window is centered at x=6 and looks inward from y=10.  The interior
    # stack at x=3.5 is within the target depth but outside the aperture's
    # projected x interval.  Its two shafts are separated by a typed capital.
    return [
        _box(
            "window-host-detail",
            "cella",
            [4.0, 10.0, 6.0],
            [4.0, 1.0, 1.0],
            window_side="right",
            window_clear={
                "center_x": 6.0,
                "width": 2.0,
                "sill_z": 2.0,
                "height": 3.0,
            },
        ),
        _column(
            "semantic-inner-tier-a",
            center=[3.5, 7.0, 0.0],
            height=3.0,
            diameter=1.0,
        ),
        {
            "operation_id": "semantic-inner-bearing",
            "component_id": "interior-colonnade",
            "kind": "column_capital",
            "parameters": {
                "origin": [3.0, 6.5, 3.0],
                "size": [1.0, 1.0, 0.25],
            },
        },
        _column(
            "semantic-inner-tier-b",
            center=[3.5, 7.0, 3.25],
            height=3.0,
            diameter=0.8,
        ),
    ]


def _metadata_chain_successor() -> list[dict[str, object]]:
    operations = [
        _box(
            "window-host-detail",
            "cella",
            [4.0, 10.0, 6.0],
            [4.0, 1.0, 1.0],
            window_side="right",
            window_clear={
                "center_x": 6.0,
                "width": 2.0,
                "sill_z": 2.0,
                "height": 3.0,
            },
        ),
        _column(
            "inner-lower",
            center=[3.5, 7.0, 0.0],
            height=3.0,
            diameter=1.0,
        ),
        {
            "operation_id": "inner-capital",
            "component_id": "interior-colonnade",
            "kind": "column_capital",
            "parameters": {
                "origin": [3.0, 6.5, 3.0],
                "size": [1.0, 1.0, 0.2],
            },
            "host": "inner-lower",
            "contact_contract": {"relation": "supports"},
            "contact_policy": "touch_only",
        },
        {
            "operation_id": "inner-bearing",
            "component_id": "interior-colonnade",
            "kind": "bearing_block",
            "parameters": {
                "origin": [3.05, 6.55, 3.2],
                "size": [0.9, 0.9, 0.2],
            },
            "host": "inner-capital",
            "contact_contract": {"relation": "supports"},
            "contact_policy": "touch_only",
        },
        {
            **_column(
                "inner-upper",
                center=[3.5, 7.0, 3.4],
                height=3.0,
                diameter=0.8,
            ),
            "host": "inner-bearing",
            "contact_contract": {"relation": "supports"},
            "contact_policy": "touch_only",
        },
    ]
    return operations


def _compiled_stage4_operations() -> tuple[dict[str, object], ...]:
    predecessor = stage3.build_stage_operations(3, {})
    operations, _ = stage4.compile_stage4_operations(
        predecessor,
        column_source_refs=("evidence:columns",),
        entablature_source_refs=("evidence:entablature",),
        opening_source_refs=("evidence:windows",),
        visual_manifest_ref="artifact:visual-manifest",
    )
    return operations


class ParthenonStage4RelationTests(unittest.TestCase):
    def test_current_successor_catches_preserved_inner_columns_and_window_projection(self) -> None:
        receipt = validate_stage4_successor_relations(_compiled_stage4_operations())

        classification = receipt["checks"]["column_classification"]
        self.assertFalse(classification["operation_id_prefix_used"])
        self.assertEqual(58, classification["kind_counts"]["doric_shaft"])
        self.assertEqual(46, classification["kind_counts"]["fluted_column"])
        self.assertEqual(4, classification["kind_counts"]["ionic_column"])

        projection = receipt["checks"]["window_projection"]
        self.assertEqual(2, projection["window_denominator"])
        self.assertEqual(50, projection["interior_shaft_denominator"])
        self.assertEqual(2, projection["obstruction_count"])
        obstructions = [
            item
            for item in receipt["failure_details"]["window_projection"]
            if item.get("classification") == "host_local_clear_cone_obstruction"
        ]
        self.assertEqual(
            {"naos-north-09-lower", "naos-south-09-lower"},
            {item["column_operation_id"] for item in obstructions},
        )
        self.assertTrue(
            all(
                math.isclose(
                    item["horizontal_projection_overlap_m"], 0.7645, abs_tol=1.0e-9
                )
                for item in obstructions
            )
        )
        # This is the relation the old volume-only gate cannot see: the shaft
        # and host wall are separated in depth even though the view is blocked.
        self.assertTrue(
            all(item["host_volume_intersection_m3"] == 0.0 for item in obstructions)
        )
        self.assertEqual(
            23,
            receipt["checks"]["interior_column_stacks"][
                "direct_shaft_contact_count"
            ],
        )
        self.assertFalse(receipt["passed"])

    def test_preserved_delta_positive_volume_collision_is_rechecked(self) -> None:
        operations = _corrected_successor()
        operations.extend(
            [
                _box("legacy-preserved-volume", "cella", [-5.0, -5.0, 0.0], [2.0, 2.0, 2.0]),
                {
                    **_box(
                        "successor-delta-volume",
                        "decoration",
                        [-4.5, -4.5, 0.5],
                        [1.0, 1.0, 1.0],
                    ),
                    "refines_operation_id": "some-predecessor-detail",
                },
            ]
        )
        receipt = validate_stage4_successor_relations(
            operations,
            typed_relation_contracts=_corrected_exact_contracts(),
        )

        broad = receipt["checks"]["successor_pair_broad_phase"]
        self.assertEqual(1, broad["confirmed_box_collision_count"])
        self.assertGreater(broad["lineage_pair_denominators"]["delta_preserved"], 0)
        collision = next(
            item
            for item in receipt["failure_details"]["successor_pair_broad_phase"]
            if item["classification"] == "confirmed_axis_aligned_box_collision"
        )
        self.assertEqual("delta_preserved", collision["lineage_pair"])
        self.assertEqual(1.0, collision["aabb_overlap_volume_m3"])
        self.assertFalse(receipt["passed"])

    def test_broad_component_kind_allowlist_cannot_mask_direct_contact(self) -> None:
        operations = [
            item
            for item in copy.deepcopy(_corrected_successor())
            if item["operation_id"] != "semantic-inner-bearing"
        ]
        upper = next(
            item for item in operations if item["operation_id"] == "semantic-inner-tier-b"
        )
        upper["parameters"]["center"][2] = 3.0
        direct_rule = {
            "lower_component_id": "interior-colonnade",
            "lower_kind": "fluted_column",
            "upper_component_id": "interior-colonnade",
            "upper_kind": "fluted_column",
            "relation": "supports",
        }

        receipt = validate_stage4_successor_relations(
            operations,
            typed_contact_allowlist=(direct_rule,),
        )

        self.assertEqual(
            0,
            receipt["checks"]["successor_pair_broad_phase"][
                "allowed_typed_contact_count"
            ],
        )
        self.assertEqual(
            1,
            receipt["checks"]["successor_pair_broad_phase"][
                "legacy_broad_match_ignored_count"
            ],
        )
        self.assertFalse(
            receipt["checks"]["typed_contact_allowlist"]["authorizes_geometry"]
        )
        self.assertEqual(
            1,
            receipt["checks"]["interior_column_stacks"][
                "direct_shaft_contact_count"
            ],
        )
        self.assertFalse(receipt["passed"])

    def test_corrected_window_position_and_typed_stack_pass_deterministically(self) -> None:
        operations = _corrected_successor()
        rules = _typed_stack_contact_rules()

        receipt = validate_stage4_successor_relations(
            operations,
            typed_contact_allowlist=rules,
            typed_relation_contracts=_corrected_exact_contracts(),
        )
        reversed_receipt = validate_stage4_successor_relations(
            tuple(reversed(operations)),
            typed_contact_allowlist=tuple(reversed(rules)),
            typed_relation_contracts=tuple(reversed(_corrected_exact_contracts())),
        )

        self.assertTrue(receipt["passed"], receipt["failures"])
        self.assertEqual(0, receipt["checks"]["window_projection"]["obstruction_count"])
        self.assertEqual(
            1,
            receipt["checks"]["interior_column_stacks"][
                "valid_lower_support_upper_count"
            ],
        )
        self.assertEqual(2, receipt["checks"]["successor_pair_broad_phase"]["contact_count"])
        self.assertEqual(receipt, reversed_receipt)

    def test_exact_structural_join_within_strict_bound_passes(self) -> None:
        operations = _corrected_successor()
        operations.extend(
            [
                _box("structural-a", "roof", [-5.0, -5.0, 0.0], [1.0, 1.0, 1.0]),
                _box("structural-b", "roof", [-4.1, -5.0, 0.0], [1.0, 1.0, 1.0]),
            ]
        )
        contracts = [
            *_corrected_exact_contracts(),
            _relation_contract(
                "structural-a",
                "structural-b",
                relation="joins",
                intersection_policy="bounded_structural_union",
                max_overlap_m3=0.11,
            ),
        ]

        receipt = validate_stage4_successor_relations(
            operations,
            typed_relation_contracts=contracts,
        )

        broad = receipt["checks"]["successor_pair_broad_phase"]
        self.assertTrue(receipt["passed"], receipt["failures"])
        self.assertEqual(1, broad["allowed_bounded_overlap_count"])
        self.assertEqual(0, broad["unresolved_positive_volume_overlap_count"])
        outcome = next(
            item
            for item in broad["exact_relation_contract_outcomes"]
            if item["pair"] == "structural-a::structural-b"
        )
        self.assertEqual("exact_axis_aligned_box_volume", outcome["overlap_bound_basis"])
        self.assertEqual(0.1, outcome["aabb_overlap_volume_m3"])

    def test_exact_structural_join_over_bound_fails(self) -> None:
        operations = _corrected_successor()
        operations.extend(
            [
                _box("structural-a", "roof", [-5.0, -5.0, 0.0], [1.0, 1.0, 1.0]),
                _box("structural-b", "roof", [-4.1, -5.0, 0.0], [1.0, 1.0, 1.0]),
            ]
        )
        contracts = [
            *_corrected_exact_contracts(),
            _relation_contract(
                "structural-a",
                "structural-b",
                relation="joins",
                intersection_policy="bounded_structural_union",
                max_overlap_m3=0.05,
            ),
        ]

        receipt = validate_stage4_successor_relations(
            operations,
            typed_relation_contracts=contracts,
        )

        broad = receipt["checks"]["successor_pair_broad_phase"]
        self.assertFalse(receipt["passed"])
        self.assertEqual(1, broad["confirmed_box_collision_count"])
        self.assertEqual(1, broad["failed_exact_relation_contract_count"])
        failure = next(
            item
            for item in receipt["failure_details"]["successor_pair_broad_phase"]
            if item["classification"] == "exact_contract_overlap_exceeds_bound"
        )
        self.assertEqual(0.1, failure["aabb_overlap_volume_m3"])
        self.assertEqual(0.05, failure["contract"]["max_overlap_m3"])

    def test_operation_host_metadata_normalizes_multistep_inner_stack_contracts(self) -> None:
        receipt = validate_stage4_successor_relations(_metadata_chain_successor())

        contracts = receipt["checks"]["exact_relation_contracts"]
        broad = receipt["checks"]["successor_pair_broad_phase"]
        stacks = receipt["checks"]["interior_column_stacks"]
        self.assertTrue(receipt["passed"], receipt["failures"])
        self.assertEqual(3, contracts["normalized_exact_pair_count"])
        self.assertEqual(3, contracts["source_counts"]["operation_metadata"])
        self.assertEqual(3, broad["allowed_exact_contact_count"])
        self.assertEqual(3, broad["satisfied_exact_relation_contract_count"])
        self.assertEqual(1, stacks["valid_lower_support_upper_count"])
        stack_detail = receipt["failure_details"]["interior_column_stacks"][0]
        self.assertEqual(
            ["inner-capital", "inner-bearing"],
            stack_detail["bridge_operation_ids"],
        )

    def test_exact_relation_contract_rejects_wildcard_endpoint(self) -> None:
        contracts = [
            *_corrected_exact_contracts(),
            _relation_contract(
                "semantic-inner-*",
                "window-host-detail",
                relation="joins",
                intersection_policy="touch_only",
            ),
        ]

        receipt = validate_stage4_successor_relations(
            _corrected_successor(),
            typed_relation_contracts=contracts,
        )

        self.assertFalse(receipt["passed"])
        self.assertTrue(any("wildcards are prohibited" in item for item in receipt["failures"]))
        self.assertEqual(
            2,
            receipt["checks"]["exact_relation_contracts"][
                "normalized_exact_pair_count"
            ],
        )

    def test_analytically_disjoint_curved_pair_needs_no_false_narrow_ref(self) -> None:
        operations = _corrected_successor()
        operations.extend(
            [
                _column(
                    "curved-a",
                    component_id="peristyle",
                    center=[-10.0, -10.0, 0.0],
                    height=2.0,
                    diameter=1.0,
                ),
                _column(
                    "curved-b",
                    component_id="peristyle",
                    center=[-9.2, -9.2, 0.0],
                    height=2.0,
                    diameter=1.0,
                ),
            ]
        )

        receipt = validate_stage4_successor_relations(
            operations,
            typed_relation_contracts=(
                *_corrected_exact_contracts(),
                _relation_contract(
                    "curved-a",
                    "curved-b",
                    relation="clear_of",
                    intersection_policy="analytic_disjoint",
                ),
            ),
        )

        broad = receipt["checks"]["successor_pair_broad_phase"]
        boundary = receipt["checks"]["model_narrow_phase_boundary"]
        self.assertEqual(0, broad["broad_phase_narrow_ref_required_count"])
        self.assertEqual(1, broad["analytic_disjoint_pair_count"])
        self.assertFalse(boundary["narrow_phase_ref_required"])
        self.assertFalse(boundary["narrow_phase_intersection_available"])
        self.assertEqual([], boundary["required_narrow_phase_refs"])
        self.assertIn("rhino3dm", boundary["boundary"])
        self.assertTrue(receipt["passed"], receipt["failures"])


if __name__ == "__main__":
    unittest.main()
