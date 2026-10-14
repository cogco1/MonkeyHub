"""A native editable 3DM: the compiled program written as rhino3dm primitives whose numbers sit in the file.

MonkeyHub's ``.preview.3dm`` holds render meshes only, so nothing in it can be
edited as a dimension. Here every delivered object that a rhino3dm primitive
holds exactly is one, under the object name, layer and ``archflow:*`` user text
the mesh export gives it (``monkeycad.program.expected_object_semantics``):

| program operation | written as |
|---|---|
| ``extrusion`` of a planar polygon: stylobate, podium, slabs, lintel, beams, door frame and leaf, the 24-sided columns, the ring pieces, the drum | ``Extrusion``: outer profile polyline and a path (start, end, up) |
| ``solid`` (a box) | ``Extrusion`` of its plan rectangle |
| ``loft`` of two sections, the second a translate of the first: pediment, straight stair | ``Extrusion`` of the first section along the translation (the stepped side profile along the stair's width) |
| ``boolean_difference`` of a box and boxes that pass through it along one axis: a wall and its openings | ``Extrusion`` of the elevation outline along the thickness; an opening that reaches an edge is a notch in the outer profile, one inside is an inner profile (``AddInnerProfile``) |
| ``boolean_intersection`` of boxes: an opening's hidden aperture witness | ``Extrusion`` of the common box |
| ``loft`` through more than two polygonal sections: the dome | ``Mesh`` (double-precision vertices): the exact triangulation of the ruled loft. Not native |
| anything else | ``unsupported``, with the reason; ``write_native`` refuses to write a file it cannot make faithful |

The program's columns are 24-sided prisms and its rings, drum and dome are
polygonal, so a ``CreateCylinderExtrusion`` column or a ``RevSurface`` dome would
be a different solid (a cylinder holds about 1.1 % more than its 24-gon). Each
is written as the polygon the program states; a regular polygon is recognised
and its centre, radius and segment count are written beside its points.

Frame: the model file's own, x along, y plan depth, z up, metres; a program
point ``(x, y up, z plan)`` is ``(x, z, y)`` here (``cad_point`` of the OCCT backend).
An extrusion's path starts at the outer profile's vertex centroid (so a column's
path is its axis) and its profile is stored around that point in the path's local
frame: ``T`` the path direction, ``U`` the up vector (world y for a vertical path,
world z otherwise), ``X = U x T``; a plan profile reads as plan ``(x, y)`` and an
elevation profile as ``(x, z)``. Moving or re-heighting an object is a path edit,
reshaping it a profile edit. The outer loop is counter-clockwise about ``T``,
holes clockwise, as openNURBS stores them.

Per object, ``archflow:primitive`` is JSON of what the geometry holds: ``kind``,
``path``, ``up``, ``height``, ``outer``, ``holes``, a recognised ``form`` and the
``source`` operation; a mesh says ``native: false`` and why. The document carries
the state as the C-full arm does: ``archflow:state`` is ``carrier.payload`` and
``archflow:binding`` is ``carrier.binding`` (schema ``ArchFlowStateFile@1``); the
per-object geometry digest is ``canonical_digest`` of the object's encoded
rhino3dm geometry, the digest ``inspect_three_dm`` takes, computed in memory
because the inspector's full read requires render meshes this file does not
carry. No render mesh is written: Rhino meshes extrusions when it opens the
file, a rhino3dm-only viewer shows them only after meshing them itself.

``apply_edits`` is the field-level edit hook (path, profile, height, translate,
mesh, primitive, regular polygon, rectangle, add, delete); ``mesh_model``
converts a native file to named meshes for the oracle, with its own exact
triangulation, in the harness. Nothing here runs OCCT or Rhino.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.state.geometry_program import delivered_object_ids, lift_to_base_level, operation_parameters
from archflow.state.state_record import StateRecord

EPS = 1e-9
Vec = tuple[float, float, float]
Loop = tuple[tuple[float, float], ...]


class NativeError(ValueError):
    """The program or an edit cannot be written as the native primitives this module knows."""


# ---------------------------------------------------------------- small vector algebra (pure Python: a few hundred points)

def _sub(a, b) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a) -> Vec:
    n = _norm(a)
    if n <= EPS:
        raise NativeError("a zero-length direction")
    return _clean((a[0] / n, a[1] / n, a[2] / n))


def _clean(v) -> Vec:
    return tuple(float(x) + 0.0 for x in v)  # type: ignore[return-value]


def cad(point) -> Vec:
    """Program ``(x, y up, z plan)`` to the model frame ``(x, plan, up)``."""

    x, y, z = (float(v) for v in point)
    return (x, z, y)


def _close(a, b, tol: float = EPS) -> bool:
    return all(abs(p - q) <= tol for p, q in zip(a, b))


# ---------------------------------------------------------------- primitives

@dataclass(frozen=True)
class Primitive:
    """One object as the native file holds it: an extrusion (path and local loops), a mesh, or unsupported."""

    object_id: str
    kind: str                                   # extrusion | mesh | unsupported
    source: dict[str, Any] = field(default_factory=dict)
    start: Vec = (0.0, 0.0, 0.0)
    end: Vec = (0.0, 0.0, 0.0)
    up: Vec = (0.0, 0.0, 0.0)
    outer: Loop = ()
    holes: tuple[Loop, ...] = ()
    vertices: tuple[Vec, ...] = ()
    triangles: tuple[tuple[int, int, int], ...] = ()
    reason: str = ""

    @property
    def native(self) -> bool:
        return self.kind == "extrusion"

    def frame(self) -> tuple[Vec, Vec, Vec]:
        """(X, U, T) of an extrusion: profile axes and path direction."""

        t = _unit(_sub(self.end, self.start))
        u = _unit(_sub(self.up, _mul(t, _dot(self.up, t))))
        return _clean(_cross(u, t)), u, t

    def world_loops(self) -> list[list[Vec]]:
        """The loops on the start plane, in the model frame (outer first)."""

        x, u, _ = self.frame()
        return [[_clean(_add(self.start, _add(_mul(x, a), _mul(u, b)))) for a, b in loop] for loop in (self.outer, *self.holes)]


def _signed_area(loop) -> float:
    return 0.5 * sum(loop[i][0] * loop[(i + 1) % len(loop)][1] - loop[(i + 1) % len(loop)][0] * loop[i][1] for i in range(len(loop)))


def _reversed_keep_first(loop: list) -> list:
    """The loop reversed with its first point kept first, as openNURBS reverses a profile."""

    return [loop[0], *reversed(loop[1:])]


def _up_for(t: Vec) -> Vec:
    up = (0.0, 1.0, 0.0) if abs(abs(t[2]) - 1.0) <= 1e-12 else (0.0, 0.0, 1.0)
    return _unit(_sub(up, _mul(t, _dot(up, t))))


def extrusion_from_world(object_id: str, loops: list[list[Vec]], vector: Vec, source: dict[str, Any]) -> Primitive:
    """An extrusion of planar loops (outer first) along ``vector``; unsupported when the loops are not square to it."""

    loops = [list(loop[:-1]) if len(loop) > 3 and _close(loop[0], loop[-1]) else list(loop) for loop in loops]
    if not loops or len(loops[0]) < 3:
        return Primitive(object_id, "unsupported", source, reason="a profile needs at least three points")
    try:
        t = _unit(vector)
    except NativeError:
        return Primitive(object_id, "unsupported", source, reason="a zero extrusion vector")
    level = _dot(loops[0][0], t)
    if any(abs(_dot(p, t) - level) > 1e-9 for loop in loops for p in loop):
        return Primitive(object_id, "unsupported", source, reason="the profile is not square to the extrusion vector")
    u = _up_for(t)
    x = _clean(_cross(u, t))
    # The path stands at the outer loop's vertex centroid (to 1e-9 m): placement is the path, shape is the profile around it.
    centroid = [sum(p[a] for p in loops[0]) / len(loops[0]) for a in range(3)]
    start = _clean(round(c, 9) for c in _sub(centroid, _mul(t, _dot(centroid, t) - level)))
    local = [[(_dot(_sub(p, start), x) + 0.0, _dot(_sub(p, start), u) + 0.0) for p in loop] for loop in loops]
    outer = local[0] if _signed_area(local[0]) > 0 else _reversed_keep_first(local[0])
    holes = [h if _signed_area(h) < 0 else _reversed_keep_first(h) for h in local[1:]]
    return Primitive(object_id, "extrusion", source, start=start, end=_clean(_add(start, vector)), up=u,
                     outer=tuple(outer), holes=tuple(tuple(h) for h in holes))


def _box_extrusion(object_id: str, lo: Vec, hi: Vec, source: dict[str, Any]) -> Primitive:
    z = lo[2]
    loop = [(lo[0], lo[1], z), (hi[0], lo[1], z), (hi[0], hi[1], z), (lo[0], hi[1], z)]
    return extrusion_from_world(object_id, [loop], (0.0, 0.0, hi[2] - lo[2]), source)


def as_box(p: Primitive) -> tuple[Vec, Vec] | None:
    """(lo, hi) when ``p`` is an axis-aligned box written as an extrusion of a rectangle; otherwise None."""

    if p.kind != "extrusion" or p.holes or len(p.outer) != 4:
        return None
    _, _, t = p.frame()
    if sorted(abs(c) for c in t) != [0.0, 0.0, 1.0]:
        return None
    points = [q for loop in p.world_loops() for q in loop]
    points += [_add(q, _sub(p.end, p.start)) for q in points]
    for axis in range(3):
        if len({round(q[axis], 12) for q in points}) > 2:
            return None
    return (tuple(min(q[a] for q in points) for a in range(3)), tuple(max(q[a] for q in points) for a in range(3)))  # type: ignore[return-value]


def _difference(object_id: str, base: tuple[Vec, Vec], tools: list[tuple[Vec, Vec]], source: dict[str, Any]) -> Primitive:
    """A box minus boxes that pass through it along one axis: an extrusion of the outline left, along that axis."""

    lo, hi = base
    cutting = [(tlo, thi) for tlo, thi in tools if all(tlo[a] < hi[a] - EPS and thi[a] > lo[a] + EPS for a in range(3))]
    if not cutting:
        return _box_extrusion(object_id, lo, hi, source)
    axes = [a for a in range(3) if all(tlo[a] <= lo[a] + EPS and thi[a] >= hi[a] - EPS for tlo, thi in cutting)]
    if not axes:
        return Primitive(object_id, "unsupported", source, reason="an opening that does not pass through the solid: needs a boolean")
    a = min(axes, key=lambda k: (hi[k] - lo[k], k))
    b, c = {0: (1, 2), 1: (0, 2), 2: (0, 1)}[a]
    r0, r1, s0, s1 = lo[b], hi[b], lo[c], hi[c]
    rects = []
    for tlo, thi in cutting:
        rect = (max(tlo[b], r0), min(thi[b], r1), max(tlo[c], s0), min(thi[c], s1))
        touches = {"left": rect[0] <= r0 + EPS, "right": rect[1] >= r1 - EPS, "bottom": rect[2] <= s0 + EPS, "top": rect[3] >= s1 - EPS}
        sides = [k for k, v in touches.items() if v]
        if len(sides) > 1:
            return Primitive(object_id, "unsupported", source, reason="an opening that cuts a corner or splits the solid: needs a boolean")
        rects.append((rect, sides[0] if sides else "hole"))
    for i, (p, _) in enumerate(rects):
        for q, _ in rects[i + 1:]:
            if p[0] < q[1] - EPS and q[0] < p[1] - EPS and p[2] < q[3] - EPS and q[2] < p[3] - EPS:
                return Primitive(object_id, "unsupported", source, reason="overlapping openings: needs a boolean")
    by = {side: sorted((r for r, s in rects if s == side), key=lambda r: (r[0], r[2])) for side in ("bottom", "right", "top", "left", "hole")}
    outline: list[tuple[float, float]] = [(r0, s0)]
    for x0, x1, _, y1 in by["bottom"]:
        outline += [(x0, s0), (x0, y1), (x1, y1), (x1, s0)]
    outline.append((r1, s0))
    for x0, _, y0, y1 in sorted(by["right"], key=lambda r: r[2]):
        outline += [(r1, y0), (x0, y0), (x0, y1), (r1, y1)]
    outline.append((r1, s1))
    for x0, x1, y0, _ in sorted(by["top"], key=lambda r: -r[0]):
        outline += [(x1, s1), (x1, y0), (x0, y0), (x0, s1)]
    outline.append((r0, s1))
    for _, x1, y0, y1 in sorted(by["left"], key=lambda r: -r[2]):
        outline += [(r0, y1), (x1, y1), (x1, y0), (r0, y0)]
    holes = [[(x0, y0), (x0, y1), (x1, y1), (x1, y0)] for x0, x1, y0, y1 in by["hole"]]
    # Elevation along y runs from the face at hi to the face at lo, so the profile reads as (x, z); x and z run from lo.
    plane = hi[a] if a == 1 else lo[a]

    def lift(p2):
        q = [0.0, 0.0, 0.0]
        q[a], q[b], q[c] = plane, p2[0], p2[1]
        return tuple(q)

    vector = [0.0, 0.0, 0.0]
    vector[a] = (lo[a] - hi[a]) if a == 1 else (hi[a] - lo[a])
    dedup = [p for i, p in enumerate(outline) if i == 0 or not _close(p, outline[i - 1])]
    if _close(dedup[0], dedup[-1]):
        dedup.pop()
    prim = extrusion_from_world(object_id, [[lift(p) for p in dedup], *[[lift(p) for p in h] for h in holes]], tuple(vector), source)
    return prim


def primitives(program) -> dict[str, Primitive]:
    """Every delivered object of a compiled program as the primitive the native file holds."""

    proposal = program.proposal
    ops = {op.op_id: op for op in proposal.operations}
    built: dict[str, Primitive] = {}
    for op_id in program.operation_order:
        op = ops[op_id]
        kind = op.kind.value
        params = operation_parameters(op)
        source = {"op": op_id, "op_kind": kind}
        for out in op.output_object_ids:
            built[out] = _convert(out, kind, op, params, built, source)
    delivered = set(delivered_object_ids(proposal))
    return {k: built[k] for k in sorted(built) if k in delivered}


def _convert(out: str, kind: str, op, params: dict[str, Any], built: dict[str, Primitive], source: dict[str, Any]) -> Primitive:
    if kind == "extrusion":
        points = [cad(p) for p in lift_to_base_level(params["profile"], params, op.op_id)]
        return extrusion_from_world(out, [points], cad(params["vector"]), source)
    if kind == "solid":
        a = cad(params["origin"])
        b = cad([float(o) + float(s) for o, s in zip(params["origin"], params["size"])])
        return _box_extrusion(out, tuple(min(p, q) for p, q in zip(a, b)), tuple(max(p, q) for p, q in zip(a, b)), source)
    if kind == "loft":
        size = int(params["profile_size"])
        points = [cad(p) for p in lift_to_base_level(params["profiles"], params, op.op_id)]
        sections = [points[i:i + size] for i in range(0, len(points), size)]
        if params.get("profile_basis", "polyline") != "polyline" or params.get("cap_ends", True) is not True:
            return Primitive(out, "unsupported", source, reason="an open or curve-based loft")
        if len(sections) == 2:
            shift = _sub(sections[1][0], sections[0][0])
            if all(_close(_sub(q, p), shift) for p, q in zip(*sections)):
                prim = extrusion_from_world(out, [sections[0]], shift, {**source, "read_as": "two congruent sections: an extrusion"})
                if prim.native:
                    return prim
        if params.get("loft_type", "normal") != "straight":
            return Primitive(out, "unsupported", source, reason="a smooth loft through more than two sections")
        vertices, triangles = loft_mesh(sections)
        return Primitive(out, "mesh", source, vertices=tuple(vertices), triangles=tuple(triangles),
                         reason=f"a ruled loft through {len(sections)} polygonal sections: no rhino3dm primitive holds it; "
                                "written as its exact triangulation (a RevSurface would be a smooth, different solid)")
    if kind in ("boolean_difference", "boolean_intersection"):
        inputs = sorted(op.input_object_ids)
        boxes = [as_box(built[i]) if i in built else None for i in inputs]
        if any(b is None for b in boxes):
            return Primitive(out, "unsupported", source, reason=f"{kind} of solids that are not boxes: needs a boolean")
        if kind == "boolean_intersection":
            lo = tuple(max(b[0][a] for b in boxes) for a in range(3))
            hi = tuple(min(b[1][a] for b in boxes) for a in range(3))
            if any(hi[a] <= lo[a] + EPS for a in range(3)):
                return Primitive(out, "unsupported", source, reason="an empty intersection")
            return _box_extrusion(out, lo, hi, source)  # type: ignore[arg-type]
        base_index = int(params.get("base_index", 0))
        base = boxes[base_index]
        return _difference(out, base, [b for i, b in enumerate(boxes) if i != base_index], source)  # type: ignore[arg-type]
    return Primitive(out, "unsupported", source, reason=f"operation kind {kind!r} has no native primitive here")


# ---------------------------------------------------------------- exact triangulation (planar polygons, ruled lofts)

def _cross2(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _in_triangle(p, a, b, c) -> bool:
    return _cross2(a, b, p) >= -1e-12 and _cross2(b, c, p) >= -1e-12 and _cross2(c, a, p) >= -1e-12


def _bridge(points, ring: list[int], hole: list[int]) -> list[int]:
    """Join a clockwise hole to the counter-clockwise ring through a mutually visible pair of vertices (Eberly)."""

    m = max(hole, key=lambda i: (points[i][0], -points[i][1]))
    mx, my = points[m]
    best = None
    for k in range(len(ring)):
        a, b = points[ring[k]], points[ring[(k + 1) % len(ring)]]
        if a[1] == b[1] or (a[1] - my) * (b[1] - my) > 0:
            continue
        x = a[0] + (my - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        if x >= mx - 1e-12 and (best is None or x < best[0]):
            best = (x, k)
    if best is None:
        raise NativeError("an inner profile lies outside the outer profile")
    x, k = best
    ia, ib = k, (k + 1) % len(ring)
    pos = ia if points[ring[ia]][0] > points[ring[ib]][0] else ib
    p = points[ring[pos]]
    if not _close(p, (x, my)):
        tri = [(mx, my), (x, my), p]
        if _cross2(*tri) < 0:
            tri = [tri[0], tri[2], tri[1]]
        angle = abs(math.atan2(p[1] - my, p[0] - mx))
        for j, v in enumerate(ring):
            q = points[v]
            prev, nxt = points[ring[j - 1]], points[ring[(j + 1) % len(ring)]]
            if j == pos or _cross2(prev, q, nxt) > 0 or not _in_triangle(q, *tri):
                continue
            a = abs(math.atan2(q[1] - my, q[0] - mx))
            if a < angle:
                pos, angle = j, a
    start = hole.index(m)
    return ring[:pos + 1] + hole[start:] + hole[:start] + [m, ring[pos]] + ring[pos + 1:]


def triangulate(outer, holes=()) -> list[tuple[int, int, int]]:
    """Counter-clockwise triangles over a counter-clockwise outer loop and clockwise holes, indexed outer then holes."""

    points = [tuple(p) for p in outer] + [tuple(p) for h in holes for p in h]
    ring = list(range(len(outer)))
    offset, hole_rings = len(outer), []
    for h in holes:
        hole_rings.append(list(range(offset, offset + len(h))))
        offset += len(h)
    for hole in sorted(hole_rings, key=lambda r: -max(points[i][0] for i in r)):
        ring = _bridge(points, ring, hole)
    triangles = []
    while len(ring) > 3:
        n = len(ring)
        for i in range(n):
            ip, ii, inn = ring[i - 1], ring[i], ring[(i + 1) % n]
            a, b, c = points[ip], points[ii], points[inn]
            if _cross2(a, b, c) <= 1e-14:
                continue
            if any(_in_triangle(points[v], a, b, c) for v in ring
                   if v not in (ip, ii, inn) and points[v] not in (a, b, c)):
                continue
            triangles.append((ip, ii, inn))
            del ring[i]
            break
        else:  # only degenerate corners are left: clip the flattest one
            i = min(range(n), key=lambda j: abs(_cross2(points[ring[j - 1]], points[ring[j]], points[ring[(j + 1) % n]])))
            triangles.append((ring[i - 1], ring[i], ring[(i + 1) % n]))
            del ring[i]
    triangles.append(tuple(ring))  # type: ignore[arg-type]
    return triangles


def extrusion_mesh(p: Primitive) -> tuple[list[Vec], list[tuple[int, int, int]]]:
    """Closed, outward triangles of an extrusion: exact, since every face is planar."""

    loops = p.world_loops()
    shift = _sub(p.end, p.start)
    bottom = [q for loop in loops for q in loop]
    n = len(bottom)
    vertices = bottom + [_clean(_add(q, shift)) for q in bottom]
    cap = triangulate(p.outer, p.holes)
    triangles = [(a + n, b + n, c + n) for a, b, c in cap] + [(a, c, b) for a, b, c in cap]
    offset = 0
    for loop in loops:
        k = len(loop)
        for i in range(k):
            a0, b0 = offset + i, offset + (i + 1) % k
            triangles += [(a0, b0, b0 + n), (a0, b0 + n, a0 + n)]
        offset += k
    return vertices, triangles


def _newell(loop) -> Vec:
    n = [0.0, 0.0, 0.0]
    for i, p in enumerate(loop):
        q = loop[(i + 1) % len(loop)]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    return tuple(n)  # type: ignore[return-value]


def loft_mesh(sections: list[list[Vec]]) -> tuple[list[Vec], list[tuple[int, int, int]]]:
    """A capped ruled loft through polygonal sections of equal size, as OCCT's ``ThruSections(solid, ruled)`` builds it."""

    def centroid(s):
        return tuple(sum(p[a] for p in s) / len(s) for a in range(3))

    direction = _sub(centroid(sections[-1]), centroid(sections[0]))
    if _dot(_newell(sections[0]), direction) < 0:
        sections = [_reversed_keep_first(list(s)) for s in sections]
    d = _unit(direction)
    e1 = _unit(_sub(sections[0][1], sections[0][0]))
    e1 = _unit(_sub(e1, _mul(d, _dot(e1, d))))
    e2 = _cross(d, e1)
    size = len(sections[0])
    vertices = [_clean(p) for s in sections for p in s]
    triangles: list[tuple[int, int, int]] = []
    for k in range(len(sections) - 1):
        for i in range(size):
            a0, b0 = k * size + i, k * size + (i + 1) % size
            triangles += [(a0, b0, b0 + size), (a0, b0 + size, a0 + size)]
    for k, flip in ((0, True), (len(sections) - 1, False)):
        flat = [(_dot(p, e1), _dot(p, e2)) for p in sections[k]]
        for a, b, c in triangulate(flat):
            triangles.append((k * size + a, k * size + c, k * size + b) if flip else (k * size + a, k * size + b, k * size + c))
    if mesh_volume(vertices, triangles) < 0:
        triangles = [(a, c, b) for a, b, c in triangles]
    return vertices, triangles


