"""Existing project sheet styles applied to caller-supplied orthographic lines.

ARCH400 uses the adopted ARCH 401 Housing 09-sheet white set of 10 September
2026: 22-inch square, 31.75-mm margin and Arial Bold 24/12/10-pt hierarchy.
ARCH364 uses the cabinet revision-03 A3 frame and its 45-mm right title column.
The caller supplies projections, source bounds, fonts and design statements.
A view sheet places drawings the caller already retained, each at its own
paper size, where the caller put them, in the style's frame and type sizes.
This module neither reads models nor chooses project paths or confirms sizes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import ceil, hypot, isfinite
from typing import Any, Mapping, Sequence

from monkeycad.cad_execution import OcctDrawingPolyline
from ..drawing_output import PaperCanvas

_MM = 72.0 / 25.4
_UNITS = {"meter": 1000.0, "millimeter": 1.0, "foot": 304.8, "inch": 25.4}
_PUBLIC = ("id", "name", "nameEn", "description", "descriptionEn", "paperSizeMm", "previewKind")
_STYLES = {
    "arch400-white": {
        "id": "arch400-white", "name": "ARCH400 白底图版", "nameEn": "ARCH400 White Sheet",
        "description": "22 英寸方形白底，角部信息、粗体标题与大留白。",
        "descriptionEn": "22-inch white square with corner information, bold titles and generous spacing.",
        "paperSizeMm": [558.8, 558.8], "previewKind": "presentation", "version": "1",
        "margin_mm": 31.75, "default_scale_denominator": 5,
        "font_sizes_pt": {"title": 24, "view": 12, "body": 10},
        "content_right_mm": 527.05, "view_top_mm": 453.8, "view_title_mm": 488.8,
        "row_gap_mm": 90.0, "view_gap_mm": 34.0, "notes_bottom_mm": 65.0,
    },
    "arch364-technical": {
        "id": "arch364-technical", "name": "ARCH364 技术图框", "nameEn": "ARCH364 Technical Sheet",
        "description": "A3 横向图框、右侧图签与明确尺寸，沿用柜墙深化图模板。",
        "descriptionEn": "Landscape A3 with the retained right title column and dimensioned technical views.",
        "paperSizeMm": [420.0, 297.0], "previewKind": "technical", "version": "1",
        "margin_mm": 10.0, "default_scale_denominator": 10,
        "font_sizes_pt": {"title": 16, "view": 11, "body": 9},
        "content_right_mm": 355.0, "view_top_mm": 225.0, "view_title_mm": 251.0,
        "row_gap_mm": 53.0, "view_gap_mm": 28.0, "notes_bottom_mm": 35.0,
    },
}


def initialize_drawing_runtime() -> None:
    """Load NumPy/GEOS before a host starts concurrent drawing workers."""
    import shapely  # noqa: F401


def drawing_style(style_id: str) -> dict:
    """Return a detached style configuration; a style never owns source facts."""
    try:
        return deepcopy(_STYLES[style_id])
    except KeyError as exc:
        raise ValueError(f"Unknown drawing style: {style_id}") from exc


def list_drawing_styles() -> tuple[dict, ...]:
    return tuple({key: deepcopy(style[key]) for key in _PUBLIC} for style in _STYLES.values())


def _text(canvas, x, y, value, size, *, align="left", bold=True):
    canvas.setFont("bold" if bold else "normal", size)
    {"left": canvas.drawString, "center": canvas.drawCentredString, "right": canvas.drawRightString}[align](
        x * _MM, y * _MM, str(value))


def _line(canvas, a, b, width=0.25):
    canvas.setLineWidth(width * _MM)
    canvas.line(a[0] * _MM, a[1] * _MM, b[0] * _MM, b[1] * _MM)


def _wrapped(canvas, text, width, size):
    """Wrap supplied copy using the same caller-supplied font as its scene."""
    rows = []
    for paragraph in str(text).splitlines():
        current = ""
        for word in paragraph.split():
            if canvas._font("bold").measure(word, size)[0] > width * _MM:
                if current:
                    rows.append(current)
                    current = ""
                for character in word:
                    if current and canvas._font("bold").measure(current + character, size)[0] > width * _MM:
                        rows.append(current)
                        current = ""
                    current += character
                continue
            trial = (current + " " + word).strip()
            if current and canvas._font("bold").measure(trial, size)[0] > width * _MM:
                rows.append(current)
                current = word
            else:
                current = trial
        rows.append(current)
    return rows


def _outline(lines: Sequence[OcctDrawingPolyline], object_ids: tuple[str, ...], precision: float):
    """Omit internal projected facet edges only for explicitly named objects.

    Polygonizing an actual visible line network does not invent its silhouette.
    Open remnants outside the merged faces are retained, including small feet
    and rim edges which do not close a projected polygon by themselves.
    """
    if not object_ids:
        return tuple(lines)
    from shapely import set_precision
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    result = [line for line in lines if line.object_id not in object_ids or line.kind != "visible"]
    for object_id in object_ids:
        visible = [line for line in lines if line.object_id == object_id and line.kind == "visible"]
        if not visible:
            continue
        network = unary_union([set_precision(LineString(line.points), precision) for line in visible])
        faces = unary_union(tuple(polygonize(network)))
        if faces.is_empty:
            result.extend(visible)
            continue
        kept = unary_union((faces.boundary, network.difference(faces.buffer(-precision))))
        geometries = [kept]
        while geometries:
            geometry = geometries.pop()
            if hasattr(geometry, "geoms"):
                geometries.extend(geometry.geoms)
            elif geometry.geom_type in {"LineString", "LinearRing"} and geometry.length > precision:
                result.append(OcctDrawingPolyline(object_id, "visible", tuple(geometry.coords)))
    return tuple(result)


def _dimension(canvas, start, end, offset, label, *, vertical=False, size=10):
    axis = 0 if vertical else 1
    a, b = list(start), list(end)
    a[axis] = b[axis] = offset
    for source, target in ((start, a), (end, b)):
        direction = 1 if target[axis] >= source[axis] else -1
        gap, overrun = list(source), list(target)
        gap[axis] += direction * 1.5
        overrun[axis] += direction * 2.0
        _line(canvas, gap, overrun, 0.13)
        _line(canvas, (target[0] - .95, target[1] - .95), (target[0] + .95, target[1] + .95), .13)
    _line(canvas, a, b, .13)
    if vertical:
        canvas.saveState()
        canvas.translate((offset - 2.0) * _MM, ((a[1] + b[1]) / 2) * _MM)
        canvas.rotate(90)
        _text(canvas, 0, 0, label, size, align="center")
        canvas.restoreState()
    else:
        _text(canvas, (a[0] + b[0]) / 2, offset + 2.0, label, size, align="center")


def _technical_titleblock(canvas, title, scale):
    # Actual ARCH364 title-column division positions, transformed exactly as
    # cabinet-drawings-standard.py revision 03 (45 x 277 mm on A3).
    y0, y1 = 12.3176, 546.4824
    py = lambda y: 287 - (y - y0) * 277 / (y1 - y0)
    canvas.setLineWidth(.25 * _MM)
    canvas.rect(10 * _MM, 10 * _MM, 400 * _MM, 277 * _MM)
    _line(canvas, (365, 10), (365, 287), .16)
    for y in (78.2933, 471.7629, 494.0198, 500.3788, 506.738, 513.0971, 519.4561, 536.9437):
        _line(canvas, (365, py(y)), (410, py(y)), .10)
    _text(canvas, 387.5, py(44), "REVIEW", 14, align="center")
    _text(canvas, 387.5, py(62.897), "MODEL DRAWING", 7, align="center")
    title_rows = _wrapped(canvas, title, 39, 8)
    if len(title_rows) > 3:
        raise ValueError("The project title does not fit the retained title block")
    for index, row in enumerate(title_rows):
        _text(canvas, 387.5, py(420.743) - index * 4, row, 8, align="center")
    _text(canvas, 387.5, py(456.219), "DESIGN REVIEW", 8, align="center")
    _text(canvas, 387.5, py(464.631), "CANDIDATE / NOT ISSUED", 6.5, align="center")
    _text(canvas, 387.5, py(480.1), "ORTHOGRAPHIC VIEWS", 7, align="center")
    for label, y in (("Project", 498.37), ("Date", 504.831), ("Designer", 511.625), ("Reviewer", 517.978)):
        _text(canvas, 367.1, py(y), label, 6, bold=False)
    _text(canvas, 387.5, py(533.117), "01", 16, align="center")
    _text(canvas, 367.1, py(541.548), "Scale", 6, bold=False)
    _text(canvas, 407.5, py(541.976), f"1:{scale} / A3 / mm", 6, align="right")


def compose_review_sheet(*, style_id: str, views: Mapping[str, Sequence[OcctDrawingPolyline]],
                         bounds: Mapping[str, Mapping[str, Sequence[float]]], length_unit: str,
                         title: str, scale_denominator: int, notes: tuple[str, ...] = (),
                         outline_object_ids: tuple[str, ...] = (), font_mapping: Mapping) -> PaperCanvas:
    """Compose one same-scale front/right/top sheet from verified caller values.

    Bounds and projection points use the supplied CAD unit and Z-up frame.
    The returned canvas is consumed by render_pdf/render_dxf. A scale that
    cannot fit is refused rather than silently reduced or geometrically cropped.
    normal/bold are explicit font file mappings; no machine path is inferred.
    """
    style = drawing_style(style_id)
    if length_unit not in _UNITS:
        raise ValueError(f"Unsupported drawing length unit: {length_unit}")
    if isinstance(scale_denominator, bool) or not isinstance(scale_denominator, int) or scale_denominator <= 0:
        raise ValueError("The drawing scale must be a positive integer denominator")
    if set(views) != {"front", "right", "top"} or not bounds:
        raise ValueError("A review sheet needs front, right and top projections and source bounds")
    if not {"normal", "bold"}.issubset(font_mapping) or not str(title).strip():
        raise ValueError("Supply a title and normal/bold font files")
    for row in bounds.values():
        if any(len(row.get(key, ())) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
               for v in row[key]) for key in ("min", "max")) or any(a > b for a, b in zip(row["min"], row["max"])):
            raise ValueError("Every source object needs ordered finite CAD bounds")
    if set(outline_object_ids) - bounds.keys():
        raise ValueError("Outline selection names an object outside the supplied source")
    for lines in views.values():
        if any(views.values()) and not any(line.kind == "visible" for line in lines):
            raise ValueError("Every requested view must contain visible source geometry")
        if any(line.object_id not in bounds for line in lines):
            raise ValueError("Projected lines name an object outside the supplied source")
    low = [min(row["min"][axis] for row in bounds.values()) for axis in range(3)]
    high = [max(row["max"][axis] for row in bounds.values()) for axis in range(3)]
    unit_mm = _UNITS[length_unit]
    factor = unit_mm / scale_denominator
    width, depth, height = [(b - a) * factor for a, b in zip(low, high)]
    left = style["margin_mm"] + (20 if style_id == "arch364-technical" else 16)
    right_left = left + width + style["view_gap_mm"]
    base = style["view_top_mm"] - height
    top_top = base - style["row_gap_mm"]
    top_bottom = top_top - depth
    if right_left + max(depth, 55) > style["content_right_mm"] or top_bottom < style["notes_bottom_mm"] + 15:
        raise ValueError(f"The model does not fit {style['nameEn']} at 1:{scale_denominator}; select a larger scale denominator")
    canvas = PaperCanvas(font_mapping=font_mapping)
    canvas.start_sheet("01", tuple(style["paperSizeMm"]))
    canvas.setTitle(title)
    canvas.setStrokeColor((38/255, 40/255, 35/255))
    canvas.setFillColor((38/255, 40/255, 35/255))
    page_width, page_height = style["paperSizeMm"]
    sizes = style["font_sizes_pt"]
    header = "01 / " + title.upper() if style_id == "arch400-white" else title
    header_width = style["content_right_mm"] - style["margin_mm"] - (95 if style_id == "arch400-white" else 10)
    if canvas._font("bold").measure(header, sizes["title"])[0] > header_width * _MM:
        raise ValueError("The drawing title is too long for the sheet header")
    if style_id == "arch400-white":
        _text(canvas, 31.75, page_height - 28, "01 / " + title.upper(), sizes["title"])
        _text(canvas, 31.75, page_height - 40, "FRONT / RIGHT / TOP PROJECTION", sizes["body"])
        _text(canvas, 527.05, page_height - 28, "MODEL REVIEW", sizes["body"], align="right")
        _text(canvas, 527.05, page_height - 40, "CANDIDATE", sizes["body"], align="right")
    else:
        _technical_titleblock(canvas, title, scale_denominator)
        _text(canvas, 18, 279, title, sizes["title"])
        _text(canvas, 18, 269, "FRONT / RIGHT / TOP PROJECTION", sizes["body"])
    placements = {
        "front": (left, base, (low[0], low[2]), "01 / FRONT ELEVATION", style["view_title_mm"]),
        "right": (right_left, base, (low[1], low[2]), "02 / RIGHT ELEVATION", style["view_title_mm"]),
        "top": (left, top_bottom, (low[0], low[1]), "03 / TOP PROJECTION", top_top + 20),
    }
    for direction, (x, y, minimum, label, label_y) in placements.items():
        _text(canvas, x, label_y, label, sizes["view"])
        selected_outlines = () if direction == "top" else outline_object_ids
        for line in _outline(views[direction], selected_outlines, 0.0001 / unit_mm):
            if line.kind != "visible":
                continue
            points = [(x + (u - minimum[0]) * factor, y + (v - minimum[1]) * factor) for u, v in line.points]
            path = canvas.beginPath()
            path.moveTo(points[0][0] * _MM, points[0][1] * _MM)
            for point in points[1:]:
                path.lineTo(point[0] * _MM, point[1] * _MM)
            pen = .09 if direction == "top" and line.object_id in outline_object_ids else .35 if line.object_id in selected_outlines else .25
            canvas.setLineWidth(pen * _MM)
            canvas.drawPath(path, stroke=1, fill=0)
    def dimension(span):
        value = span * scale_denominator
        return str(round(value)) if abs(value - round(value)) < .05 else f"{value:.1f}"
    _dimension(canvas, (left, base), (left + width, base), base - 12, dimension(width), size=sizes["body"])
    _dimension(canvas, (left, base), (left, base + height), left - 12, dimension(height), vertical=True, size=sizes["body"])
    _dimension(canvas, (right_left, base), (right_left + depth, base), base - 12, dimension(depth), size=sizes["body"])
    _dimension(canvas, (left, top_bottom), (left + width, top_bottom), top_bottom - 12, dimension(width), size=sizes["body"])
    _dimension(canvas, (left + width, top_bottom), (left + width, top_top), left + width + 12, dimension(depth), vertical=True, size=sizes["body"])
    note_width = style["content_right_mm"] - right_left
    note_y = top_top + 20
    _text(canvas, right_left, note_y, "DRAWING NOTES", sizes["view"])
    note_y -= 10
    for note in notes:
        for row in _wrapped(canvas, note, note_width, sizes["body"]):
            if note_y < style["notes_bottom_mm"]:
                raise ValueError("Drawing notes do not fit the sheet; shorten them or use a separate sheet")
            _text(canvas, right_left, note_y, row, sizes["body"])
            note_y -= sizes["body"] / _MM * 1.45
        note_y -= 3
    footer_x = 31.75 if style_id == "arch400-white" else 18
    footer_y = 28 if style_id == "arch400-white" else 18
    _text(canvas, footer_x, footer_y, "MODEL DIMENSIONS / mm / REVIEW ONLY", sizes["body"])
    _text(canvas, style["content_right_mm"], footer_y, f"1:{scale_denominator} AT ORIGINAL SHEET SIZE", sizes["body"], align="right")
    for primitive in canvas.scenes[0].primitives:
        x0, y0, x1, y1 = primitive.bounds_mm
        if x0 < 5 or y0 < 5 or x1 > page_width - 5 or y1 > page_height - 5:
            raise ValueError("Drawing content exceeds the paper boundary; shorten titles or select another scale")
    return canvas


@dataclass(frozen=True, slots=True)
class SheetView:
    """One retained drawing placed on a view sheet at its own paper size.

    ``marks`` are the drawing's ``drawing_svg.DrawingMark`` values: polylines
    and poché in the drawing's own paper mm from its top-left, with pens in
    mm and grey levels. ``place_mm`` is where that top-left goes, in mm from
    the sheet's top-left. ``title``, ``subtitle`` and ``scale_label`` are the
    caller's words, set above the drawing; nothing here reads a model.
    """

    view_id: str
    size_mm: tuple[float, float]
    marks: tuple[Any, ...]
    place_mm: tuple[float, float]
    title: str
    subtitle: str = ""
    scale_label: str = ""


@dataclass(frozen=True, slots=True)
class SheetSectionMark:
    """A section's plane where it crosses a drawing on the same sheet: the cut line A-A drawn on a plan.

    ``start_mm`` and ``end_mm`` are where the plane's trace enters and leaves
    that drawing, in its own paper mm from its top-left (y down); ``look_mm``
    points toward the section's kept side on that paper; ``label`` names the cut.
    """

    view_id: str
    start_mm: tuple[float, float]
    end_mm: tuple[float, float]
    look_mm: tuple[float, float]
    label: str


#: Most drawings one sheet places; each is a retained drawing of its own.
MAX_SHEET_VIEWS = 12
_PT = 25.4 / 72.0  # millimetres per point
_TRACE_DASH_MM = (4.0, 1.0, 0.6, 1.0)


def _paper(value, label: str) -> tuple[float, float]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in value)):
        raise ValueError(f"{label} must be two finite millimetre values")
    return float(value[0]), float(value[1])


def _unit_direction(value, label: str) -> tuple[float, float]:
    x, y = _paper(value, label)
    length = hypot(x, y)
    if length <= 1e-9:
        raise ValueError(f"{label} has no direction")
    return x / length, y / length


class _Sheet:
    """Paper placement from the sheet's top-left in mm, recorded on one PaperCanvas."""

    def __init__(self, canvas: PaperCanvas, height_mm: float):
        self.canvas, self.height = canvas, height_mm

    def at(self, x: float, y: float) -> tuple[float, float]:
        return x * _MM, (self.height - y) * _MM

    def path(self, points, *, width: float = 0.0, grey: float = 0.0, dash=(), fill: float | None = None,
             close: bool = False) -> None:
        canvas = self.canvas
        canvas.saveState()
        canvas.setStrokeColor((grey, grey, grey))
        canvas.setLineWidth(width * _MM)
        canvas.setDash([value * _MM for value in dash])
        if fill is not None:
            canvas.setFillColor((fill, fill, fill))
        path = canvas.beginPath()
        path.moveTo(*self.at(*points[0]))
        for point in points[1:]:
            path.lineTo(*self.at(*point))
        if close:
            path.close()
        canvas.drawPath(path, stroke=1 if width else 0, fill=0 if fill is None else 1)
        canvas.restoreState()

    def text(self, x: float, y: float, value: str, size: float, *, bold: bool = False, align: str = "left") -> None:
        canvas = self.canvas
        canvas.saveState()
        canvas.setFillColor((0, 0, 0))
        canvas.setFont("bold" if bold else "normal", size)
        {"left": canvas.drawString, "center": canvas.drawCentredString, "right": canvas.drawRightString}[align](
            *self.at(x, y), value)
        canvas.restoreState()

    def width(self, value: str, size: float, *, bold: bool = False) -> float:
        return self.canvas._font("bold" if bold else "normal").measure(value, size)[0] * _PT

    def extent(self, first: int) -> tuple[float, float, float, float]:
        """What was drawn since primitive ``first``: left, top, right, bottom in mm from the sheet's top-left."""

        drawn = self.canvas.scenes[-1].primitives[first:]
        return (min(p.bounds_mm[0] for p in drawn), self.height - max(p.bounds_mm[3] for p in drawn),
                max(p.bounds_mm[2] for p in drawn), self.height - min(p.bounds_mm[1] for p in drawn))

    def count(self) -> int:
        return len(self.canvas.scenes[-1].primitives)


