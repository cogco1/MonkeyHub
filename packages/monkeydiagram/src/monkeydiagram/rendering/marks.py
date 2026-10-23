"""What a drawn page shows under a mark: each object's ink inside the mark and inside the mark's frame.

The page is what was drawn, never a re-projection: a drawing's own marks as
``svg_object_marks`` reads them back (placed at their paper position on a
sheet), or a model view's object-ID image with its legend.

A mark is a zone on the page, by the tool that drew it:

- ``outline``: the area a closed line encloses (a circle, a closed polyline);
- ``freehand``: an outline when the stroke returns near its start, else a stroke;
- ``stroke``: the band within ``reach`` of an open line;
- ``tip``: the disc within ``reach`` of a point (an arrow's head).

For each object the zone touches this module measures its line length and the
area it bounds (poché, and the shapes its closed polylines outline) inside the
zone and inside the zone's frame: the smallest rectangle round the zone at any
angle. Comparing the two says whether the object runs through the mark or is
only grazed by it, whatever it does beyond. For a tip it measures nearness,
and the innermost shape the tip lies in counts as under it. What the measures
mean for the design is the caller's question; this module knows no record,
component tree or project, and the same page and mark give the same numbers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
import math

from .svg import DrawingMark

OUTLINE = "outline"
FREEHAND = "freehand"
STROKE = "stroke"
TIP = "tip"
TOOLS = (OUTLINE, FREEHAND, STROKE, TIP)

#: The zone a mark stands for.
AREA_ZONE = "area"
STROKE_ZONE = "stroke"
POINT_ZONE = "point"

#: A freehand stroke whose ends lie within this share of its extent closes its outline.
CLOSING_GAP = 0.2

Point2 = tuple[float, float]


class PageMarkError(ValueError):
    """A mark or a page that cannot be measured."""


@dataclass(frozen=True, slots=True)
class ObjectInk:
    """One object's ink on a page, in page units from the top-left (y down): its lines and its filled regions."""

    object_id: str
    component: str | None
    lines: tuple[tuple[Point2, ...], ...] = ()
    regions: tuple[tuple[Point2, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class InkUnderMark:
    """One object's ink inside a mark's zone and inside its frame, and how near a tip is to it (1 on it, 0 out of reach).

    ``object_id`` is None for ink that names no single object (an ID colour
    standing for a layer or a material).
    """

    object_id: str | None
    component: str | None
    line_in: float = 0.0
    line_framed: float = 0.0
    area_in: float = 0.0
    area_framed: float = 0.0
    nearness: float = 0.0


@dataclass(frozen=True, slots=True)
class MarkInk:
    """The zone a mark stands for, its area, and the ink under it, object by object in page order."""

    zone: str
    zone_area: float
    objects: tuple[InkUnderMark, ...]


def page_ink(marks: Iterable[DrawingMark], *, place: Point2 = (0.0, 0.0)) -> tuple[ObjectInk, ...]:
    """A drawing's object-named marks (``svg_object_marks``) as ink, placed at ``place`` on the page.

    A drawing that is its own page is placed at the origin; a sheet places
    each view at its own paper position. Marks naming no object are left out.
    """

    dx, dy = place
    grouped: dict[str, list] = {}
    for mark in marks:
        if not mark.object_id:
            continue
        row = grouped.setdefault(mark.object_id, [mark.component, [], []])
        row[0] = row[0] if row[0] is not None else mark.component
        row[2 if mark.polygon else 1].append(tuple((x + dx, y + dy) for x, y in mark.points_mm))
    return tuple(ObjectInk(object_id, component, tuple(lines), tuple(regions))
                 for object_id, (component, lines, regions) in sorted(grouped.items()))


def ink_under_mark(points: Sequence[Point2], tool: str, reach: float, ink: Sequence[ObjectInk]) -> MarkInk:
    """The ink of each object a mark's zone touches (``tip``: each object within reach), in ``ink`` order."""

    zone_kind, zone, tip = _zone(points, tool, reach)
    drawn = [(row, _lines(row.lines), _body(row)) for row in ink]
    if zone_kind == POINT_ZONE:
        return MarkInk(zone_kind, zone.area, tuple(_near_tip(tip, reach, drawn)))
    frame = zone.minimum_rotated_rectangle
    found = []
    for row, lines, body in drawn:
        if not any(part is not None and zone.intersects(part) for part in (lines, body)):
            continue
        found.append(InkUnderMark(
            row.object_id, row.component,
            line_in=zone.intersection(lines).length if lines is not None else 0.0,
            line_framed=frame.intersection(lines).length if lines is not None else 0.0,
            area_in=zone.intersection(body).area if body is not None else 0.0,
            area_framed=frame.intersection(body).area if body is not None else 0.0,
        ))
    return MarkInk(zone_kind, zone.area, tuple(found))


def id_image_under_marks(marks: Sequence[tuple[Sequence[Point2], str, float]], id_png: bytes,
                         legend: Sequence[Mapping], object_components: Mapping[str, str] | None = None,
                         *, blank: float = 0.05) -> tuple[MarkInk, ...]:
    """Each mark's ink on an object-ID image, the page being the image in pixels.

    ``legend`` rows are a camera pass's ``{key, color, objects, pixels}``; a
    row standing for one object is that object, any other (a layer, a
    material) is ink naming no object. Every pixel of an object is area it
    bounds. A zone whose drawn pixels are under ``blank`` of it shows nothing.
    """

    import numpy as np
    from PIL import Image

    components = object_components or {}
    with Image.open(BytesIO(id_png)) as image:
        pixels = np.asarray(image.convert("RGB"), dtype=np.uint32)
    height, width = pixels.shape[:2]
    packed = (pixels[..., 0] << 16) | (pixels[..., 1] << 8) | pixels[..., 2]
    named: dict[int, str | None] = {}
    for row in legend:
        try:
            colour = int(str(row["color"]).lstrip("#"), 16)
        except (KeyError, ValueError) as exc:
            raise PageMarkError("an ID legend row needs a #RRGGBB color") from exc
        named[colour] = str(row["key"]) if row.get("objects", 1) == 1 else None

    def counts(zone) -> tuple[int, dict[int, int]]:
        mask = _mask(zone, width, height)
        inside = packed[mask]
        return int(mask.sum()), {colour: int((inside == colour).sum()) for colour in named}

    found = []
    for points, tool, reach in marks:
        zone_kind, zone, _ = _zone(points, tool, reach)
        zone_pixels, inside = counts(zone)
        objects = []
        if zone_pixels and sum(inside.values()) >= blank * zone_pixels:
            _, framed = counts(zone.minimum_rotated_rectangle)
            for colour, count in inside.items():
                if count:
                    object_id = named[colour]
                    objects.append(InkUnderMark(
                        object_id, None if object_id is None else components.get(object_id),
                        area_in=float(count), area_framed=float(framed[colour]),
                        nearness=count / zone_pixels if zone_kind == POINT_ZONE else 0.0))
        found.append(MarkInk(zone_kind, float(zone_pixels), tuple(objects)))
    return tuple(found)


def _zone(points: Sequence[Point2], tool: str, reach: float):
    """(zone kind, shape, tip or None) for a mark's points in page units."""

    from shapely import make_valid
    from shapely.geometry import LineString, Point, Polygon

    if tool not in TOOLS:
        raise PageMarkError(f"a mark is drawn with one of {', '.join(TOOLS)}")
    if not points or not all(math.isfinite(value) for point in points for value in point):
        raise PageMarkError("a mark needs finite page points")
    if not (math.isfinite(reach) and reach > 0):
        raise PageMarkError("a mark's reach is a positive page distance")
    if tool == TIP:
        tip = Point(points[-1])
        return POINT_ZONE, tip.buffer(reach), tip
    if tool == OUTLINE or (tool == FREEHAND and _returns(points)):
        area = _areas([make_valid(Polygon(points))]) if len(points) >= 3 else None
        if area is not None:
            return AREA_ZONE, area, None
    stroke = LineString(points) if len(points) >= 2 else Point(points[0])
    return STROKE_ZONE, stroke.buffer(reach), None


def _returns(points: Sequence[Point2]) -> bool:
    if len(points) < 3:
        return False
    xs, ys = [x for x, _ in points], [y for _, y in points]
    extent = max(max(xs) - min(xs), max(ys) - min(ys))
    return extent > 0 and math.dist(points[0], points[-1]) <= CLOSING_GAP * extent


def _lines(lines):
    from shapely.geometry import MultiLineString

    kept = [line for line in lines if len(line) >= 2]
    return MultiLineString(kept) if kept else None


def _areas(shapes):
    from shapely.ops import unary_union

    parts = [part for shape in shapes for part in getattr(shape, "geoms", [shape]) if part.area > 0]
    shape = unary_union(parts) if parts else None
    return shape if shape is not None and not shape.is_empty else None


def _body(row: ObjectInk):
    """What an object bounds on the page: its poché and the shapes its closed polylines outline."""

    from shapely import make_valid
    from shapely.geometry import Polygon

    closed = [line for line in row.lines if len(line) >= 4 and line[0] == line[-1]]
    return _areas(make_valid(Polygon(points)) for points in (*row.regions, *closed) if len(points) >= 3)


def _near_tip(tip, reach: float, drawn) -> list[InkUnderMark]:
    """Objects within reach of a tip, by nearness to their lines; the innermost shape around the tip is on it."""

    near = []
    for row, lines, body in drawn:
        distance = min((tip.distance(part) for part in (lines, body) if part is not None), default=math.inf)
        if distance > reach:
            continue
        around = body.area if body is not None and body.contains(tip) else math.inf
        near.append((row, distance if lines is None else tip.distance(lines), around))
    innermost = min((around for *_, around in near), default=math.inf)
    found = []
    for row, distance, around in near:
        nearness = 1.0 if around == innermost < math.inf else max(0.0, 1.0 - distance / reach)
        if nearness > 0:
            found.append(InkUnderMark(row.object_id, row.component, nearness=nearness))
    return found


def _mask(zone, width: int, height: int):
    import numpy as np
    from PIL import Image, ImageDraw

    image = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for part in getattr(zone, "geoms", [zone]):
        draw.polygon(list(part.exterior.coords), fill=1)
        for hole in part.interiors:
            draw.polygon(list(hole.coords), fill=0)
    return np.asarray(image, dtype=bool)


__all__ = [
    "AREA_ZONE",
    "FREEHAND",
    "InkUnderMark",
    "MarkInk",
    "OUTLINE",
    "ObjectInk",
    "POINT_ZONE",
    "PageMarkError",
    "STROKE",
    "STROKE_ZONE",
    "TIP",
    "id_image_under_marks",
    "ink_under_mark",
    "page_ink",
]