def mesh_volume(vertices, triangles) -> float:
    total = 0.0
    for a, b, c in triangles:
        total += _dot(vertices[a], _cross(vertices[b], vertices[c]))
    return total / 6.0


def mesh_of(p: Primitive) -> tuple[list[Vec], list[tuple[int, int, int]]]:
    if p.kind == "extrusion":
        return extrusion_mesh(p)
    if p.kind == "mesh":
        return list(p.vertices), list(p.triangles)
    raise NativeError(f"{p.object_id}: {p.reason}")


def shape_numbers(p: Primitive) -> dict[str, Any]:
    """Box and enclosed volume of the exact triangulation, in double precision."""

    vertices, triangles = mesh_of(p)
    return {"min": [min(v[a] for v in vertices) for a in range(3)], "max": [max(v[a] for v in vertices) for a in range(3)],
            "volume": mesh_volume(vertices, triangles)}


# ---------------------------------------------------------------- what the user text says about a primitive

def _r(value: float) -> float:
    return round(float(value), 9) + 0.0


def form(outer: Loop, holes: tuple[Loop, ...] = ()) -> dict[str, Any]:
    """A rectangle or a regular polygon recognised from the outer loop, with the numbers that define it."""

    if holes:
        return {"polygon_with_holes": [len(outer), [len(h) for h in holes]]}
    us, vs = sorted({round(p[0], 12) for p in outer}), sorted({round(p[1], 12) for p in outer})
    if len(outer) == 4 and len(us) == 2 and len(vs) == 2:
        return {"rectangle": [_r(us[0]), _r(vs[0]), _r(us[1]), _r(vs[1])]}
    n = len(outer)
    if n >= 6:
        cu, cv = sum(p[0] for p in outer) / n, sum(p[1] for p in outer) / n
        radii = [math.hypot(p[0] - cu, p[1] - cv) for p in outer]
        angles = [math.atan2(p[1] - cv, p[0] - cu) for p in outer]
        steps = [((angles[(i + 1) % n] - angles[i]) % (2 * math.pi)) for i in range(n)]
        if max(radii) - min(radii) <= 1e-6 and all(abs(s - 2 * math.pi / n) <= 1e-6 for s in steps):
            return {"regular_polygon": {"center": [_r(cu), _r(cv)], "radius": _r(sum(radii) / n), "segments": n,
                                        "start_angle_deg": _r(math.degrees(angles[0]))}}
    return {"polygon": n}


