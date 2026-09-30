"""The two retained sheet styles use exact-scale source projections and shared output."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import unittest

from monkeycad.backends.occt.kernel import occt_available
from monkeycad.backends.occt.step import StepEntry
from monkeycad.backends.occt.projection import OcctDrawingPolyline, project_occt_lines
from monkeydiagram.documentation.styles import compose_review_sheet, drawing_style, list_drawing_styles
from monkeydiagram.rendering.paper import render_dxf, render_pdf


def fonts():
    import reportlab
    root = Path(reportlab.__file__).parent / "fonts"
    return {"normal": root / "Vera.ttf", "bold": root / "VeraBd.ttf"}


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class DrawingStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt
        shape = BRepPrimAPI_MakeBox(gp_Pnt(-.8, -.19, 0), 1.6, .38, .8).Shape()
        entry = StepEntry("body", (), None, shape)
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


MM = 25.4 / 72


def _mark(points, *, polygon=False, stroke_mm=0.25, dash_mm=(), grey=0, group="visible"):
    from monkeydiagram.rendering.svg import DrawingMark
    return DrawingMark(polygon, group, "object", tuple(points), 0.0 if polygon else stroke_mm, tuple(dash_mm), grey)


class ViewSheetTests(unittest.TestCase):
    """Caller-placed drawings on one sheet: each at its own paper size, marks moved and never redrawn."""

    def views(self, **changes):
        from monkeydiagram.documentation.styles import SheetView
        plan = SheetView(view_id="plan", size_mm=(120.0, 80.0), place_mm=(20.0, 30.0), title="PLAN",
                         subtitle="Horizontal cut at Z +1.200 m, looking down", scale_label="1:75",
                         marks=(_mark(((0.0, 0.0), (120.0, 80.0)), stroke_mm=0.5, dash_mm=(2.0, 1.0)),
                                _mark(((10.0, 10.0), (20.0, 10.0), (20.0, 20.0)), polygon=True, grey=128,
                                      group="section-hatch")))
        section = SheetView(view_id="section-a", size_mm=(120.0, 50.0), place_mm=(20.0, 140.0), title="SECTION A-A",
                            subtitle="Vertical cut at Y -3.048 m, looking +Y", scale_label="1:75",
                            marks=(_mark(((0.0, 50.0), (120.0, 50.0)), stroke_mm=0.35, group="section"),))
        values = {"plan": plan, "section-a": section}
        for name, view in changes.items():
            values[name] = view
        return tuple(view for view in values.values() if view is not None)

    def compose(self, **kwargs):
        from monkeydiagram.documentation.styles import compose_view_sheet
        values = dict(style_id="arch364-technical", paper_size_mm=(420.0, 297.0), views=self.views(),
                      title="SYNTHETIC PAVILION", sheet_number="A3-01", font_mapping=fonts(),
                      source_text="Source: pavilion.skp, sha256 0eedb1d8; lengths in metres")
        return compose_view_sheet(**{**values, **kwargs})

    def test_each_drawing_keeps_its_own_paper_size_pens_and_marks_where_it_is_placed(self):
        from pypdf import PdfReader
        canvas = self.compose()
        (scene,) = canvas.scenes
        self.assertEqual(scene.number, "A3-01")
        self.assertEqual(tuple(round(v, 6) for v in scene.size_mm), (420.0, 297.0))
        paths = [p for p in scene.primitives if p.kind == "path"]
        diagonal = next(p for p in paths if abs(p.data["line_width_pt"] * MM - 0.5) < 1e-9)
        (_, start), (_, end) = diagonal.data["commands"]
        # Paper mm from the top-left become PDF points from the bottom-left: (20, 30) is (20, 267).
        self.assertAlmostEqual(start[0] * MM, 20.0, places=9)
        self.assertAlmostEqual(start[1] * MM, 297.0 - 30.0, places=9)
        self.assertAlmostEqual(end[0] * MM, 140.0, places=9)
        self.assertAlmostEqual(end[1] * MM, 297.0 - 110.0, places=9)
        self.assertEqual(tuple(round(v * MM, 9) for v in diagonal.data["dash_pt"]), (2.0, 1.0))
        poche = next(p for p in paths if p.data["fill"])
        self.assertEqual((poche.data["stroke"], poche.data["fill_mode"]), (False, 0))
        self.assertAlmostEqual(poche.data["fill_color"][0], 128 / 255)
        text = PdfReader(BytesIO(render_pdf(canvas))).pages[0].extract_text()
        for expected in ("PLAN", "SECTION A-A", "1:75", "SYNTHETIC PAVILION", "A3-01", "Vertical cut at Y -3.048 m",
                         "pavilion.skp"):
            self.assertIn(expected, text)
        self.assertEqual(render_pdf(self.compose()), render_pdf(canvas), "the same placement gives the same sheet")

    def test_a_section_line_is_drawn_on_the_plan_it_names(self):
        from monkeydiagram.documentation.styles import SheetSectionMark
        plain = self.compose().scenes[0].primitives
        mark = SheetSectionMark(view_id="plan", start_mm=(0.0, 60.0), end_mm=(120.0, 60.0), look_mm=(0.0, -1.0), label="A")
        marked = self.compose(section_marks=(mark,)).scenes[0].primitives
        added = [p for p in marked if p not in plain]
        labels = [p.data["text"] for p in added if p.kind == "text"]
        self.assertEqual(labels, ["A", "A"])
        trace = [p for p in added if p.kind == "path" and p.data["dash_pt"]]
        self.assertEqual(len(trace), 1)
        ys = {round(point[1] * MM, 6) for command in trace[0].data["commands"] for point in command[1:]}
        self.assertEqual(ys, {297.0 - 90.0}, "the trace runs across the plan at its own paper position")
        with self.assertRaisesRegex(ValueError, "no view"):
            self.compose(section_marks=(SheetSectionMark("elsewhere", (0, 0), (1, 0), (0, 1), "B"),))

    def test_placement_off_the_paper_over_another_view_or_the_title_strip_is_refused_by_name(self):
        from monkeydiagram.documentation.styles import SheetView
        plan = self.views()[0]
        for changes, words in (
            ({"plan": SheetView(**{**_fields(plan), "place_mm": (350.0, 30.0)})}, "plan"),
            ({"section-a": SheetView(**{**_fields(self.views()[1]), "place_mm": (60.0, 90.0)})}, "overlaps"),
            ({"section-a": SheetView(**{**_fields(self.views()[1]), "place_mm": (20.0, 230.0)})}, "title strip"),
        ):
            with self.subTest(words=words), self.assertRaisesRegex(ValueError, words):
                self.compose(views=self.views(**changes))
        for values, words in (({"paper_size_mm": (0.0, 297.0)}, "paper"), ({"views": ()}, "at least one"),
                              ({"notes": ("A long note that keeps going " * 40,)}, "title strip")):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, words):
                self.compose(**values)

    def test_the_same_scene_is_one_pdf_svg_and_dxf(self):
        import ezdxf
        from io import StringIO
        from xml.etree import ElementTree
        from monkeydiagram.rendering.paper import render_svg
        canvas = self.compose()
        svg = ElementTree.fromstring(render_svg(canvas))
        self.assertEqual((svg.get("width"), svg.get("height")), ("420mm", "297mm"))
        texts = [element.text for element in svg.iter("{http://www.w3.org/2000/svg}text")]
        self.assertIn("SECTION A-A", texts)
        dxf = ezdxf.read(StringIO(render_dxf(canvas).decode()))
        layout = dxf.layouts.get("A3-01")
        self.assertTrue(layout.query("LINE LWPOLYLINE"))
        self.assertIn("SECTION A-A", [entity.dxf.text for entity in layout.query("TEXT")])


def _fields(view):
    return {name: getattr(view, name) for name in ("view_id", "size_mm", "marks", "place_mm", "title", "subtitle",
                                                  "scale_label")}


if __name__ == "__main__":
    unittest.main()
