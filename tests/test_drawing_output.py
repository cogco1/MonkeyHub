"""PDF and native DXF preserve the same paper geometry and supplied model units."""

from importlib.util import find_spec
from io import StringIO
from pathlib import Path
import re
import unittest

from archflow.adapters.drawing_output import MM_PER_PT, PaperCanvas, render_dxf, render_pdf


DRAWING_DEPS = all(find_spec(name) is not None for name in ("reportlab", "fontTools", "ezdxf"))


@unittest.skipUnless(DRAWING_DEPS, "Install the drawings optional dependencies")
class DrawingOutputTests(unittest.TestCase):
    def setUp(self):
        import reportlab
        self.font = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        self.canvas = PaperCanvas(font_mapping={"Test": self.font})
        self.canvas.start_sheet("A01", (420, 297))
        self.canvas.setFont("Test", 12)

    def read_dxf(self, **kwargs):
        import ezdxf
        doc = ezdxf.read(StringIO(render_dxf(self.canvas, **kwargs).decode("utf-8")))
        audit = doc.audit()
        self.assertFalse(audit.has_errors)
        self.assertFalse(audit.has_fixes)
        return doc

    def test_explicit_sheets_keep_paper_size_and_do_not_invent_a_final_page(self):
        c = self.canvas
        c.drawString(30, 40, "SHEET A01")
        c.showPage()
        c.start_sheet("A02", (594, 420))
        c.setFont("Test", 12)
        c.drawString(30, 40, "SHEET A02")
        c.save()
        pdf = render_pdf(c)
        media = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf)
        self.assertEqual(len(media), 2)
        for actual, expected in zip(media, ((420, 297), (594, 420))):
            for value, mm in zip(actual, expected):
                self.assertAlmostEqual(float(value)*MM_PER_PT, mm, places=3)
        doc = self.read_dxf()
        self.assertEqual(doc.units, 4)
        self.assertEqual(list(doc.layouts.names()), ["Model", "A01", "A02"])
        for number, size in (("A01", (420, 297)), ("A02", (594, 420))):
            layout = doc.layouts.get(number)
            self.assertEqual(layout.dxf.paper_width, size[0])
            self.assertEqual(layout.dxf.paper_height, size[1])
            self.assertEqual(layout.dxf.plot_paper_units, 1)
            self.assertEqual(layout.dxf.scale_numerator/layout.dxf.scale_denominator, 1)
        self.assertEqual(len(doc.modelspace()), 0)

    def test_rotated_centered_text_keeps_glyph_bounds_baseline_and_cad_cap_height(self):
        c = self.canvas
        c.drawCentredString(0, 0, "H1200")
        original = c.scenes[0].primitives[-1]
        c.saveState()
        c.translate(100, 200)
        c.rotate(90)
        c.drawCentredString(0, 0, "H1200")
        c.restoreState()
        rotated = c.scenes[0].primitives[-1]
        x0, y0, x1, y1 = original.bounds_mm
        expected = (100*MM_PER_PT-y1, 200*MM_PER_PT+x0,
                    100*MM_PER_PT-y0, 200*MM_PER_PT+x1)
        for actual, value in zip(rotated.bounds_mm, expected):
            self.assertAlmostEqual(actual, value, places=8)
        doc = self.read_dxf()
        texts = list(doc.layouts.get("A01").query("TEXT"))
        self.assertEqual([t.dxf.text for t in texts], ["H1200", "H1200"])
        self.assertAlmostEqual(texts[1].dxf.rotation, 90)
        self.assertAlmostEqual(texts[1].dxf.insert.x, 100*MM_PER_PT)
        self.assertAlmostEqual(texts[1].dxf.insert.y, rotated.data["origin_pt"][1]*MM_PER_PT)
        from fontTools.ttLib import TTFont
        from fontTools.pens.boundsPen import BoundsPen
        with TTFont(self.font) as font:
            glyphs = font.getGlyphSet()
            pen = BoundsPen(glyphs)
            glyphs[font.getBestCmap()[ord("H")]].draw(pen)
            cap_mm = 12*MM_PER_PT*pen.bounds[3]/font["head"].unitsPerEm
        self.assertAlmostEqual(texts[1].dxf.height, cap_mm)
        c.line(0, 0, 10, 0)
        self.assertEqual(c.scenes[0].primitives[-1].data["commands"][-1], ("L", (10.0, 0.0)))

    @unittest.skipUnless(find_spec("fitz") is not None, "PyMuPDF is needed for independent PDF text readback")
    def test_pdf_readback_preserves_rotated_text_and_vector_line(self):
        import fitz
        c = self.canvas
        c.saveState()
        c.translate(100, 200)
        c.rotate(90)
        c.drawString(0, 0, "1200")
        c.line(0, 20, 50, 20)
        c.restoreState()
        pdf = fitz.open(stream=render_pdf(c), filetype="pdf")
        page = pdf[0]
        line = page.get_text("dict")["blocks"][0]["lines"][0]
        self.assertEqual(line["spans"][0]["text"], "1200")
        self.assertAlmostEqual(line["dir"][0], 0, places=7)
        self.assertAlmostEqual(line["dir"][1], -1, places=7)
        self.assertAlmostEqual(line["spans"][0]["origin"][0], 100, places=3)
        self.assertAlmostEqual(line["spans"][0]["origin"][1], page.rect.height-200, places=3)
        segment = page.get_drawings()[0]["items"][0]
        self.assertEqual(segment[0], "l")
        self.assertAlmostEqual(segment[1].x, 80, places=3)
        self.assertAlmostEqual(abs(segment[2].y-segment[1].y), 50, places=3)
        pdf.close()

    def test_paths_preserve_holes_circles_beziers_pen_and_native_dash_pattern(self):
        c = self.canvas
        c.setLineWidth(0.35/MM_PER_PT)
        c.setStrokeColor((.2, .3, .4))
        c.setDash([6/MM_PER_PT, 3/MM_PER_PT])
        c.line(20, 20, 200, 20)
        c.setDash()
        c.circle(40, 60, 12, fill=1)
        path = c.beginPath()
        for x, y, size in ((80, 40, 80), (100, 60, 40)):
            path.moveTo(x, y)
            path.lineTo(x+size, y)
            path.lineTo(x+size, y+size)
            path.lineTo(x, y+size)
            path.close()
        c.drawPath(path, stroke=1, fill=1, fillMode=0)
        curve = c.beginPath()
        curve.moveTo(20, 180)
        curve.curveTo(20, 280, 120, 280, 120, 180)
        c.drawPath(curve)
        # A cubic's true top is 255 pt, below the 280 pt control polygon.
        self.assertAlmostEqual(c.scenes[0].primitives[-1].bounds_mm[3], 255*MM_PER_PT+.175)
        doc = self.read_dxf()
        sheet = doc.layouts.get("A01")
        line = sheet.query("LINE")[0]
        self.assertEqual(line.dxf.lineweight, 35)
        self.assertEqual(line.rgb, (51, 76, 102))
        pattern = doc.linetypes.get(line.dxf.linetype).pattern_tags.tags
        self.assertEqual([tag.value for tag in pattern if tag.code == 49], [6, -3])
        self.assertEqual(len(sheet.query("CIRCLE")), 1)
        self.assertEqual(len(sheet.query("SPLINE")), 1)
        self.assertEqual(sorted(len(hatch.paths) for hatch in sheet.query("HATCH")), [1, 2])
        self.assertEqual(len(sheet.query("LWPOLYLINE")), 2)

    def test_model_blocks_use_only_supplied_geometry_in_metre_to_mm_scale(self):
        visible = {"object_id": "panel", "kind": "visible", "points": ((-.1, .1), (1.1, .1))}
        hidden = {"object_id": "panel", "kind": "hidden", "points": ((0, .2), (1, .2))}
        envelope = {"object_id": "equipment-envelope", "kind": "visible", "points": ((0, .9), (1, .9))}
        section = {"object_id": "panel", "kind": "section", "points": ((0, 0), (1, 0))}
        outer = ((0, 0), (1, 0), (1, .8), (0, .8), (0, 0))
        hole = ((.2, .2), (.8, .2), (.8, .6), (.2, .6), (.2, .2))
        views = {"front": ((visible, hidden, envelope, section), (0, 0, 1, 1))}
        regions = {"front": ({"object_id": "panel", "loops": (outer, hole)},)}
        doc = self.read_dxf(model_views=views, regions=regions, needs_hidden=("front",))
        inserts = list(doc.modelspace().query("INSERT"))
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0].dxf.xscale, 1)
        self.assertEqual(inserts[0].dxf.yscale, 1)
        block = doc.blocks.get(inserts[0].dxf.name)
        self.assertEqual(block.units, 4)
        lines = list(block.query("LINE"))
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0].dxf.start.x, 0)
        self.assertEqual(lines[0].dxf.end.x, 1000)
        self.assertEqual(lines[0].dxf.end.y, 100)
        self.assertEqual(len(block.query('LINE[layer=="MODEL_HIDDEN"]')), 1)
        self.assertEqual(len(block.query("LWPOLYLINE")), 2)
        hatch = block.query("HATCH")[0]
        self.assertEqual(len(block.query("HATCH")), 1)
        self.assertEqual(len(hatch.paths), 2)
        self.assertEqual(hatch.dxf.solid_fill, 0)
        self.assertEqual(hatch.pattern.lines[0].angle, 45)

    def test_invalid_material_loop_and_unrepresentable_dash_are_rejected(self):
        regions = {"front": ({"object_id": "panel", "loops": (((0, 0), (1, 0), (1, 1)),)},)}
        with self.assertRaisesRegex(ValueError, "explicitly closed"):
            render_dxf(self.canvas, model_views={"front": ((), (0, 0, 1, 1))}, regions=regions)
        self.canvas.setDash([6, 3], 1)
        self.canvas.line(20, 20, 100, 20)
        with self.assertRaisesRegex(ValueError, "zero dash phase"):
            render_dxf(self.canvas)


if __name__ == "__main__":
    unittest.main()
