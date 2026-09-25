"""Deterministic SVG for drawing polylines, and a PNG rendered from that SVG.

The first real drawing consumer: ``adapters.cad_execution`` returns plain
``OcctDrawingPolyline`` values (object id, ``visible``/``hidden``, points in
the drawing frame); this module crops them to the view's window and
serialises them as one SVG whose every polyline still names its source
physical object.  The PNG is rasterised from those SVG bytes and nothing
else: the renderer parses the SVG back (viewBox, physical size, the polyline
groups and dimension text with its embedded font). Section hatch strokes
come from each solid's even-odd cut boundaries. It is not a screenshot,
not a re-projection, and knows no project, run or path.

Cleanup.  ``clean_drawing`` sits beside ``crop_polylines`` between the
projection and the SVG: it drops what a pen should not draw (lines shorter
than the paper tolerance, projected edges lying on the cut, hidden lines
under visible ones or inside the cut) and joins an object's collinear
pieces, and it reports what it did by rule.  It never moves a vertex.

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
import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

from archflow.adapters.occt_backend import OcctDrawingPolyline, OcctDrawingRegion

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


# ---------------------------------------------------------------- cleanup between projection and SVG

#: Joined pieces may turn by at most this much where they meet.
_COLLINEAR_RADIANS = math.radians(0.5)
#: Numeric slack when a line's parameter range is checked for full cover.
_COVER_SLACK = 1e-9
_CLEANED_KINDS = ("visible", "hidden", "section")


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """What ``clean_drawing`` did, counted per rule in the order the rules ran.

    ``tolerance`` is in drawing units.  ``input_lines`` counts the distinct
    lines given and ``output_lines`` those returned; each other line was
    dropped by ``micro``, ``cut_precedence``, ``duplicate`` or
    ``hidden_under_cut`` or joined to a neighbour by ``collinear``, so the
    five counts add up to ``input_lines - output_lines``.
    """

    tolerance: float
    input_lines: int
    output_lines: int
    micro: int
    collinear: int
    cut_precedence: int
    duplicate: int
    hidden_under_cut: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tolerance": self.tolerance, "input_lines": self.input_lines, "output_lines": self.output_lines,
            "micro": self.micro, "collinear": self.collinear, "cut_precedence": self.cut_precedence,
            "duplicate": self.duplicate, "hidden_under_cut": self.hidden_under_cut,
        }


def _finite_points(points, minimum: int) -> bool:
    try:
        return len(points) >= minimum and all(len(p) == 2 and math.isfinite(p[0]) and math.isfinite(p[1]) for p in points)
    except TypeError:
        return False


def _closed_loops(region) -> None:
    if not isinstance(region, OcctDrawingRegion):
        raise DrawingSvgError("section regions must be OcctDrawingRegion values")
    for loop in region.loops:
        if not _finite_points(loop, 4) or loop[0] != loop[-1]:
            raise DrawingSvgError("section material needs explicitly closed finite loops")


def _path_length(points) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _end_direction(points, at_end: bool, tolerance: float):
    """The unit direction in which a line leaves through one of its ends, or None.

    It is measured from the first vertex at least ``tolerance`` inside that end
    (or the far end), so a short final chord cannot swing it.
    """

    ordered = points[::-1] if at_end else points
    end = ordered[0]
    inner = ordered[-1]
    for point in ordered[1:]:
        if math.dist(point, end) >= tolerance:
            inner = point
            break
    length = math.dist(inner, end)
    if length == 0.0:
        return None
    return (end[0] - inner[0]) / length, (end[1] - inner[1]) / length


class _EndIndex:
    """Line ends on a grid of tolerance-sized cells: every end within ``tolerance`` is in the 3 x 3 block."""

    def __init__(self, tolerance: float) -> None:
        self.tolerance = tolerance
        self.cells: dict[tuple[int, int], set[tuple[int, bool]]] = {}

    def _cell(self, point) -> tuple[int, int]:
        return math.floor(point[0] / self.tolerance), math.floor(point[1] / self.tolerance)

    def add(self, number: int, points) -> None:
        for at_end, point in ((False, points[0]), (True, points[-1])):
            self.cells.setdefault(self._cell(point), set()).add((number, at_end))

    def remove(self, number: int, points) -> None:
        for at_end, point in ((False, points[0]), (True, points[-1])):
            self.cells[self._cell(point)].discard((number, at_end))

    def near(self, point) -> list[tuple[int, bool]]:
        column, row = self._cell(point)
        return sorted(end for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                      for end in self.cells.get((column + dx, row + dy), ()))


def _join_collinear(lines, tolerance: float):
    """Join each object's pieces that continue one another; returns the lines and the number of joins.

    Two lines of one object and kind are joined where an end of each lies
    within ``tolerance`` of the other and they leave that joint in opposite
    directions to within 0.5 degrees.  All vertices stay where they were: a
    shared end point is written once, and a gap of at most the tolerance
    becomes a bridging segment.  Lines are taken in sorted order and each is
    extended at its end, then at its start, as long as a continuation exists,
    choosing the straightest, then the nearest, then the first; the result
    is a fixed point, so joining again finds nothing.
    """

    limit = math.cos(_COLLINEAR_RADIANS)
    groups: dict[tuple[str, str], list[OcctDrawingPolyline]] = {}
    for line in lines:
        groups.setdefault((line.object_id, line.kind), []).append(line)
    result: list[OcctDrawingPolyline] = []
    joins = 0
    for (object_id, kind), members in groups.items():
        chains = [list(line.points) for line in members]
        changed = [False] * len(chains)
        alive = [True] * len(chains)
        index = _EndIndex(tolerance)
        for number, chain in enumerate(chains):
            index.add(number, chain)
        for number in range(len(chains)):
            if not alive[number]:
                continue
            for at_end in (True, False):
                while True:
                    chain = chains[number]
                    joint = chain[-1] if at_end else chain[0]
                    outward = _end_direction(chain, at_end, tolerance)
                    if outward is None:
                        break
                    best = None
                    for other, other_at_end in index.near(joint):
                        if other == number or not alive[other]:
                            continue
                        candidate = chains[other]
                        meeting = candidate[-1] if other_at_end else candidate[0]
                        gap = math.dist(joint, meeting)
                        leaving = _end_direction(candidate, other_at_end, tolerance)
                        if gap > tolerance or leaving is None:
                            continue
                        # Continuation: the two lines leave the joint in opposite directions.
                        straightness = -(outward[0] * leaving[0] + outward[1] * leaving[1])
                        if straightness < limit:
                            continue
                        key = (-straightness, gap, other, other_at_end)
                        if best is None or key < best:
                            best = key
                    if best is None:
                        break
                    _, _, other, other_at_end = best
                    index.remove(number, chain)
                    index.remove(other, chains[other])
                    piece = chains[other]
                    if at_end:
                        piece = piece[::-1] if other_at_end else piece
                        joined = chain + (piece[1:] if piece[0] == chain[-1] else piece)
                    else:
                        piece = piece if other_at_end else piece[::-1]
                        joined = (piece[:-1] if piece[-1] == chain[0] else piece) + chain
                    chains[number] = joined
                    alive[other] = False
                    changed[number] = True
                    joins += 1
                    index.add(number, joined)
        for number, chain in enumerate(chains):
            if alive[number]:
                points = tuple(chain)
                if changed[number]:
                    points = min(points, points[::-1])
                result.append(OcctDrawingPolyline(object_id, kind, points))
    distinct = sorted(set(result), key=lambda line: (line.object_id, line.kind, line.points))
    # Two chains that came out identical are one line: the join that made the second absorbed it.
    return distinct, joins + len(result) - len(distinct)


def _slab(start: float, step: float, low: float, high: float):
    """The parameters t with low <= start + t * step <= high, or None."""

    if step == 0.0:
        return (-math.inf, math.inf) if low <= start <= high else None
    first, second = (low - start) / step, (high - start) / step
    return (first, second) if first <= second else (second, first)


def _capsule_interval(a, b, c, d, tolerance: float):
    """The parameters t in [0, 1] where a + t (b - a) lies within ``tolerance`` of the segment c-d, or None.

    The points within the tolerance of a segment form a convex capsule (a
    rectangle along it and a disc at each end), so the answer is one
    interval: from the lowest entry to the highest exit over the three parts.
    """

    ex, ey = b[0] - a[0], b[1] - a[1]
    found = []
    for centre in (c, d):
        ox, oy = a[0] - centre[0], a[1] - centre[1]
        quadratic, linear = ex * ex + ey * ey, 2.0 * (ox * ex + oy * ey)
        constant = ox * ox + oy * oy - tolerance * tolerance
        discriminant = linear * linear - 4.0 * quadratic * constant
        if quadratic > 0.0 and discriminant >= 0.0:
            root = math.sqrt(discriminant)
            found.append(((-linear - root) / (2.0 * quadratic), (-linear + root) / (2.0 * quadratic)))
    length = math.dist(c, d)
    if length > 0.0:
        ux, uy = (d[0] - c[0]) / length, (d[1] - c[1]) / length
        ox, oy = a[0] - c[0], a[1] - c[1]
        along = _slab(ox * ux + oy * uy, ex * ux + ey * uy, 0.0, length)
        across = _slab(oy * ux - ox * uy, ey * ux - ex * uy, -tolerance, tolerance)
        if along is not None and across is not None and max(along[0], across[0]) <= min(along[1], across[1]):
            found.append((max(along[0], across[0]), min(along[1], across[1])))
    if not found:
        return None
    low, high = max(0.0, min(row[0] for row in found)), min(1.0, max(row[1] for row in found))
    return (low, high) if low <= high else None


def _covered(intervals, slack: float) -> bool:
    """Whether the intervals leave no gap longer than ``slack`` in [0, 1]."""

    reach = 0.0
    for low, high in sorted(intervals):
        if reach >= 1.0 - slack:
            break
        if low > reach + slack:
            return False
        reach = max(reach, high)
    return reach >= 1.0 - slack


class _SegmentCover:
    """The segments of some lines, to ask whether another line lies within ``tolerance`` of them all along."""

    _MAX_CELLS = 1024

    def __init__(self, lines, tolerance: float) -> None:
        self.tolerance = tolerance
        self.segments = [(a, b) for line in lines for a, b in zip(line.points, line.points[1:]) if a != b]
        xs = [p[0] for segment in self.segments for p in segment]
        ys = [p[1] for segment in self.segments for p in segment]
        extent = max(max(xs) - min(xs), max(ys) - min(ys)) if xs else 0.0
        self.cell = max(4.0 * tolerance, extent / 256.0)
        self.cells: dict[tuple[int, int], list[int]] = {}
        self.large: list[int] = []
        for number, (a, b) in enumerate(self.segments):
            keys = self._keys(a, b)
            if keys is None:
                self.large.append(number)
            else:
                for key in keys:
                    self.cells.setdefault(key, []).append(number)

    def _keys(self, a, b):
        pad = self.tolerance
        x0, x1 = math.floor((min(a[0], b[0]) - pad) / self.cell), math.floor((max(a[0], b[0]) + pad) / self.cell)
        y0, y1 = math.floor((min(a[1], b[1]) - pad) / self.cell), math.floor((max(a[1], b[1]) + pad) / self.cell)
        if (x1 - x0 + 1) * (y1 - y0 + 1) > self._MAX_CELLS:
            return None
        return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

    def _candidates(self, a, b):
        keys = self._keys(a, b)
        if keys is None:
            return range(len(self.segments))
        found = set(self.large)
        for key in keys:
            found.update(self.cells.get(key, ()))
        return sorted(found)

    def covers(self, points) -> bool:
        if not self.segments:
            return False
        for a, b in zip(points, points[1:]):
            if a == b:
                continue
            intervals = []
            for number in self._candidates(a, b):
                interval = _capsule_interval(a, b, *self.segments[number], self.tolerance)
                if interval is not None:
                    intervals.append(interval)
            if not _covered(intervals, _COVER_SLACK):
                return False
        return True


class _RegionCover:
    """Section regions, to ask whether a line lies inside them (even-odd) all along."""

    def __init__(self, regions, tolerance: float) -> None:
        self.tolerance = tolerance
        self.regions = []
        for region in regions:
            points = [p for loop in region.loops for p in loop]
            edges = [(a, b) for loop in region.loops for a, b in zip(loop, loop[1:]) if a != b]
            bounds = (min(p[0] for p in points), min(p[1] for p in points),
                      max(p[0] for p in points), max(p[1] for p in points))
            self.regions.append((bounds, edges))

    @staticmethod
    def _inside(a, b, edges):
        """The parameter intervals of the line through a and b that lie inside one region, even-odd."""

        ex, ey = b[0] - a[0], b[1] - a[1]
        squared = ex * ex + ey * ey
        crossings = []
        for c, d in edges:
            side_c = ex * (c[1] - a[1]) - ey * (c[0] - a[0])
            side_d = ex * (d[1] - a[1]) - ey * (d[0] - a[0])
            # Half-open, like the hatch: a vertex on the line is counted once or not at all.
            if (side_c > 0.0) != (side_d > 0.0):
                share = side_c / (side_c - side_d)
                x, y = c[0] + share * (d[0] - c[0]), c[1] + share * (d[1] - c[1])
                crossings.append(((x - a[0]) * ex + (y - a[1]) * ey) / squared)
        crossings.sort()
        return list(zip(crossings[::2], crossings[1::2]))

    def covers(self, points) -> bool:
        if not self.regions:
            return False
        pad = self.tolerance
        for a, b in zip(points, points[1:]):
            if a == b:
                continue
            low_x, high_x = min(a[0], b[0]) - pad, max(a[0], b[0]) + pad
            low_y, high_y = min(a[1], b[1]) - pad, max(a[1], b[1]) + pad
            intervals = []
            for (x0, y0, x1, y1), edges in self.regions:
                if x1 < low_x or x0 > high_x or y1 < low_y or y0 > high_y:
                    continue
                intervals.extend(self._inside(a, b, edges))
            # An end may overrun the boundary by up to the tolerance.
            if not _covered(intervals, self.tolerance / math.dist(a, b)):
                return False
        return True


def clean_drawing(
    lines: Sequence[OcctDrawingPolyline], regions: Sequence[OcctDrawingRegion], *, tolerance: float,
) -> tuple[tuple[OcctDrawingPolyline, ...], CleanupReport]:
    """Remove what a projection draws that no drawing should; a pure step between projection and SVG.

    ``lines`` are ``visible``, ``hidden`` and ``section`` polylines in the
    drawing frame, as ``crop_polylines`` returns them; ``regions`` are the
    section regions of the same view.  ``tolerance`` is in drawing units:
    the paper tolerance times the sheet scale.  The rules run in this order,
    each on what the one before kept:

    1. ``micro``: a line shorter than the tolerance is dropped;
    2. ``collinear``: an object's pieces that meet within the tolerance and
       continue one another to within 0.5 degrees are joined into one line
       (``_join_collinear``); no vertex moves;
    3. ``cut_precedence``: a visible or hidden line lying within the
       tolerance of section lines all along is dropped, so the cut edge is
       drawn once, by the section;
    4. ``duplicate``: a hidden line lying within the tolerance of visible
       lines all along is dropped; the visible line stays;
    5. ``hidden_under_cut``: a hidden line lying inside the section regions
       (even-odd) is dropped.  Pass hidden lines only when they will be drawn.

    A line is kept or dropped whole: a partly covered line stays.  The lines
    come back sorted as ``crop_polylines`` sorts them, with the report;
    cleaning them again changes nothing.  What the hidden-line solve itself
    misjudges is outside these rules.
    """

    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance)
            or tolerance <= 0.0):
        raise DrawingSvgError("the cleanup tolerance must be a positive distance in drawing units")
    tolerance = float(tolerance)
    distinct = set()
    for line in lines:
        if not isinstance(line, OcctDrawingPolyline):
            raise DrawingSvgError("lines must be OcctDrawingPolyline values")
        if line.kind not in _CLEANED_KINDS:
            raise DrawingSvgError(f"clean_drawing takes visible, hidden and section lines, not {line.kind!r}")
        if not _finite_points(line.points, 2):
            raise DrawingSvgError("a drawing line needs at least two finite points")
        distinct.add(line)
    for region in regions:
        _closed_loops(region)
    kept = sorted(distinct, key=lambda line: (line.object_id, line.kind, line.points))
    counts = {}
    remaining = [line for line in kept if _path_length(line.points) >= tolerance]
    counts["micro"] = len(kept) - len(remaining)
    remaining, counts["collinear"] = _join_collinear(remaining, tolerance)
    cut = _SegmentCover([line for line in remaining if line.kind == "section"], tolerance)
    kept = [line for line in remaining if line.kind == "section" or not cut.covers(line.points)]
    counts["cut_precedence"] = len(remaining) - len(kept)
    seen = _SegmentCover([line for line in kept if line.kind == "visible"], tolerance)
    remaining = [line for line in kept if line.kind != "hidden" or not seen.covers(line.points)]
    counts["duplicate"] = len(kept) - len(remaining)
    under = _RegionCover(regions, tolerance)
    kept = [line for line in remaining if line.kind != "hidden" or not under.covers(line.points)]
    counts["hidden_under_cut"] = len(remaining) - len(kept)
    result = tuple(sorted(kept, key=lambda line: (line.object_id, line.kind, line.points)))
    return result, CleanupReport(tolerance=tolerance, input_lines=len(distinct), output_lines=len(result), **counts)


def _number(value: float) -> str:
    text = f"{value:.{_DECIMALS}f}"
    return "0." + "0" * _DECIMALS if text == "-0." + "0" * _DECIMALS else text



def dressing_assets() -> list[dict]:
    """Small editable plan symbols. Coordinates use a centred one-unit square."""
    def circle(cx, cy, rx, ry, count=24):
        return [(round(cx + rx * math.cos(i * 2 * math.pi / count), 6),
                 round(cy + ry * math.sin(i * 2 * math.pi / count), 6)) for i in range(count + 1)]
    return [
        {"id": "person-plan", "polylines": [circle(0, .12, .16, .18),
            [(-.4, -.1), (-.28, -.25), (.18, -.3), (.38, -.15), (.28, .02)],
            [(-.2, -.28), (-.27, -.48)], [( .12, -.3), (.26, -.42)]]},
        {"id": "tree-plan", "polylines": [
            [(round((.44 + .04 * math.sin(i * 10 * math.pi / 48)) * math.cos(i * 2 * math.pi / 48), 6),
              round((.44 + .04 * math.sin(i * 10 * math.pi / 48)) * math.sin(i * 2 * math.pi / 48), 6)) for i in range(49)],
            [(-.18, -.22), (0, 0), (.17, .25)], [(0, 0), (-.27, .16)], [(0, 0), (.31, -.08)]]},
    ]


def _dressing_svg(dressing, crop, paper_per_unit):
    assets = {row["id"]: row["polylines"] for row in dressing_assets()}
    u0, _, _, v1 = crop
    result = ['  <g id="dressing" fill="none" stroke="#000" stroke-linecap="round" stroke-linejoin="round" '
              f'stroke-width="{_number(.15 / paper_per_unit)}">']
    for item in dressing:
        if item["status"] != "resolved":
            continue
        u, v = item["resolvedUv"]
        factor = -1 if item.get("flipped", False) else 1
        result.append(f'    <g data-dressing={quoteattr(item["id"])} data-asset={quoteattr(item["assetId"])}>')
        for line in assets[item["assetId"]]:
            points = " ".join(f"{_number(u + x * item['size'] * factor - u0)},{_number(v1 - v - y * item['size'])}" for x, y in line)
            result.append(f'      <polyline points="{points}"/>')
        result.append('    </g>')
    result.append('  </g>')
    return result


def drawing_svg(
    lines: Sequence[OcctDrawingPolyline], *, crop_uv, unit: str, scale_denominator: int,
    hidden_lines: bool, title: str, regions: Sequence[OcctDrawingRegion] = (),
    graphics: Mapping | None = None, dimensions: Sequence[Mapping] = (), dressing: Sequence[Mapping] = (),
    projection: str | None = None,
) -> bytes:
    """One SVG of the cropped polylines: hidden lines (dashed, optional) under visible lines.

    ``crop_uv`` is the drawing window in frame units and becomes the viewBox
    (u rightwards, v upwards; SVG y runs down, so ``y = v_max - v``).
    ``unit`` names the frame unit; ``scale_denominator`` gives the sheet
    scale 1:N for the physical size.  ``hidden_lines`` False omits the hidden
    group entirely; the polylines still carry their object ids either way.
    ``projection`` "section-perspective" marks a perspective whose scale holds
    at its section plane (``data-projection``, ``data-scale-at``); an
    orthographic drawing, the default, keeps its bytes.
    """

    if projection not in (None, "section-perspective"):
        raise DrawingSvgError("projection must be None (orthographic) or section-perspective")
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
        + ('data-projection="section-perspective" data-scale-at="section-plane" ' if projection else "")
        + f'data-crop-uv="{" ".join(_number(v) for v in crop)}" data-hidden-lines="{"true" if hidden_lines else "false"}">',
        f"  <title>{_escape(title)}</title>",
    ]
    body: list[str] = []
    if hidden_lines:
        dash = " ".join(pen(mm) for mm in _HIDDEN_DASH_MM)
        body.extend(group("hidden", f'stroke-width="{pen(_HIDDEN_PEN_MM)}" stroke-dasharray="{dash}"'))
    if graphics is not None:
        for key in ("visibleLineMm", "cutLineMm", "hatchSpacingMm"):
            value = graphics.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise DrawingSvgError(f"{key} must be a positive paper millimetre value")
    body.extend(group("visible", f'stroke-width="{pen(_VISIBLE_PEN_MM if graphics is None else graphics["visibleLineMm"])}"'))
    if graphics is not None:
        hatch = _section_hatch(regions, crop, graphics["hatchSpacingMm"] / paper_per_unit)
        body.append(f'  <g id="section-hatch" fill="none" stroke="#000" stroke-width="{pen(0.1)}">')
        body.extend(f'    <polyline data-object={quoteattr(line.object_id)} points="{points_attribute(line.points)}"/>' for line in hatch)
        body.append("  </g>")
        body.extend(group("section", f'stroke-width="{pen(graphics["cutLineMm"])}"'))
    if dimensions:
        body.extend(_dimension_svg(dimensions, crop, paper_per_unit))
    if dressing:
        body.extend(_dressing_svg(dressing, crop, paper_per_unit))
    return ("\n".join(head + body + ["</svg>", ""])).encode("utf-8")


def _section_hatch(regions, crop, spacing):
    """45 degree strokes clipped by each solid's even-odd loops, including holes."""
    result = []
    # y - x is constant on a hatch line; the perpendicular spacing is explicit on paper.
    step = spacing * math.sqrt(2)
    for region in regions:
        if not isinstance(region, OcctDrawingRegion):
            raise DrawingSvgError("section regions must be OcctDrawingRegion values")
        for loop in region.loops:
            if (len(loop) < 4 or loop[0] != loop[-1]
                    or any(len(p) != 2 or any(not math.isfinite(v) for v in p) for p in loop)):
                raise DrawingSvgError("section material needs explicitly closed finite loops")
        coordinates = [y - x for loop in region.loops for x, y in loop]
        if not coordinates:
            continue
        low = max(min(coordinates), crop[1] - crop[2])
        high = min(max(coordinates), crop[3] - crop[0])
        first, last = math.ceil(low / step), math.floor(high / step)
        if last - first > 100_000:
            raise DrawingSvgError("the section hatch exceeds 100000 strokes")
        for index in range(first, last + 1):
            level = index * step
            crossings = []
            for loop in region.loops:
                for a, b in zip(loop, loop[1:]):
                    da, db = a[1] - a[0], b[1] - b[0]
                    if (da <= level < db) or (db <= level < da):
                        t = (level - da) / (db - da)
                        crossings.append(a[0] + t * (b[0] - a[0]))
            crossings.sort()
            if len(crossings) % 2:
                raise DrawingSvgError("section material does not have even-odd hatch intersections")
            for a, b in zip(crossings[::2], crossings[1::2]):
                clipped = _clip_segment((a, a + level), (b, b + level), crop)
                if clipped is not None and math.dist(*clipped) > 1e-10:
                    result.append(OcctDrawingPolyline(region.object_id, "hatch", clipped))
    return tuple(sorted(set(result), key=lambda row: (row.object_id, row.points)))


