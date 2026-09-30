"""A view sheet's layout: each placed drawing's labels, a section's line and its mark on a plan (#519).

The Project Runtime draws or reads back each view, retains the sheet and serves
``POST /api/drawings/sheets``; its suite covers that. Here the layout is read
off recipes written in place, as a retained view's receipt states them.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from monkeydiagram.documentation.sheet_layout import (
    SheetLayoutError,
    section_line,
    section_mark,
    view_labels,
    view_sheet_scene,
)

# A horizontal cut plan at 1.2 m, a 4 x 3 m window at 1:50: 20 paper mm per metre.
PLAN = {"frame": {"origin": [0.0, 0.0, 1.2], "look": [0, 0, -1], "crop_uv": [0.0, 0.0, 4.0, 3.0], "scale": "1:50"}}
SECTION = {"frame": {"origin": [2.0, 1.5, 0.0], "look": [0, 1, 0], "up": [0, 0, 1], "scale": "1:50"}}


def _view(kind: str = "plan", **values) -> dict:
    return {"id": f"{kind}-a", "kind": kind, "arguments": {}, **values}


class LabelTests(unittest.TestCase):
    def test_each_kind_is_titled_by_what_its_own_frame_states(self) -> None:
        for view, kind, recipe, unit, expected in (
            (_view(), "plan", PLAN, "meter", ("PLAN", "Horizontal cut at Z +1.200 m, looking down", "1:50")),
            (_view(mark_label="A"), "section", SECTION, "meter",
             ("SECTION A-A", "Vertical cut at Y +1.500 m, looking +Y", "1:50")),
            (_view("elevation", arguments={"view": "front"}), "elevation", {"look": [0, 1, 0], "scale": "1:100"},
             "millimeter", ("FRONT ELEVATION", "Orthographic, looking +Y", "1:100")),
            (_view("elevation", arguments={"view": "top"}), "elevation", {"look": [0, 0, -1], "scale": "1:100"},
             "meter", ("TOP VIEW", "Orthographic, looking down; not a cut plan", "1:100")),
            (_view("elevation", arguments={"view": "axon"}), "axon", {"look": [1, 1, -1], "scale": "1:40"}, "meter",
             ("ISOMETRIC", "Parallel view from -X / -Y / +Z, whole model, not to scale", "display 1:40")),
            (_view("elevation", arguments={"view": "axon"}), "axon", {"look": [1, 2, -1], "scale": "1:40"}, "meter",
             ("AXONOMETRIC", "Parallel view from -X / -Y / +Z, whole model, not to scale", "display 1:40")),
            (_view("section-perspective", mark_label="B"), "section-perspective", {"scale": "1:20"}, "meter",
             ("SECTION PERSPECTIVE B-B", "Cut plane at 1:20; depth in perspective, not to scale", "1:20 at the cut")),
        ):
            with self.subTest(kind=kind, expected=expected[0]):
                self.assertEqual(view_labels(view, kind, recipe, unit), expected)

    def test_the_callers_words_win_and_an_empty_subtitle_is_kept(self) -> None:
        self.assertEqual(view_labels(_view(title="GROUND FLOOR", subtitle=""), "plan", PLAN, "foot"),
                         ("GROUND FLOOR", "", "1:50"))
        self.assertEqual(view_labels(_view(), "plan", PLAN, "foot")[1], "Horizontal cut at Z +1.200 ft, looking down")


class SectionMarkTests(unittest.TestCase):
    def test_a_section_reads_as_a_line_in_plan_only_when_its_plane_is_vertical(self) -> None:
        self.assertEqual(section_line("section", SECTION), ([2.0, 1.5, 0.0], [0, 1, 0]))
        upright = {"section": {"origin": [1.0, 2.0, 0.0], "normal": [-1.0, 0.0, 0.0]}}
        self.assertEqual(section_line("section-perspective", upright), ([1.0, 2.0, 0.0], [1.0, -0.0, -0.0]))
        tilted = {"section": {"origin": [1.0, 2.0, 0.0], "normal": [0.0, -0.6, 0.8]}}
        self.assertIsNone(section_line("section-perspective", tilted))
        self.assertIsNone(section_line("plan", PLAN))

    def test_the_mark_is_where_the_plane_crosses_the_plans_window_in_its_paper_mm(self) -> None:
        mark = section_mark("section-a", "A", section_line("section", SECTION), "plan-a", PLAN, "meter")
        self.assertEqual(mark.view_id, "plan-a")
        self.assertEqual((mark.start_mm, mark.end_mm), ((80.0, 30.0), (0.0, 30.0)))
        self.assertEqual((mark.look_mm, mark.label), ((0.0, -1.0), "A"))

    def test_a_plane_outside_the_window_is_refused_by_name(self) -> None:
        outside = ([2.0, 5.0, 0.0], [0, 1, 0])
        with self.assertRaises(SheetLayoutError) as raised:
            section_mark("section-a", "A", outside, "plan-a", PLAN, "meter")
        self.assertEqual(raised.exception.code, "DRAWING_SECTION_MARK_OUTSIDE")
        self.assertEqual(str(raised.exception),
                         "Section A (section-a) does not cross the plan's window; mark it on a plan it cuts.")


class SceneTests(unittest.TestCase):
    def test_the_scene_places_each_recipe_view_with_its_own_marks(self) -> None:
        import reportlab
        from monkeydiagram.rendering.svg import DrawingMark

        fonts = {"normal": Path(reportlab.__file__).parent / "fonts" / "Vera.ttf",
                 "bold": Path(reportlab.__file__).parent / "fonts" / "VeraBd.ttf"}
        recipe = {
            "views": [{"id": "plan-a", "sizeMm": [120.0, 80.0], "placeMm": [20.0, 30.0], "title": "PLAN",
                       "subtitle": "Horizontal cut at Z +1.200 m, looking down", "scaleLabel": "1:50"}],
            "paperSizeMm": [420.0, 297.0], "title": "PAVILION", "sheetNumber": "A3-01", "subtitle": "",
            "notes": ["Synthetic."], "sourceText": "Source: pavilion; lengths in metres",
        }
        placed = {"plan-a": (DrawingMark(False, "visible", "object", ((0.0, 0.0), (120.0, 80.0)), 0.5, (), 0),)}
        (scene,) = view_sheet_scene("arch364-technical", recipe, placed, (), fonts).scenes
        self.assertEqual(scene.number, "A3-01")
        self.assertEqual(tuple(round(value, 6) for value in scene.size_mm), (420.0, 297.0))


if __name__ == "__main__":
    unittest.main()