def regular_polygon(center, radius: float, segments: int, start_angle_deg: float = 0.0) -> list[tuple[float, float]]:
    a0 = math.radians(start_angle_deg)
    return [(center[0] + radius * math.cos(a0 + 2 * math.pi * k / segments), center[1] + radius * math.sin(a0 + 2 * math.pi * k / segments))
            for k in range(segments)]


def primitive_json(p: Primitive) -> dict[str, Any]:
    if p.kind == "extrusion":
        _, _, t = p.frame()
        return {"kind": "extrusion", "native": True, "path": [[_r(v) for v in p.start], [_r(v) for v in p.end]],
                "up": [_r(v) for v in p.up], "axis": [_r(v) for v in t], "height": _r(_norm(_sub(p.end, p.start))),
                "outer": [[_r(a), _r(b)] for a, b in p.outer], "holes": [[[_r(a), _r(b)] for a, b in h] for h in p.holes],
                "form": form(p.outer, p.holes), "source": p.source}
    return {"kind": p.kind, "native": False, "reason": p.reason, "vertex_count": len(p.vertices), "triangle_count": len(p.triangles),
            "source": p.source}


# ---------------------------------------------------------------- rhino3dm geometry

def _curve(rhino3dm, loop):
    return rhino3dm.PolylineCurve([rhino3dm.Point3d(a, b, 0.0) for a, b in loop] + [rhino3dm.Point3d(loop[0][0], loop[0][1], 0.0)])


