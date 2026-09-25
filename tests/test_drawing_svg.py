"""Deterministic SVG of drawing polylines, and the PNG rendered from those SVG bytes."""

from __future__ import annotations

import unittest
from io import BytesIO
from xml.etree import ElementTree

from monkeydiagram.drawing_svg import (
    CleanupReport,
    DrawingSvgError,
    clean_drawing,
    crop_polylines,
    drawing_svg,
    dimension_placement_fits,
    render_svg_png,
    svg_objects,
)
from archflow.adapters.occt_backend import OcctDrawingPolyline, OcctDrawingRegion

SVG = "{http://www.w3.org/2000/svg}"


def _line(name: str, kind: str, *points) -> OcctDrawingPolyline:
    return OcctDrawingPolyline(name, kind, tuple((float(x), float(y)) for x, y in points))


class CropTests(unittest.TestCase):
    def test_segments_are_clipped_split_and_dropped_at_the_window(self) -> None:
        crossing = _line("wall", "visible", (-2, 1), (6, 1))
        leaving_and_returning = _line("wall", "visible", (1, 1), (1, 9), (3, 9), (3, 1))
        outside = _line("post", "visible", (10, 0), (11, 0))
        kept = crop_polylines((crossing, leaving_and_returning, outside), (0, 0, 4, 4))
        self.assertEqual(kept, (
            _line("wall", "visible", (0, 1), (4, 1)),
            _line("wall", "visible", (1, 1), (1, 4)),
            _line("wall", "visible", (3, 4), (3, 1)),
        ))
        self.assertNotIn("post", {line.object_id for line in kept})

    def test_a_window_that_is_not_a_window_is_refused(self) -> None:
        for crop in ((0, 0, 0, 4), (4, 0, 0, 4), (0, 0, 4), (0, float("nan"), 4, 4)):
            with self.subTest(crop=crop), self.assertRaises(DrawingSvgError):
                crop_polylines((_line("wall", "visible", (0, 0), (1, 1)),), crop)


