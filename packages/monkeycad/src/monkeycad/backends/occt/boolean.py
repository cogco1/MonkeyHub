"""How the OCCT backend realizes a difference and an intersection of several inputs.

A ``boolean_difference`` whose voids pierce an extruded base along its
extrusion can be realized as one planar face with holes, extruded once
(#419); ``_difference_plan`` decides that in plain planar geometry before
any kernel call. A ``boolean_intersection`` is folded pairwise, because
OCCT's one-call common is not the intersection of every input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from archflow.state.geometry_program import lift_to_base_level, operation_parameters
from monkeycad.backends.occt.errors import OcctBuildError
from monkeycad.backends.occt.kernel import _count, _polygon, _shape_list, cad_point


#: How a boolean_difference may be realized (#419): ``auto`` extrudes one face
#: with holes when every void pierces an extruded base along its extrusion and
#: cuts otherwise; ``boolean`` always cuts; ``profile_with_holes`` refuses
#: a difference it cannot realize that way.
DIFFERENCE_STRATEGIES = ("auto", "boolean", "profile_with_holes")
_PLAN_TOLERANCE = 1e-7
#: The required gap, in the program's length unit (``cad_point`` applies no
#: scale; a millimetre or foot program keeps this value in millimetres or
#: feet), between a void and the outline and between voids (#419
#: IMPORTANT 2). Coarser than ``_PLAN_TOLERANCE``, which bounds coplanarity,
#: reach and vector displacement: at tolerance-scale clearance a boolean cut
#: can merge or shift faces where the profile keeps them apart, so a gap
#: closer than this falls back to a cut under ``auto`` and is refused under
#: ``profile_with_holes``.
_PLAN_CLEARANCE = 1e-5


@dataclass(frozen=True)
class _ProfileWithHoles:
    """One planar face with inner wires, extruded once: a difference whose voids pierce an extruded base."""

    outer: tuple[tuple[float, float, float], ...]
    holes: tuple[tuple[tuple[float, float, float], ...], ...]
    vector: tuple[float, float, float]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _loop(points):
    """A profile's distinct vertices, without a repeated closing point."""

    loop = [tuple(float(value) for value in point) for point in points]
    return loop[:-1] if len(loop) > 1 and loop[0] == loop[-1] else loop


def _unit_normal(loop):
    """Newell's normal of a planar loop, or None when the loop has no area."""

    normal = [0.0, 0.0, 0.0]
    for a, b in zip(loop, loop[1:] + loop[:1]):
        normal[0] += (a[1] - b[1]) * (a[2] + b[2])
        normal[1] += (a[2] - b[2]) * (a[0] + b[0])
        normal[2] += (a[0] - b[0]) * (a[1] + b[1])
    length = _length(normal)
    return None if length <= _PLAN_TOLERANCE else tuple(value / length for value in normal)


def _in_plane(loop, origin, normal):
    """The loop in an orthonormal basis of the plane through ``origin`` with ``normal``."""

    seed = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    first = _cross(normal, seed)
    first = tuple(value / _length(first) for value in first)
    second = _cross(normal, first)
    return [(_dot(_sub(point, origin), first), _dot(_sub(point, origin), second)) for point in loop]


def _signed_area(flat):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(flat, flat[1:] + flat[:1])) / 2.0


def _edges(flat):
    return list(zip(flat, flat[1:] + flat[:1]))


def _point_segment_gap(point, a, b):
    """The distance from ``point`` to segment ``ab`` in the plane."""

    dx, dy = b[0] - a[0], b[1] - a[1]
    span = dx * dx + dy * dy
    t = 0.0 if span == 0.0 else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / span))
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


def _segment_gap(p, q, a, b):
    """The distance between segments pq and ab in the plane; zero when they cross or touch."""

    def orient(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])

    d1, d2, d3, d4 = orient(a, b, p), orient(a, b, q), orient(p, q, a), orient(p, q, b)
    if 0.0 not in (d1, d2, d3, d4) and (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0):
        return 0.0

    return min(
        _point_segment_gap(p, a, b), _point_segment_gap(q, a, b),
        _point_segment_gap(a, p, q), _point_segment_gap(b, p, q),
    )