_DIMENSION_FONT_PREFIX = "@font-face{font-family:DrawingDimension;src:url(data:font/ttf;base64,"
_DIMENSION_FONT_SUFFIX = ") format('truetype');}"


def _dimension_layout(row, crop, paper_per_unit):
    """One layout in serialized SVG coordinates, including its stroked/glyph bounds."""
    from PIL import ImageFont

    crop = _crop(crop)
    try:
        if (isinstance(paper_per_unit, bool) or not isinstance(paper_per_unit, (int, float))
                or not math.isfinite(paper_per_unit) or paper_per_unit <= 0):
            raise ValueError("paper_per_unit must be positive finite millimetres per drawing unit")
        name, label = row["id"], row["label"]
        a, b = tuple(row["start"]), tuple(row["end"])
        offset = row["offsetMm"] / paper_per_unit
        value = row["value"]
        if (not isinstance(name, str) or not name or not isinstance(label, str) or not label
                or any(c in label for c in "\r\n\t") or len(a) != 2 or len(b) != 2
                or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or any(isinstance(v, bool) or not math.isfinite(v) for v in (*a, *b, offset)) or a == b):
            raise ValueError("invalid resolved dimension")
    except (KeyError, TypeError, ValueError) as exc:
        raise DrawingSvgError(f"a resolved dimension is invalid: {exc}") from exc
    length = math.dist(a, b)
    along = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    normal = (-along[1], along[0])
    shifted = [tuple(p[i] + normal[i] * offset for i in range(2)) for p in (a, b)]
    marks = [(a, shifted[0]), (b, shifted[1]), tuple(shifted)]
    tick = 0.7 / paper_per_unit
    for p in shifted:
        marks.append(tuple(tuple(p[i] + sign * tick * (along[i] + normal[i]) for i in range(2)) for sign in (-1, 1)))
    # Bounds use the coordinates and sizes the SVG actually serializes, not unrounded approximations.
    quantize = lambda value: float(_number(value))
    marks = tuple(tuple((quantize(p[0] - crop[0]), quantize(crop[3] - p[1])) for p in mark) for mark in marks)
    x = quantize((shifted[0][0] + shifted[1][0]) / 2 - crop[0])
    y = quantize(crop[3] - (shifted[0][1] + shifted[1][1]) / 2 - 1.2 / paper_per_unit)
    size, pen = quantize(3 / paper_per_unit), quantize(0.13 / paper_per_unit)
    # This is the same embedded native font as the serializer and PNG renderer. A large em
    # keeps hinted metric rounding below a thousandth of the 3 mm text size.
    font = ImageFont.load_default(size=1000)
    left, top, right, bottom = font.getbbox(label, anchor="ms")
    text_box = (x + (left - 1) * size / 1000, y + (top - 1) * size / 1000,
                x + (right + 1) * size / 1000, y + (bottom + 1) * size / 1000)
    xs = [p[0] for mark in marks for p in mark]
    ys = [p[1] for mark in marks for p in mark]
    bounds = (min(min(xs) - pen / 2, text_box[0]), min(min(ys) - pen / 2, text_box[1]),
              max(max(xs) + pen / 2, text_box[2]), max(max(ys) + pen / 2, text_box[3]))
    fits = (bounds[0] >= 0 and bounds[1] >= 0
            and bounds[2] <= quantize(crop[2] - crop[0]) and bounds[3] <= quantize(crop[3] - crop[1]))
    return marks, (x, y, size), fits


