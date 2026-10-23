"""The ink a drawn page shows under a mark, object by object, inside the mark and inside its frame."""

from __future__ import annotations

from io import BytesIO
import math
import unittest

from monkeydiagram.rendering.marks import (
    AREA_ZONE, CORE, FREEHAND, OUTLINE, POINT_ZONE, STROKE, STROKE_ZONE, TIP, ObjectInk, PageMarkError,
    id_image_under_marks, ink_under_mark, page_ink,
)
from monkeydiagram.rendering.svg import DrawingMark

# A 20 x 2 band crossing the page, a 4 x 4 square, and a single line, in page units.
BAND = ObjectInk("obj-band", "floor", lines=(((0, 10), (20, 10), (20, 12), (0, 12), (0, 10)),))
SQUARE = ObjectInk("obj-square", "frame", lines=(((30, 4), (34, 4), (34, 8), (30, 8), (30, 4)),))
WIRE = ObjectInk("obj-wire", None, lines=(((30, 20), (40, 20)),))
INK = (BAND, SQUARE, WIRE)


def circle(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(i * math.pi / 32), cy + r * math.sin(i * math.pi / 32)) for i in range(64)]


class InkUnderMarkTests(unittest.TestCase):
    def by_object(self, ink):
        return {row.object_id: row for row in ink.objects}

    def test_a_band_through_a_circle_reaches_its_core_although_it_runs_on_beyond_it(self) -> None:
        ink = ink_under_mark(circle(10, 11, 4), OUTLINE, 0.1, INK)
        self.assertEqual(ink.zone, AREA_ZONE)
        self.assertAlmostEqual(ink.core_radius, 4 * (1 - CORE), delta=0.05)
        [band] = ink.objects
        self.assertEqual(band.object_id, "obj-band")
        self.assertGreater(band.area_core, 0)
        self.assertLess(band.area_in, 40 / 2, "only the part of the 20 x 2 band inside the circle")
        self.assertEqual((band.area_total, band.line_total), (40.0, 44.0))
        self.assertGreater(band.line_core, 0)

    def test_a_band_at_the_rim_and_a_grazed_corner_stay_out_of_the_core(self) -> None:
        [band] = ink_under_mark(circle(10, 15, 4), OUTLINE, 0.1, INK).objects
        self.assertGreater(band.area_in, 0)
        self.assertEqual((band.area_core, band.line_core), (0.0, 0.0))
        [square] = ink_under_mark(circle(36, 10, 3), OUTLINE, 0.1, INK).objects
        self.assertEqual(square.area_core, 0.0)

    def test_an_open_line_without_area_is_measured_by_its_length(self) -> None:
        ink = ink_under_mark([(32, 20), (38, 20)], STROKE, 0.5, INK)
        self.assertEqual(ink.zone, STROKE_ZONE)
        [wire] = ink.objects
        self.assertEqual((wire.object_id, wire.component, wire.area_in), ("obj-wire", None, 0.0))
        self.assertAlmostEqual(wire.line_in, 7.0, places=6)

    def test_freehand_is_an_outline_only_when_it_returns_to_its_start(self) -> None:
        loop = circle(10, 11, 4)[:-2]
        self.assertEqual(ink_under_mark(loop, FREEHAND, 0.1, INK).zone, AREA_ZONE)
        self.assertEqual(ink_under_mark(loop[:30], FREEHAND, 0.1, INK).zone, STROKE_ZONE)

    def test_a_tip_inside_a_shape_is_on_it_and_one_out_of_reach_is_on_nothing(self) -> None:
        ink = ink_under_mark([(0, 0), (32, 6)], TIP, 0.5, INK)
        self.assertEqual(ink.zone, POINT_ZONE)
        self.assertEqual([(row.object_id, row.nearness) for row in ink.objects], [("obj-square", 1.0)])
        self.assertEqual(ink_under_mark([(25, 30)], TIP, 0.5, INK).objects, ())
        near = ink_under_mark([(35, 20.25)], TIP, 0.5, INK)
        self.assertEqual([(row.object_id, row.nearness) for row in near.objects], [("obj-wire", 0.5)])

    def test_unmeasurable_marks_are_refused(self) -> None:
        for points, tool, reach in (([], OUTLINE, 1.0), ([(0, 0), (1, 1)], "lasso", 1.0),
                                    ([(0, math.nan)], TIP, 1.0), ([(0, 0), (1, 1)], STROKE, 0.0)):
            with self.subTest(tool=tool, reach=reach), self.assertRaises(PageMarkError):
                ink_under_mark(points, tool, reach, INK)

    def test_page_ink_groups_a_drawings_marks_by_object_at_their_paper_place(self) -> None:
        marks = (DrawingMark(False, "visible", "obj-a", ((0, 0), (1, 0)), 0.25, (), 0, "wall"),
                 DrawingMark(True, "section-hatch", "obj-a", ((0, 0), (1, 0), (1, 1)), 0.0, (), 0),
                 DrawingMark(False, "visible", None, ((5, 5), (6, 6)), 0.25, (), 0))
        [row] = page_ink(marks, place=(10.0, 20.0))
        self.assertEqual((row.object_id, row.component), ("obj-a", "wall"))
        self.assertEqual(row.lines, (((10.0, 20.0), (11.0, 20.0)),))
        self.assertEqual(row.regions, (((10.0, 20.0), (11.0, 20.0), (11.0, 21.0)),))

    def test_page_ink_leaves_out_hidden_lines_and_hatch_strokes(self) -> None:
        marks = (DrawingMark(False, "hidden", "obj-behind", ((0, 0), (1, 0)), 0.13, (0.2, 0.1), 0),
                 DrawingMark(False, "section-hatch", "obj-cut", ((0, 0), (1, 1)), 0.1, (), 0),
                 DrawingMark(True, "section-hatch", "obj-cut", ((0, 0), (1, 0), (1, 1)), 0.0, (), 0),
                 DrawingMark(False, "section", "obj-cut", ((0, 0), (1, 0), (1, 1), (0, 0)), 0.35, (), 0))
        [cut] = page_ink(marks)
        self.assertEqual(cut.object_id, "obj-cut")
        self.assertEqual(cut.lines, (((0, 0), (1, 0), (1, 1), (0, 0)),), "the cut line, not its hatch")
        self.assertEqual(len(cut.regions), 1, "the poché is the cut's area")


