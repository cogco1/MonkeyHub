"""Diagrams drawn from data (gap 38): a network, a section schematic or a timeline from a spec, with no model.

A spec is checked, placed on one sheet in millimetres from the top left, and drawn on a ``PaperCanvas``, which the
paper renderers write as PDF, SVG and DXF. A span or a segment stands where its spec puts it, scaled to the sheet;
so does a node, or, when no node of a network states a place, every node is laid out in columns along the edges
(``lay_out``). Two styles: ``diagram-colour`` (low-saturation group tones,
lines coloured by kind) and ``diagram-mono`` (black lines, no fills, hierarchy by weight and dash) for print.

Spec coordinates are the author's own units. A network's y runs down the page, as a Board's does; a section's y is
height and runs up; a timeline's x is time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .rendering.paper import MM_PER_PT, PaperCanvas

KINDS = ("network", "section", "timeline")
STYLE_IDS = ("diagram-colour", "diagram-mono")
PAPER_MM = (420.0, 297.0)  # A3 landscape
MARGIN_MM = 15.0
LIMITS = {"nodes": 200, "edges": 400, "groups": 24, "notes": 100, "label": 120, "points": 64}

# Low-saturation fills a group names; a style's mono variant draws none of them.
TONES: dict[str, tuple[float, float, float]] = {
    "grey": (0.84, 0.84, 0.84), "lavender": (0.87, 0.84, 0.95), "sand": (0.97, 0.85, 0.66),
    "sage": (0.83, 0.91, 0.83), "rose": (0.96, 0.85, 0.85), "sky": (0.83, 0.90, 0.97), "white": (1.0, 1.0, 1.0),
}
BLACK = (0.12, 0.12, 0.12)
INK = (0.2, 0.22, 0.25)
# A topology edge's kind: its colour in diagram-colour, its width (mm) and its dash (mm) unless the edge states one.
LINES: dict[str, dict[str, Any]] = {
    "main": {"colour": BLACK, "width": 0.5, "dash": "solid", "label": "Main route"},
    "loop": {"colour": (0.91, 0.35, 0.05), "width": 0.5, "dash": "solid", "label": "Loop"},
    "return": {"colour": (0.10, 0.44, 0.76), "width": 0.4, "dash": "dashed", "label": "Return"},
    "branch": {"colour": (0.53, 0.56, 0.59), "width": 0.35, "dash": "dashed", "label": "Branch"},
}
DASHES_MM = {"solid": (), "dashed": (2.4, 1.6), "dotted": (0.5, 1.2), "long": (6.0, 1.4)}
# In mono every line is black: its kind shows in its dash where the edge states none.
MONO_DASH = {"main": "solid", "loop": "long", "return": "dashed", "branch": "dotted"}


class DiagramError(ValueError):
    """A spec that cannot be drawn, named by the path of what is wrong in it."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


@dataclass(frozen=True)
class Diagram:
    """What was drawn: the canvas with its one sheet open, the checked spec, and what the drawing had to note."""

    canvas: PaperCanvas
    spec: dict[str, Any]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------- checks
def _number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DiagramError(path, f"must be a number, not {value!r}")
    if positive and value <= 0:
        raise DiagramError(path, "must be greater than zero")
    return float(value)


