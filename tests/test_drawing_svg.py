"""Deterministic SVG of drawing polylines, and the PNG rendered from those SVG bytes."""

from __future__ import annotations

import unittest
from io import BytesIO
from xml.etree import ElementTree

from archflow.adapters.drawing_svg import (
    DrawingSvgError,
    crop_polylines,
    drawing_svg,
    render_svg_png,
    svg_objects,
)
from archflow.adapters.occt_backend import OcctDrawingPolyline

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


if __name__ == "__main__":
    unittest.main()