class CleanupTests(unittest.TestCase):
    """clean_drawing on synthetic lines at 0.05 mm on a 1:100 sheet: 5 mm in metres."""

    tolerance = 0.005
    # A slab cut at the plan plane: its region is the material the section lines outline.
    regions = (OcctDrawingRegion("slab", (((0.0, 2.0), (4.0, 2.0), (4.0, 3.0), (0.0, 3.0), (0.0, 2.0)),)),)
    lines = (
        # One wall face in pieces: a 3 mm gap and an exact joint continue it; a 0.9 degree turn does not.
        _line("wall", "visible", (0, 0), (1, 0)),
        _line("wall", "visible", (1.003, 0), (2, 0)),
        _line("wall", "visible", (2, 0), (3, 0.0156)),
        _line("wall", "visible", (3, 1), (2.5, 1)),
        _line("wall", "visible", (2.5, 1), (1, 1)),
        # A return meeting the joint square-on, and a 2 mm stub.
        _line("wall", "visible", (1, 0), (1, 1)),
        _line("wall", "visible", (5, 5), (5.002, 5)),
        # The slab's cut edge, and the clipped slab's top edge drawn again in two pieces 0.1 mm off it.
        _line("slab", "section", (0, 2), (4, 2), (4, 3), (0, 3), (0, 2)),
        _line("slab", "visible", (0, 2.0001), (2, 2.0001)),
        _line("slab", "visible", (2, 2.0001), (4, 2.0001)),
        # A slab edge running past the cut is only partly on it: it stays.
        _line("slab", "visible", (0, 1.9999), (6, 1.9999)),
        # Behind the wall face: a hidden line of another object under the visible one.
        _line("rear", "hidden", (0.2, 0), (0.8, 0)),
        # A footing under the cut: inside the cut material, and leaving it.
        _line("footing", "hidden", (1.2, 2.5), (1.8, 2.5)),
        _line("footing", "hidden", (1.2, 2.5), (1.2, 5.5)),
    )

    def test_cleanup_merges_collinear_drops_micro_and_prefers_cut_edges(self) -> None:
        cleaned, report = clean_drawing(self.lines, self.regions, tolerance=self.tolerance)
        self.assertEqual(cleaned, (
            _line("footing", "hidden", (1.2, 2.5), (1.2, 5.5)),
            _line("slab", "section", (0, 2), (4, 2), (4, 3), (0, 3), (0, 2)),
            _line("slab", "visible", (0, 1.9999), (6, 1.9999)),
            # Joined pieces keep every vertex; the 3 mm gap is bridged, nothing moves.
            _line("wall", "visible", (0, 0), (1, 0), (1.003, 0), (2, 0)),
            _line("wall", "visible", (1, 0), (1, 1)),
            _line("wall", "visible", (1, 1), (2.5, 1), (3, 1)),
            _line("wall", "visible", (2, 0), (3, 0.0156)),
        ))
        self.assertEqual(report, CleanupReport(
            tolerance=0.005, input_lines=14, output_lines=7, micro=1, collinear=3, cut_precedence=1,
            duplicate=1, hidden_under_cut=1))
        self.assertEqual(report.input_lines - report.output_lines,
                         report.micro + report.collinear + report.cut_precedence + report.duplicate + report.hidden_under_cut)
        self.assertEqual(report.to_dict(), {
            "tolerance": 0.005, "input_lines": 14, "output_lines": 7, "micro": 1, "collinear": 3,
            "cut_precedence": 1, "duplicate": 1, "hidden_under_cut": 1})
        # The tolerance is paper space: a 30 mm post is drawn at 1:100 and is micro at 1:1000 (50 mm),
        # while a 0.9 degree turn stays a turn at any scale.
        post = _line("post", "visible", (8, 8), (8.03, 8))
        self.assertIn(post, clean_drawing(self.lines + (post,), self.regions, tolerance=self.tolerance)[0])
        coarse, coarse_report = clean_drawing(self.lines + (post,), self.regions, tolerance=0.05)
        self.assertNotIn(post, coarse)
        self.assertEqual(coarse_report.micro, 2)
        self.assertIn(_line("wall", "visible", (2, 0), (3, 0.0156)), coarse)

    def test_cleanup_is_deterministic_and_idempotent(self) -> None:
        cleaned, report = clean_drawing(self.lines, self.regions, tolerance=self.tolerance)
        for order in (tuple(reversed(self.lines)), self.lines[1::2] + self.lines[::2], self.lines + self.lines):
            with self.subTest(order=order[:2]):
                self.assertEqual(clean_drawing(order, self.regions, tolerance=self.tolerance), (cleaned, report))
        again, second = clean_drawing(cleaned, self.regions, tolerance=self.tolerance)
        self.assertEqual(again, cleaned, "cleaning a clean drawing changes nothing")
        self.assertEqual(second, CleanupReport(0.005, 7, 7, 0, 0, 0, 0, 0))
        # A hidden line is dropped under the cut only when regions are given; without hidden lines nothing hides.
        without_regions, _ = clean_drawing(self.lines, (), tolerance=self.tolerance)
        self.assertIn(_line("footing", "hidden", (1.2, 2.5), (1.8, 2.5)), without_regions)
        self.assertEqual(clean_drawing((), (), tolerance=self.tolerance), ((), CleanupReport(0.005, 0, 0, 0, 0, 0, 0, 0)))

    def test_cleanup_refuses_what_it_cannot_clean(self) -> None:
        for tolerance in (0, -1, float("nan"), True, "5"):
            with self.subTest(tolerance=tolerance), self.assertRaises(DrawingSvgError):
                clean_drawing(self.lines, self.regions, tolerance=tolerance)
        for bad in (_line("wall", "hatch", (0, 0), (1, 0)), _line("wall", "visible", (0, 0)),
                    _line("wall", "visible", (0, 0), (float("inf"), 0)), ("wall", "visible", ((0, 0), (1, 0)))):
            with self.subTest(line=bad), self.assertRaises(DrawingSvgError):
                clean_drawing((bad,), (), tolerance=self.tolerance)
        with self.assertRaises(DrawingSvgError):
            clean_drawing(self.lines, (OcctDrawingRegion("slab", (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)),)),),
                          tolerance=self.tolerance)


