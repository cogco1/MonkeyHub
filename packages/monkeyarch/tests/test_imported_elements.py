"""An imported element: objects of a registered model file, placed by the record (#300 item 22 B).

It produces no operation; the composed model copies its objects. What the
record owns is where they stand: a move adds to it, a turn about a vertical
axis through a point turns where they stand about that point, and anything
that would change their shape is refused with what can be done instead.
"""

from __future__ import annotations

import math
import unittest

from monkeyarch.authoring.element_producers import (
    ElementProducerError,
    ElementRow,
    ProductionContext,
    edit_drawn_element,
    imported_placement,
    produce_imported,
)
from monkeyarch.domain.reference_resolver import ReferenceContext

SHA = "a" * 64


def _row(**params) -> ElementRow:
    return ElementRow("stair-1", "stair", "imported", {}, {"asset": SHA, "objects": ["o1", "o2"], **params})


class ImportedElementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ProductionContext(ReferenceContext(grids=None, levels=None), {})

    def test_it_builds_nothing_and_names_its_file_and_objects(self) -> None:
        produced = produce_imported(_row(), self.context)
        self.assertEqual(produced.operations, ())
        self.assertEqual(imported_placement(_row()), (SHA, ("o1", "o2"), (0.0, 0.0, 0.0), 0.0))
        for bad in ({"asset": "nope"}, {"objects": []}, {"objects": ["o1", "o1"]}, {"move": [1, 2]}):
            with self.subTest(bad=bad), self.assertRaises(ElementProducerError):
                produce_imported(ElementRow("x", "c", "imported", {}, {"asset": SHA, "objects": ["o1"], **bad}), self.context)

    def test_moves_add_and_a_turn_turns_where_it_stands_about_its_point(self) -> None:
        moved = edit_drawn_element(_row(move=[1.0, 0.0, 0.0]), self.context, kind="move", translation=[0.3, 0.0, -0.5])
        self.assertEqual(moved.params["move"], [1.3, 0.0, -0.5])
        turned = edit_drawn_element(moved, self.context, kind="rotate", axis=[0, 1, 0], angle_degrees=90, origin=[1.3, 0, 0])
        # About (1.3, 0): where it stood, (1.3, -0.5), turns +90 about +Y to (1.3 - 0.5, 0): x' = x cos + z sin.
        self.assertEqual(turned.params["turn"], 90.0)
        self.assertTrue(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(turned.params["move"], [0.8, 0.0, 0.0])))
        back = edit_drawn_element(turned, self.context, kind="rotate", axis=[0, -1, 0], angle_degrees=90, origin=[1.3, 0, 0])
        self.assertEqual(back.params["turn"], 0.0)
        self.assertTrue(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(back.params["move"], [1.3, 0.0, -0.5])))

    def test_its_shape_is_the_files(self) -> None:
        for kind, action in (("scale", {"scale": [2, 2, 2]}), ("push_pull", {"distance": 1.0}), ("copy", {})):
            with self.subTest(kind=kind), self.assertRaisesRegex(ElementProducerError, "Rhino"):
                edit_drawn_element(_row(), self.context, kind=kind, **action)
        with self.assertRaisesRegex(ElementProducerError, "vertical"):
            edit_drawn_element(_row(), self.context, kind="rotate", axis=[1, 0, 0], angle_degrees=10, origin=[0, 0, 0])
        with self.assertRaisesRegex(ElementProducerError, "point it turns about"):
            edit_drawn_element(_row(), self.context, kind="rotate", axis=[0, 1, 0], angle_degrees=10)


if __name__ == "__main__":
    unittest.main()
