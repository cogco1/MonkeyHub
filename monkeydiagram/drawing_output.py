"""In-memory paper scenes shared by PDF and editable DXF output.

The canvas accepts the small ReportLab subset used by drawing builders. Input
coordinates, font sizes and pen widths are points, with a bottom-left origin.
Scene bounds are paper millimetres. Callers supply fonts and own artifact writes;
this adapter neither reads a project nor extracts geometry from a rendered PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO, StringIO
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


MM_PER_PT = 25.4 / 72.0
Point = tuple[float, float]
Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class PaperPrimitive:
    kind: str
    data: Mapping[str, Any]
    bounds_mm: Bounds


@dataclass(frozen=True)
class PaperSheet:
    number: str
    size_mm: tuple[float, float]
    primitives: tuple[PaperPrimitive, ...]


@dataclass
class _GraphicsState:
    matrix: tuple[float, ...] = (1, 0, 0, 1, 0, 0)
    stroke: tuple[float, ...] = (0, 0, 0, 1)
    fill: tuple[float, ...] = (0, 0, 0, 1)
    width: float = 1.0
    dash: tuple[float, ...] = ()
    phase: float = 0.0
    font: str = "Helvetica"
    font_size: float = 12.0


def _transform(point: Point, matrix: tuple[float, ...]) -> Point:
    a, b, c, d, e, f = matrix
    x, y = point
    return a * x + c * y + e, b * x + d * y + f


def _bounds(points: Iterable[Point], padding: float = 0.0) -> Bounds:
    points = tuple(points)
    xs, ys = zip(*points)
    return tuple(v * MM_PER_PT for v in (
        min(xs) - padding, min(ys) - padding,
        max(xs) + padding, max(ys) + padding,
    ))


def _curve_extrema(a: Point, b: Point, c: Point, d: Point) -> list[Point]:
    ts = {0.0, 1.0}
    for axis in (0, 1):
        aa = -a[axis] + 3*b[axis] - 3*c[axis] + d[axis]
        bb = 2*(a[axis] - 2*b[axis] + c[axis])
        cc = b[axis] - a[axis]
        if abs(aa) < 1e-12:
            roots = [-cc / bb] if abs(bb) > 1e-12 else []
        else:
            discriminant = bb*bb - 4*aa*cc
            roots = [] if discriminant < 0 else [
                (-bb + math.sqrt(discriminant))/(2*aa),
                (-bb - math.sqrt(discriminant))/(2*aa),
            ]
        ts.update(t for t in roots if 0 < t < 1)
    return [tuple((1-t)**3*a[i] + 3*(1-t)**2*t*b[i]
                  + 3*(1-t)*t*t*c[i] + t**3*d[i] for i in (0, 1))
            for t in ts]


def _path_points(commands: Iterable[tuple]) -> list[Point]:
    result: list[Point] = []
    current = start = None
    for command in commands:
        op = command[0]
        if op == "M":
            current = start = command[1]
        elif op == "L":
            result.extend((current, command[1]))
            current = command[1]
        elif op == "C":
            result.extend(_curve_extrema(current, *command[1:]))
            current = command[-1]
        elif op == "Z":
            result.extend((current, start))
            current = start
    return result


def _color(value: Any) -> tuple[float, ...]:
    if hasattr(value, "red"):
        return value.red, value.green, value.blue, getattr(value, "alpha", 1)
    if isinstance(value, (tuple, list)) and len(value) in (3, 4):
        return tuple(value) if len(value) == 4 else (*value, 1)
    from reportlab.lib.colors import toColor
    return _color(toColor(value))


class _FontMetrics:
    def __init__(self, path: str | Path):
        from fontTools.ttLib import TTFont
        from fontTools.pens.boundsPen import BoundsPen
        self._pen_type = BoundsPen
        self.font = TTFont(path, fontNumber=0)
        self.units = self.font["head"].unitsPerEm
        self.cmap = self.font.getBestCmap()
        self.metrics = self.font["hmtx"].metrics
        self.glyphs = self.font.getGlyphSet()
        self._ink: dict[str, tuple | None] = {}
        cap = self.glyph_bounds(self.cmap.get(ord("H"), ".notdef"))
        self.cap_height = cap[3] if cap else self.font["hhea"].ascent

    def glyph_bounds(self, name: str):
        if name not in self._ink:
            pen = self._pen_type(self.glyphs)
            self.glyphs[name].draw(pen)
            self._ink[name] = pen.bounds
        return self._ink[name]

    def measure(self, text: str, size: float) -> tuple[float, Bounds]:
        advance, corners = 0.0, []
        for char in text:
            name = self.cmap.get(ord(char), ".notdef")
            ink = self.glyph_bounds(name)
            if ink:
                x0, y0, x1, y1 = ink
                corners.extend(((advance+x0, y0), (advance+x1, y1)))
            advance += self.metrics[name][0]
        factor = size / self.units
        if corners:
            xs, ys = zip(*corners)
            bounds = tuple(v*factor for v in (min(xs), min(ys), max(xs), max(ys)))
        else:
            bounds = (0.0, 0.0, 0.0, 0.0)
        return advance*factor, bounds


class _PaperPath:
    def __init__(self):
        self.commands: list[tuple] = []

    def moveTo(self, x, y):
        self.commands.append(("M", (float(x), float(y))))

    def lineTo(self, x, y):
        if not self.commands:
            raise ValueError("A paper path must start with moveTo")
        self.commands.append(("L", (float(x), float(y))))

    def curveTo(self, x1, y1, x2, y2, x3, y3):
        if not self.commands:
            raise ValueError("A paper path must start with moveTo")
        self.commands.append(("C", (x1, y1), (x2, y2), (x3, y3)))

    def close(self):
        if self.commands:
            self.commands.append(("Z",))

    closePath = close


class PaperCanvas:
    """Record explicitly numbered sheets; ``save`` never writes a file."""

    def __init__(self, *, font_mapping: Mapping[str, str | Path] | None = None,
                 pagesize: tuple[float, float] = (595.2756, 841.8898)):
        self.font_mapping = dict(font_mapping or {})
        self._fonts: dict[str, _FontMetrics] = {}
        self._pagesize = pagesize
        self._sheets: list[PaperSheet] = []
        self._number: str | None = None
        self._primitives: list[PaperPrimitive] = []
        self._state = _GraphicsState()
        self._stack: list[_GraphicsState] = []
        self.title = self.author = ""

    def start_sheet(self, number: str, size_mm: tuple[float, float]):
        if self._number is not None:
            raise ValueError("Finish the current sheet with showPage before starting another")
        if len(size_mm) != 2 or any(not math.isfinite(v) or v <= 0 for v in size_mm):
            raise ValueError("A sheet requires two positive paper dimensions")
        self._number = str(number)
        self._pagesize = tuple(v / MM_PER_PT for v in size_mm)

    @property
    def scenes(self) -> tuple[PaperSheet, ...]:
        current = () if self._number is None else (PaperSheet(
            self._number, tuple(v*MM_PER_PT for v in self._pagesize), tuple(self._primitives)),)
        return (*self._sheets, *current)

    def _append(self, kind: str, data: dict, bounds: Bounds):
        if self._number is None:
            raise ValueError("Call start_sheet(number, size_mm) before drawing")
        self._primitives.append(PaperPrimitive(kind, data, bounds))

    def _font(self, name: str) -> _FontMetrics:
        if name not in self._fonts:
            if name not in self.font_mapping:
                raise ValueError(f"No caller-supplied font mapping for {name!r}")
            self._fonts[name] = _FontMetrics(self.font_mapping[name])
        return self._fonts[name]

    def _style(self, stroke: bool, fill: bool, fill_mode: int = 0) -> dict:
        state = self._state
        return dict(stroke=bool(stroke), fill=bool(fill), stroke_color=state.stroke,
                    fill_color=state.fill, line_width_pt=state.width,
                    dash_pt=state.dash, dash_phase_pt=state.phase, fill_mode=fill_mode)

    def setTitle(self, value):
        self.title = str(value)

    def setAuthor(self, value):
        self.author = str(value)

    def setFont(self, name, size, leading=None):
        if size <= 0:
            raise ValueError("Font size must be positive")
        self._font(name)
        self._state.font, self._state.font_size = name, float(size)

    def stringWidth(self, text, fontName=None, fontSize=None):
        return self._font(fontName or self._state.font).measure(
            str(text), self._state.font_size if fontSize is None else fontSize)[0]

    def setStrokeColor(self, value):
        self._state.stroke = _color(value)

    def setFillColor(self, value):
        self._state.fill = _color(value)

    def setLineWidth(self, width):
        if width < 0:
            raise ValueError("Pen width cannot be negative")
        self._state.width = float(width)

    def setDash(self, array=(), phase=0):
        if isinstance(array, (int, float)):
            array, phase = (array, phase), 0
        pattern = tuple(float(v) for v in array)
        if any(v < 0 for v in pattern) or (pattern and sum(pattern) <= 0):
            raise ValueError("Dash lengths must be nonnegative and have positive total length")
        self._state.dash, self._state.phase = pattern, float(phase)

    def saveState(self):
        self._stack.append(replace(self._state))

    def restoreState(self):
        if not self._stack:
            raise ValueError("No saved graphics state")
        self._state = self._stack.pop()

    def translate(self, dx, dy):
        a, b, c, d, e, f = self._state.matrix
        self._state.matrix = (a, b, c, d, e+a*dx+c*dy, f+b*dx+d*dy)

    def rotate(self, degrees):
        a, b, c, d, e, f = self._state.matrix
        angle = math.radians(degrees)
        co, si = math.cos(angle), math.sin(angle)
        self._state.matrix = (a*co+c*si, b*co+d*si, -a*si+c*co, -b*si+d*co, e, f)

    def beginPath(self):
        return _PaperPath()

    def drawPath(self, path, stroke=1, fill=0, fillMode=None):
        commands = tuple((command[0], *(_transform(p, self._state.matrix)
                                        for p in command[1:])) for command in path.commands)
        points = _path_points(commands)
        if points and (stroke or fill):
            self._append("path", dict(commands=commands, **self._style(stroke, fill, fillMode or 0)),
                         _bounds(points, self._state.width/2 if stroke else 0))

    def line(self, x1, y1, x2, y2):
        path = self.beginPath()
        path.moveTo(x1, y1)
        path.lineTo(x2, y2)
        self.drawPath(path)

    def rect(self, x, y, width, height, stroke=1, fill=0):
        path = self.beginPath()
        path.moveTo(x, y)
        for point in ((x+width, y), (x+width, y+height), (x, y+height)):
            path.lineTo(*point)
        path.close()
        self.drawPath(path, stroke, fill)

    def circle(self, x, y, radius, stroke=1, fill=0):
        if radius < 0:
            raise ValueError("Circle radius cannot be negative")
        center = _transform((x, y), self._state.matrix)
        extent = radius + (self._state.width/2 if stroke else 0)
        bounds = _bounds(((center[0]-extent, center[1]-extent),
                          (center[0]+extent, center[1]+extent)))
        self._append("circle", dict(center_pt=center, radius_pt=radius,
                                    **self._style(stroke, fill)), bounds)

    def _text(self, x, y, text, alignment):
        text = str(text)
        if not text:
            return
        state = self._state
        metrics = self._font(state.font)
        advance, ink = metrics.measure(text, state.font_size)
        x -= advance*alignment
        origin = _transform((x, y), state.matrix)
        corners = [_transform((x+xx, y+yy), state.matrix)
                   for xx, yy in ((ink[0], ink[1]), (ink[0], ink[3]),
                                  (ink[2], ink[1]), (ink[2], ink[3]))]
        self._append("text", dict(text=text, origin_pt=origin, font=state.font,
                                  font_size_pt=state.font_size,
                                  cap_height_pt=state.font_size*metrics.cap_height/metrics.units,
                                  rotation=math.degrees(math.atan2(state.matrix[1], state.matrix[0])),
                                  fill_color=state.fill), _bounds(corners))

    def drawString(self, x, y, text):
        self._text(x, y, text, 0)

    def drawCentredString(self, x, y, text):
        self._text(x, y, text, .5)

    def drawRightString(self, x, y, text):
        self._text(x, y, text, 1)

    def showPage(self):
        if self._number is not None:
            self._sheets.append(self.scenes[-1])
        self._number, self._primitives = None, []
        self._state, self._stack = _GraphicsState(), []

    def save(self):
        self.showPage()


def _input(source, font_mapping):
    scenes = source.scenes if hasattr(source, "scenes") else tuple(source)
    fonts = font_mapping if font_mapping is not None else getattr(source, "font_mapping", {})
    if not scenes:
        raise ValueError("No paper sheets to render")
    return scenes, fonts


def render_pdf(source, font_mapping: Mapping[str, str | Path] | None = None) -> bytes:
    """Render the common scenes to bytes, without selecting an output path."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as pdf_canvas
    scenes, fonts = _input(source, font_mapping)
    aliases = {}
    for index, (name, path) in enumerate(fonts.items()):
        alias = f"AF_Drawing_{index}"
        pdfmetrics.registerFont(TTFont(alias, str(path)))
        aliases[name] = alias
    buffer = BytesIO()
    canvas = pdf_canvas.Canvas(buffer, pagesize=tuple(v/MM_PER_PT for v in scenes[0].size_mm), invariant=1)
    canvas.setTitle(getattr(source, "title", ""))
    canvas.setAuthor(getattr(source, "author", ""))
    for sheet in scenes:
        canvas.setPageSize(tuple(v/MM_PER_PT for v in sheet.size_mm))
        for primitive in sheet.primitives:
            data = primitive.data
            canvas.saveState()
            canvas.setFillColorRGB(*data["fill_color"][:3])
            canvas.setFillAlpha(data["fill_color"][3])
            if primitive.kind == "text":
                if data["font"] not in aliases:
                    raise ValueError(f"No caller-supplied font mapping for {data['font']!r}")
                canvas.translate(*data["origin_pt"])
                canvas.rotate(data["rotation"])
                canvas.setFont(aliases[data["font"]], data["font_size_pt"])
                canvas.drawString(0, 0, data["text"])
            else:
                canvas.setStrokeColorRGB(*data["stroke_color"][:3])
                canvas.setStrokeAlpha(data["stroke_color"][3])
                canvas.setLineWidth(data["line_width_pt"])
                canvas.setDash(data["dash_pt"], data["dash_phase_pt"])
                if primitive.kind == "circle":
                    canvas.circle(*data["center_pt"], data["radius_pt"], stroke=data["stroke"], fill=data["fill"])
                elif primitive.kind == "path":
                    path = canvas.beginPath()
                    for command in data["commands"]:
                        op, points = command[0], command[1:]
                        if op == "M":
                            path.moveTo(*points[0])
                        elif op == "L":
                            path.lineTo(*points[0])
                        elif op == "C":
                            path.curveTo(*(v for point in points for v in point))
                        else:
                            path.close()
                    canvas.drawPath(path, stroke=data["stroke"], fill=data["fill"], fillMode=data["fill_mode"])
                else:
                    raise ValueError(f"Unknown paper primitive {primitive.kind!r}")
            canvas.restoreState()
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _dxf_paths(commands):
    from ezdxf import path as cad_path
    paths, current = [], None
    for command in commands:
        op = command[0]
        points = [tuple(v*MM_PER_PT for v in p) for p in command[1:]]
        if op == "M":
            if current is not None and len(current):
                paths.append(current)
            current = cad_path.Path(points[0])
        elif op == "L":
            current.line_to(points[0])
        elif op == "C":
            current.curve4_to(points[2], points[0], points[1])
        elif op == "Z":
            current.close()
    if current is not None and len(current):
        paths.append(current)
    return paths


