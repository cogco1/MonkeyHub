"""The wall solver's exclusion test: contact within the stage-5 tolerance is not an intersection."""

from __future__ import annotations

import unittest

from archflow.capabilities.wall_solver import CONTACT_TOLERANCE_M, _overlaps


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


if __name__ == "__main__":
    unittest.main()
