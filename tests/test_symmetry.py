"""P074: axial symmetry measurement contracts."""

import unittest

from archflow.evaluation import (
    SymmetryMeasurementError,
    axial_group_offsets,
)


def scene_object(object_id, minimum, maximum, bindings, physical=True):
    return {
        "object_id": object_id,
        "bounds": {"minimum": list(minimum), "maximum": list(maximum)},
        "semantic_binding_ids": list(bindings),
        "physical": physical,
    }


SCENE = [
    scene_object("slab", (0.0, 0.0, 0.0), (20.0, 1.0, 30.0), ["base-b"]),
    scene_object("row", (3.0, 1.0, 2.0), (18.0, 8.0, 4.0), ["row-b"]),
    scene_object("tool", (8.0, 1.0, 0.0), (12.0, 6.0, 2.0), ["cut-b"],
                 physical=False),
]


class AxialGroupOffsetTest(unittest.TestCase):
    def test_symmetric_group_measures_zero(self):
        findings = axial_group_offsets(
            SCENE, axis_value=10.0, axis_index=0,
            groups={"base": ["base-b"]},
        )
        self.assertEqual(0.0, findings[0].offset)
        self.assertEqual(("slab",), findings[0].object_ids)

    def test_offset_group_measures_exact_signed_offset(self):
        findings = axial_group_offsets(
            SCENE, axis_value=10.0, axis_index=0,
            groups={"row": ["row-b"]},
        )
        self.assertEqual(0.5, findings[0].offset)
        self.assertEqual(10.5, findings[0].center)

    def test_non_physical_objects_are_excluded_by_default(self):
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(
                SCENE, axis_value=10.0, axis_index=0,
                groups={"cut": ["cut-b"]},
            )
        findings = axial_group_offsets(
            SCENE, axis_value=10.0, axis_index=0,
            groups={"cut": ["cut-b"]}, physical_only=False,
        )
        self.assertEqual(0.0, findings[0].offset)

    def test_empty_group_fails_closed(self):
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(
                SCENE, axis_value=10.0, axis_index=0,
                groups={"ghost": ["missing-b"]},
            )

    def test_group_aggregates_multiple_members(self):
        scene = SCENE + [
            scene_object("row-2", (2.0, 1.0, 26.0), (17.0, 8.0, 28.0),
                         ["row-b"]),
        ]
        findings = axial_group_offsets(
            scene, axis_value=10.0, axis_index=0,
            groups={"row": ["row-b"]},
        )
        self.assertEqual(2.0, findings[0].minimum)
        self.assertEqual(18.0, findings[0].maximum)
        self.assertEqual(0.0, findings[0].offset)

    def test_axis_index_selects_dimension(self):
        findings = axial_group_offsets(
            SCENE, axis_value=15.0, axis_index=2,
            groups={"base": ["base-b"]},
        )
        self.assertEqual(0.0, findings[0].offset)

    def test_invalid_requests_are_typed(self):
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(SCENE, axis_value=10.0, axis_index=3,
                                groups={"base": ["base-b"]})
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(SCENE, axis_value=10.0, axis_index=0,
                                groups={})
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(SCENE, axis_value=10.0, axis_index=0,
                                groups={"base": []})


class DerivedRowOriginTest(unittest.TestCase):
    def test_monument_rows_center_on_the_axis(self):
        from tests.integration.test_monument_derivation import (
            CENTER_X,
            _axial_row_origin,
        )
        for count, step, width in ((8, 3.5, 2), (8, 3.5, 3), (9, 3.3, 1.4)):
            origin = _axial_row_origin(count, step, width)
            span_min = origin
            span_max = origin + (count - 1) * step + width
            self.assertAlmostEqual(CENTER_X, (span_min + span_max) / 2)


if __name__ == "__main__":
    unittest.main()