def _section_mark(sheet: _Sheet, view: SheetView, mark: SheetSectionMark, size_pt: float) -> None:
    """The cut line across a placed drawing: a thin dash-dot trace, heavy ends, arrows to the kept side, the label."""

    left, top = view.place_mm
    start = (left + mark.start_mm[0], top + mark.start_mm[1])
    end = (left + mark.end_mm[0], top + mark.end_mm[1])
    along = _unit_direction((end[0] - start[0], end[1] - start[1]), f"the {mark.label} section line")
    look = _unit_direction(mark.look_mm, f"the {mark.label} section direction")
    if hypot(end[0] - start[0], end[1] - start[1]) < 20.0:
        raise ValueError(f"Section {mark.label} crosses less than 20 mm of {view.view_id}; it cannot be marked there")
    if abs(along[0] * look[0] + along[1] * look[1]) > 1e-6:
        raise ValueError(f"Section {mark.label} must look across its own line")
    point = lambda origin, direction, distance: (origin[0] + direction[0] * distance,  # noqa: E731
                                                 origin[1] + direction[1] * distance)
    back = (-along[0], -along[1])
    sheet.path([point(start, along, 7.5), point(end, back, 7.5)], width=0.13, grey=0.45, dash=_TRACE_DASH_MM)
    cap = size_pt * _PT * 0.72
    across = (-look[1], look[0])
    for tip, inward in ((start, along), (end, back)):
        a, b = point(tip, inward, 1.5), point(tip, inward, 7.5)
        sheet.path([a, b], width=0.5)
        middle = point(tip, inward, 4.5)
        sheet.path([middle, point(middle, look, 3.2)], width=0.25)
        head = point(middle, look, 2.2)
        sheet.path([point(head, across, 1.1), point(middle, look, 4.0), point(head, across, -1.1)], fill=0.0, close=True)
        label = point(middle, look, -(2.4 + cap / 2))
        sheet.text(label[0], label[1] + cap / 2, mark.label, size_pt, bold=True, align="center")


