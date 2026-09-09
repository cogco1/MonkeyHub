"""Deterministic SVG for orthographic drawing polylines, and a PNG rendered from that SVG.

The first real drawing consumer: ``adapters.cad_execution`` returns plain
``OcctDrawingPolyline`` values (object id, ``visible``/``hidden``, points in
the drawing frame); this module crops them to the view's window and
serialises them as one SVG whose every polyline still names its source
physical object.  The PNG is rasterised from those SVG bytes and nothing
else: the renderer parses the SVG back (viewBox, physical size, the polyline
groups) and draws exactly the polylines it finds.  It is not a screenshot,
not a re-projection, and knows no project, run or path.

Byte determinism.  The same polylines, crop and options give the same SVG
bytes: coordinates are written with fixed decimals, elements are sorted by
(object id, points), there is no timestamp, id counter or random value.
The PNG follows: Pillow's encoder is deterministic for identical pixels.

Units.  SVG user units are the drawing frame's units (metres for a metre
STEP read); ``width``/``height`` state the physical sheet size in mm at the
given scale, so a 1:100 elevation of a 46 m crop is a 460 mm wide sheet.
Pen widths are given in paper millimetres and converted to user units.
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import Sequence
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

from archflow.adapters.occt_backend import OcctDrawingPolyline

SVG_MEDIA_TYPE = "image/svg+xml"
PNG_MEDIA_TYPE = "image/png"
SVG_NS = "http://www.w3.org/2000/svg"

#: Fixed decimals for user-unit coordinates: 4 decimals of a metre is 0.1 mm,
#: the same chord tolerance the projection is asked for.
_DECIMALS = 4
_VISIBLE_PEN_MM = 0.25
_HIDDEN_PEN_MM = 0.13
_HIDDEN_DASH_MM = (2.0, 1.0)


class DrawingSvgError(ValueError):
    """The polylines, crop or SVG could not be serialised or rendered as asked."""


def _crop(crop_uv) -> tuple[float, float, float, float]:
    if (not isinstance(crop_uv, Sequence) or isinstance(crop_uv, (str, bytes)) or len(crop_uv) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in crop_uv)):
        raise DrawingSvgError("crop_uv must be four finite numbers (u_min, v_min, u_max, v_max)")
    u0, v0, u1, v1 = (float(v) for v in crop_uv)
    if not (u0 < u1 and v0 < v1):
        raise DrawingSvgError("crop_uv must have u_min < u_max and v_min < v_max")
    return u0, v0, u1, v1


def _clip_segment(a, b, crop):
    """Liang-Barsky: the part of segment a-b inside the crop rectangle, or None."""

    u0, v0, u1, v1 = crop
    dx, dy = b[0] - a[0], b[1] - a[1]
    t_enter, t_exit = 0.0, 1.0
    for p, q in ((-dx, a[0] - u0), (dx, u1 - a[0]), (-dy, a[1] - v0), (dy, v1 - a[1])):
        if p == 0.0:
            if q < 0.0:
                return None
            continue
        t = q / p
        if p < 0.0:
            if t > t_exit:
                return None
            t_enter = max(t_enter, t)
        else:
            if t < t_enter:
                return None
            t_exit = min(t_exit, t)
    if t_enter > t_exit:
        return None
    start = a if t_enter == 0.0 else (a[0] + t_enter * dx, a[1] + t_enter * dy)
    end = b if t_exit == 1.0 else (a[0] + t_exit * dx, a[1] + t_exit * dy)
    return start, end


def crop_polylines(lines: Sequence[OcctDrawingPolyline], crop_uv) -> tuple[OcctDrawingPolyline, ...]:
    """The polylines clipped to the crop window; a line leaving and re-entering becomes several.

    Clipping happens on the projected result, after visibility was solved
    with every participating object, so an object outside the window still
    hid what it stood in front of.  Zero-length remnants are dropped.
    """

    crop = _crop(crop_uv)
    kept: list[OcctDrawingPolyline] = []
    for line in lines:
        if not isinstance(line, OcctDrawingPolyline):
            raise DrawingSvgError("lines must be OcctDrawingPolyline values")
        run: list[tuple[float, float]] = []
        for a, b in zip(line.points, line.points[1:]):
            clipped = _clip_segment(a, b, crop)
            if clipped is None:
                if len(run) > 1:
                    kept.append(OcctDrawingPolyline(line.object_id, line.kind, tuple(run)))
                run = []
                continue
            start, end = clipped
            if start == end:
                continue
            if run and run[-1] != start:
                if len(run) > 1:
                    kept.append(OcctDrawingPolyline(line.object_id, line.kind, tuple(run)))
                run = []
            if not run:
                run.append(start)
            run.append(end)
        if len(run) > 1:
            kept.append(OcctDrawingPolyline(line.object_id, line.kind, tuple(run)))
    return tuple(sorted(set(kept), key=lambda line: (line.object_id, line.kind, line.points)))


def _number(value: float) -> str:
    text = f"{value:.{_DECIMALS}f}"
    return "0." + "0" * _DECIMALS if text == "-0." + "0" * _DECIMALS else text


def drawing_svg(
    lines: Sequence[OcctDrawingPolyline], *, crop_uv, unit: str, scale_denominator: int,
    hidden_lines: bool, title: str,
) -> bytes:
    """One SVG of the cropped polylines: hidden lines (dashed, optional) under visible lines.

    ``crop_uv`` is the drawing window in frame units and becomes the viewBox
    (u rightwards, v upwards; SVG y runs down, so ``y = v_max - v``).
    ``unit`` names the frame unit; ``scale_denominator`` gives the sheet
    scale 1:N for the physical size.  ``hidden_lines`` False omits the hidden
    group entirely; the polylines still carry their object ids either way.
    """

    crop = _crop(crop_uv)
    if not isinstance(unit, str) or not unit.strip():
        raise DrawingSvgError("unit must name the drawing frame unit")
    if isinstance(scale_denominator, bool) or not isinstance(scale_denominator, int) or scale_denominator <= 0:
        raise DrawingSvgError("scale_denominator must be a positive integer")
    if not isinstance(title, str) or not title.strip():
        raise DrawingSvgError("title must be non-empty text")
    unit_mm = {"meter": 1000.0, "millimeter": 1.0, "inch": 25.4, "foot": 304.8}.get(unit)
    if unit_mm is None:
        raise DrawingSvgError(f"unit {unit!r} has no paper size conversion")
    kept = crop_polylines(lines, crop)
    u0, v0, u1, v1 = crop
    width, height = u1 - u0, v1 - v0
    paper_per_unit = unit_mm / scale_denominator
    pen = lambda mm: _number(mm / paper_per_unit)  # noqa: E731 - paper mm to user units

    def points_attribute(points) -> str:
        return " ".join(f"{_number(u - u0)},{_number(v1 - v)}" for u, v in points)

    def group(kind: str, style: str) -> list[str]:
        members = [line for line in kept if line.kind == kind]
        out = [f'  <g id="{kind}" fill="none" stroke="#000" stroke-linecap="round" stroke-linejoin="round" {style}>']
        out.extend(
            f'    <polyline data-object={quoteattr(line.object_id)} points="{points_attribute(line.points)}"/>'
            for line in members
        )
        out.append("  </g>")
        return out

    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="{SVG_NS}" width="{_number(width * paper_per_unit)}mm" height="{_number(height * paper_per_unit)}mm" '
        f'viewBox="0 0 {_number(width)} {_number(height)}" '
        f'data-unit={quoteattr(unit)} data-scale="1:{scale_denominator}" '
        f'data-crop-uv="{" ".join(_number(v) for v in crop)}" data-hidden-lines="{"true" if hidden_lines else "false"}">',
        f"  <title>{_escape(title)}</title>",
    ]
    body: list[str] = []
    if hidden_lines:
        dash = " ".join(pen(mm) for mm in _HIDDEN_DASH_MM)
        body.extend(group("hidden", f'stroke-width="{pen(_HIDDEN_PEN_MM)}" stroke-dasharray="{dash}"'))
    body.extend(group("visible", f'stroke-width="{pen(_VISIBLE_PEN_MM)}"'))
    return ("\n".join(head + body + ["</svg>", ""])).encode("utf-8")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _parse_svg(svg: bytes):
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise DrawingSvgError(f"the SVG could not be parsed: {exc}") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise DrawingSvgError("the document is not an SVG")
    try:
        min_x, min_y, width, height = (float(v) for v in root.get("viewBox", "").split())
        width_mm = _millimetres(root.get("width", ""))
        height_mm = _millimetres(root.get("height", ""))
    except ValueError as exc:
        raise DrawingSvgError("the SVG viewBox or physical size is invalid") from exc
    if width <= 0 or height <= 0 or width_mm <= 0 or height_mm <= 0:
        raise DrawingSvgError("the SVG viewBox or physical size is invalid")
    return root, (min_x, min_y, width, height), (width_mm, height_mm)


def _millimetres(text: str) -> float:
    if not text.endswith("mm"):
        raise ValueError(text)
    return float(text[:-2])


def _svg_polylines(root):
    """Every polyline with its inherited stroke width and dash array, in document order."""

    found = []

    def walk(element, stroke_width, dasharray):
        stroke_width = element.get("stroke-width", stroke_width)
        dasharray = element.get("stroke-dasharray", dasharray)
        if element.tag == f"{{{SVG_NS}}}polyline":
            try:
                points = tuple(
                    (float(pair.split(",")[0]), float(pair.split(",")[1]))
                    for pair in element.get("points", "").split()
                )
                width = float(stroke_width) if stroke_width is not None else 0.0
                dashes = tuple(float(v) for v in dasharray.split()) if dasharray else ()
            except (ValueError, IndexError) as exc:
                raise DrawingSvgError("an SVG polyline has invalid points or stroke") from exc
            found.append((element.get("data-object"), points, width, dashes))
        for child in element:
            walk(child, stroke_width, dasharray)

    walk(root, None, None)
    return found


def _dashed(points, dashes):
    """Split a polyline into dash pieces following the dash array along its length."""

    if not dashes or sum(dashes) <= 0.0:
        return [points]
    pieces = []
    index, remaining, drawing = 0, dashes[0], True
    current: list[tuple[float, float]] = [points[0]]
    for a, b in zip(points, points[1:]):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        travelled = 0.0
        while length - travelled > remaining:
            travelled += remaining
            t = travelled / length
            point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if drawing:
                current.append(point)
                pieces.append(current)
            current = [point]
            drawing = not drawing
            index = (index + 1) % len(dashes)
            remaining = dashes[index]
        remaining -= length - travelled
        if drawing:
            current.append(b)
        else:
            current = [b]
    if drawing and len(current) > 1:
        pieces.append(current)
    return pieces


def svg_objects(svg: bytes) -> tuple[str, ...]:
    """The distinct source object ids the SVG's polylines name, sorted."""

    root, _, _ = _parse_svg(svg)
    return tuple(sorted({name for name, _, _, _ in _svg_polylines(root) if name}))