def to_geometry(p: Primitive):
    """A new rhino3dm Extrusion or Mesh holding exactly ``p``."""

    import rhino3dm

    if p.kind == "extrusion":
        ext = rhino3dm.Extrusion()
        if not ext.SetPathAndUp(rhino3dm.Point3d(*p.start), rhino3dm.Point3d(*p.end), rhino3dm.Vector3d(*p.up)):
            raise NativeError(f"{p.object_id}: rhino3dm refused the path")
        if not ext.SetOuterProfile(_curve(rhino3dm, p.outer), True):
            raise NativeError(f"{p.object_id}: rhino3dm refused the outer profile")
        for hole in p.holes:
            if not ext.AddInnerProfile(_curve(rhino3dm, hole)):
                raise NativeError(f"{p.object_id}: rhino3dm refused an inner profile")
        if not ext.IsValid or not ext.IsSolid:
            raise NativeError(f"{p.object_id}: the extrusion is not a valid closed solid")
        return ext
    if p.kind == "mesh":
        mesh = rhino3dm.Mesh()
        mesh.Vertices.UseDoublePrecisionVertices = True
        for v in p.vertices:
            mesh.Vertices.AddPoint3d(*v)
        for a, b, c in p.triangles:
            mesh.Faces.AddFace(a, b, c)
        mesh.Normals.ComputeNormals()
        if not mesh.IsValid:
            raise NativeError(f"{p.object_id}: the mesh is invalid")
        return mesh
    raise NativeError(f"{p.object_id}: {p.reason}")


