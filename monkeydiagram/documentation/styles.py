"""Existing project sheet styles applied to caller-supplied orthographic lines.

ARCH400 uses the adopted ARCH 401 Housing 09-sheet white set of 10 September
2026: 22-inch square, 31.75-mm margin and Arial Bold 24/12/10-pt hierarchy.
ARCH364 uses the cabinet revision-03 A3 frame and its 45-mm right title column.
The caller supplies projections, source bounds, fonts and design statements.
This module neither reads models nor chooses project paths or confirms sizes.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Mapping, Sequence

from archflow.adapters.cad_execution import OcctDrawingPolyline
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