class SvgTests(unittest.TestCase):
    lines = (
        _line("wall", "visible", (0, 0), (4, 0), (4, 3), (0, 3), (0, 0)),
        _line("rear", "hidden", (1, 1), (2, 1), (2, 2)),
        _line("wall", "hidden", (0, 0), (4, 3)),
        _line("beyond", "visible", (10, 10), (11, 11)),
    )

    def render(self, hidden: bool) -> bytes:
        return drawing_svg(self.lines, crop_uv=(-1, -1, 5, 4), unit="meter", scale_denominator=100,
                           hidden_lines=hidden, title="test elevation")

    def test_the_same_input_gives_the_same_bytes_with_sheet_size_and_object_ids(self) -> None:
        svg = self.render(False)
        self.assertEqual(svg, self.render(False))
        root = ElementTree.fromstring(svg)
        self.assertEqual((root.get("width"), root.get("height"), root.get("viewBox")),
                         ("60.0000mm", "50.0000mm", "0 0 6.0000 5.0000"))
        self.assertEqual((root.get("data-unit"), root.get("data-scale"), root.get("data-hidden-lines")),
                         ("meter", "1:100", "false"))
        self.assertEqual([group.get("id") for group in root.findall(f"{SVG}g")], ["visible"])
        polylines = root.findall(f".//{SVG}polyline")
        self.assertEqual([p.get("data-object") for p in polylines], ["wall"])
        # u = 0 sits 1 unit right of the crop edge; v = 3 sits 1 unit below the crop top (SVG y runs down).
        self.assertEqual(polylines[0].get("points"), "1.0000,4.0000 5.0000,4.0000 5.0000,1.0000 1.0000,1.0000 1.0000,4.0000")
        self.assertEqual(svg_objects(svg), ("wall",))
        self.assertNotIn(b"beyond", svg)

    def test_hidden_lines_are_drawn_dashed_under_the_visible_lines_only_when_asked(self) -> None:
        svg = self.render(True)
        root = ElementTree.fromstring(svg)
        groups = root.findall(f"{SVG}g")
        self.assertEqual([group.get("id") for group in groups], ["hidden", "visible"])
        self.assertEqual(groups[0].get("stroke-dasharray"), "0.2000 0.1000")
        self.assertEqual([p.get("data-object") for p in groups[0].findall(f"{SVG}polyline")], ["rear", "wall"])
        self.assertEqual(svg_objects(svg), ("rear", "wall"))
        self.assertNotEqual(svg, self.render(False))

    def test_invalid_options_are_refused(self) -> None:
        for kwargs in (
            dict(unit="furlong"), dict(scale_denominator=0), dict(scale_denominator=True), dict(title=""),
            dict(crop_uv=(0, 0, 0, 1)),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(DrawingSvgError):
                drawing_svg(self.lines, **{**dict(crop_uv=(-1, -1, 5, 4), unit="meter", scale_denominator=100,
                                                  hidden_lines=False, title="t"), **kwargs})


class PngTests(unittest.TestCase):
    def test_the_png_is_rendered_from_the_svg_polylines_at_its_physical_size(self) -> None:
        from PIL import Image

        svg = drawing_svg((_line("wall", "visible", (0, 1), (4, 1)),), crop_uv=(0, 0, 4, 2), unit="meter",
                          scale_denominator=100, hidden_lines=False, title="t")
        png = render_svg_png(svg, dots_per_inch=254)
        self.assertEqual(png, render_svg_png(svg, dots_per_inch=254))
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.size, (400, 200))
            pixels = image.load()
            self.assertLess(pixels[200, 100], 128, "the line at v = 1 is drawn mid-height")
            self.assertEqual(pixels[200, 50], 255)
            self.assertEqual(pixels[200, 150], 255)

    def test_dashed_hidden_lines_leave_gaps(self) -> None:
        from PIL import Image

        svg = drawing_svg((_line("rear", "hidden", (0, 1), (4, 1)),), crop_uv=(0, 0, 4, 2), unit="meter",
                          scale_denominator=100, hidden_lines=True, title="t")
        png = render_svg_png(svg, dots_per_inch=254)
        with Image.open(BytesIO(png)) as image:
            row = [image.load()[x, 100] for x in range(400)]
        self.assertTrue(any(value < 128 for value in row))
        self.assertTrue(any(value == 255 for value in row[10:390]), "a dashed line has gaps")

    def test_foreign_or_broken_svg_is_refused(self) -> None:
        for svg in (b"<svg/>", b"not xml",
                    b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 1 1"><rect/></svg>'):
            with self.subTest(svg=svg), self.assertRaises(DrawingSvgError):
                render_svg_png(svg)


class CutPlanSvgTests(unittest.TestCase):
    graphics = {"cutLineMm": 0.35, "visibleLineMm": 0.18, "hatchSpacingMm": 2.0}

    def render(self, *, multiplier=1, unit="meter", dimensions=()):
        loops = (
            ((0, 0), (4, 0), (4, 4), (0, 4), (0, 0)),
            ((1, 1), (3, 1), (3, 3), (1, 3), (1, 1)),
        )
        loops = tuple(tuple((x * multiplier, y * multiplier) for x, y in loop) for loop in loops)
        lines = tuple(_line("wall", "section", *loop) for loop in loops)
        return drawing_svg(lines, crop_uv=tuple(v * multiplier for v in (-1, -1, 5, 5)), unit=unit,
                           scale_denominator=100, hidden_lines=False, title="cut-plan",
                           regions=(OcctDrawingRegion("wall", loops),), graphics=self.graphics, dimensions=dimensions)

    def test_section_hatch_keeps_hole_white_and_uses_paper_spacing_in_both_units(self):
        from PIL import Image, ImageChops, ImageStat

        svg = self.render()
        root = ElementTree.fromstring(svg)
        self.assertEqual(root.find(f"{SVG}g[@id='section']").get("stroke-width"), "0.0350")
        self.assertTrue(root.findall(f"{SVG}g[@id='section-hatch']/{SVG}polyline"))
        self.assertEqual(svg_objects(svg), ("wall",))
        png = render_svg_png(svg, dots_per_inch=254)
        mm_png = render_svg_png(self.render(multiplier=1000, unit="millimeter"), dots_per_inch=254)
        with Image.open(BytesIO(png)) as image, Image.open(BytesIO(mm_png)) as mm:
            self.assertEqual(image.size, (600, 600))
            self.assertEqual(image.size, mm.size)
            self.assertEqual(image.crop((210, 210, 390, 390)).getextrema(), (255, 255), "no hatch crosses the real hole")
            self.assertLess(image.crop((110, 110, 190, 190)).getextrema()[0], 128, "material is hatched")
            # SVG coordinate rounding may shift a single antialiased pixel; paper stroke widths and spacing remain identical.
            self.assertLess(ImageStat.Stat(ImageChops.difference(image, mm)).mean[0], 1)

    def test_resolved_dimension_text_and_marks_are_drawn_from_the_svg_and_unresolved_are_not(self):
        from PIL import Image

        resolved = {"id": "opening-width", "status": "resolved", "start": [1, 1], "end": [3, 1],
                    "value": 2, "label": "2000 mm", "offsetMm": -12}
        missing = {"id": "old-opening", "status": "missing", "label": "9999 BAD", "offsetMm": 8}
        svg = self.render(dimensions=(resolved, missing))
        self.assertEqual(svg, self.render(dimensions=(resolved, missing)))
        root = ElementTree.fromstring(svg)
        text = root.find(f".//{SVG}text")
        self.assertEqual(text.text, "2000 mm")
        self.assertNotIn(b"9999 BAD", svg)
        self.assertNotIn(b"old-opening", svg)
        png = render_svg_png(svg, dots_per_inch=254)
        x, y = (float(text.get(key)) * 100 for key in ("x", "y"))
        with Image.open(BytesIO(png)) as image:
            self.assertLess(image.crop((int(x - 80), int(y - 35), int(x + 80), int(y))).getextrema()[0], 128)
        text.text = ""
        self.assertNotEqual(png, render_svg_png(ElementTree.tostring(root), dots_per_inch=254),
                            "PNG labels must come from the supplied SVG, not from dimensions outside it")

    def test_invalid_graphics_or_resolved_coordinates_are_refused(self):
        for changed in ({"cutLineMm": 0}, {"hatchSpacingMm": float("nan")}, {"visibleLineMm": False}):
            with self.subTest(changed=changed), self.assertRaises(DrawingSvgError):
                drawing_svg((), crop_uv=(0, 0, 4, 4), unit="meter", scale_denominator=100,
                            hidden_lines=False, title="cut", graphics={**self.graphics, **changed})
        with self.assertRaises(DrawingSvgError):
            self.render(dimensions=({"id": "bad", "status": "resolved", "start": [9, 9], "end": [3, 1],
                                     "value": 2, "label": "2", "offsetMm": 5},))


    def test_complete_dimension_placement_checks_offset_ticks_and_actual_text_bounds(self):
        row = {"id": "door-width", "status": "resolved", "start": [1, 1], "end": [3, 1],
               "value": 2, "label": "2000 mm", "offsetMm": 8}
        crop = (0, 0, 4, 4)
        self.assertTrue(dimension_placement_fits(row, crop, 10))
        mm = {**row, "start": [1000, 1000], "end": [3000, 1000]}
        self.assertTrue(dimension_placement_fits(mm, (0, 0, 4000, 4000), .01))
        cases = (
            {**row, "offsetMm": 100},
            {**row, "offsetMm": -100},
            {**row, "start": [.05, 1], "end": [2, 1]},  # endpoints fit, but the left tick crosses the edge
            {**row, "start": [1, 3.8], "end": [3, 3.8], "offsetMm": 0},  # marks fit, glyphs above baseline do not
            {**row, "start": [3, 1], "end": [3.5, 1], "label": "200000000000 mm"},
        )
        for changed in cases:
            with self.subTest(row=changed):
                self.assertTrue(all(0 <= v <= 4 for p in (changed["start"], changed["end"]) for v in p),
                                "each source endpoint is valid; only the displayed placement leaves the crop")
                self.assertFalse(dimension_placement_fits(changed, crop, 10))
                with self.assertRaisesRegex(DrawingSvgError, "text or marks outside"):
                    drawing_svg((), crop_uv=crop, unit="meter", scale_denominator=100,
                                hidden_lines=False, title="plan", dimensions=(changed,))
        self.assertEqual(row["offsetMm"], 8, "fit checking cannot rewrite retained representation intent")
        with self.assertRaises(DrawingSvgError):
            dimension_placement_fits(row, crop, 0)


if __name__ == "__main__":
    unittest.main()