def primitive_of_geometry(name: str, geometry, source: dict[str, Any] | None = None) -> Primitive:
    """What a native object's geometry holds, read from the geometry itself (never from its user text)."""

    import rhino3dm

    source = source or {}
    if isinstance(geometry, rhino3dm.Extrusion):
        loops = []
        for i in range(geometry.ProfileCount):
            polyline = geometry.Profile3d(i, 0.0).TryGetPolyline()
            if polyline is None or len(polyline) < 4:
                return Primitive(name, "unsupported", source, reason="a profile that is not a closed polyline")
            loops.append([(polyline[k].X, polyline[k].Y, polyline[k].Z) for k in range(len(polyline))])
        start, end = geometry.PathStart, geometry.PathEnd
        prim = extrusion_from_world(name, loops, (end.X - start.X, end.Y - start.Y, end.Z - start.Z), source)
        return prim
    if isinstance(geometry, rhino3dm.Mesh):
        vertices = []
        for i in range(len(geometry.Vertices)):
            q = geometry.Vertices.Point3dAt(i)
            vertices.append((q.X, q.Y, q.Z))
        triangles = []
        for i in range(geometry.Faces.Count):
            a, b, c, d = geometry.Faces[i]
            triangles.append((a, b, c))
            if d != c:
                triangles.append((a, c, d))
        return Primitive(name, "mesh", source, vertices=tuple(vertices), triangles=tuple(triangles), reason=source.get("reason", ""))
    return Primitive(name, "unsupported", source, reason=f"{type(geometry).__name__} is not read here")


def same_primitive(a: Primitive, b: Primitive, tol: float = EPS) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == "extrusion":
        return (_close(a.start, b.start, tol) and _close(a.end, b.end, tol) and _close(a.up, b.up, tol) and _same_loops(a, b, tol))
    if a.kind == "mesh":
        return (a.triangles == b.triangles and len(a.vertices) == len(b.vertices)
                and all(_close(p, q, tol) for p, q in zip(a.vertices, b.vertices)))
    return False


def _same_loops(a: Primitive, b: Primitive, tol: float) -> bool:
    loops_a, loops_b = (a.outer, *a.holes), (b.outer, *b.holes)
    return len(loops_a) == len(loops_b) and all(
        len(p) == len(q) and all(_close(u, v, tol) for u, v in zip(p, q)) for p, q in zip(loops_a, loops_b))


def geometry_digest(geometry) -> str:
    """The digest ``inspect_three_dm`` takes of an object's geometry: its canonical encoded form."""

    return canonical_digest(geometry.Encode())


# ---------------------------------------------------------------- writing a native file

@dataclass
class NativeExport:
    path: Path
    program: Any
    objects_by_element: dict[str, tuple[str, ...]]
    primitives: dict[str, Primitive]
    binding: dict[str, Any] | None
    bytes: int
    seconds: dict[str, float]

    @property
    def native_ids(self) -> list[str]:
        return sorted(k for k, p in self.primitives.items() if p.native)

    @property
    def mesh_ids(self) -> list[str]:
        return sorted(k for k, p in self.primitives.items() if p.kind == "mesh")


