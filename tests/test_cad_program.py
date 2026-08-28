"""P071: deterministic CAD translation and analytic equivalence bounds."""

import json
import unittest
from types import SimpleNamespace

from archflow.adapters.cad_program import (
    expected_object_bounds,
    translate_to_rhino_python,
)


def op(op_id, kind, outputs, inputs=(), **params):
    return SimpleNamespace(
        op_id=op_id,
        kind=SimpleNamespace(value=kind),
        output_object_ids=tuple(outputs),
        input_object_ids=tuple(inputs),
        parameters=tuple(
            SimpleNamespace(name=name, value_json=json.dumps(value))
            for name, value in sorted(params.items())
        ),
    )


def program(*operations):
    return SimpleNamespace(
        proposal=SimpleNamespace(operations=tuple(operations)),
        operation_order=tuple(item.op_id for item in operations),
    )


class TranslateTest(unittest.TestCase):
    def test_script_is_deterministic_and_names_physical_objects(self):
        build = program(
            op(
                "base",
                "solid",
                ["base-object"],
                origin=[0.0, 0.0, 0.0],
                size=[4.0, 2.0, 6.0],
            ),
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[10.0, 0.0, 0.0],
                size=[1.0, 3.0, 1.0],
            ),
            op(
                "row",
                "array",
                ["row-object"],
                ["seed-object"],
                count=3,
                step=[2.0, 0.0, 0.0],
            ),
        )
        first = translate_to_rhino_python(build)
        second = translate_to_rhino_python(build)
        self.assertEqual(first.script, second.script)
        self.assertEqual(
            ("base-object", "row-object"), first.physical_object_ids
        )
        self.assertEqual((), first.losses)
        self.assertIn("rs.AddBox", first.script)
        self.assertIn("CAD_MEASURES=", first.script)

    def test_curve_and_transform_become_typed_losses(self):
        build = program(
            op(
                "guide",
                "curve",
                ["guide-object"],
                points=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            ),
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[0.0, 0.0, 0.0],
                size=[1.0, 1.0, 1.0],
            ),
            op(
                "moved",
                "transform",
                ["moved-object"],
                ["seed-object"],
                translation=[5.0, 0.0, 0.0],
            ),
        )
        translation = translate_to_rhino_python(build)
        codes = {loss["code"] for loss in translation.losses}
        self.assertEqual(
            {"cad.curve_reference_only", "cad.transform_copy_only"},
            codes,
        )
        self.assertNotIn("guide-object", translation.physical_object_ids)


class ExpectedBoundsTest(unittest.TestCase):
    def test_solid_and_array_bounds(self):
        build = program(
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[1.0, 2.0, 3.0],
                size=[1.0, 1.0, 1.0],
            ),
            op(
                "row",
                "array",
                ["row-object"],
                ["seed-object"],
                count=4,
                step=[2.0, 0.0, 0.0],
            ),
        )
        bounds = expected_object_bounds(build)
        self.assertEqual(["row-object"], list(bounds))
        row = bounds["row-object"]
        self.assertEqual([1.0, 2.0, 3.0], row["bbox_min"])
        self.assertEqual([8.0, 3.0, 4.0], row["bbox_max"])
        self.assertEqual(4, row["brep_count"])

    def test_full_radial_ring_is_symmetric_about_center(self):
        build = program(
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[9.0, 0.0, -0.5],
                size=[1.0, 2.0, 1.0],
            ),
            op(
                "ring",
                "radial_array",
                ["ring-object"],
                ["seed-object"],
                count=4,
                center=[0.0, 0.0, 0.0],
                axis=[0.0, 1.0, 0.0],
                angle_step_degrees=90.0,
                start_angle_degrees=0.0,
            ),
        )
        ring = expected_object_bounds(build)["ring-object"]
        for axis in (0, 2):
            self.assertAlmostEqual(
                -ring["bbox_min"][axis], ring["bbox_max"][axis]
            )
        self.assertAlmostEqual(10.0, ring["bbox_max"][0])
        self.assertEqual(4, ring["brep_count"])

    def test_boolean_difference_keeps_base_bounds(self):
        build = program(
            op(
                "big",
                "solid",
                ["big-object"],
                origin=[0.0, 0.0, 0.0],
                size=[10.0, 10.0, 10.0],
            ),
            op(
                "cut",
                "solid",
                ["cut-object"],
                origin=[4.0, 4.0, 4.0],
                size=[2.0, 2.0, 2.0],
            ),
            op(
                "shell",
                "boolean_difference",
                ["shell-object"],
                ["big-object", "cut-object"],
                base_index=0,
            ),
        )
        shell = expected_object_bounds(build)["shell-object"]
        self.assertEqual([0.0, 0.0, 0.0], shell["bbox_min"])
        self.assertEqual([10.0, 10.0, 10.0], shell["bbox_max"])
        self.assertEqual(1, shell["brep_count"])

    def test_revolve_bounds_span_both_radii(self):
        build = program(
            op(
                "cone",
                "revolve",
                ["cone-object"],
                axis_start=[5.0, 0.0, 5.0],
                axis_end=[5.0, 8.0, 5.0],
                start_radius=3.0,
                end_radius=1.0,
            ),
        )
        cone = expected_object_bounds(build)["cone-object"]
        self.assertEqual([2.0, 0.0, 2.0], cone["bbox_min"])
        self.assertEqual([8.0, 8.0, 8.0], cone["bbox_max"])

    def test_bounds_cover_exactly_the_physical_set(self):
        build = program(
            op(
                "guide",
                "curve",
                ["guide-object"],
                points=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            ),
            op(
                "seed",
                "solid",
                ["seed-object"],
                origin=[0.0, 0.0, 0.0],
                size=[1.0, 1.0, 1.0],
            ),
            op(
                "row",
                "array",
                ["row-object"],
                ["seed-object"],
                count=2,
                step=[0.0, 0.0, 3.0],
            ),
        )
        translation = translate_to_rhino_python(build)
        bounds = expected_object_bounds(build)
        self.assertEqual(
            sorted(translation.physical_object_ids), sorted(bounds)
        )


if __name__ == "__main__":
    unittest.main()
