from __future__ import annotations

import copy
import unittest

from tools import parthenon_stage4_roof as roof


TEXT_REFS = (
    "evidence:stage3-roof-system-museum",
    "evidence:stage3-roof-system-saylor",
    "evidence:stage4-penrose-entablature",
)
VISUAL_REFS = (
    "evidence:successor-selection#roi_ysma_11_2_pediment",
    "evidence:successor-selection#roi_ysma_11_3_pediment",
    "evidence:successor-selection#roi_ysma_11_4_roof",
)


def _operation(
    operation_id: str,
    *,
    component_id: str,
    kind: str,
    parameters: dict[str, object],
) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "component_id": component_id,
        "kind": kind,
        "material_id": "pentelic-marble",
        "parameters": parameters,
        "source_refs": ["evidence:inherited"],
        "decision_refs": ["decision:inherited"],
    }


def _current_operations() -> tuple[dict[str, object], ...]:
    cornices = {
        "east": ([-15.44, 33.55, 14.21], [30.88, 1.2, 0.87]),
        "north": ([14.24, -34.75, 14.21], [1.2, 69.5, 0.87]),
        "south": ([-15.44, -34.75, 14.21], [1.2, 69.5, 0.87]),
        "west": ([-15.44, -34.75, 14.21], [30.88, 1.2, 0.87]),
    }
    operations: list[dict[str, object]] = [
        _operation(
            "crepidoma-step-0",
            component_id="base",
            kind="box",
            parameters={"origin": [-17.24, -36.55, 0.0], "size": [34.48, 73.1, 0.45]},
        ),
        _operation(
            "main-gabled-roof",
            component_id="roof",
            kind="roof_prism",
            parameters={
                "width": 32.28,
                "length": 70.3,
                "eave_z": 15.08,
                "ridge_z": 19.38,
                "y_center": 0.0,
            },
        ),
        _operation(
            "pediment-east",
            component_id="pediments",
            kind="gable_panel",
            parameters={
                "width": 30.88,
                "depth": 0.32,
                "eave_z": 15.08,
                "ridge_z": 19.38,
                "y_center": 34.77,
            },
        ),
        _operation(
            "pediment-west",
            component_id="pediments",
            kind="gable_panel",
            parameters={
                "width": 30.88,
                "depth": 0.32,
                "eave_z": 15.08,
                "ridge_z": 19.38,
                "y_center": -34.77,
            },
        ),
    ]
    for side, (origin, size) in cornices.items():
        operations.append(
            _operation(
                f"entablature-{side}-frieze",
                component_id="entablature",
                kind="entablature_layer",
                parameters={
                    "layer": "frieze",
                    "side": side,
                    "origin": [origin[0], origin[1], 12.93],
                    "size": [size[0], size[1], 1.28],
                },
            )
        )
        operations.append(
            _operation(
                f"entablature-{side}-cornice",
                component_id="entablature",
                kind="entablature_layer",
                parameters={
                    "layer": "cornice",
                    "side": side,
                    "origin": origin,
                    "size": size,
                },
            )
        )
    return tuple(operations)


def _compiled() -> tuple[list[dict[str, object]], dict[str, object]]:
    operations, lineage = roof.compile_roof_eaves_pediment_delta(
        _current_operations(),
        textual_evidence_refs=TEXT_REFS,
        selected_visual_refs=VISUAL_REFS,
    )
    return list(copy.deepcopy(operations)), copy.deepcopy(lineage)