def _dxf_color(color):
    from ezdxf.colors import float2transparency, rgb2int
    attrs = {"true_color": rgb2int(tuple(round(v*255) for v in color[:3]))}
    if color[3] < 1:
        attrs["transparency"] = float2transparency(1-color[3])
    return attrs


def _dxf_stroke(doc, data, patterns):
    from ezdxf.lldxf.const import VALID_DXF_LINEWEIGHTS
    width = data["line_width_pt"]*MM_PER_PT*100
    attrs = {"layer": "PAPER_STROKE", **_dxf_color(data["stroke_color"]),
             "lineweight": min((w for w in VALID_DXF_LINEWEIGHTS if w >= 0), key=lambda w: abs(w-width))}
    pattern = tuple(v*MM_PER_PT for v in data["dash_pt"])
    if pattern:
        if abs(data["dash_phase_pt"]) > 1e-9:
            raise ValueError("Native DXF output requires zero dash phase")
        if len(pattern) % 2:
            pattern *= 2
        if pattern not in patterns:
            name = f"PAPER_DASH_{len(patterns)+1}"
            doc.linetypes.new(name, dxfattribs={"pattern": [sum(pattern), *(
                v if i % 2 == 0 else -v for i, v in enumerate(pattern))]})
            patterns[pattern] = name
        attrs["linetype"] = patterns[pattern]
    return attrs


