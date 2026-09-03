from __future__ import annotations

import inspect
import unittest
from dataclasses import replace

import archive.archflow.capabilities.spatial_validation as legacy_spatial_validation
import archive.archflow.validation.spatial as canonical_spatial_validation
from archive.archflow.validation.spatial import (
    AABB,
    HostRegion,
    OpeningClearRegion,
    SpatialCheckKind,
    SpatialElement,
    SpatialElementKind,
    SpatialValidationReceipt,
    SpatialValidationStatus,
    validate_spatial_layout,
)


HOSTS = (
    HostRegion("building-host", AABB((0, 0, 0), (20, 10, 20))),
)


def _elements() -> tuple[SpatialElement, ...]:
    return (
        SpatialElement(
            "column-main",
            "column-component",
            SpatialElementKind.COLUMN,
            AABB((4, 0, 4), (5, 4, 5)),
            "building-host",
        ),
        SpatialElement(
            "door-main",
            "door-component",
            SpatialElementKind.DOOR,
            AABB((8, 0, 9), (12, 3, 10)),
            "building-host",
        ),
        SpatialElement(
            "wall-left",
            "wall-component",
            SpatialElementKind.WALL,
            AABB((0, 0, 9), (8, 4, 10)),
            "building-host",
        ),
        SpatialElement(
            "wall-right",
            "wall-component",
            SpatialElementKind.WALL,
            AABB((12, 0, 9), (20, 4, 10)),
            "building-host",
        ),
    )


OPENINGS = (
    OpeningClearRegion(
        "door-main-clear",
        "door-component",
        AABB((8, 0, 9), (12, 3, 10)),
    ),
)

REQUIRED = (
    "wall-component",
    "door-component",
    "column-component",
)


def _validate(
    elements: tuple[SpatialElement, ...],
    *,
    openings: tuple[OpeningClearRegion, ...] = OPENINGS,
):
    return validate_spatial_layout(
        elements=elements,
        host_regions=HOSTS,
        required_component_ids=REQUIRED,
        opening_clear_regions=openings,
        minimum_column_wall_clearance=1.0,
        length_unit="meter",
    )


