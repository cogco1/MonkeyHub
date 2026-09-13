"""The two retained sheet styles use exact-scale source projections and shared output."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import unittest

from archflow.adapters import occt_backend
from archflow.adapters.cad_execution import OcctDrawingPolyline, project_occt_lines
from monkeydiagram.documentation.styles import compose_review_sheet, drawing_style, list_drawing_styles
from monkeydiagram.drawing_output import render_dxf, render_pdf


def fonts():
    import reportlab
    root = Path(reportlab.__file__).parent / "fonts"
    return {"normal": root / "Vera.ttf", "bold": root / "VeraBd.ttf"}


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class DrawingStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt
        shape = BRepPrimAPI_MakeBox(gp_Pnt(-.8, -.19, 0), 1.6, .38, .8).Shape()
        entry = occt_backend.StepEntry("body", (), None, shape)
        cls.views = {name: project_occt_lines((entry,), object_ids=("body",), origin=(0, 0, 0), right=right, up=up, linear_deflection=.0001)
                     for name, right, up in (("front", (1, 0, 0), (0, 0, 1)),
                                              ("right", (0, 1, 0), (0, 0, 1)),
                                              ("top", (1, 0, 0), (0, 1, 0)))}
        cls.bounds = {"body": {"min": [-.8, -.19, 0], "max": [.8, .19, .8]}}

    def compose(self, style="arch400-white", **kwargs):
        values = dict(style_id=style, views=self.views, bounds=self.bounds, length_unit="meter", title="MODEL REVIEW",
                      scale_denominator=drawing_style(style)["default_scale_denominator"], font_mapping=fonts(),
                      notes=("Dimensions follow the candidate geometry.",))
        return compose_review_sheet(**{**values, **kwargs})

    def test_two_real_styles_produce_readable_vector_pdf_and_editable_dxf(self):
        from pypdf import PdfReader
        import ezdxf
        from io import StringIO
        for style in list_drawing_styles():
            with self.subTest(style=style["id"]):
                canvas = self.compose(style["id"])
                pdf = PdfReader(BytesIO(render_pdf(canvas)))
                self.assertEqual(len(pdf.pages), 1)
                page = pdf.pages[0]
                for actual, expected in zip((float(page.mediabox.width), float(page.mediabox.height)), style["paperSizeMm"]):
                    self.assertAlmostEqual(actual * 25.4 / 72, expected, places=3)
                text = page.extract_text()
                for expected in ("FRONT ELEVATION", "RIGHT ELEVATION", "TOP PROJECTION", "1600", "800", "380"):
                    self.assertIn(expected, text)
                dxf = ezdxf.read(StringIO(render_dxf(canvas).decode()))
                self.assertTrue(dxf.layouts.get("01").query("LWPOLYLINE LINE"))
                self.assertTrue(dxf.layouts.get("01").query("TEXT"))

    def test_unit_conversion_keeps_every_paper_primitive_at_the_same_scale(self):
        reference = self.compose().scenes[0].primitives
        for unit, multiplier in (("millimeter", 1000), ("foot", 1000 / 304.8), ("inch", 1000 / 25.4)):
            with self.subTest(unit=unit):
                lines = {key: tuple(OcctDrawingPolyline(row.object_id, row.kind, tuple((u * multiplier, v * multiplier) for u, v in row.points))
                                    for row in value) for key, value in self.views.items()}
                bounds = {key: {name: [v * multiplier for v in point] for name, point in value.items()} for key, value in self.bounds.items()}
                actual = self.compose(views=lines, bounds=bounds, length_unit=unit).scenes[0].primitives
                self.assertEqual(len(actual), len(reference))
                for a, b in zip(actual, reference):
                    for x, y in zip(a.bounds_mm, b.bounds_mm):
                        self.assertAlmostEqual(x, y, places=7)

    def test_scale_refusal_uses_the_same_layout_before_geometry_projection(self):
        for views in (self.views, {name: () for name in self.views}):
            with self.subTest(preflight=not any(views.values())), self.assertRaisesRegex(ValueError, "does not fit"):
                self.compose(views=views, scale_denominator=1)
        self.assertTrue(self.compose(views={name: () for name in self.views}).scenes)

    def test_style_lookup_is_detached_and_invalid_sources_are_refused(self):
        listing = list_drawing_styles()
        listing[0]["paperSizeMm"][0] = 1
        self.assertEqual(drawing_style("arch400-white")["paperSizeMm"][0], 558.8)
        for values in ({"outline_object_ids": ("unknown",)}, {"length_unit": "yards"},
                       {"bounds": {"body": {"min": [0, 0, 0], "max": [1, float("nan"), 1]}}}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.compose(**values)

    def test_outline_option_keeps_top_opening_lines_while_reducing_elevation_facets(self):
        # A closed 2x2 projected face split into two facets; the open top has an
        # inner perimeter which must stay in the sheet when elevation edges simplify.
        outer = ((-.5, -.5), (.5, -.5), (.5, .5), (-.5, .5), (-.5, -.5))
        inner = ((-.2, -.2), (.2, -.2), (.2, .2), (-.2, .2), (-.2, -.2))
        perimeter = OcctDrawingPolyline("body", "visible", outer)
        views = {"front": (perimeter, OcctDrawingPolyline("body", "visible", ((-.5, -.5), (.5, .5)))),
                 "right": (perimeter,), "top": (perimeter, OcctDrawingPolyline("body", "visible", inner))}
        bounds = {"body": {"min": [-.5, -.5, -.5], "max": [.5, .5, .5]}}
        simplified = self.compose(views=views, bounds=bounds, outline_object_ids=("body",), scale_denominator=10)
        top_paths = [p for p in simplified.scenes[0].primitives if p.kind == "path" and abs(p.data["line_width_pt"] * 25.4 / 72 - .09) < 1e-8]
        self.assertEqual(len(top_paths), 2)
        # Both original top contours are retained, not replaced with one filled disk.
        self.assertTrue(all(not p.data["fill"] for p in top_paths))
        outlines = [p for p in simplified.scenes[0].primitives if p.kind == "path" and abs(p.data["line_width_pt"] * 25.4 / 72 - .35) < 1e-8]
        self.assertTrue(outlines)
        for outline in outlines:
            points = [command[1] for command in outline.data["commands"]]
            for a, b in zip(points, points[1:]):
                self.assertLess(min(abs(b[0] - a[0]), abs(b[1] - a[1])), .001,
                                "the interior diagonal must not survive as a long elevation edge")


if __name__ == "__main__":
    unittest.main()