class ParthenonStage4RoofCompilerTests(unittest.TestCase):
    def test_compiler_replaces_all_coarse_hosts_with_typed_soft_delta(self) -> None:
        current = _current_operations()
        operations, lineage = roof.compile_roof_eaves_pediment_delta(
            current,
            textual_evidence_refs=TEXT_REFS,
            selected_visual_refs=VISUAL_REFS,
        )
        repeated_operations, repeated_lineage = roof.compile_roof_eaves_pediment_delta(
            current,
            textual_evidence_refs=TEXT_REFS,
            selected_visual_refs=VISUAL_REFS,
        )

        by_id = {str(item["operation_id"]): item for item in operations}
        self.assertFalse(set(roof.COARSE_HOST_IDS).intersection(by_id))
        self.assertEqual(set(roof.REQUIRED_DELTA_IDS), {
            operation_id
            for operation_id, item in by_id.items()
            if item.get("delta_scope") == roof.SCOPE
        })
        self.assertEqual(34, lineage["delta_operation_count"])
        self.assertEqual(len(current) - 7 + 34, len(operations))
        self.assertEqual(7, len(lineage["replacement_map"]))
        self.assertEqual(operations, repeated_operations)
        self.assertEqual(lineage, repeated_lineage)

        material_role_counts = {role: 0 for role in roof.MATERIAL_BY_ROLE}
        for operation_id in roof.REQUIRED_DELTA_IDS:
            operation = by_id[operation_id]
            material_role_counts[str(operation["material_role"])] += 1
            self.assertEqual("SOFT", operation["parameter_basis"]["classification"])
            self.assertIs(False, operation["parameter_basis"]["metric_authority"])
            self.assertIs(False, operation["parameter_basis"]["pixel_measurement_authority"])
            self.assertTrue(operation["parameter_basis"]["soft_parameter_ranges"])
            self.assertTrue(operation["source_refs"])
            self.assertTrue(operation["visual_region_refs"])
            self.assertIn(operation["host_id"], by_id)
            self.assertEqual(
                "touch_only_no_volume",
                operation["contact_policy"]["collision_policy"],
            )
            self.assertEqual(
                1,
                sum(
                    key in operation
                    for key in ("replaces_operation_id", "refines_operation_id")
                ),
            )
        self.assertEqual(
            {"structural_marble": 22, "roof_tile": 7, "timber_frame": 5},
            material_role_counts,
        )

        preserved = by_id["crepidoma-step-0"]
        self.assertEqual(
            next(item for item in current if item["operation_id"] == "crepidoma-step-0"),
            preserved,
        )

    def test_compiled_delta_passes_deterministic_validator(self) -> None:
        operations, _ = _compiled()
        receipt = roof.validate_roof_eaves_pediment_operations(operations)
        repeated = roof.validate_roof_eaves_pediment_operations(operations)

        self.assertTrue(receipt["passed"], receipt)
        self.assertEqual("PASSED", receipt["status"])
        self.assertEqual(34, receipt["checks"]["delta_operation_count"])
        self.assertTrue(receipt["checks"]["typed_host_chain"])
        self.assertTrue(receipt["checks"]["declared_nonvolumetric_contacts"])
        self.assertTrue(receipt["checks"]["sculpture_excluded"])
        self.assertEqual(receipt, repeated)

    def test_compiler_fails_without_evidence_or_with_parked_asset(self) -> None:
        with self.assertRaisesRegex(roof.ParthenonStage4RoofError, "at least one"):
            roof.compile_roof_eaves_pediment_delta(
                _current_operations(),
                textual_evidence_refs=(),
                selected_visual_refs=VISUAL_REFS,
            )
        with self.assertRaisesRegex(roof.ParthenonStage4RoofError, "PARKED"):
            roof.compile_roof_eaves_pediment_delta(
                _current_operations(),
                textual_evidence_refs=TEXT_REFS,
                selected_visual_refs=("asset:ut-austin-east-pediment-cast",),
            )

    def test_compiler_requires_exact_inherited_host_identities(self) -> None:
        current = list(copy.deepcopy(_current_operations()))
        roof_host = next(item for item in current if item["operation_id"] == "main-gabled-roof")
        roof_host["kind"] = "box"
        with self.assertRaisesRegex(roof.ParthenonStage4RoofError, "roof_prism"):
            roof.compile_roof_eaves_pediment_delta(
                current,
                textual_evidence_refs=TEXT_REFS,
                selected_visual_refs=VISUAL_REFS,
            )


class ParthenonStage4RoofValidationNegativeTests(unittest.TestCase):
    def test_original_roof_prism_cannot_survive(self) -> None:
        operations, _ = _compiled()
        operations.append(copy.deepcopy(next(
            item for item in _current_operations() if item["operation_id"] == "main-gabled-roof"
        )))
        receipt = roof.validate_roof_eaves_pediment_operations(operations)
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["checks"]["coarse_hosts_removed"])

    def test_missing_member_or_broken_host_chain_fails(self) -> None:
        operations, _ = _compiled()
        operations = [
            item for item in operations if item["operation_id"] != "eave-north-geison"
        ]
        receipt = roof.validate_roof_eaves_pediment_operations(operations)
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["checks"]["required_delta_complete"])
        self.assertFalse(receipt["checks"]["typed_host_chain"])

    def test_material_role_mismatch_fails(self) -> None:
        operations, _ = _compiled()
        ridge = next(item for item in operations if item["operation_id"] == "roof-timber-ridge")
        ridge["material_id"] = "pentelic-marble-roof-tile"
        receipt = roof.validate_roof_eaves_pediment_operations(operations)
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["checks"]["material_roles"])

    def test_volumetric_contact_or_duplicate_occupancy_fails(self) -> None:
        operations, _ = _compiled()
        ridge = next(item for item in operations if item["operation_id"] == "roof-timber-ridge")
        ridge["contact_policy"]["collision_policy"] = "volumetric_overlap"
        ridge["parameters"]["occupancy_key"] = "roof-timber-bearing-north"
        receipt = roof.validate_roof_eaves_pediment_operations(operations)
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["checks"]["declared_nonvolumetric_contacts"])

    def test_unsupported_exact_tile_metric_fails(self) -> None:
        operations, _ = _compiled()
        tile = next(
            item for item in operations if item["operation_id"] == "roof-pan-tile-field-north"
        )
        tile["parameters"]["tile_module_m"] = 0.50
        tile["parameter_basis"]["classification"] = "HARD"
        tile["parameter_basis"]["metric_authority"] = True
        receipt = roof.validate_roof_eaves_pediment_operations(operations)
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["checks"]["soft_metric_policy"])

    def test_sculpture_or_parked_asset_leakage_fails(self) -> None:
        operations, _ = _compiled()
        rogue = copy.deepcopy(next(
            item for item in operations if item["operation_id"] == "pediment-east-tympanum"
        ))
        rogue["operation_id"] = "pediment-east-figure-zeus"
        rogue["kind"] = "figure"
        rogue["parameters"]["occupancy_key"] = rogue["operation_id"]
        rogue["source_refs"] = ["asset:ut-austin-east-pediment-cast"]
        operations.append(rogue)
        receipt = roof.validate_roof_eaves_pediment_operations(operations)
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["checks"]["sculpture_excluded"])


if __name__ == "__main__":
    unittest.main()