def dimension_placement_fits(row: Mapping, crop, paper_per_unit: float) -> bool:
    """Whether all dimension marks, stroke widths and native text fit in the view.

    Endpoints/crop use STEP drawing units; ``paper_per_unit`` is millimetres
    per STEP unit at the sheet scale. Invalid inputs raise DrawingSvgError;
    valid layouts crossing the crop return False without changing the row.
    """
    return _dimension_layout(row, crop, paper_per_unit)[2]


def _dimension_svg(dimensions, crop, paper_per_unit):
    """Dimension marks and native text; the embedded font is also read by the PNG renderer."""
    from PIL import ImageFont

    body = []
    resolved = [row for row in dimensions if row.get("status") == "resolved"]
    if not resolved:
        return body
    # Pillow ships this compact font. Embedding those bytes avoids a machine font/path dependency.
    font = ImageFont.load_default(size=12)
    font_data = base64.b64encode(font.font_bytes).decode("ascii")
    body.append(f'  <style>{_DIMENSION_FONT_PREFIX}{font_data}{_DIMENSION_FONT_SUFFIX}</style>')
    body.append(f'  <g id="dimensions" fill="none" stroke="#000" stroke-width="{_number(0.13 / paper_per_unit)}">')
    for row in resolved:
        marks, (x, y, size), fits = _dimension_layout(row, crop, paper_per_unit)
        name, label = row["id"], row["label"]
        if not fits:
            raise DrawingSvgError(f"resolved dimension {name!r} has text or marks outside the view")
        for mark in marks:
            points = " ".join(f"{_number(px)},{_number(py)}" for px, py in mark)
            body.append(f'    <polyline data-dimension={quoteattr(name)} points="{points}"/>')
        body.append(f'    <text data-dimension={quoteattr(name)} x="{_number(x)}" y="{_number(y)}" '
                    f'font-family="DrawingDimension" font-size="{_number(size)}" '
                    f'text-anchor="middle" fill="#000" stroke="none">{_escape(label)}</text>')
    body.append("  </g>")
    return body


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
    polylines and dimension text with an embedded font. Anything else is an error, not silently
    skipped.  Lines are drawn black on white, 3x supersampled and
    box-filtered down, so the PNG is a faithful preview of the vector file.
    """

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - Pillow ships in the cad-occt extra
        raise DrawingSvgError("Pillow is required to render the SVG preview") from exc
    if isinstance(dots_per_inch, bool) or not isinstance(dots_per_inch, int) or dots_per_inch <= 0:
        raise DrawingSvgError("dots_per_inch must be a positive integer")
    root, (min_x, min_y, width, height), (width_mm, height_mm) = _parse_svg(svg)
    for element in root.iter():
        if element.tag not in {f"{{{SVG_NS}}}{name}" for name in ("svg", "title", "g", "polyline", "style", "text")}:
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
    texts = list(root.iter(f"{{{SVG_NS}}}text"))
    styles = list(root.iter(f"{{{SVG_NS}}}style"))
    if texts or styles:
        try:
            if len(styles) != 1:
                raise ValueError("dimension text needs exactly one embedded font")
            style = styles[0].text or ""
            if not style.startswith(_DIMENSION_FONT_PREFIX) or not style.endswith(_DIMENSION_FONT_SUFFIX):
                raise ValueError("unsupported SVG font style")
            font_data = base64.b64decode(style[len(_DIMENSION_FONT_PREFIX):-len(_DIMENSION_FONT_SUFFIX)], validate=True)
            for element in texts:
                if (element.get("font-family") != "DrawingDimension" or element.get("text-anchor") != "middle"
                        or element.get("fill") != "#000" or element.get("stroke") != "none"):
                    raise ValueError("unsupported dimension text style")
                x, y, size = (float(element.get(key, "")) for key in ("x", "y", "font-size"))
                if any(not math.isfinite(v) for v in (x, y, size)) or size <= 0:
                    raise ValueError("invalid text position or size")
                font = ImageFont.truetype(BytesIO(font_data), size=max(1, round(size * scale_y)))
                draw.text(((x - min_x) * scale_x, (y - min_y) * scale_y), element.text or "", font=font, fill=0, anchor="ms")
        except (ValueError, TypeError, OSError) as exc:
            raise DrawingSvgError(f"the SVG dimension text cannot be drawn: {exc}") from exc
    image = image.resize((pixels_x, pixels_y), Image.Resampling.BOX)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


__all__ = [
    "CleanupReport",
    "DrawingSvgError",
    "PNG_MEDIA_TYPE",
    "SVG_MEDIA_TYPE",
    "clean_drawing",
    "crop_polylines",
    "drawing_svg",
    "dimension_placement_fits",
    "dressing_assets",
    "render_svg_png",
    "svg_objects",
]
