from __future__ import annotations

import copy
import math
import unittest
from collections.abc import Mapping, Sequence

from archive.tools import parthenon_stage4_inner_colonnade as inner
from archive.tools import parthenon_stage4_roof as roof
from archive.tools import run_parthenon_reconstruction as stage3
from archive.tools import run_parthenon_stage4_reconstruction as stage4
from archive.tools import parthenon_stage4_relations as relations
from archive.tools.parthenon_stage4_relation_contracts import (
    compile_full_building_relation_contracts,
)


HUMAN_AUTHORIZATION_REF = (
    "decision:human-authorized-stage4-principal-door-closed-double-leaf"
)


def _full_building_operations() -> tuple[dict[str, object], ...]:
    sources = {
        decision.decision_ref: (
            f"evidence:{decision.decision_ref.split(':')[-1]}",
        )
        for decision in stage3.DECISIONS
    }
    predecessor = stage3.build_stage_operations(3, sources)
    operations, _ = stage4.compile_full_building_stage4_operations(
        predecessor,
        column_source_refs=("evidence:penrose-column",),
        entablature_source_refs=("evidence:perseus-entablature",),
        opening_source_refs=("evidence:bsa-openings",),
        visual_manifest_ref="evidence:selected-visual-regions",
        human_authorization_ref=HUMAN_AUTHORIZATION_REF,
    )
    return operations