def _layer_index(model, full_path: str) -> int:
    import rhino3dm

    existing = {}
    by_id = {str(layer.Id): layer for layer in model.Layers}
    for index, layer in enumerate(model.Layers):
        names, cursor = [layer.Name], layer
        while str(cursor.ParentLayerId) in by_id:
            cursor = by_id[str(cursor.ParentLayerId)]
            names.insert(0, cursor.Name)
        existing["::".join(names)] = index
    if full_path in existing:
        return existing[full_path]
    segments = full_path.split("::")
    parent = _layer_index(model, "::".join(segments[:-1])) if len(segments) > 1 else None
    layer = rhino3dm.Layer()
    layer.Name = segments[-1]
    if parent is not None:
        layer.ParentLayerId = model.Layers[parent].Id
    return model.Layers.Add(layer)


def add_object(model, p: Primitive, *, layer: str | None, user_text: dict[str, str], visible: bool = True):
    """Add ``p`` under its name with the export's layer and user text, and its ``archflow:primitive``."""

    import rhino3dm

    attributes = rhino3dm.ObjectAttributes()
    attributes.Name = p.object_id
    if layer:
        attributes.LayerIndex = _layer_index(model, layer)
    attributes.Visible = bool(visible)
    for key in sorted(user_text):
        attributes.SetUserString(key, user_text[key])
    attributes.SetUserString("archflow:primitive", canonical_json(primitive_json(p)))
    geometry = to_geometry(p)
    return model.Objects.AddExtrusion(geometry, attributes) if p.kind == "extrusion" else model.Objects.AddMesh(geometry, attributes)


def semantics_of(program) -> dict[str, dict[str, Any]]:
    from monkeycad.program import expected_object_semantics

    return expected_object_semantics(program)["objects"]


def seal(model, record: StateRecord, program, *, parent_sha256: str | None, state: str | None = "C-full") -> dict[str, Any] | None:
    """Write the document strings: the design-state reference, the program digest and, with ``state``, the C-full state and binding."""

    from labs.revision_tasks import carrier, fixtures

    state_digest = fixtures.bound(record).state_digest
    proposal = program.proposal
    strings = {"archflow:design_state_digest": state_digest, "archflow:program_digest": program.program_digest,
               "archflow:project_id": proposal.project_id, "archflow:run_id": proposal.run_id,
               "archflow:length_unit": "meter", "archflow:up_axis": "Z-up"}
    for key, value in strings.items():
        model.Strings[key] = value
    if state is None:
        model.Strings.Delete("archflow:state")
        model.Strings.Delete("archflow:binding")
        return None
    if state != "C-full":
        raise ValueError("a native file carries the C-full state or none")
    model.Strings["archflow:state"] = canonical_json(carrier.payload(record, "C-full", None))
    geometry = {obj.Attributes.Name: geometry_digest(obj.Geometry) for obj in model.Objects}
    bound = carrier.binding(record, state_digest, "C-full", None, parent_sha256, geometry)
    model.Strings["archflow:binding"] = canonical_json(bound)
    return bound


def write_native(record: StateRecord, path: Path, *, state: str | None = "C-full", parent_sha256: str | None = None,
                 compiled=None) -> NativeExport:
    """Compile ``record`` (producers and compiler, no OCCT) and write it as a native editable 3DM at ``path``.

    ``state`` is ``"C-full"`` (the record and its binding in the document strings, as the C-full arm) or None.
    ``compiled`` is a ``pipeline.compile_record`` result already made for this record.
    Refuses (``NativeError``) when an object has no faithful native or mesh form.
    """

    import time

    import rhino3dm

    from labs.revision_tasks import fixtures, pipeline

    t0 = time.perf_counter()
    bound = record if record.base is not None else fixtures.bound(record)
    program, objects_by_element, _ = compiled if compiled is not None else pipeline.compile_record(bound)
    t1 = time.perf_counter()
    prims = primitives(program)
    unsupported = {k: p.reason for k, p in prims.items() if p.kind == "unsupported"}
    if unsupported:
        raise NativeError(f"no faithful native form: {unsupported}")
    semantics = semantics_of(program)
    t2 = time.perf_counter()
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
    for name, p in prims.items():
        row = semantics.get(name, {})
        add_object(model, p, layer=row.get("layer"), user_text=row.get("user_text", {}), visible=row.get("visible", True))
    binding = seal(model, bound, program, parent_sha256=parent_sha256, state=state)
    if not model.Write(str(path), 8):
        raise NativeError(f"{path.name}: rhino3dm could not write the model")
    t3 = time.perf_counter()
    return NativeExport(Path(path), program, objects_by_element, prims, binding, Path(path).stat().st_size,
                        {"compile_s": t1 - t0, "convert_s": t2 - t1, "write_s": t3 - t2})


# ---------------------------------------------------------------- reading a native file

@dataclass(frozen=True)
class NativeFile:
    record: StateRecord
    binding: dict[str, Any]
    file_sha256: str
    primitives: dict[str, Primitive]
    model: Any                                   # the opened rhino3dm.File3dm, for a caller that edits it


def strings_of(model) -> dict[str, str]:
    return {str(model.Strings[i][0]): str(model.Strings[i][1]) for i in range(len(model.Strings))}


def read_native(path: Path) -> NativeFile:
    """A native file's state, checked as the carrier checks a C-full file, and every object as its geometry holds it.

    The checks are ``carrier``'s (state and binding present, record and state
    digests, the design-state reference, every object's geometry digest); a file
    that fails one is refused with ``carrier.CarrierRefused``.
    """

    import rhino3dm

    from labs.revision_tasks import carrier

    data = Path(path).read_bytes()
    model = rhino3dm.File3dm.FromByteArray(data)
    if model is None:
        raise carrier.CarrierRefused("FILE_CORRUPT", f"{Path(path).name}: not a readable 3dm")
    strings = strings_of(model)
    try:
        state, bound = json.loads(strings["archflow:state"]), json.loads(strings["archflow:binding"])
    except KeyError as exc:
        raise carrier.CarrierRefused("METADATA_MISSING", f"no {exc.args[0]} in the file") from exc
    except ValueError as exc:
        raise carrier.CarrierRefused("METADATA_CORRUPT", str(exc)) from exc
    geometry = {obj.Attributes.Name: geometry_digest(obj.Geometry) for obj in model.Objects}
    checked = carrier._checked(state, bound, hashlib.sha256(data).hexdigest(), geometry, strings.get("archflow:design_state_digest"), "3dm")
    prims = {obj.Attributes.Name: primitive_of_geometry(obj.Attributes.Name, obj.Geometry) for obj in model.Objects}
    return NativeFile(checked.record, checked.binding, checked.file_sha256, prims, model)