class SpatialValidationTests(unittest.TestCase):
    def test_legacy_facade_reexports_canonical_objects_by_identity(self) -> None:
        self.assertEqual(
            canonical_spatial_validation.__all__,
            legacy_spatial_validation.__all__,
        )
        for name in canonical_spatial_validation.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(canonical_spatial_validation, name),
                    getattr(legacy_spatial_validation, name),
                )

    def test_valid_layout_passes_all_derived_hard_gates(self) -> None:
        receipt = _validate(_elements())

        self.assertIs(receipt.status, SpatialValidationStatus.PASSED)
        self.assertTrue(receipt.hard_gates_passed)
        self.assertFalse(receipt.failed_checks)
        self.assertTrue(all(item.passed for item in receipt.checks))
        self.assertTrue(receipt.receipt_id.startswith("spatial-validation-"))
        self.assertEqual(64, len(receipt.receipt_digest))
        self.assertTrue(receipt.to_dict()["hard_gates_passed"])
        self.assertFalse(receipt.to_dict()["canonical_write_authority"])

    def test_reference_fixture_preserves_schema_payload_and_digests(self) -> None:
        receipt = _validate(_elements())

        self.assertEqual(
            "7158c1042b9104581e5f54fd485d1ed93610f4c9f89daaa11ba9b7feb352a53d",
            receipt.input_digest,
        )
        self.assertEqual(
            "f2f1963a3b91c42f263caac98db3ff46b81e118b8346cd0d0d163d28ad54b958",
            receipt.receipt_digest,
        )
        self.assertEqual(
            "spatial-validation-948da3bc5b9cc6741c4516fc",
            receipt.receipt_id,
        )
        self.assertEqual("SpatialValidationReceipt@1", receipt.SCHEMA)
        self.assertEqual(
            "SpatialValidationReceipt@1",
            receipt.to_dict()["schema"],
        )

    def test_deleted_door_fails_required_component_coverage(self) -> None:
        elements = tuple(
            item for item in _elements() if item.element_id != "door-main"
        )

        receipt = _validate(elements)

        self.assertFalse(receipt.hard_gates_passed)
        failed = tuple(
            item
            for item in receipt.failed_checks
            if item.kind is SpatialCheckKind.REQUIRED_COMPONENT_COVERAGE
        )
        self.assertEqual(1, len(failed))
        self.assertEqual(("component:door-component",), failed[0].subject_refs)
        self.assertEqual(0.0, failed[0].observed_value)

    def test_column_through_wall_fails_intersection_gate(self) -> None:
        elements = tuple(
            replace(
                item,
                bounds=AABB((4, 0, 9.2), (5, 4, 9.8)),
            )
            if item.element_id == "column-main"
            else item
            for item in _elements()
        )

        receipt = _validate(elements)

        self.assertFalse(receipt.hard_gates_passed)
        intersections = tuple(
            item
            for item in receipt.failed_checks
            if item.kind is SpatialCheckKind.FORBIDDEN_INTERSECTION_VOLUME
        )
        self.assertEqual(1, len(intersections))
        self.assertGreater(intersections[0].observed_value or 0.0, 0.0)

    def test_column_outside_host_fails_containment_gate(self) -> None:
        elements = tuple(
            replace(
                item,
                bounds=AABB((-1, 0, 4), (0.5, 4, 5)),
            )
            if item.element_id == "column-main"
            else item
            for item in _elements()
        )

        receipt = _validate(elements)

        self.assertFalse(receipt.hard_gates_passed)
        containment = tuple(
            item
            for item in receipt.failed_checks
            if item.kind is SpatialCheckKind.ELEMENT_WITHIN_HOST
        )
        self.assertEqual(1, len(containment))
        self.assertEqual(
            ("element:column-main", "host:building-host"),
            containment[0].subject_refs,
        )
        self.assertEqual(1.0, containment[0].observed_value)

    def test_opening_clear_region_cannot_overlap_wall_solid(self) -> None:
        openings = (
            replace(
                OPENINGS[0],
                bounds=AABB((7.5, 0, 9), (12, 3, 10)),
            ),
        )

        receipt = _validate(_elements(), openings=openings)

        opening_failures = tuple(
            item
            for item in receipt.failed_checks
            if item.kind is SpatialCheckKind.OPENING_CLEAR_OF_WALL
        )
        self.assertEqual(1, len(opening_failures))
        self.assertGreater(opening_failures[0].observed_value or 0.0, 0.0)

    def test_non_intersecting_column_can_fail_minimum_clearance(self) -> None:
        elements = tuple(
            replace(
                item,
                bounds=AABB((4, 0, 8.5), (5, 4, 8.8)),
            )
            if item.element_id == "column-main"
            else item
            for item in _elements()
        )

        receipt = _validate(elements)

        self.assertFalse(receipt.hard_gates_passed)
        failed_kinds = {item.kind for item in receipt.failed_checks}
        self.assertIn(SpatialCheckKind.MINIMUM_CLEARANCE, failed_kinds)
        self.assertNotIn(
            SpatialCheckKind.FORBIDDEN_INTERSECTION_VOLUME,
            failed_kinds,
        )

    def test_input_order_does_not_change_receipt(self) -> None:
        first = _validate(_elements())
        second = validate_spatial_layout(
            elements=tuple(reversed(_elements())),
            host_regions=tuple(reversed(HOSTS)),
            required_component_ids=tuple(reversed(REQUIRED)),
            opening_clear_regions=tuple(reversed(OPENINGS)),
            minimum_column_wall_clearance=1.0,
            length_unit="meter",
        )

        self.assertEqual(first, second)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.receipt_digest, second.receipt_digest)

    def test_caller_cannot_supply_hard_gate_result(self) -> None:
        receipt = _validate(_elements())

        self.assertNotIn(
            "hard_gates_passed",
            inspect.signature(validate_spatial_layout).parameters,
        )
        self.assertNotIn(
            "hard_gates_passed",
            inspect.signature(SpatialValidationReceipt).parameters,
        )
        with self.assertRaises(TypeError):
            SpatialValidationReceipt(
                input_digest=receipt.input_digest,
                checks=receipt.checks,
                hard_gates_passed=True,  # type: ignore[call-arg]
            )


if __name__ == "__main__":
    unittest.main()