class IdImageTests(unittest.TestCase):
    legend = ({"key": "obj-slab", "color": "#1F77B4", "objects": 1, "pixels": 0},
              {"key": "A-FURNITURE", "color": "#2CA02C", "objects": 3, "pixels": 0})

    def png(self) -> bytes:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (40, 20), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 15, 39, 19), fill=(0x1F, 0x77, 0xB4))
        draw.rectangle((2, 2, 8, 8), fill=(0x2C, 0xA0, 0x2C))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_pixels_under_each_mark_name_their_object_or_no_object(self) -> None:
        slab, furniture, blank = id_image_under_marks(
            [(circle(20, 17, 2), OUTLINE, 0.5), (circle(5, 5, 3), OUTLINE, 0.5), (circle(30, 6, 3), OUTLINE, 0.5)],
            self.png(), self.legend, {"obj-slab": "floor"})
        [row] = slab.objects
        self.assertEqual((row.object_id, row.component), ("obj-slab", "floor"))
        self.assertGreater(row.area_in, 0)
        [row] = furniture.objects
        self.assertIsNone(row.object_id, "a layer colour names no single object")
        self.assertEqual(blank.objects, ())

    def test_a_legend_row_without_a_colour_is_refused(self) -> None:
        with self.assertRaises(PageMarkError):
            id_image_under_marks([(circle(5, 5, 3), OUTLINE, 0.5)], self.png(), ({"key": "obj-slab"},))


if __name__ == "__main__":
    unittest.main()