def _inside(point, flat):
    """Even-odd containment of a point that lies on no edge."""

    inside = False
    for a, b in _edges(flat):
        if (a[1] > point[1]) != (b[1] > point[1]):
            if point[0] < a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                inside = not inside
    return inside


def _apart(first, second):
    return all(_segment_gap(p, q, a, b) > _PLAN_CLEARANCE for p, q in _edges(first) for a, b in _edges(second))


def _simple(flat):
    """No two non-adjacent edges, and no vertex against an edge it does not touch, are closer than the plan clearance.

    A triangle has no non-adjacent edge pair (every edge shares a vertex
    with every other), so the first test alone never holds a triangular
    void to clearance; the second test does, and also catches a sliver in
    any larger polygon, by checking every vertex against every edge it is
    not an endpoint of.
    """

    edges = _edges(flat)
    count = len(edges)
    if not all(
        _segment_gap(*edges[i], *edges[j]) > _PLAN_CLEARANCE
        for i in range(count) for j in range(i + 2, count) if not (i == 0 and j == count - 1)
    ):
        return False
    return all(
        _point_segment_gap(flat[vertex], *edges[edge]) > _PLAN_CLEARANCE
        for vertex in range(count) for edge in range(count)
        if vertex != edge and vertex != (edge + 1) % count
    )


def _difference_plan(operation, operations, producers):
    """A face-with-holes realization of this boolean_difference, or ``None`` and why not (#419).

    It applies when the base is an extrusion and every void is an extrusion
    whose own vector reaches, at every one of its vertices, across the
    base's whole extrusion extent (projected onto the base's normal), and
    whose direction agrees with the base's own vector within an absolute
    displacement bound measured across the base's thickness - never in how
    far the void's profile sits from the base plane, which the bound never
    depends on. Each void vertex is then projected onto the base's plane
    along the VOID's own vector (exact for a tilted profile or a leaning
    vector alike), giving a simple, non-degenerate hole that must lie
    strictly inside the base profile, apart from every other void by at
    least the plan clearance. The result is then exactly the base profile
    minus the projected void profiles, extruded once.
    """

    params = operation_parameters(operation)
    inputs = sorted(operation.input_object_ids)
    base_id = inputs[int(params.get("base_index", 0))]
    source = operations.get(producers.get(base_id, ""))
    if source is None or source.kind.value != "extrusion":
        return None, f"the base {base_id} is not an extrusion"
    base_params = operation_parameters(source)
    outer = _loop(lift_to_base_level(base_params["profile"], base_params, source.op_id))
    vector = tuple(float(value) for value in base_params["vector"])
    normal = _unit_normal(outer)
    rise = 0.0 if normal is None else _dot(vector, normal)
    if normal is None or abs(rise) <= _PLAN_TOLERANCE:
        return None, f"the base {base_id} has no area across its extrusion"
    origin = outer[0]
    extent = sorted((0.0, rise))
    holes = []
    for void_id in inputs:
        if void_id == base_id:
            continue
        void = operations.get(producers.get(void_id, ""))
        if void is None or void.kind.value != "extrusion":
            return None, f"the void {void_id} is not an extrusion"
        void_params = operation_parameters(void)
        loop = _loop(lift_to_base_level(void_params["profile"], void_params, void.op_id))
        void_vector = tuple(float(value) for value in void_params["vector"])
        v_n = _dot(void_vector, normal)
        if abs(v_n) <= _PLAN_TOLERANCE:
            return None, f"the void {void_id} is not extruded along the base"
        # Vertex-independent (#419 CRITICAL 1): how far the void's walls
        # would drift from the base's own extrusion across the base's
        # thickness, in the program's length unit (an absolute displacement,
        # not a fraction of it) - never in how far the void's profile plane
        # sits from the base, which the old angle-only test left unbounded.
        scaled = tuple((rise / v_n) * component for component in void_vector)
        if _length(_sub(scaled, vector)) > _PLAN_TOLERANCE:
            return None, f"the void {void_id} is not extruded along the base"
        projected = []
        for point in loop:
            offset = _dot(_sub(point, origin), normal)
            reach = sorted((offset, offset + v_n))
            if reach[0] > extent[0] + _PLAN_TOLERANCE or reach[1] < extent[1] - _PLAN_TOLERANCE:
                return None, f"the void {void_id} does not pass through the base"
            # Project this vertex onto the base plane along the VOID's own
            # vector, not the base's (#419 CRITICAL 1): exact for every
            # vertex of a tilted profile, since each uses its own offset.
            projected.append(tuple(point[i] - (offset / v_n) * void_vector[i] for i in range(3)))
        holes.append(tuple(projected))
    flat_outer = _in_plane(outer, origin, normal)
    flat_holes = [_in_plane(hole, origin, normal) for hole in holes]
    for index, flat in enumerate(flat_holes):
        if not _simple(flat):
            return None, "a void profile crosses itself or is thinner than the clearance"
        if not _apart(flat, flat_outer) or not all(_inside(point, flat_outer) for point in flat):
            return None, "a void does not lie strictly inside the base profile"
        for other in flat_holes[:index]:
            if not _apart(flat, other) or _inside(flat[0], other) or _inside(other[0], flat):
                return None, "two voids touch or overlap"
    clockwise = _signed_area(flat_outer) < 0
    oriented = tuple(
        hole if (_signed_area(flat) < 0) != clockwise else tuple(reversed(hole))
        for hole, flat in zip(holes, flat_holes)
    )
    return _ProfileWithHoles(tuple(outer), oriented, vector), None