def render_svg_png(svg: bytes, *, dots_per_inch: int = 150) -> bytes:
    """Rasterise the SVG's own polylines to a PNG at the SVG's physical size.

    Only what ``drawing_svg`` writes is understood: the viewBox, the mm
    size, nested groups carrying stroke-width and stroke-dasharray, and
    polylines.  Anything else in the document is an error, not silently
    skipped.  Lines are drawn black on white, 3x supersampled and
    box-filtered down, so the PNG is a faithful preview of the vector file.
    """

    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - Pillow ships in the cad-occt extra
        raise DrawingSvgError("Pillow is required to render the SVG preview") from exc
    if isinstance(dots_per_inch, bool) or not isinstance(dots_per_inch, int) or dots_per_inch <= 0:
        raise DrawingSvgError("dots_per_inch must be a positive integer")
    root, (min_x, min_y, width, height), (width_mm, height_mm) = _parse_svg(svg)
    for element in root.iter():
        if element.tag not in {f"{{{SVG_NS}}}{name}" for name in ("svg", "title", "g", "polyline")}:
            raise DrawingSvgError(f"the SVG carries an element the renderer does not draw: {element.tag}")
    supersample = 3
    pixels_x = max(1, round(width_mm / 25.4 * dots_per_inch))
    pixels_y = max(1, round(height_mm / 25.4 * dots_per_inch))
    if pixels_x * pixels_y > 40_000_000:
        raise DrawingSvgError("the requested PNG exceeds 40 megapixels")
    scale_x = pixels_x * supersample / width
    scale_y = pixels_y * supersample / height
    image = Image.new("L", (pixels_x * supersample, pixels_y * supersample), 255)
    draw = ImageDraw.Draw(image)
    for _, points, stroke_width, dashes in _svg_polylines(root):
        if len(points) < 2:
            continue
        pixel_width = max(1, round(stroke_width * scale_x))
        for piece in _dashed(points, dashes):
            pixels = [((x - min_x) * scale_x, (y - min_y) * scale_y) for x, y in piece]
            draw.line(pixels, fill=0, width=pixel_width, joint="curve")
    image = image.resize((pixels_x, pixels_y), Image.Resampling.BOX)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


__all__ = [
    "DrawingSvgError",
    "PNG_MEDIA_TYPE",
    "SVG_MEDIA_TYPE",
    "crop_polylines",
    "drawing_svg",
    "render_svg_png",
    "svg_objects",
]