# ---------------------------------------------------------------- the field-level edit hook

EDIT_FIELDS = ("path", "height", "translate", "profile", "outer", "holes", "regular_polygon", "rectangle", "primitive", "mesh",
               "add", "delete")


def _find(model, name: str):
    found = [obj for obj in model.Objects if obj.Attributes.Name == name]
    if len(found) != 1:
        raise NativeError(f"{name!r}: {len(found)} objects carry this name")
    return found[0]


def _stored_source(obj) -> dict[str, Any]:
    text = obj.Attributes.GetUserString("archflow:primitive")
    try:
        return dict(json.loads(text).get("source", {})) if text else {}
    except ValueError:
        return {}


def _replace(model, obj, p: Primitive) -> None:
    """Swap the object's geometry for ``p``, keeping its id, name, layer and user text."""

    attributes = obj.Attributes
    model.Objects.Delete(attributes.Id)
    attributes.SetUserString("archflow:primitive", canonical_json(primitive_json(p)))
    geometry = to_geometry(p)
    if p.kind == "extrusion":
        model.Objects.AddExtrusion(geometry, attributes)
    else:
        model.Objects.AddMesh(geometry, attributes)


def _refresh(obj, source: dict[str, Any]) -> Primitive:
    p = primitive_of_geometry(obj.Attributes.Name, obj.Geometry, source)
    obj.Attributes.SetUserString("archflow:primitive", canonical_json(primitive_json(p)))
    return p


def _primitive_from_json(name: str, value: dict[str, Any], source: dict[str, Any]) -> Primitive:
    if value.get("kind", "extrusion") == "mesh":
        return Primitive(name, "mesh", source, vertices=tuple(tuple(map(float, v)) for v in value["vertices"]),
                         triangles=tuple(tuple(int(i) for i in t) for t in value["triangles"]), reason=value.get("reason", ""))
    start, end = (tuple(map(float, p)) for p in value["path"])
    up = tuple(map(float, value["up"])) if "up" in value else _up_for(_unit(_sub(end, start)))
    p = Primitive(name, "extrusion", source, start=start, end=end, up=up,  # type: ignore[arg-type]
                  outer=tuple((float(a), float(b)) for a, b in value["outer"]),
                  holes=tuple(tuple((float(a), float(b)) for a, b in h) for h in value.get("holes", [])))
    return extrusion_from_world(name, p.world_loops(), _sub(end, start), source)