def _profile_with_holes(occ: SimpleNamespace, plan: _ProfileWithHoles, op_id: str):
    maker = occ.BRepBuilderAPI.BRepBuilderAPI_MakeFace(_polygon(occ, plan.outer, op_id), True)
    # IsDone reflects only this outer-wire construction; Add (below) reports
    # no per-hole status in this binding, so the error names what is actually
    # checked here rather than claiming the holes were verified too.
    if not maker.IsDone():
        raise OcctBuildError(f"{op_id}: the outer profile is not one planar face")
    for hole in plan.holes:
        maker.Add(_polygon(occ, hole, op_id))
    vx, vy, vz = cad_point(plan.vector)
    return occ.BRepPrimAPI.BRepPrimAPI_MakePrism(maker.Face(), occ.gp.gp_Vec(vx, vy, vz)).Shape()


def _joint_intersection(occ: SimpleNamespace, op_id: str, inputs: Sequence[str], shapes: Mapping[str, Any]):
    """The volume common to every input: ``A ∩ B ∩ C ...``, as the IR states it.

    ``BRepAlgoAPI_Common`` with one argument and several tools computes
    ``A ∩ (B ∪ C)``, which is not the analytic contract
    (``expected_object_bounds`` bounds the intersection of all inputs).
    The joint intersection is therefore folded pairwise: each step meets the
    running result with the next input.  A step whose result holds no solid
    means the inputs share no volume; that fails here by name rather than
    feeding an empty shape into the next kernel call.
    """

    result = shapes[inputs[0]]
    for item in inputs[1:]:
        algorithm = occ.BRepAlgoAPI.BRepAlgoAPI_Common()
        algorithm.SetArguments(_shape_list(occ, (result,)))
        algorithm.SetTools(_shape_list(occ, (shapes[item],)))
        algorithm.SetRunParallel(False)
        algorithm.Build()
        if not algorithm.IsDone():
            raise OcctBuildError(
                f"{op_id} (boolean_intersection): OCCT boolean did not complete meeting {item}"
            )
        result = algorithm.Shape()
        if result is None or result.IsNull() or _count(occ, result, occ.TopAbs.TopAbs_SOLID) == 0:
            raise OcctBuildError(
                f"{op_id} (boolean_intersection): inputs share no volume once {item} is met; "
                "the intersection is empty"
            )
    return result


__all__ = [
    "DIFFERENCE_STRATEGIES",
]