def _point(value: Any, path: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise DiagramError(path, "must be a point [x, y]")
    return _number(value[0], f"{path}[0]"), _number(value[1], f"{path}[1]")


def _text(value: Any, path: str, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise DiagramError(path, "must be text")
    if len(value) > LIMITS["label"] * 4:
        raise DiagramError(path, f"is longer than {LIMITS['label'] * 4} characters")
    return value


def _choice(value: Any, path: str, allowed: Sequence[str], default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if value not in allowed:
        raise DiagramError(path, f"must be one of {', '.join(allowed)}, not {value!r}")
    return str(value)


def _list(value: Any, path: str, limit: int, *, required: bool = False) -> list:
    if value is None and not required:
        return []
    if not isinstance(value, list):
        raise DiagramError(path, "must be a list")
    if len(value) > limit:
        raise DiagramError(path, f"holds more than {limit} entries")
    return value


def _object(value: Any, path: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DiagramError(path, "must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DiagramError(path, f"has no field {unknown[0]!r}; its fields are {', '.join(sorted(allowed))}")
    return dict(value)


def check_network(spec: Any) -> dict[str, Any]:
    """The network spec, checked and with its defaults: every reference resolves and every place is a number."""

    spec = _object(spec, "spec", {"nodes", "groups", "edges", "lines", "notes", "north", "scale"})
    groups = {}
    for i, raw in enumerate(_list(spec.get("groups"), "spec.groups", LIMITS["groups"])):
        row = _object(raw, f"spec.groups[{i}]", {"id", "label", "tone"})
        gid = _text(row.get("id"), f"spec.groups[{i}].id")
        if gid in groups:
            raise DiagramError(f"spec.groups[{i}].id", f"{gid!r} is named twice")
        groups[gid] = {"id": gid, "label": _text(row.get("label"), f"spec.groups[{i}].label", required=False),
                       "tone": _choice(row.get("tone"), f"spec.groups[{i}].tone", tuple(TONES), "white")}
    nodes = {}
    for i, raw in enumerate(_list(spec.get("nodes"), "spec.nodes", LIMITS["nodes"], required=True)):
        path = f"spec.nodes[{i}]"
        row = _object(raw, path, {"id", "label", "sublabel", "group", "at", "size", "shape", "ref"})
        nid = _text(row.get("id"), f"{path}.id")
        if nid in nodes:
            raise DiagramError(f"{path}.id", f"{nid!r} is named twice")
        group = row.get("group")
        if group is not None and group not in groups:
            raise DiagramError(f"{path}.group", f"names no group; the groups are {', '.join(groups) or 'none'}")
        size = row.get("size", [40, 24])
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            raise DiagramError(f"{path}.size", "must be [width, height]")
        nodes[nid] = {"id": nid, "label": _text(row.get("label"), f"{path}.label"),
                      "sublabel": _text(row.get("sublabel"), f"{path}.sublabel", required=False),
                      "group": group,
                      "at": None if row.get("at") is None else _point(row.get("at"), f"{path}.at"),
                      "size": (_number(size[0], f"{path}.size[0]", positive=True),
                               _number(size[1], f"{path}.size[1]", positive=True)),
                      "shape": _choice(row.get("shape"), f"{path}.shape", ("box", "ellipse"), "box"),
                      "ref": _text(row.get("ref"), f"{path}.ref", required=False) or None}
    if not nodes:
        raise DiagramError("spec.nodes", "names no node")
    placed = [nid for nid, node in nodes.items() if node["at"] is not None]
    if placed and len(placed) < len(nodes):
        raise DiagramError("spec.nodes", f"{len(placed)} of {len(nodes)} nodes give at: give every node at, or none "
                                         "for a layout")
    laid_out = not placed
    edges = []
    for i, raw in enumerate(_list(spec.get("edges"), "spec.edges", LIMITS["edges"])):
        path = f"spec.edges[{i}]"
        row = _object(raw, path, {"from", "to", "line", "style", "arrow", "label", "mark", "via", "route"})
        ends = []
        for key in ("from", "to"):
            value = row.get(key)
            if isinstance(value, str):
                if value not in nodes:
                    raise DiagramError(f"{path}.{key}", f"names no node: {value!r}")
                ends.append(value)
            else:
                ends.append(_point(value, f"{path}.{key}"))
        if isinstance(ends[0], tuple) and isinstance(ends[1], tuple):
            raise DiagramError(path, "joins two points; an edge starts or ends at a node")
        if laid_out and (isinstance(ends[0], tuple) or isinstance(ends[1], tuple) or row.get("via")):
            raise DiagramError(path, "a point or via is a place in the diagram's units, which no node gives here: "
                                     "give every node at, or join two nodes")
        line = _choice(row.get("line"), f"{path}.line", tuple(LINES), "main")
        via = [_point(p, f"{path}.via[{j}]") for j, p in enumerate(_list(row.get("via"), f"{path}.via", LIMITS["points"]))]
        edges.append({"from": ends[0], "to": ends[1], "line": line,
                      "style": _choice(row.get("style"), f"{path}.style", ("solid", "dashed", "dotted"), None)
                      if row.get("style") else None,
                      "arrow": _choice(row.get("arrow"), f"{path}.arrow", ("end", "both", "none"),
                                       "none" if line == "branch" else "end"),
                      "label": _text(row.get("label"), f"{path}.label", required=False),
                      "mark": _text(row.get("mark"), f"{path}.mark", required=False),
                      "via": via,
                      "route": _choice(row.get("route"), f"{path}.route", ("orthogonal", "straight"),
                                       "straight" if via else "orthogonal")})
    lines = _object(spec.get("lines") or {}, "spec.lines", set(LINES))
    for key, value in lines.items():
        _text(value, f"spec.lines.{key}")
    notes = []
    if laid_out and (spec.get("notes") or spec.get("scale") is not None):
        raise DiagramError("spec.notes" if spec.get("notes") else "spec.scale",
                           "is placed in the diagram's units, which no node gives here: give every node at")
    for i, raw in enumerate(_list(spec.get("notes"), "spec.notes", LIMITS["notes"])):
        row = _object(raw, f"spec.notes[{i}]", {"at", "text", "tone"})
        notes.append({"at": _point(row.get("at"), f"spec.notes[{i}].at"), "text": _text(row.get("text"), f"spec.notes[{i}].text"),
                      "tone": _choice(row.get("tone"), f"spec.notes[{i}].tone", ("ink", *LINES), "ink")})
    north = None if spec.get("north") is None else _number(spec["north"], "spec.north")
    scale = None
    if spec.get("scale") is not None:
        row = _object(spec["scale"], "spec.scale", {"length", "label"})
        scale = {"length": _number(row.get("length"), "spec.scale.length", positive=True),
                 "label": _text(row.get("label"), "spec.scale.label")}
    return {"groups": list(groups.values()), "nodes": list(nodes.values()), "edges": edges, "lines": lines,
            "notes": notes, "north": north, "scale": scale, "laidOut": laid_out}


def _notes(value: Any, path: str) -> list[dict[str, Any]]:
    notes = []
    for i, raw in enumerate(_list(value, path, LIMITS["notes"])):
        row = _object(raw, f"{path}[{i}]", {"at", "text", "tone"})
        notes.append({"at": _point(row.get("at"), f"{path}[{i}].at"), "text": _text(row.get("text"), f"{path}[{i}].text"),
                      "tone": _choice(row.get("tone"), f"{path}[{i}].tone", ("ink", *LINES), "ink")})
    return notes


def _range(value: Any, path: str) -> tuple[float, float]:
    low, high = _point(value, path)
    if high <= low:
        raise DiagramError(path, "runs from a lower value to a higher one")
    return low, high


def check_section(spec: Any) -> dict[str, Any]:
    """The section spec, checked: x along the cut and y up, in the author's units (metres, as the axis says)."""

    spec = _object(spec, "spec", {"axis", "ground", "spans", "shapes", "moves", "notes"})
    axis = _object(spec.get("axis"), "spec.axis", {"x", "y", "ticks", "unit", "xLabel"})
    ticks = _object(axis.get("ticks") or {}, "spec.axis.ticks", {"x", "y"})
    checked_axis = {
        "x": _range(axis.get("x"), "spec.axis.x"), "y": _range(axis.get("y"), "spec.axis.y"),
        "ticks": {key: [_number(v, f"spec.axis.ticks.{key}[{i}]") for i, v in
                        enumerate(_list(ticks.get(key), f"spec.axis.ticks.{key}", 40))] for key in ("x", "y")},
        "unit": _text(axis.get("unit"), "spec.axis.unit", required=False) or "m",
        "xLabel": _text(axis.get("xLabel"), "spec.axis.xLabel", required=False),
    }
    ground = None
    if spec.get("ground") is not None:
        row = _object(spec["ground"], "spec.ground", {"y", "label"})
        ground = {"y": _number(row.get("y", 0), "spec.ground.y"),
                  "label": _text(row.get("label"), "spec.ground.label", required=False)}
    spans = []
    for i, raw in enumerate(_list(spec.get("spans"), "spec.spans", LIMITS["nodes"], required=True)):
        path = f"spec.spans[{i}]"
        row = _object(raw, path, {"id", "label", "x0", "x1", "y0", "y1", "tone", "floors", "floorStyle", "labelAt", "ref"})
        x0, x1 = _number(row.get("x0"), f"{path}.x0"), _number(row.get("x1"), f"{path}.x1")
        y0, y1 = _number(row.get("y0"), f"{path}.y0"), _number(row.get("y1"), f"{path}.y1")
        if x1 <= x0 or y1 <= y0:
            raise DiagramError(path, "needs x1 greater than x0 and y1 greater than y0")
        floors = [_number(v, f"{path}.floors[{j}]") for j, v in enumerate(_list(row.get("floors"), f"{path}.floors", 60))]
        if any(not y0 < y < y1 for y in floors):
            raise DiagramError(f"{path}.floors", f"every floor lies between y0 {y0} and y1 {y1}")
        spans.append({"id": _text(row.get("id"), f"{path}.id"), "label": _text(row.get("label"), f"{path}.label", required=False),
                      "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                      "tone": _choice(row.get("tone"), f"{path}.tone", tuple(TONES), "white"), "floors": floors,
                      "floorStyle": _choice(row.get("floorStyle"), f"{path}.floorStyle", ("solid", "dashed"), "solid"),
                      "labelAt": _choice(row.get("labelAt"), f"{path}.labelAt", ("inside", "above", "below"), "inside"),
                      "ref": _text(row.get("ref"), f"{path}.ref", required=False) or None})
    shapes = []
    for i, raw in enumerate(_list(spec.get("shapes"), "spec.shapes", LIMITS["nodes"])):
        path = f"spec.shapes[{i}]"
        row = _object(raw, path, {"kind", "points", "at", "size", "tone", "line"})
        kind = _choice(row.get("kind"), f"{path}.kind", ("polygon", "ellipse", "marker"))
        shape: dict[str, Any] = {"kind": kind, "tone": _choice(row.get("tone"), f"{path}.tone", tuple(TONES), None)
                                 if row.get("tone") is not None else None,
                                 "line": _choice(row.get("line"), f"{path}.line", tuple(LINES), "main")}
        if kind == "polygon":
            points = [_point(p, f"{path}.points[{j}]") for j, p in
                      enumerate(_list(row.get("points"), f"{path}.points", LIMITS["points"], required=True))]
            if len(points) < 3:
                raise DiagramError(f"{path}.points", "a polygon has three or more points")
            shape["points"] = points
        else:
            shape["at"] = _point(row.get("at"), f"{path}.at")
            if kind == "ellipse":
                size = row.get("size")
                if not isinstance(size, (list, tuple)) or len(size) != 2:
                    raise DiagramError(f"{path}.size", "must be [width, height]")
                shape["size"] = (_number(size[0], f"{path}.size[0]", positive=True),
                                 _number(size[1], f"{path}.size[1]", positive=True))
        shapes.append(shape)
    moves = []
    for i, raw in enumerate(_list(spec.get("moves"), "spec.moves", LIMITS["edges"])):
        path = f"spec.moves[{i}]"
        row = _object(raw, path, {"from", "to", "line", "style", "arrow", "label"})
        moves.append({"from": _point(row.get("from"), f"{path}.from"), "to": _point(row.get("to"), f"{path}.to"),
                      "line": _choice(row.get("line"), f"{path}.line", tuple(LINES), "main"),
                      "style": _choice(row.get("style"), f"{path}.style", ("solid", "dashed", "dotted"), None)
                      if row.get("style") else None,
                      "arrow": _choice(row.get("arrow"), f"{path}.arrow", ("end", "both", "none"), "end"),
                      "label": _text(row.get("label"), f"{path}.label", required=False)})
    return {"axis": checked_axis, "ground": ground, "spans": spans, "shapes": shapes, "moves": moves,
            "notes": _notes(spec.get("notes"), "spec.notes")}


def _colour(value: Any, path: str) -> tuple[float, float, float] | None:
    """A tone's name or a #rrggbb colour, as data (a level's own colours) rather than a style's choice."""

    if value is None:
        return None
    if isinstance(value, str) and value in TONES:
        return TONES[value]
    if isinstance(value, str) and len(value) == 7 and value[0] == "#":
        try:
            return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
        except ValueError:
            pass
    raise DiagramError(path, f"must be a tone ({', '.join(TONES)}) or a #rrggbb colour, not {value!r}")


def check_timeline(spec: Any) -> dict[str, Any]:
    """The timeline spec, checked: lanes across a time axis, segments and events on them."""

    spec = _object(spec, "spec", {"axis", "lanes", "segments", "events"})
    axis = _object(spec.get("axis"), "spec.axis", {"start", "end", "step", "unit"})
    start, end = _number(axis.get("start"), "spec.axis.start"), _number(axis.get("end"), "spec.axis.end")
    if end <= start:
        raise DiagramError("spec.axis", "runs from start to a later end")
    step = _number(axis.get("step", 1), "spec.axis.step", positive=True)
    if (end - start) / step > 200:
        raise DiagramError("spec.axis.step", "would draw more than 200 ticks")
    lanes = {}
    for i, raw in enumerate(_list(spec.get("lanes"), "spec.lanes", 30, required=True)):
        row = _object(raw, f"spec.lanes[{i}]", {"id", "label"})
        lid = _text(row.get("id"), f"spec.lanes[{i}].id")
        if lid in lanes:
            raise DiagramError(f"spec.lanes[{i}].id", f"{lid!r} is named twice")
        lanes[lid] = {"id": lid, "label": _text(row.get("label"), f"spec.lanes[{i}].label", required=False)}
    if not lanes:
        raise DiagramError("spec.lanes", "names no lane")

    def lane(value: Any, path: str) -> str:
        if value not in lanes:
            raise DiagramError(path, f"names no lane; the lanes are {', '.join(lanes)}")
        return value

    def time(value: Any, path: str) -> float:
        t = _number(value, path)
        if not start <= t <= end:
            raise DiagramError(path, f"{t:g} lies outside the axis {start:g} to {end:g}")
        return t

    segments = []
    for i, raw in enumerate(_list(spec.get("segments"), "spec.segments", 500)):
        path = f"spec.segments[{i}]"
        row = _object(raw, path, {"lane", "start", "end", "label", "colour"})
        a, b = time(row.get("start"), f"{path}.start"), time(row.get("end"), f"{path}.end")
        if b <= a:
            raise DiagramError(path, "ends after it starts")
        segments.append({"lane": lane(row.get("lane"), f"{path}.lane"), "start": a, "end": b,
                         "label": _text(row.get("label"), f"{path}.label", required=False),
                         "colour": _colour(row.get("colour", "white"), f"{path}.colour")})
    events = []
    for i, raw in enumerate(_list(spec.get("events"), "spec.events", 500)):
        path = f"spec.events[{i}]"
        row = _object(raw, path, {"lane", "at", "label", "mark", "colour"})
        events.append({"lane": lane(row.get("lane"), f"{path}.lane"), "at": time(row.get("at"), f"{path}.at"),
                       "label": _text(row.get("label"), f"{path}.label", required=False),
                       "mark": _choice(row.get("mark"), f"{path}.mark", ("diamond", "circle", "tick"), "diamond"),
                       "colour": _colour(row.get("colour"), f"{path}.colour") or BLACK})
    return {"axis": {"start": start, "end": end, "step": step,
                     "unit": _text(axis.get("unit"), "spec.axis.unit", required=False)},
            "lanes": list(lanes.values()), "segments": segments, "events": events}


def check(kind: str, spec: Any) -> dict[str, Any]:
    """A spec of ``kind``, checked; ``DiagramError`` names the first thing wrong by its path."""

    if kind == "network":
        return check_network(spec)
    if kind == "section":
        return check_section(spec)
    if kind == "timeline":
        return check_timeline(spec)
    raise DiagramError("kind", f"must be one of {', '.join(KINDS)}, not {kind!r}")


# ---------------------------------------------------------------- the sheet in millimetres
class _Sheet:
    """Drawing in millimetres from the top left on one open canvas sheet."""

    def __init__(self, canvas: PaperCanvas, size_mm: tuple[float, float]) -> None:
        self.canvas, self.height = canvas, size_mm[1]

    def _pt(self, x: float, y: float) -> tuple[float, float]:
        return x / MM_PER_PT, (self.height - y) / MM_PER_PT

    def path(self, points: Sequence[tuple[float, float]], *, width: float = 0.25, colour=BLACK,
             dash: Sequence[float] = (), fill=None, close: bool = False) -> None:
        c = self.canvas
        c.setLineWidth(width / MM_PER_PT)
        c.setStrokeColor((*colour, 1))
        c.setDash(tuple(v / MM_PER_PT for v in dash) if dash else ())
        if fill is not None:
            c.setFillColor((*fill, 1))
        p = c.beginPath()
        p.moveTo(*self._pt(*points[0]))
        for point in points[1:]:
            p.lineTo(*self._pt(*point))
        if close:
            p.close()
        c.drawPath(p, stroke=1 if width > 0 else 0, fill=1 if fill is not None else 0)
        c.setDash(())

    def fill(self, points: Sequence[tuple[float, float]], colour) -> None:
        c = self.canvas
        c.setFillColor((*colour, 1))
        p = c.beginPath()
        p.moveTo(*self._pt(*points[0]))
        for point in points[1:]:
            p.lineTo(*self._pt(*point))
        p.close()
        c.drawPath(p, stroke=0, fill=1)

    def circle(self, x: float, y: float, r: float, *, width: float = 0.25, colour=BLACK, fill=None) -> None:
        c = self.canvas
        c.setLineWidth(width / MM_PER_PT)
        c.setStrokeColor((*colour, 1))
        c.setDash(())
        if fill is not None:
            c.setFillColor((*fill, 1))
        c.circle(*self._pt(x, y), r / MM_PER_PT, stroke=1, fill=1 if fill is not None else 0)

    def width(self, value: str, size_pt: float, bold: bool = False) -> float:
        return self.canvas.stringWidth(value, "bold" if bold else "normal", size_pt) * MM_PER_PT

    def text(self, x: float, y: float, value: str, size_pt: float, *, bold: bool = False, align: str = "left",
             colour=BLACK) -> None:
        """One line whose baseline is at y."""

        c = self.canvas
        c.setFillColor((*colour, 1))
        c.setFont("bold" if bold else "normal", size_pt)
        draw = {"left": c.drawString, "center": c.drawCentredString, "right": c.drawRightString}[align]
        draw(*self._pt(x, y), value)

    def lines(self, x: float, top: float, value: str, size_pt: float, *, bold: bool = False, align: str = "left",
              colour=BLACK, leading: float = 1.3) -> float:
        """Lines split at newlines, the first with its cap height at ``top``; returns the y under the last."""

        step = size_pt * MM_PER_PT * leading
        y = top + size_pt * MM_PER_PT * 0.75
        for line in value.split("\n"):
            self.text(x, y, line, size_pt, bold=bold, align=align, colour=colour)
            y += step
        return y - step + size_pt * MM_PER_PT * 0.3

    def arrowhead(self, tip: tuple[float, float], back: tuple[float, float], *, size: float, colour) -> None:
        heading = math.atan2(tip[1] - back[1], tip[0] - back[0])
        wing = math.radians(24)
        left = (tip[0] - size * math.cos(heading - wing), tip[1] - size * math.sin(heading - wing))
        right = (tip[0] - size * math.cos(heading + wing), tip[1] - size * math.sin(heading + wing))
        self.fill([tip, left, right], colour)


# ---------------------------------------------------------------- network
def _box(node: Mapping[str, Any]) -> tuple[float, float, float, float]:
    (cx, cy), (w, h) = node["at"], node["size"]
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def _crosses(a: tuple[float, float], b: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    """Whether the axis-parallel or straight segment a-b passes through the inside of box (its edge not counted)."""

    x0, y0, x1, y1 = box
    inset = 1e-6
    x0, y0, x1, y1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    # Liang-Barsky clip of the segment to the box.
    dx, dy = b[0] - a[0], b[1] - a[1]
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                lo = max(lo, t)
            else:
                hi = min(hi, t)
            if lo > hi:
                return False
    return hi - lo > 1e-9


def _boundary(box: tuple[float, float, float, float], toward: tuple[float, float]) -> tuple[float, float]:
    """Where the line from the box's centre toward a point leaves the box."""

    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return cx, cy
    t = min(((x1 - cx) / dx if dx > 0 else (x0 - cx) / dx if dx < 0 else math.inf),
            ((y1 - cy) / dy if dy > 0 else (y0 - cy) / dy if dy < 0 else math.inf))
    return cx + dx * t, cy + dy * t


def _orthogonal(a: tuple[float, float, float, float], b: tuple[float, float, float, float], *,
                horizontal: bool) -> list[tuple[float, float]]:
    """An L or Z run from box a to box b: leaving a's side toward b along x (horizontal) or y."""

    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    if horizontal:
        overlap = (max(a[1], b[1]), min(a[3], b[3]))
        start_x = a[2] if bx >= ax else a[0]
        end_x = b[0] if bx >= ax else b[2]
        if overlap[1] - overlap[0] > 1e-9:
            y = (overlap[0] + overlap[1]) / 2
            return [(start_x, y), (end_x, y)]
        if (bx >= ax and b[0] >= a[2]) or (bx < ax and b[2] <= a[0]):
            mid = (start_x + end_x) / 2
            return [(start_x, ay), (mid, ay), (mid, by), (end_x, by)]
        # b lies over or under a: leave a's side, then drop to b's top or bottom.
        return [(start_x, ay), (bx, ay), (bx, b[1] if by > ay else b[3])]
    overlap = (max(a[0], b[0]), min(a[2], b[2]))
    start_y = a[3] if by >= ay else a[1]
    end_y = b[1] if by >= ay else b[3]
    if overlap[1] - overlap[0] > 1e-9:
        x = (overlap[0] + overlap[1]) / 2
        return [(x, start_y), (x, end_y)]
    if (by >= ay and b[1] >= a[3]) or (by < ay and b[3] <= a[1]):
        mid = (start_y + end_y) / 2
        return [(ax, start_y), (ax, mid), (bx, mid), (bx, end_y)]
    return [(ax, start_y), (ax, by), (b[0] if bx > ax else b[2], by)]


def _route(edge: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]]) -> tuple[list[tuple[float, float]], bool]:
    """The edge's points in spec units, and whether it runs through a node it does not join."""

    start, end = edge["from"], edge["to"]
    boxes = {nid: _box(node) for nid, node in nodes.items()}
    others = [box for nid, box in boxes.items() if nid not in (start, end)]

    def end_point(value, toward):
        """Where an edge leaves a node toward a point: straight out of a side the point lies level with."""
        if not isinstance(value, str):
            return value
        x0, y0, x1, y1 = boxes[value]
        if y0 <= toward[1] <= y1 and not x0 <= toward[0] <= x1:
            return (x1 if toward[0] > x1 else x0, toward[1])
        if x0 <= toward[0] <= x1 and not y0 <= toward[1] <= y1:
            return (toward[0], y1 if toward[1] > y1 else y0)
        return _boundary(boxes[value], toward)

    def centre(value):
        return nodes[value]["at"] if isinstance(value, str) else value

    if edge["via"] or edge["route"] == "straight":
        inner = list(edge["via"])
        first = end_point(start, inner[0] if inner else centre(end))
        last = end_point(end, inner[-1] if inner else centre(start))
        points = [first, *inner, last]
    elif isinstance(start, str) and isinstance(end, str):
        a, b = boxes[start], boxes[end]
        dx = abs(nodes[end]["at"][0] - nodes[start]["at"][0])
        dy = abs(nodes[end]["at"][1] - nodes[start]["at"][1])
        tries = [_orthogonal(a, b, horizontal=dx >= dy), _orthogonal(a, b, horizontal=dx < dy)]
        points = next((t for t in tries if not any(_crosses(p, q, box) for p, q in zip(t, t[1:]) for box in others)),
                      tries[0])
    else:
        # A node and a point: leave the node's side facing the point, straight.
        node, point = (start, end) if isinstance(start, str) else (end, start)
        box = boxes[node]
        cx, cy = nodes[node]["at"]
        if abs(point[0] - cx) >= abs(point[1] - cy):
            side = (box[2] if point[0] > cx else box[0], point[1] if box[1] <= point[1] <= box[3] else cy)
        else:
            side = (point[0] if box[0] <= point[0] <= box[2] else cx, box[3] if point[1] > cy else box[1])
        points = [side, point] if node == start else [point, side]
    crossing = any(_crosses(p, q, box) for p, q in zip(points, points[1:]) for box in others)
    return points, crossing


def _midpoint(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], tuple[float, float]]:
    """The point halfway along a polyline, and the direction of the segment it lies on."""

    lengths = [math.dist(p, q) for p, q in zip(points, points[1:])]
    half, walked = sum(lengths) / 2, 0.0
    for (p, q), length in zip(zip(points, points[1:]), lengths):
        if walked + length >= half and length > 0:
            t = (half - walked) / length
            return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t), ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
        walked += length
    return points[0], (1.0, 0.0)


# A network given no places (step 4): columns along its edges, groups kept together, few crossings, every
# line on runs of its own. The places it gives are the author's units, as stated places are.
RANK_GAP = 34.0      # spec units between two columns, before the room their lines take
CHANNEL = 5.0        # spec units between two vertical runs in one gap
ROW_GAP = 16.0       # spec units between two nodes, or a node and a passing line, in one column
SWEEPS = 12


def _ranked(spec: Mapping[str, Any]) -> tuple[dict[str, int], list[tuple[int, str, str, bool]]]:
    """Each node's column, and the node-to-node edges as (index, tail, head, reversed) running left to right.

    The edges are walked in the order given from each node in the order given; an edge that closes a cycle (a loop
    or a return back to where the route came from) is turned round, so the main route runs left to right and the
    lines that go back are drawn going back. A node's column is the longest run of edges that reaches it.
    """

    order = [node["id"] for node in spec["nodes"]]
    out: dict[str, list[tuple[int, str]]] = {nid: [] for nid in order}
    for i, edge in enumerate(spec["edges"]):
        if isinstance(edge["from"], str) and isinstance(edge["to"], str) and edge["from"] != edge["to"]:
            out[edge["from"]].append((i, edge["to"]))
    state: dict[str, int] = {}
    turned: set[int] = set()
    for root in order:
        if root in state:
            continue
        stack = [(root, iter(out[root]))]
        state[root] = 1
        while stack:
            nid, children = stack[-1]
            step = next(children, None)
            if step is None:
                state[nid] = 2
                stack.pop()
                continue
            i, head = step
            if state.get(head) == 1:
                turned.add(i)
            elif head not in state:
                state[head] = 1
                stack.append((head, iter(out[head])))
    edges = []
    for i, edge in enumerate(spec["edges"]):
        if isinstance(edge["from"], str) and isinstance(edge["to"], str) and edge["from"] != edge["to"]:
            tail, head = (edge["to"], edge["from"]) if i in turned else (edge["from"], edge["to"])
            edges.append((i, tail, head, i in turned))
    rank = {nid: 0 for nid in order}
    incoming: dict[str, list[str]] = {nid: [] for nid in order}
    for _, tail, head, _ in edges:
        incoming[head].append(tail)
    done: dict[str, int] = {}

    def depth(nid: str) -> int:
        # Iterative longest path over the acyclic edges.
        if nid in done:
            return done[nid]
        work = [nid]
        while work:
            top = work[-1]
            pending = [tail for tail in incoming[top] if tail not in done]
            if pending:
                work.extend(pending)
                continue
            done[top] = max((done[tail] + 1 for tail in incoming[top]), default=0)
            work.pop()
        return done[nid]

    for nid in order:
        rank[nid] = depth(nid)
    return rank, edges


def _packed(wanted: Sequence[float], offsets: Sequence[float]) -> list[float]:
    """Rows as near the wanted ones as their order and spacing allow (least squares, by pooling adjacent violators)."""

    blocks: list[list[float]] = []
    for target in (w - o for w, o in zip(wanted, offsets)):
        blocks.append([target, 1.0])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    shifts = [block[0] / block[1] for block in blocks for _ in range(int(block[1]))]
    return [shift + offset for shift, offset in zip(shifts, offsets)]


def lay_out(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The network with every node placed: columns along the edges, groups kept together, fewer crossings.

    Every node-to-node edge gets the via points of an orthogonal run: out of its tail's right side, across each
    gap on a vertical of its own and through each column it passes on a row kept free for it, into its head's left
    side. Nothing else in the spec changes.
    """

    rank, edges = _ranked(spec)
    nodes = {node["id"]: node for node in spec["nodes"]}
    group_of = {nid: node["group"] for nid, node in nodes.items()}
    columns: dict[int, list[str]] = {}
    for nid in nodes:
        columns.setdefault(rank[nid], []).append(nid)
    # A line that passes a column takes a slot in it, named (edge index, column).
    chains: dict[int, list[str]] = {}
    for i, tail, head, _ in edges:
        chain = [tail]
        for r in range(rank[tail] + 1, rank[head]):
            slot = f"\x00{i}:{r}"
            columns[r].append(slot)
            group_of[slot] = None
            chain.append(slot)
        chain.append(head)
        chains[i] = chain
    last = max(columns)
    neighbours: dict[str, list[str]] = {item: [] for column in columns.values() for item in column}
    for chain in chains.values():
        for a, b in zip(chain, chain[1:]):
            neighbours[a].append(b)
            neighbours[b].append(a)
    # Start by group, then the order given; then sweep: each item to the mean place of its neighbours in the column
    # just swept, a group to the mean of its members, so a group's nodes in one column stay together.
    first_seen = {nid: k for k, nid in enumerate(nodes)}
    group_order = {group["id"]: k for k, group in enumerate(spec["groups"])}
    for r, column in columns.items():
        column.sort(key=lambda item: (group_order.get(group_of[item], len(group_order)), first_seen.get(item, 1e9)))
    place = {item: float(k) for column in columns.values() for k, item in enumerate(column)}

    def crossings() -> int:
        count = 0
        for r in range(last):
            pairs = [(place[a], place[b]) for a in columns[r] for b in neighbours[a] if b in columns.get(r + 1, ())]
            count += sum(1 for p, q in pairs for s, t in pairs if p < s and q > t)
        return count

    best = (crossings(), {r: list(column) for r, column in columns.items()})
    for sweep in range(SWEEPS):
        order = range(1, last + 1) if sweep % 2 == 0 else range(last - 1, -1, -1)
        side = -1 if sweep % 2 == 0 else 1
        for r in order:
            column = columns[r]
            centre = {}
            for item in column:
                near = [place[b] for b in neighbours[item] if b in columns.get(r + side, ())]
                centre[item] = sum(near) / len(near) if near else place[item]
            weight: dict[str, list[float]] = {}
            for item in column:
                if group_of[item] is not None:
                    weight.setdefault(group_of[item], []).append(centre[item])
            column.sort(key=lambda item: (sum(weight[group_of[item]]) / len(weight[group_of[item]])
                                          if group_of[item] is not None else centre[item],
                                          group_of[item] or "", centre[item], first_seen.get(item, 1e9)))
            for k, item in enumerate(column):
                place[item] = float(k)
        now = crossings()
        if now < best[0]:
            best = (now, {r: list(column) for r, column in columns.items()})
    columns = best[1]
    # Coordinates: columns left to right, each wide as its widest node; items top to bottom, the column centred.
    size = {item: nodes[item]["size"] if item in nodes else (0.0, 0.0) for column in columns.values() for item in column}
    widths = {r: max((size[item][0] for item in column), default=0.0) for r, column in columns.items()}
    gaps = {}
    for r in range(last):
        crossing_gap = sum(1 for chain in chains.values() for a, b in zip(chain, chain[1:])
                           if a in columns[r] and b in columns[r + 1])
        gaps[r] = RANK_GAP + CHANNEL * crossing_gap
    left, x = {}, 0.0
    for r in range(last + 1):
        left[r] = x
        x += widths[r] + gaps.get(r, 0.0)
    # Rows: each column stacked in its order, then pulled toward the rows of its neighbours, sweeping left and right,
    # so a route runs as straight as the order allows. Two passing lines sit closer than two nodes.
    offsets = {}
    for r, column in columns.items():
        y, previous = 0.0, None
        for item in column:
            if previous is not None:
                y += size[previous][1] / 2 + (ROW_GAP if previous in nodes or item in nodes else CHANNEL) + size[item][1] / 2
            offsets[item] = y
            previous = item
    centre_y = {item: offsets[item] - (offsets[column[-1]]) / 2 for column in columns.values() for item in column}
    for sweep in range(2 * SWEEPS // 3):
        order = range(1, last + 1) if sweep % 2 == 0 else range(last - 1, -1, -1)
        side = -1 if sweep % 2 == 0 else 1
        for r in order:
            column = columns[r]
            wanted = []
            for item in column:
                near = [centre_y[b] for b in neighbours[item] if b in columns.get(r + side, ())]
                wanted.append(sum(near) / len(near) if near else centre_y[item])
            for item, y in zip(column, _packed(wanted, [offsets[item] for item in column])):
                centre_y[item] = y
    placed = {nid: (left[rank[nid]] + widths[rank[nid]] / 2, centre_y[nid]) for nid in nodes}
    # Ports: the lines leaving a node's right side, and entering its left, spread over it in the order they go.
    starts, ends = {}, {}
    for nid in nodes:
        h = nodes[nid]["size"][1]
        leaving = sorted((i for i, chain in chains.items() if chain[0] == nid), key=lambda i: centre_y[chains[i][1]])
        coming = sorted((i for i, chain in chains.items() if chain[-1] == nid), key=lambda i: centre_y[chains[i][-2]])
        for k, i in enumerate(leaving):
            starts[i] = centre_y[nid] - h / 2 + h * (k + 1) / (len(leaving) + 1)
        for k, i in enumerate(coming):
            ends[i] = centre_y[nid] - h / 2 + h * (k + 1) / (len(coming) + 1)
    # Each gap's verticals, one per line crossing it, ordered so they cross each other least.
    lanes: dict[tuple[int, int], float] = {}
    for r in range(last):
        crossing_here = [(i, k) for i, chain in chains.items() for k, (a, b) in enumerate(zip(chain, chain[1:]))
                         if a in columns[r] and b in columns[r + 1]]

        def ys(item):
            i, k = item
            a = starts[i] if k == 0 else centre_y[chains[i][k]]
            b = ends[i] if k + 1 == len(chains[i]) - 1 else centre_y[chains[i][k + 1]]
            return a, b

        crossing_here.sort(key=lambda item: (ys(item)[0] - ys(item)[1], ys(item)[0]))
        start = left[r] + widths[r]
        for n, item in enumerate(crossing_here):
            lanes[item] = start + gaps[r] * (n + 1) / (len(crossing_here) + 1)
    laid = {**spec, "nodes": [{**node, "at": placed[node["id"]]} for node in spec["nodes"]], "edges": []}
    for i, edge in enumerate(spec["edges"]):
        if i not in chains:
            laid["edges"].append(edge)
            continue
        chain = chains[i]
        points = []
        for k in range(len(chain) - 1):
            a = starts[i] if k == 0 else centre_y[chain[k]]
            b = ends[i] if k + 1 == len(chain) - 1 else centre_y[chain[k + 1]]
            points += [(lanes[(i, k)], a), (lanes[(i, k)], b)]
        via = [p for n, p in enumerate(points) if n == 0 or math.dist(p, points[n - 1]) > 1e-9]
        reversed_ = next(turned for j, _, _, turned in edges if j == i)
        laid["edges"].append({**edge, "via": list(reversed(via)) if reversed_ else via, "route": "straight"})
    return laid


def draw_network(spec: Mapping[str, Any], sheet: _Sheet, area: tuple[float, float, float, float], *, mono: bool,
                  legend_x: float | None) -> list[str]:
    """Nodes, edges, notes, north and scale in ``area`` (mm), the legend at legend_x; returns what it had to note."""

    warnings: list[str] = []
    nodes = {node["id"]: node for node in spec["nodes"]}
    groups = {group["id"]: group for group in spec["groups"]}
    routes = []
    for i, edge in enumerate(spec["edges"]):
        points, crossing = _route(edge, nodes)
        if crossing:
            warnings.append(f"edge {i} ({edge['from']} to {edge['to']}) runs through another node; give it via points")
        routes.append(points)
    xs = [v for node in spec["nodes"] for v in (_box(node)[0], _box(node)[2])]
    ys = [v for node in spec["nodes"] for v in (_box(node)[1], _box(node)[3])]
    for points in routes:
        xs += [p[0] for p in points]
        ys += [p[1] for p in points]
    for note in spec["notes"]:
        xs.append(note["at"][0])
        ys.append(note["at"][1])
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    # Notes run right of and below their point: keep a band for them inside the area.
    reach = (area[2] - area[0]) * 0.12
    k = min((area[2] - area[0] - reach) / max(x1 - x0, 1e-9), (area[3] - area[1] - reach * 0.4) / max(y1 - y0, 1e-9))
    ox = area[0] + ((area[2] - area[0] - reach) - (x1 - x0) * k) / 2
    oy = area[1] + ((area[3] - area[1] - reach * 0.4) - (y1 - y0) * k) / 2

    def at(point: tuple[float, float]) -> tuple[float, float]:
        return ox + (point[0] - x0) * k, oy + (point[1] - y0) * k

    for node in spec["nodes"]:
        bx0, by0 = at(_box(node)[:2])
        bx1, by1 = at(_box(node)[2:])
        tone = None if mono else TONES[groups[node["group"]]["tone"] if node["group"] else "white"]
        if node["shape"] == "ellipse":
            ring = [((bx0 + bx1) / 2 + (bx1 - bx0) / 2 * math.cos(t), (by0 + by1) / 2 + (by1 - by0) / 2 * math.sin(t))
                    for t in (2 * math.pi * i / 48 for i in range(48))]
            sheet.path(ring, width=0.35, colour=BLACK, fill=tone or (1, 1, 1), close=True)
        else:
            sheet.path([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)], width=0.35, colour=BLACK,
                       fill=tone or (1, 1, 1), close=True)
        top = by0 + 2.2
        bottom = sheet.lines((bx0 + bx1) / 2, top, node["label"], 9.5, align="center")
        if node["sublabel"]:
            sheet.lines((bx0 + bx1) / 2, bottom + 0.6, node["sublabel"], 7.5, align="center", colour=INK)

    used_lines: list[str] = []
    for edge, points in zip(spec["edges"], routes):
        kind = LINES[edge["line"]]
        if edge["line"] not in used_lines:
            used_lines.append(edge["line"])
        dash = DASHES_MM[edge["style"] or (MONO_DASH[edge["line"]] if mono else kind["dash"])]
        colour = BLACK if mono else kind["colour"]
        mm = [at(p) for p in points]
        sheet.path(mm, width=kind["width"], colour=colour, dash=dash)
        size = 2.4
        if edge["arrow"] in ("end", "both") and len(mm) > 1:
            sheet.arrowhead(mm[-1], mm[-2], size=size, colour=colour)
        if edge["arrow"] == "both" and len(mm) > 1:
            sheet.arrowhead(mm[0], mm[1], size=size, colour=colour)
        middle, direction = _midpoint(mm)
        if edge["mark"]:
            sheet.circle(*middle, 2.6, width=0.3, colour=BLACK, fill=(1, 1, 1))
            sheet.text(middle[0], middle[1] + 1.1, edge["mark"], 8, align="center")
        if edge["label"]:
            normal = (-direction[1], direction[0])
            sheet.lines(middle[0] + normal[0] * 4 + 1, middle[1] + normal[1] * 4 - 1.5, edge["label"], 7.5, colour=INK)

    for note in spec["notes"]:
        x, y = at(note["at"])
        colour = INK if note["tone"] == "ink" or mono else LINES[note["tone"]]["colour"]
        sheet.lines(x, y, note["text"], 7.5, colour=colour)

    if spec["north"] is not None:
        nx, ny = area[2] - 8, area[1] + 14
        angle = math.radians(spec["north"])
        tip = (nx + 9 * math.sin(angle), ny - 9 * math.cos(angle))
        tail = (nx - 3 * math.sin(angle), ny + 3 * math.cos(angle))
        sheet.path([tail, tip], width=0.4, colour=BLACK)
        sheet.arrowhead(tip, tail, size=2.6, colour=BLACK)
        sheet.text(tip[0], tip[1] - 2, "N", 10, bold=True, align="center")
    if spec["scale"] is not None:
        length = spec["scale"]["length"] * k
        sx, sy = area[0], area[3] - 4
        sheet.path([(sx, sy), (sx + length, sy)], width=0.45, colour=BLACK)
        for x in (sx, sx + length / 2, sx + length):
            sheet.path([(x, sy - 1.6), (x, sy)], width=0.3, colour=BLACK)
        sheet.text(sx, sy + 4, spec["scale"]["label"], 7.5, colour=INK)

    if legend_x is not None:
        sheet.text(legend_x, area[1] + 4, "图例" if _has_cjk(spec) else "Legend", 10, bold=True)
        row = area[1] + 8
        line_height = 8 * MM_PER_PT * 1.3

        def pitch(label: str) -> float:
            return max(6.5, label.count("\n") * line_height + 5.0)

        # Without fills, mono shows no group apart from another: its legend names the lines only.
        for group in () if mono else spec["groups"]:
            if not group["label"]:
                continue
            sheet.path([(legend_x, row), (legend_x + 9, row), (legend_x + 9, row + 4.2), (legend_x, row + 4.2)],
                       width=0.3, colour=BLACK, fill=(1, 1, 1) if mono else TONES[group["tone"]], close=True)
            sheet.lines(legend_x + 12, row + 0.6, group["label"], 8)
            row += pitch(group["label"])
        row += 2
        for line in used_lines:
            kind = LINES[line]
            sheet.path([(legend_x, row + 2.1), (legend_x + 9, row + 2.1)], width=kind["width"],
                       colour=BLACK if mono else kind["colour"],
                       dash=DASHES_MM[MONO_DASH[line] if mono else kind["dash"]])
            label = spec["lines"].get(line, kind["label"])
            sheet.lines(legend_x + 12, row + 0.6, label, 8)
            row += pitch(label)
    return warnings


def draw_section(spec: Mapping[str, Any], sheet: _Sheet, area: tuple[float, float, float, float], *,
                 mono: bool) -> list[str]:
    """Spans, shapes, moves and notes at one scale in x and y, height up, the axis's ticks at its left."""

    axis = spec["axis"]
    (x0, x1), (y0, y1) = axis["x"], axis["y"]
    xs = [x0, x1, *(v for s in spec["spans"] for v in (s["x0"], s["x1"]))]
    ys = [y0, y1, *(v for s in spec["spans"] for v in (s["y0"], s["y1"]))]
    for shape in spec["shapes"]:
        for x, y in shape.get("points") or [shape["at"]]:
            xs.append(x)
            ys.append(y)
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    gutter = 16.0  # the tick labels' column
    k = min((area[2] - area[0] - gutter) / (hi_x - lo_x), (area[3] - area[1] - 12) / (hi_y - lo_y))
    ox = area[0] + gutter
    oy = area[1] + ((area[3] - area[1] - 12) - (hi_y - lo_y) * k) / 2

    def at(x: float, y: float) -> tuple[float, float]:
        return ox + (x - lo_x) * k, oy + (hi_y - y) * k

    def colour(line: str):
        return BLACK if mono else LINES[line]["colour"]

    for span in spec["spans"]:
        a, b = at(span["x0"], span["y1"]), at(span["x1"], span["y0"])
        sheet.path([a, (b[0], a[1]), b, (a[0], b[1])], width=0.35, colour=BLACK,
                   fill=(1, 1, 1) if mono else TONES[span["tone"]], close=True)
        for floor in span["floors"]:
            left, right = at(span["x0"], floor), at(span["x1"], floor)
            sheet.path([left, right], width=0.15, colour=(0.62, 0.65, 0.68),
                       dash=DASHES_MM["dashed"] if span["floorStyle"] == "dashed" else ())
        if span["label"]:
            if span["labelAt"] == "inside":
                sheet.lines(a[0] + 1.5, a[1] + 1.4, span["label"], 7.5)
            elif span["labelAt"] == "above":
                lines = span["label"].count("\n") + 1
                sheet.lines(a[0], a[1] - 1.2 - lines * 7.5 * MM_PER_PT * 1.3, span["label"], 7.5)
            else:
                sheet.lines(a[0], b[1] + 1.4, span["label"], 7.5)
    for shape in spec["shapes"]:
        stroke = colour(shape["line"])
        fill = None if mono or shape["tone"] is None else TONES[shape["tone"]]
        if shape["kind"] == "polygon":
            sheet.path([at(*p) for p in shape["points"]], width=0.35, colour=stroke, fill=fill, close=fill is not None)
        elif shape["kind"] == "ellipse":
            cx, cy = at(*shape["at"])
            rx, ry = shape["size"][0] / 2 * k, shape["size"][1] / 2 * k
            ring = [(cx + rx * math.cos(t), cy + ry * math.sin(t)) for t in (2 * math.pi * i / 48 for i in range(48))]
            sheet.path(ring, width=0.3, colour=stroke, fill=fill or (1, 1, 1), close=True)
        else:
            cx, cy = at(*shape["at"])
            sheet.path([(cx, cy - 2.6), (cx + 1.8, cy), (cx, cy + 2.6), (cx - 1.8, cy)], width=0.35, colour=stroke,
                       fill=fill or (1, 1, 1), close=True)
    if spec["ground"] is not None:
        left, right = at(x0, spec["ground"]["y"]), at(x1, spec["ground"]["y"])
        sheet.path([left, right], width=0.7, colour=BLACK)
        if spec["ground"]["label"]:
            sheet.lines(left[0] + 6, left[1] + 3, spec["ground"]["label"], 8)
    for value in axis["ticks"]["y"]:
        x, y = at(x0, value)
        sheet.path([(x - 5, y), (x - 1, y)], width=0.35, colour=BLACK)
        sheet.text(x - 6, y + 1, f"{value:g} {axis['unit']}", 7.5, align="right")
    for value in axis["ticks"]["x"]:
        x, y = at(value, y0)
        sheet.path([(x, y + 1), (x, y + 4)], width=0.35, colour=BLACK)
        sheet.text(x, y + 8, f"{value:g} {axis['unit']}", 7.5, align="center")
    if axis["xLabel"]:
        x, y = at(x1, y0)
        sheet.text(x, y + 8, axis["xLabel"], 7.5, align="right")
    for move in spec["moves"]:
        start, end = at(*move["from"]), at(*move["to"])
        kind = LINES[move["line"]]
        dash = DASHES_MM[move["style"] or (MONO_DASH[move["line"]] if mono else "solid")]
        sheet.path([start, end], width=max(kind["width"], 0.5), colour=colour(move["line"]), dash=dash)
        if move["arrow"] in ("end", "both"):
            sheet.arrowhead(end, start, size=2.6, colour=colour(move["line"]))
        if move["arrow"] == "both":
            sheet.arrowhead(start, end, size=2.6, colour=colour(move["line"]))
        if move["label"]:
            middle = ((start[0] + end[0]) / 2 + 2, (start[1] + end[1]) / 2)
            sheet.lines(*middle, move["label"], 7.5, colour=colour(move["line"]))
    for note in spec["notes"]:
        x, y = at(*note["at"])
        sheet.lines(x, y, note["text"], 7.5, colour=INK if note["tone"] == "ink" or mono else LINES[note["tone"]]["colour"])
    return []


def draw_timeline(spec: Mapping[str, Any], sheet: _Sheet, area: tuple[float, float, float, float], *,
                  mono: bool) -> list[str]:
    """Lanes as rows across the time axis: segments as bars, events as marks, the grid at every step."""

    axis = spec["axis"]
    labels = 26.0  # the lanes' label column
    left, right = area[0] + labels, area[2]
    rows = len(spec["lanes"])
    row = min(24.0, (area[3] - area[1] - 12) / rows)
    top = area[1]
    k = (right - left) / (axis["end"] - axis["start"])
    lanes = {lane["id"]: index for index, lane in enumerate(spec["lanes"])}

    def x(t: float) -> float:
        return left + (t - axis["start"]) * k

    bottom = top + rows * row
    steps = int(round((axis["end"] - axis["start"]) / axis["step"]))
    for n in range(steps + 1):
        t = axis["start"] + n * axis["step"]
        sheet.path([(x(t), top), (x(t), bottom)], width=0.12, colour=(0.86, 0.88, 0.9))
        sheet.text(x(t), bottom + 5, f"{t:g}", 7, align="center", colour=INK)
    if axis["unit"]:
        sheet.text(right, bottom + 10, axis["unit"], 7, align="right", colour=INK)
    for lane in spec["lanes"]:
        y = top + lanes[lane["id"]] * row
        sheet.text(area[0], y + row / 2 + 1.2, lane["label"], 8.5, bold=True)
    for segment in spec["segments"]:
        y = top + lanes[segment["lane"]] * row
        a, b = x(segment["start"]), x(segment["end"])
        fill = (1, 1, 1) if mono else segment["colour"]
        sheet.path([(a, y + row * 0.2), (b, y + row * 0.2), (b, y + row * 0.8), (a, y + row * 0.8)], width=0.3,
                   colour=BLACK, fill=fill, close=True)
        if segment["label"]:
            light = 0.2126 * fill[0] + 0.7152 * fill[1] + 0.0722 * fill[2] > 0.55
            sheet.text(a + 1.2, y + row / 2 + 1.0, segment["label"], 7, colour=BLACK if light else (1, 1, 1))
    for event in spec["events"]:
        y = top + lanes[event["lane"]] * row + row / 2
        cx = x(event["at"])
        colour = BLACK if mono else event["colour"]
        if event["mark"] == "diamond":
            sheet.path([(cx, y - 1.6), (cx + 1.6, y), (cx, y + 1.6), (cx - 1.6, y)], width=0.2, colour=colour,
                       fill=colour, close=True)
        elif event["mark"] == "circle":
            sheet.circle(cx, y, 1.4, width=0.2, colour=colour, fill=colour)
        else:
            sheet.path([(cx, y - row * 0.3), (cx, y + row * 0.3)], width=0.7, colour=colour)
        if event["label"]:
            sheet.lines(cx + 2.4, y - 1.4, event["label"], 6.5)
    return []


def _has_cjk(value: Any) -> bool:
    if isinstance(value, str):
        return any("⺀" <= char <= "鿿" or "＀" <= char <= "￯" for char in value)
    if isinstance(value, Mapping):
        return any(_has_cjk(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_cjk(item) for item in value)
    return False


def needs_cjk_font(spec: Any, title: str = "", subtitle: str = "") -> bool:
    """Whether a spec, its title or its subtitle has Chinese, Japanese or Korean text, which a Latin font cannot draw."""

    return _has_cjk(spec) or _has_cjk(title) or _has_cjk(subtitle)


def draw(kind: str, spec: Any, *, style_id: str, title: str, subtitle: str = "",
         font_mapping: Mapping[str, Any]) -> Diagram:
    """The spec checked and drawn on one A3 sheet: title strip at the top, the diagram, its legend at the right."""

    checked = check(kind, spec)
    if style_id not in STYLE_IDS:
        raise DiagramError("styleId", f"must be one of {', '.join(STYLE_IDS)}")
    canvas = PaperCanvas(font_mapping=font_mapping)
    canvas.start_sheet("01", PAPER_MM)
    canvas.setTitle(title)
    sheet = _Sheet(canvas, PAPER_MM)
    width, height = PAPER_MM
    sheet.text(MARGIN_MM, MARGIN_MM + 6, title, 16, bold=True)
    top = MARGIN_MM + 10
    if subtitle:
        top = sheet.lines(MARGIN_MM, MARGIN_MM + 9, subtitle, 8.5, colour=INK) + 2
    mono = style_id == "diagram-mono"
    if kind == "network":
        legend_width = 72.0
        area = (MARGIN_MM, top + 6, width - MARGIN_MM - legend_width - 6, height - MARGIN_MM)
        drawn = lay_out(checked) if checked["laidOut"] else checked
        warnings = draw_network(drawn, sheet, area, mono=mono, legend_x=width - MARGIN_MM - legend_width)
    elif kind == "section":
        warnings = draw_section(checked, sheet, (MARGIN_MM, top + 6, width - MARGIN_MM, height - MARGIN_MM), mono=mono)
    else:
        warnings = draw_timeline(checked, sheet, (MARGIN_MM, top + 8, width - MARGIN_MM, height - MARGIN_MM), mono=mono)
    return Diagram(canvas, checked, tuple(warnings))