def compose_view_sheet(*, style_id: str, paper_size_mm: Sequence[float], views: Sequence[SheetView], title: str,
                       sheet_number: str = "01", subtitle: str = "", notes: tuple[str, ...] = (),
                       source_text: str = "", section_marks: Sequence[SheetSectionMark] = (),
                       font_mapping: Mapping) -> PaperCanvas:
    """Compose retained drawings, each at its own scale where the caller placed it, on one sheet.

    The style gives the frame margin and type sizes; the paper is the caller's.
    Each drawing's marks are moved to its place and drawn with their own pens
    and greys, never re-projected or rescaled; its title, subtitle and scale
    label sit above it. ``section_marks`` draw a section's cut line on the
    drawing it crosses. A title strip along the bottom carries the title, the
    sheet number, ``source_text`` and the notes. A drawing (with its labels)
    that leaves the frame, overlaps another or the title strip, or text that
    does not fit, is refused by name rather than moved, scaled or cropped.
    """

    style = drawing_style(style_id)
    width, height = _paper(paper_size_mm, "paper_size_mm")
    if not (50.0 <= width <= 2000.0 and 50.0 <= height <= 2000.0):
        raise ValueError("The paper must be 50 to 2000 mm on each side")
    if not views:
        raise ValueError("A view sheet places at least one drawing")
    if len(views) > MAX_SHEET_VIEWS:
        raise ValueError(f"A view sheet places at most {MAX_SHEET_VIEWS} drawings")
    if not {"normal", "bold"}.issubset(font_mapping) or not str(title).strip() or not str(sheet_number).strip():
        raise ValueError("Supply a title, a sheet number and normal/bold font files")
    identifiers = [view.view_id for view in views]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Each drawing on a sheet needs its own id")
    for view in views:
        size = _paper(view.size_mm, f"{view.view_id} size_mm")
        _paper(view.place_mm, f"{view.view_id} place_mm")
        if size[0] <= 0 or size[1] <= 0:
            raise ValueError(f"{view.view_id} has no paper size")
        if not str(view.title).strip():
            raise ValueError(f"{view.view_id} needs a title")
    marks_by_view: dict[str, list[SheetSectionMark]] = {}
    for mark in section_marks:
        if mark.view_id not in identifiers:
            raise ValueError(f"The section mark {mark.label} names no view on this sheet: {mark.view_id}")
        if not str(mark.label).strip():
            raise ValueError("A section mark needs a label")
        marks_by_view.setdefault(mark.view_id, []).append(mark)

    margin = float(style["margin_mm"])
    sizes = style["font_sizes_pt"]
    title_pt, view_pt, body_pt = float(sizes["title"]), float(sizes["view"]), float(sizes["body"])
    small_pt = max(6.0, body_pt - 1.5)
    strip = float(ceil(10.0 + title_pt * _PT + 1.6 * body_pt * _PT))
    strip_top = height - margin - strip
    canvas = PaperCanvas(font_mapping=font_mapping)
    canvas.start_sheet(str(sheet_number), (width, height))
    canvas.setTitle(str(title))
    sheet = _Sheet(canvas, height)

    footprints: dict[str, tuple[float, float, float, float]] = {}
    for view in views:
        left, top = view.place_mm
        view_width, view_height = view.size_mm
        first = sheet.count()
        for mark in view.marks:
            points = [(left + x, top + y) for x, y in mark.points_mm]
            if len(points) < 2:
                continue
            if mark.polygon:
                sheet.path(points, fill=mark.grey / 255, close=True)
            else:
                sheet.path(points, width=mark.stroke_mm, grey=mark.grey / 255, dash=mark.dash_mm)
        for mark in marks_by_view.get(view.view_id, ()):
            _section_mark(sheet, view, mark, body_pt)
        baseline = top - 2.2
        if view.subtitle:
            sheet.text(left, baseline, view.subtitle, small_pt)
            baseline -= small_pt * _PT * 1.2 + 1.4
        sheet.text(left, baseline, view.title, view_pt, bold=True)
        if view.scale_label:
            title_end = left + sheet.width(view.title, view_pt, bold=True) + 4.0
            label_width = sheet.width(view.scale_label, body_pt, bold=True)
            if title_end + label_width <= left + view_width:
                sheet.text(left + view_width, baseline, view.scale_label, body_pt, bold=True, align="right")
            else:
                sheet.text(title_end, baseline, view.scale_label, body_pt, bold=True)
        x0, y0, x1, y1 = sheet.extent(first)
        footprint = (min(x0, left), min(y0, top), max(x1, left + view_width), max(y1, top + view_height))
        if (footprint[0] < margin - 1e-6 or footprint[1] < margin - 1e-6
                or footprint[2] > width - margin + 1e-6 or footprint[3] > height - margin + 1e-6):
            raise ValueError(
                f"{view.view_id} ({view_width:.1f} x {view_height:.1f} mm at [{left:g}, {top:g}], with its labels "
                f"{footprint[0]:.1f}..{footprint[2]:.1f} x {footprint[1]:.1f}..{footprint[3]:.1f} mm) leaves the "
                f"{width:g} x {height:g} mm paper's {margin:g} mm frame")
        if footprint[3] > strip_top + 1e-6:
            raise ValueError(f"{view.view_id} reaches {footprint[3]:.1f} mm from the top; the title strip starts at "
                             f"{strip_top:.1f} mm")
        for other, placed in footprints.items():
            if (footprint[0] < placed[2] and placed[0] < footprint[2]
                    and footprint[1] < placed[3] and placed[1] < footprint[3]):
                raise ValueError(f"{view.view_id} overlaps {other} on the sheet; place it clear of that drawing and its labels")
        footprints[view.view_id] = footprint

    sheet.path([(margin, margin), (width - margin, margin), (width - margin, height - margin),
                (margin, height - margin)], width=0.35, close=True)
    sheet.path([(margin, strip_top), (width - margin, strip_top)], width=0.25)
    strip_text = sheet.count()
    text_left, text_right = margin + 6.0, width - margin - 6.0
    first_line = strip_top + 4.0 + title_pt * _PT * 0.72
    middle = width / 2 + 10.0
    number_width = sheet.width(str(sheet_number), title_pt, bold=True)
    if (text_left + sheet.width(str(title), title_pt, bold=True) > middle - 6.0
            or (subtitle and text_left + sheet.width(subtitle, body_pt) > middle - 6.0)):
        raise ValueError("The sheet title does not fit the title strip; shorten it")
    sheet.text(text_left, first_line, str(title), title_pt, bold=True)
    if subtitle:
        sheet.text(text_left, first_line + 1.6 * body_pt * _PT, subtitle, body_pt)
    sheet.text(text_right, first_line, str(sheet_number), title_pt, bold=True, align="right")
    rows = []
    column = text_right - number_width - 8.0 - middle
    for paragraph in ((source_text,) if source_text else ()) + tuple(notes):
        rows.extend(_wrapped(canvas, paragraph, column, small_pt) if column > 0 else [paragraph])
    line = strip_top + 3.0 + small_pt * _PT
    for row in rows:
        if line > height - margin - 1.5 or sheet.width(row, small_pt) > column:
            raise ValueError("The source line and notes do not fit the title strip; shorten the notes")
        sheet.text(middle, line, row, small_pt)
        line += small_pt * _PT * 1.45
    for primitive in canvas.scenes[-1].primitives[strip_text:]:
        x0, y0, x1, y1 = primitive.bounds_mm
        if x0 < margin or x1 > width - margin or height - y1 < strip_top or height - y0 > height - margin:
            raise ValueError("The title strip's text leaves the strip; shorten the title, sheet number or notes")
    return canvas
