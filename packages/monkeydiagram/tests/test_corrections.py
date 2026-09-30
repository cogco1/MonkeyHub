"""The difference between two cut-plan view recipes, and the class of correction it is (#519).

The Project Runtime pairs a drawing's retained revisions and offers the recipe
corrections people repeat; its suite covers that and the HTTP read. Here the
two pure functions are read on recipes written in place.
"""

from __future__ import annotations

from copy import deepcopy
import unittest

from monkeydiagram.corrections import CLASSES, classify, recipe_diff

PLAN = {"kind": "cut-plan", "name": "plan",
        "frame": {"name": "plan", "origin": [0, 0, 1.2], "crop_uv": [0, 0, 4, 3], "far_depth": 1.2, "scale": "1:50"},
        "graphics": {"cutLineMm": .35, "visibleLineMm": .18, "hatchSpacingMm": 2},
        "hiddenObjectIds": [], "dimensions": []}
BENCH = {"id": "bench", "assetId": "bench-plan", "positionUv": [1, 1], "size": 1, "flipped": False,
         "anchorObjectId": None}


class RecipeDiffTests(unittest.TestCase):
    def test_a_change_is_one_entry_named_by_its_dotted_path_in_path_order(self) -> None:
        after = deepcopy(PLAN)
        after["graphics"]["cutLineMm"] = .5
        after["frame"]["scale"] = "1:20"
        after["follow"] = "frozen"
        diff = recipe_diff(PLAN, after)
        self.assertEqual(diff, {"follow": [None, "frozen"], "frame.scale": ["1:50", "1:20"],
                                "graphics.cutLineMm": [.35, .5]})
        self.assertEqual(list(diff), sorted(diff))

    def test_hidden_objects_are_named_by_id_and_not_by_position(self) -> None:
        before = {**PLAN, "hiddenObjectIds": ["obj-1", "obj-2"]}
        after = {**PLAN, "hiddenObjectIds": ["obj-2", "obj-3"]}
        self.assertEqual(recipe_diff(before, after), {"hiddenObjectIds.obj-1": [True, False],
                                                      "hiddenObjectIds.obj-3": [False, True]})

    def test_dressing_and_dimensions_are_compared_by_the_ids_they_hold(self) -> None:
        moved = {**BENCH, "positionUv": [2, 1]}
        tree = {**BENCH, "id": "tree", "assetId": "tree-plan"}
        dimension = {"id": "door", "entityRef": "entity:wall", "openingId": "door", "placement": {"offsetMm": 8}}
        before = {**PLAN, "dressing": [BENCH], "dimensions": [dimension]}
        after = {**PLAN, "dressing": [tree, moved],
                 "dimensions": [{**dimension, "placement": {"offsetMm": 4}}]}
        self.assertEqual(recipe_diff(before, after), {
            "dimensions.door.placement.offsetMm": [8, 4],
            "dressing.bench.positionUv": [[1, 1], [2, 1]],
            "dressing.tree": [None, tree],
        })

    def test_a_list_its_ids_cannot_key_is_compared_whole(self) -> None:
        twice = {**PLAN, "dressing": [BENCH, BENCH]}
        self.assertEqual(recipe_diff(PLAN, twice), {"dressing": [None, [BENCH, BENCH]]})

    def test_a_material_rule_is_one_entry_whatever_changed_in_it(self) -> None:
        rule = {"spacingMm": 3, "angleDeg": 135, "poche": False}
        before = deepcopy(PLAN)
        before["graphics"]["hatch"] = {"byMaterial": {"timber": rule}}
        after = deepcopy(before)
        after["graphics"]["hatch"]["byMaterial"]["timber"] = {**rule, "angleDeg": 45}
        self.assertEqual(recipe_diff(before, after),
                         {"graphics.hatch.byMaterial.timber": [rule, {**rule, "angleDeg": 45}]})

    def test_equal_values_and_absent_or_empty_lists_differ_in_nothing(self) -> None:
        self.assertEqual(recipe_diff(PLAN, deepcopy(PLAN)), {})
        two = deepcopy(PLAN)
        two["graphics"]["hatchSpacingMm"] = 2.0
        self.assertEqual(recipe_diff(PLAN, two), {})
        self.assertEqual(recipe_diff({**PLAN, "dressing": []}, PLAN), {})
        self.assertEqual(recipe_diff(None, None), {})


class ClassifyTests(unittest.TestCase):
    def test_the_classes_are_asked_in_order(self) -> None:
        self.assertEqual(CLASSES, ("compiler_defect", "semantic_rule", "recipe", "local_override"))

    def test_each_difference_is_the_first_class_that_holds(self) -> None:
        rule = {"spacingMm": 3.0, "angleDeg": 135.0, "poche": False}
        for diff, cause, expected in (
            ({"graphics.cutLineMm": [.35, .5]}, "source", "compiler_defect"),
            ({"graphics.hatch.byMaterial.timber": [None, rule], "graphics.cutLineMm": [.35, .5]}, "representation",
             "semantic_rule"),
            ({"graphics.visibleLineMm": [.18, .25]}, "representation", "recipe"),
            ({"dressing.bench": [None, BENCH]}, "representation", "recipe"),
            ({"dressing.bench": [BENCH, None], "dressing.tree": [None, {**BENCH, "id": "tree"}]}, "representation",
             "local_override"),
            ({"hiddenObjectIds.obj-1": [False, True]}, None, "local_override"),
            ({"frame.scale": ["1:50", "1:20"]}, "representation", "local_override"),
            ({}, "representation", "local_override"),
        ):
            with self.subTest(diff=diff, cause=cause):
                self.assertEqual(classify(diff, None, cause), expected)

    def test_a_cleanup_report_that_names_no_object_changes_no_class(self) -> None:
        cleanup = {"tolerance": .025, "input_lines": 40, "output_lines": 31}
        hide = {"hiddenObjectIds.obj-1": [False, True]}
        self.assertEqual(classify(hide, cleanup, "representation"), "local_override")
        self.assertEqual(classify(hide, cleanup, "source"), "compiler_defect")


if __name__ == "__main__":
    unittest.main()