def _clip_segment(a, b, crop):
    xmin, ymin, xmax, ymax = crop
    dx, dy = b[0]-a[0], b[1]-a[1]
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, a[0]-xmin), (dx, xmax-a[0]), (-dy, a[1]-ymin), (dy, ymax-a[1])):
        if abs(p) < 1e-15:
            if q < 0:
                return None
        elif p < 0:
            lo = max(lo, q/p)
        else:
            hi = min(hi, q/p)
    if lo > hi:
        return None
    return ((a[0]+lo*dx, a[1]+lo*dy), (a[0]+hi*dx, a[1]+hi*dy))


def _model_views(doc, views, regions, needs_hidden):
    """Keep only caller-supplied metre geometry, in independent 1:1 mm blocks."""
    from ezdxf import path as cad_path, units
    model, y = doc.modelspace(), 0.0
    for name, weight in (("VISIBLE", 18), ("SECTION", 35), ("HIDDEN", 9)):
        doc.layers.new(f"MODEL_{name}", dxfattribs={"lineweight": weight})
    doc.layers.new("MODEL_SECTION_FILL", dxfattribs={"color": 8, "lineweight": 9})
    doc.linetypes.new("MODEL_HIDDEN_DASH", dxfattribs={"pattern": [12, 8, -4]})
    doc.layers.get("MODEL_HIDDEN").dxf.linetype = "MODEL_HIDDEN_DASH"
    def field(value, key):
        return value[key] if isinstance(value, Mapping) else getattr(value, key)
    for index, (name, (lines, crop)) in enumerate(views.items(), 1):
        block = doc.blocks.new(f"VIEW_{index:02d}_" + re.sub(r"[^A-Za-z0-9_-]", "_", name))
        block.units = units.MM
        material_objects = set()
        for region in regions.get(name, ()):
            material_objects.add(field(region, "object_id"))
            paths = []
            for loop in field(region, "loops"):
                if len(loop) < 4 or math.dist(loop[0], loop[-1]) > 1e-9:
                    raise ValueError("Material regions must provide explicitly closed loops")
                vertices = [tuple(v*1000 for v in point) for point in loop[:-1]]
                if len(vertices) < 3:
                    raise ValueError("Material boundary needs at least three vertices")
                path = cad_path.Path(vertices[0])
                for point in vertices[1:]:
                    path.line_to(point)
                path.close()
                paths.append(path)
                block.add_lwpolyline(vertices, close=True, dxfattribs={"layer": "MODEL_SECTION"})
            hatches = cad_path.render_hatches(block, paths, dxfattribs={"layer": "MODEL_SECTION_FILL"})
            for hatch in hatches:
                offset = 15/math.sqrt(2)
                hatch.set_pattern_fill("SECTION", color=256, style=0, pattern_type=0,
                                       definition=[(45, (0, 0), (-offset, offset), [])])
        for line in lines:
            kind, points = field(line, "kind"), field(line, "points")
            oid = line.get("object_id") if isinstance(line, Mapping) else getattr(line, "object_id", None)
            if kind == "hidden" and name not in needs_hidden:
                continue
            if kind == "section" and oid in material_objects:
                continue
            if kind not in ("visible", "section", "hidden"):
                raise ValueError(f"Unknown model line kind {kind!r}")
            for a, b in zip(points, points[1:]):
                segment = _clip_segment(a, b, crop)
                if segment and math.dist(*segment) > 1e-10:
                    block.add_line(*(tuple(v*1000 for v in point) for point in segment),
                                   dxfattribs={"layer": f"MODEL_{kind.upper()}"})
        model.add_blockref(block.name, (-crop[0]*1000, y-crop[3]*1000))
        y -= (crop[3]-crop[1])*1000 + 250