class ParthenonStage4RelationContractCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.operations = _full_building_operations()
        cls.contracts, cls.receipt = compile_full_building_relation_contracts(
            cls.operations
        )

    def test_complete_687_operation_contract_set_is_deterministic_and_exact(self) -> None:
        reversed_contracts, reversed_receipt = compile_full_building_relation_contracts(
            tuple(reversed(self.operations))
        )

        self.assertTrue(self.receipt["passed"], self.receipt["failures"])
        self.assertEqual(self.contracts, reversed_contracts)
        self.assertEqual(self.receipt, reversed_receipt)
        checks = self.receipt["checks"]
        self.assertEqual(687, checks["operation_denominator"])
        self.assertEqual(687, checks["aabb_operation_count"])
        self.assertEqual(235641, checks["pair_denominator"])
        self.assertEqual(1408, checks["physical_relation_pair_count"])
        self.assertEqual(1408, checks["contract_count"])
        self.assertEqual(1140, checks["touch_pair_count"])
        self.assertEqual(268, checks["positive_volume_pair_count"])
        self.assertEqual(0, checks["unrecognized_pair_count"])
        self.assertEqual(0, checks["unrealized_metadata_contract_count"])
        self.assertEqual(0, checks["wildcard_endpoint_count"])
        self.assertFalse(checks["validator_failure_list_consumed"])
        self.assertEqual(
            {
                "bounded_embedded_finish": 190,
                "bounded_structural_union": 78,
                "touch_only": 1140,
            },
            checks["policy_counts"],
        )
        self.assertTrue(
            all(
                not any(token in str(contract[key]) for token in ("*", "?", "[", "]"))
                for contract in self.contracts
                for key in ("first_operation_id", "second_operation_id")
            )
        )

    def test_bounded_contracts_use_constructed_geometry_upper_bounds(self) -> None:
        bounded = [
            item
            for item in self.contracts
            if str(item["intersection_policy"]).startswith("bounded_")
        ]

        self.assertEqual(268, len(bounded))
        for contract in bounded:
            observed = float(contract["observed_aabb_overlap_m3"])
            maximum = float(contract["max_overlap_m3"])
            tolerance = max(1.0e-9, observed * 1.0e-6)
            self.assertTrue(math.isfinite(maximum))
            self.assertGreater(maximum, observed)
            self.assertLessEqual(maximum - observed, tolerance + 2.0e-12)
            self.assertIn(
                contract["overlap_bound_basis"],
                {
                    "exact_constructed_box_intersection_plus_relative_tolerance",
                    "constructed_aabb_upper_bound_plus_relative_tolerance",
                },
            )

    def test_final_contracts_close_validator_without_narrow_phase_debt(self) -> None:
        receipt = relations.validate_stage4_successor_relations(
            self.operations,
            typed_relation_contracts=self.contracts,
        )

        self.assertTrue(receipt["passed"], receipt["failures"])
        broad = receipt["checks"]["successor_pair_broad_phase"]
        self.assertEqual(235641, broad["pair_denominator"])
        self.assertEqual(1408, broad["exact_relation_contract_denominator"])
        self.assertEqual(1408, broad["satisfied_exact_relation_contract_count"])
        self.assertEqual(0, broad["failed_exact_relation_contract_count"])
        self.assertEqual(0, broad["unexpected_contact_count"])
        self.assertEqual(0, broad["unresolved_positive_volume_overlap_count"])
        self.assertEqual(0, broad["broad_phase_narrow_ref_required_count"])
        self.assertEqual(
            0, receipt["checks"]["window_projection"]["obstruction_count"]
        )
        stacks = receipt["checks"]["interior_column_stacks"]
        self.assertEqual(23, stacks["valid_lower_support_upper_count"])
        self.assertEqual(0, stacks["direct_shaft_contact_count"])
        self.assertFalse(
            receipt["checks"]["model_narrow_phase_boundary"][
                "narrow_phase_ref_required"
            ]
        )

    def test_roof_aabbs_inner_exact_metadata_and_door_hosts_are_readable(self) -> None:
        parsed, failures, malformed = relations._parse_operations(self.operations)
        parsed_by_id = {item.operation_id: item for item in parsed}
        roof_ids = {
            str(item["operation_id"])
            for item in self.operations
            if item.get("delta_scope") == roof.SCOPE
        }

        self.assertEqual([], failures)
        self.assertEqual([], malformed)
        self.assertEqual(set(roof.REQUIRED_DELTA_IDS), roof_ids)
        self.assertEqual(34, len(roof_ids))
        self.assertTrue(all(parsed_by_id[item].bounds is not None for item in roof_ids))

        scoped_inner = [
            item
            for item in self.operations
            if item.get("refinement_scope") == inner.REFINEMENT_SCOPE
            and item.get("component_id") == "interior-colonnade"
        ]
        inner_contracts: list[Mapping[str, object]] = []
        for operation in scoped_inner:
            value = operation["contact_contract"]
            entries = [value] if isinstance(value, Mapping) else list(value)
            inner_contracts.extend(entries)
        self.assertEqual(187, len(scoped_inner))
        self.assertEqual(230, len(inner_contracts))
        self.assertTrue(
            all(
                all(
                    key in contract
                    for key in (
                        "first_operation_id",
                        "second_operation_id",
                        "intersection_policy",
                        "host_operation_id",
                    )
                )
                and contract["intersection_policy"] == "touch_only"
                and contract["host_operation_id"]
                in {
                    contract["first_operation_id"],
                    contract["second_operation_id"],
                }
                for contract in inner_contracts
            )
        )

        operations_by_id = {
            str(item["operation_id"]): item for item in self.operations
        }
        shared_wall_contracts = [
            item
            for item in self.contracts
            if item.get("semantic_rule") == "door-frame-shared-wall-host"
        ]
        frame_joint_contracts = [
            item
            for item in self.contracts
            if item.get("semantic_rule") == "door-frame-member-joint"
        ]
        self.assertEqual(14, len(shared_wall_contracts))
        self.assertEqual(8, len(frame_joint_contracts))
        for contract in shared_wall_contracts:
            endpoints = [
                operations_by_id[str(contract["first_operation_id"])],
                operations_by_id[str(contract["second_operation_id"])],
            ]
            door = next(
                item for item in endpoints if str(item["component_id"]).startswith("door-")
            )
            wall = next(item for item in endpoints if item is not door)
            self.assertIn(
                wall["operation_id"],
                door["parameters"]["host_operation_ids"],
            )

    def test_unknown_touch_and_missing_aabb_fail_closed(self) -> None:
        unknown_touch = list(copy.deepcopy(self.operations))
        unknown_touch.append(
            {
                "operation_id": "untyped-contact-probe",
                "component_id": "untyped-probe",
                "kind": "box",
                "parameters": {
                    "origin": [17.24, 0.0, 0.0],
                    "size": [1.0, 1.0, 0.45],
                },
            }
        )
        contracts, receipt = compile_full_building_relation_contracts(
            unknown_touch,
            expected_operation_count=688,
        )

        self.assertFalse(receipt["passed"])
        self.assertEqual(1, receipt["checks"]["unrecognized_pair_count"])
        self.assertEqual(
            "crepidoma-step-0::untyped-contact-probe",
            receipt["failure_details"]["unrecognized_pairs"][0]["pair"],
        )
        self.assertFalse(
            any(
                "untyped-contact-probe"
                in {
                    item["first_operation_id"],
                    item["second_operation_id"],
                }
                for item in contracts
            )
        )

        missing_aabb = list(copy.deepcopy(self.operations))
        target = next(
            item
            for item in missing_aabb
            if item["operation_id"] == "roof-ridge-terminal"
        )
        target["kind"] = "unparsed-roof-primitive"
        target["parameters"] = {}
        _, malformed_receipt = compile_full_building_relation_contracts(missing_aabb)
        self.assertFalse(malformed_receipt["passed"])
        self.assertLess(
            malformed_receipt["checks"]["aabb_operation_count"],
            malformed_receipt["checks"]["operation_denominator"],
        )
        self.assertIn(
            "full-building relation compilation requires an AABB for every operation",
            malformed_receipt["failures"],
        )

    def test_same_role_roof_cella_and_wrong_side_decoration_do_not_auto_authorize(self) -> None:
        same_role_roof = list(copy.deepcopy(self.operations))
        roof_host = next(
            item for item in same_role_roof if item["operation_id"] == "eave-north-geison"
        )
        parsed_roof_host, _, _ = relations._parse_operations((roof_host,))
        roof_bounds = parsed_roof_host[0].bounds
        assert roof_bounds is not None
        roof_probe_size = [0.01, 0.01, 0.01]
        roof_probe_origin = [
            (roof_bounds.minimum[axis] + roof_bounds.maximum[axis]) / 2.0
            - roof_probe_size[axis] / 2.0
            for axis in range(3)
        ]
        same_role_roof.append(
            {
                "operation_id": "roof-untyped-same-role-collision",
                "component_id": "entablature",
                "kind": "box",
                "parameters": {
                    "origin": roof_probe_origin,
                    "size": roof_probe_size,
                },
                "delta_scope": roof.SCOPE,
                "material_role": "structural_marble",
                "host_id": "entablature-north-frieze",
                "contact_policy": copy.deepcopy(roof_host["contact_policy"]),
            }
        )
        _, roof_receipt = compile_full_building_relation_contracts(
            same_role_roof,
            expected_operation_count=688,
        )
        roof_unrecognized = {
            str(item["pair"])
            for item in roof_receipt["failure_details"]["unrecognized_pairs"]
        }
        self.assertFalse(roof_receipt["passed"])
        self.assertTrue(
            any("roof-untyped-same-role-collision" in pair for pair in roof_unrecognized)
        )

        arbitrary_cella = list(copy.deepcopy(self.operations))
        north_wall = next(
            item for item in arbitrary_cella if item["operation_id"] == "cella-wall-north"
        )
        arbitrary_cella.append(
            {
                "operation_id": "cella-arbitrary-through-wall",
                "component_id": "cella",
                "kind": "box",
                "parameters": copy.deepcopy(north_wall["parameters"]),
            }
        )
        _, cella_receipt = compile_full_building_relation_contracts(
            arbitrary_cella,
            expected_operation_count=688,
        )
        cella_unrecognized = {
            str(item["pair"])
            for item in cella_receipt["failure_details"]["unrecognized_pairs"]
        }
        self.assertFalse(cella_receipt["passed"])
        self.assertIn(
            "cella-arbitrary-through-wall::cella-wall-north",
            cella_unrecognized,
        )

        wrong_side = list(copy.deepcopy(self.operations))
        east_metope = next(
            item for item in wrong_side if item["operation_id"] == "metope-east-05"
        )
        west_metope = next(
            item for item in wrong_side if item["operation_id"] == "metope-west-05"
        )
        east_metope["parameters"] = copy.deepcopy(west_metope["parameters"])
        _, decoration_receipt = compile_full_building_relation_contracts(wrong_side)
        decoration_unrecognized = {
            str(item["pair"])
            for item in decoration_receipt["failure_details"]["unrecognized_pairs"]
        }
        self.assertFalse(decoration_receipt["passed"])
        self.assertIn(
            "entablature-west-frieze::metope-east-05",
            decoration_unrecognized,
        )


if __name__ == "__main__":
    unittest.main()
