"""The wall solver's exclusion test: contact within the stage-5 tolerance is not an intersection."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from monkeyarch.capabilities.wall_solver import (
    CONTACT_TOLERANCE_M, OpeningKind, OpeningRequest, WallElement, WallSolverError, _overlaps, solve_wall,
)


class OverlapTests(unittest.TestCase):
    def test_a_shared_face_is_contact_not_overlap(self) -> None:
        void = ((-10.76, 3.57, -1.25), (-10.24, 8.1, 1.25))        # a door void whose sill is the landing top
        landing = ((-10.71, 3.33, -5.55), (-5.55, 3.57, 5.55))     # the landing, its top at 3.57
        self.assertFalse(_overlaps(void, landing))

    def test_an_embed_within_the_tolerance_is_contact(self) -> None:
        a = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        b = ((0.5, 0.5, 1.0 - CONTACT_TOLERANCE_M + 0.001), (1.5, 1.5, 2.0))
        self.assertFalse(_overlaps(a, b))

    def test_a_deeper_penetration_is_an_overlap(self) -> None:
        a = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        b = ((0.5, 0.5, 0.9), (1.5, 1.5, 2.0))
        self.assertTrue(_overlaps(a, b))
        self.assertTrue(_overlaps(a, b, tolerance=0.0))


class WallEndOpeningTests(unittest.TestCase):
    """An opening ending exactly at a wall end is inside it; a real overrun is not."""

    def setUp(self) -> None:
        # west-dining-wall-finish as element_producers resolves it: from (0.2, 6.86) up the axis
        # to (0.2, 9.68), so length = 9.68 - 6.86 = 2.8199999999999994; the window's authored
        # along 1.4849999999999985 arrives rounded to 1.485 and its end lands 8.9e-16 past that.
        self.wall = WallElement("wall", (0.2, 6.86), (0.0, 1.0), 9.68 - 6.86, 0.02, 3.0,
                                "level-ground", "world", "binding-wall")
        self.window = OpeningRequest("window", OpeningKind.WINDOW, round(1.4849999999999985, 9), 2.67,
                                     0.19, 2.69, "binding-opening")

    def _profile(self, result, op_id: str) -> set[float]:
        tool = next(op for op in result.operations if op.op_id == op_id)
        return {p[2] for p in json.loads(next(p.value_json for p in tool.parameters if p.name == "profile"))}

    def test_an_opening_ending_at_the_wall_end_keeps_its_full_width(self) -> None:
        result = solve_wall(self.wall, (self.window,))
        self.assertEqual(max(self._profile(result, "wall")), 9.68)
        self.assertEqual(self._profile(result, "wall-void-window"), {7.01, 9.68})
        self.assertAlmostEqual(result.voids[0].width, 2.67)

    def test_an_opening_starting_at_the_wall_start_tolerates_subtraction_roundoff(self) -> None:
        opening = replace(self.window, along=0.15, width=0.30000000000000004)   # along0 = -2.8e-17
        result = solve_wall(self.wall, (opening,))
        self.assertEqual(self._profile(result, "wall-void-window"), {6.86, 7.16})

    def test_a_real_overrun_at_either_end_or_by_a_repeat_is_refused(self) -> None:
        for excess in (1e-8, 0.001):
            for change in ({"along": self.window.width / 2.0 - excess},
                           {"along": self.window.along + excess},
                           {"along": 0.485, "width": 0.67, "count": 2, "step": 2.0 + excess}):
                with self.subTest(excess=excess, change=change), self.assertRaisesRegex(
                    WallSolverError, "lies outside the wall length"
                ):
                    solve_wall(self.wall, (replace(self.window, **change),))


class ArchOpeningTests(unittest.TestCase):
    def test_semicircular_dimensions_are_explicit_and_checked(self) -> None:
        opening = OpeningRequest("arch", OpeningKind.DOOR, 3.0, 2.4, 0.0, 2.7,
                                 "binding-opening", shape="semicircular_arch", spring_height=1.5)
        for change in ({"spring_height": None}, {"spring_height": -0.1}, {"head": 2.8},
                       {"shape": "elliptical"}, {"shape": "rectangular"},
                       {"width": 0.01, "head": 1.505}):
            with self.subTest(change=change), self.assertRaises(WallSolverError):
                replace(opening, **change)
        self.assertEqual(opening.to_dict()["shape"], "semicircular_arch")
        self.assertEqual(opening.to_dict()["spring_height"], 1.5)
        rectangle = replace(opening, shape="rectangular", spring_height=None)
        self.assertNotIn("shape", rectangle.to_dict())
        self.assertNotIn("spring_height", rectangle.to_dict())

    def test_repeated_arches_keep_their_shape_and_each_datum_binding(self) -> None:
        wall = WallElement("wall", (0.0, 0.0), (1.0, 0.0), 8.0, 0.3, 4.0,
                           "level-ground", "world", "binding-wall")
        opening = OpeningRequest("arch", OpeningKind.DOOR, 2.0, 2.4, 0.0, 2.7,
                                 "binding-opening", count=2, step=4.0,
                                 shape="semicircular_arch", spring_height=1.5)
        result = solve_wall(wall, (opening,))
        void = result.voids[0]
        self.assertEqual((void.shape, void.spring_height, void.count), ("semicircular_arch", 1.5, 2))
        self.assertEqual(len(void.aperture_object_ids), 2)
        self.assertEqual(void.to_dict()["shape"], "semicircular_arch")
        primitives = {op.op_id for op in result.operations if not op.input_object_ids}
        self.assertEqual({binding.op_id for binding in result.datum_bindings}, primitives)
        self.assertEqual(sum(op.kind.value == "revolve" for op in result.operations), 2)


if __name__ == "__main__":
    unittest.main()