def apply_edits(model, edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply field-level edits ``{"object", "field", "value"}`` to an open rhino3dm model; returns what each did.

    The hook a model-driven arm writes its edits through. Profile values are in
    the path's local frame (see the module docstring); ``path`` is
    ``[[x, y, z], [x, y, z]]``; ``height`` keeps the start; ``translate`` moves any
    object; ``regular_polygon`` (``center``, ``radius``, ``segments``,
    ``start_angle_deg``) and ``rectangle`` (``[u0, v0, u1, v1]``) rebuild the outer
    profile; ``primitive`` and ``mesh`` replace the geometry; ``add`` takes
    ``{"primitive", "layer", "user_text", "visible"}``. A path, height or
    translation is set on the stored geometry in place; a new profile needs a new
    extrusion (openNURBS sets a profile only on an empty one), which takes the
    object's id, name and attributes. ``archflow:primitive`` is rewritten from
    the edited geometry. An edit that cannot be applied raises ``NativeError``.
    """

    import rhino3dm

    applied = []
    for edit in edits:
        name, kind, value = edit["object"], edit["field"], edit.get("value")
        if kind not in EDIT_FIELDS:
            raise NativeError(f"{name}: unknown field {kind!r}")
        if kind == "add":
            p = _primitive_from_json(name, value["primitive"], value["primitive"].get("source", {}))
            if any(obj.Attributes.Name == name for obj in model.Objects):
                raise NativeError(f"{name}: an object of this name exists")
            add_object(model, p, layer=value.get("layer"), user_text=value.get("user_text", {}), visible=value.get("visible", True))
            applied.append({"object": name, "field": kind, "mode": "added", "native": p.native})
            continue
        obj = _find(model, name)
        source = _stored_source(obj)
        if kind == "delete":
            model.Objects.Delete(obj.Attributes.Id)
            applied.append({"object": name, "field": kind, "mode": "deleted"})
            continue
        geometry = obj.Geometry
        current = primitive_of_geometry(name, geometry, source)
        if kind == "translate":
            geometry.Translate(rhino3dm.Vector3d(*map(float, value)))
            _refresh(obj, source)
            applied.append({"object": name, "field": kind, "mode": "in_place", "native": current.native})
            continue
        if kind in ("primitive", "mesh"):
            spec = dict(value) if kind == "primitive" else {"kind": "mesh", **value}
            p = _primitive_from_json(name, spec, source)
            _replace(model, obj, p)
            applied.append({"object": name, "field": kind, "mode": "replaced", "native": p.native})
            continue
        if current.kind != "extrusion":
            raise NativeError(f"{name}: {kind} needs an extrusion, the object is a {current.kind}")
        if kind in ("path", "height"):
            if kind == "path":
                start, end = (tuple(map(float, q)) for q in value)
            else:
                _, _, t = current.frame()
                start, end = current.start, _add(current.start, _mul(t, float(value)))
            plane = geometry.GetPathPlane(0.0)
            up = (plane.YAxis.X, plane.YAxis.Y, plane.YAxis.Z)
            if abs(_dot(_unit(_sub(end, start)), up)) > 1e-9:
                raise NativeError(f"{name}: the new path is not square to the profile's up direction")
            if not geometry.SetPathAndUp(rhino3dm.Point3d(*start), rhino3dm.Point3d(*end), rhino3dm.Vector3d(*up)):
                raise NativeError(f"{name}: rhino3dm refused the path")
            _refresh(obj, source)
            applied.append({"object": name, "field": kind, "mode": "in_place", "native": True})
            continue
        outer, holes = list(current.outer), [list(h) for h in current.holes]
        if kind == "profile":
            outer, holes = value["outer"], value.get("holes", [])
        elif kind == "outer":
            outer = value
        elif kind == "holes":
            holes = value
        elif kind == "regular_polygon":
            outer = regular_polygon(value["center"], float(value["radius"]), int(value["segments"]), float(value.get("start_angle_deg", 0.0)))
        elif kind == "rectangle":
            u0, v0, u1, v1 = (float(v) for v in value)
            outer = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        p = _primitive_from_json(name, {"path": [current.start, current.end], "up": current.up, "outer": outer, "holes": holes}, source)
        if not p.native:
            raise NativeError(f"{name}: {p.reason}")
        _replace(model, obj, p)
        applied.append({"object": name, "field": kind, "mode": "replaced", "native": True})
    return applied


def edits_between(current: dict[str, Primitive], new: dict[str, Primitive], semantics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The smallest field-level edits that turn ``current`` (read from a file) into ``new`` (from a program).

    A moved or re-heighted extrusion is one ``path`` edit; a changed profile is
    one ``profile`` edit (plus ``path`` when the path moved too); a mesh that only
    moved is one ``translate``; anything else is replaced whole. Unchanged
    objects get no edit.
    """

    edits: list[dict[str, Any]] = []
    for name in sorted(set(current) | set(new)):
        if name not in new:
            edits.append({"object": name, "field": "delete"})
            continue
        p = new[name]
        if p.kind == "unsupported":
            edits.append({"object": name, "field": "unsupported", "value": p.reason})
            continue
        if name not in current:
            row = semantics.get(name, {})
            edits.append({"object": name, "field": "add", "value": {"primitive": _edit_value(p), "layer": row.get("layer"),
                                                                     "user_text": row.get("user_text", {}), "visible": row.get("visible", True)}})
            continue
        c = current[name]
        if same_primitive(c, p):
            continue
        if c.kind == p.kind == "extrusion" and _close(c.frame()[2], p.frame()[2]) and _close(c.up, p.up):
            if not _same_loops(c, p, EPS):
                edits.append({"object": name, "field": "profile", "value": {"outer": [list(q) for q in p.outer],
                                                                             "holes": [[list(q) for q in h] for h in p.holes]}})
            if not (_close(c.start, p.start) and _close(c.end, p.end)):
                edits.append({"object": name, "field": "path", "value": [list(p.start), list(p.end)]})
            continue
        if c.kind == p.kind == "mesh" and c.triangles == p.triangles and len(c.vertices) == len(p.vertices):
            shift = _sub(p.vertices[0], c.vertices[0])
            if all(_close(_sub(q, v), shift, 1e-9) for v, q in zip(c.vertices, p.vertices)):
                edits.append({"object": name, "field": "translate", "value": list(shift)})
                continue
        edits.append({"object": name, "field": "mesh" if p.kind == "mesh" else "primitive", "value": _edit_value(p)})
    return edits


def _edit_value(p: Primitive) -> dict[str, Any]:
    if p.kind == "mesh":
        return {"kind": "mesh", "vertices": [list(v) for v in p.vertices], "triangles": [list(t) for t in p.triangles],
                "reason": p.reason, "source": p.source}
    return {"kind": "extrusion", "path": [list(p.start), list(p.end)], "up": list(p.up), "outer": [list(q) for q in p.outer],
            "holes": [[list(q) for q in h] for h in p.holes], "source": p.source}


# ---------------------------------------------------------------- meshes for the oracle (harness side)

def mesh_model(native_path: Path, target: Path) -> dict[str, dict[str, Any]]:
    """Write every object of a native file as a named mesh (the oracle reads meshes only); returns each one's box and volume.

    Extrusions are triangulated exactly from their stored profiles and path;
    meshes are copied. Names and visibility are kept; nothing else is.
    """

    import rhino3dm

    source = rhino3dm.File3dm.Read(str(native_path))
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
    numbers = {}
    for obj in source.Objects:
        name = obj.Attributes.Name
        p = primitive_of_geometry(name, obj.Geometry)
        vertices, triangles = mesh_of(p)
        mesh = to_geometry(Primitive(name, "mesh", vertices=tuple(vertices), triangles=tuple(triangles)))
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        attributes.Visible = obj.Attributes.Visible
        model.Objects.AddMesh(mesh, attributes)
        numbers[name] = {"kind": p.kind, "min": [min(v[a] for v in vertices) for a in range(3)],
                         "max": [max(v[a] for v in vertices) for a in range(3)], "volume": mesh_volume(vertices, triangles)}
    if not model.Write(str(target), 8):
        raise NativeError(f"{target.name}: could not write the mesh model")
    return numbers


def compare_numbers(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]], *, box_tol: float = 1e-6,
                    volume_rel: float = 1e-6) -> dict[str, Any]:
    """Per object: the box within ``box_tol`` m and the volume within ``volume_rel`` relative (both maps: min, max, volume)."""

    rows, worst_box, worst_vol = {}, 0.0, 0.0
    for name in sorted(set(a) & set(b)):
        box = max(abs(p - q) for key in ("min", "max") for p, q in zip(a[name][key], b[name][key]))
        vol = abs(a[name]["volume"] - b[name]["volume"]) / max(abs(b[name]["volume"]), 1e-12)
        worst_box, worst_vol = max(worst_box, box), max(worst_vol, vol)
        rows[name] = {"box_dev_m": box, "volume_rel_dev": vol, "agree": box <= box_tol and vol <= volume_rel}
    missing, extra = sorted(set(b) - set(a)), sorted(set(a) - set(b))
    return {"agree": not missing and not extra and all(r["agree"] for r in rows.values()), "missing": missing, "extra": extra,
            "max_box_dev_m": worst_box, "max_volume_rel_dev": worst_vol, "objects": rows}


def readback_numbers(realization) -> dict[str, dict[str, Any]]:
    """Each object's cold-read STEP box and volume from a ``pipeline.Realization`` (model frame, as exported)."""

    return {name: {"min": list(row["bbox"]["min"]), "max": list(row["bbox"]["max"]), "volume": float(row["volume"])}
            for name, row in realization.receipt.readback.items()}


def geometry_boxes(native_path: Path) -> dict[str, dict[str, Any]]:
    """Each object's box as rhino3dm itself computes it from the stored geometry, and the volume of its exact triangulation."""

    import rhino3dm

    rows = {}
    for obj in rhino3dm.File3dm.Read(str(native_path)).Objects:
        box = obj.Geometry.GetBoundingBox()
        p = primitive_of_geometry(obj.Attributes.Name, obj.Geometry)
        rows[obj.Attributes.Name] = {"kind": p.kind, "min": [box.Min.X, box.Min.Y, box.Min.Z], "max": [box.Max.X, box.Max.Y, box.Max.Z],
                                     "volume": shape_numbers(p)["volume"]}
    return rows