def render_dxf(source, font_mapping: Mapping[str, str | Path] | None = None, *,
               model_views=None, regions=None, needs_hidden=()) -> bytes:
    """Render the same scene sequence as editable mm layouts; never read a PDF."""
    import ezdxf
    from ezdxf import path as cad_path, units
    scenes, fonts = _input(source, font_mapping)
    doc = ezdxf.new("R2013")
    doc.units = units.MM
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LWDISPLAY"] = True
    doc.header["$LTSCALE"] = doc.header["$PSLTSCALE"] = 1.0
    for name in ("PAPER_STROKE", "PAPER_FILL", "PAPER_TEXT"):
        doc.layers.new(name)
    styles, patterns = {}, {}
    for index, (name, path) in enumerate(fonts.items()):
        style = f"DRAWING_FONT_{index}"
        doc.styles.new(style, dxfattribs={"font": Path(path).name})
        styles[name] = style
    for index, scene in enumerate(scenes):
        name = re.sub(r'[<>/\\":;?*|=\[\]]', "-", scene.number).strip()
        if not name or name.lower() == "model":
            raise ValueError("A paper sheet needs a nonempty non-Model number")
        if index == 0 and name == "Layout1":
            sheet = doc.layouts.get("Layout1")
        else:
            sheet = doc.layouts.new(name)
            if index == 0:
                doc.layouts.delete("Layout1")
        sheet.page_setup(size=scene.size_mm, margins=(0, 0, 0, 0), units="mm", scale=(1, 1))
        for primitive in scene.primitives:
            data = primitive.data
            if primitive.kind == "text":
                if data["font"] not in styles:
                    raise ValueError(f"No caller-supplied font mapping for {data['font']!r}")
                sheet.add_text(data["text"], dxfattribs={
                    "layer": "PAPER_TEXT", "style": styles[data["font"]],
                    "height": data["cap_height_pt"]*MM_PER_PT,
                    "insert": tuple(v*MM_PER_PT for v in data["origin_pt"]),
                    "rotation": data["rotation"], **_dxf_color(data["fill_color"]),
                })
                continue
            if primitive.kind == "circle":
                center = tuple(v*MM_PER_PT for v in data["center_pt"])
                radius = data["radius_pt"]*MM_PER_PT
                if data["fill"]:
                    hatch = sheet.add_hatch(dxfattribs={"layer": "PAPER_FILL", **_dxf_color(data["fill_color"])})
                    hatch.paths.add_edge_path().add_arc(center, radius, 0, 360)
                if data["stroke"]:
                    sheet.add_circle(center, radius, dxfattribs=_dxf_stroke(doc, data, patterns))
                continue
            if primitive.kind != "path":
                raise ValueError(f"Unknown paper primitive {primitive.kind!r}")
            paths = _dxf_paths(data["commands"])
            if data["fill"]:
                if data["fill_mode"] == 1 and len(paths) > 1:
                    raise ValueError("Compound DXF fills require explicit even-odd material boundaries")
                cad_path.render_hatches(sheet, paths, edge_path=True, dxfattribs={
                    "layer": "PAPER_FILL", **_dxf_color(data["fill_color"]),
                })
            if data["stroke"]:
                attrs = _dxf_stroke(doc, data, patterns)
                for path in paths:
                    if path.has_curves:
                        cad_path.render_splines_and_polylines(sheet, [path], dxfattribs=attrs)
                    else:
                        vertices = list(path.control_vertices())
                        if len(vertices) == 2:
                            sheet.add_line(*vertices, dxfattribs=attrs)
                        elif len(vertices) > 2:
                            entity = sheet.add_lwpolyline(vertices, format="xy", close=path.is_closed, dxfattribs=attrs)
                            entity.dxf.flags |= 128
    if model_views:
        _model_views(doc, model_views, regions or {}, set(needs_hidden))
    audit = doc.audit()
    if audit.has_errors or audit.has_fixes:
        raise ValueError(f"Invalid DXF scene output: {len(audit.errors)} errors, {len(audit.fixes)} fixes")
    buffer = StringIO()
    doc.write(buffer)
    return buffer.getvalue().encode("utf-8")
