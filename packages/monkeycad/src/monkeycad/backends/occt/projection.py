"""Orthographic visible and hidden lines of named shapes: one exact HLR solve in a caller-stated drawing frame."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from monkeycad.backends.occt.errors import OcctBackendError, OcctBuildError
from monkeycad.backends.occt.kernel import _count, _explore, _occt
from monkeycad.backends.occt.step import StepEntry


@dataclass(frozen=True, slots=True)
class OcctDrawingPolyline:
    """One object's visible or hidden edge, discretized, in the caller's drawing frame."""

    object_id: str
    kind: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class OcctDrawingRegion:
    """One solid's section boundaries; fill these closed loops with even-odd winding."""

    object_id: str
    loops: tuple[tuple[tuple[float, float], ...], ...]

def _drawing_frame(origin, right, up, linear_deflection):
    vectors = []
    for label, value in (("origin", origin), ("right", right), ("up", up)):
        if (not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
            raise OcctBackendError(f"drawing {label} must be three finite numbers")
        vectors.append(tuple(float(v) for v in value))
    origin, right, up = vectors
    for label, vector in (("right", right), ("up", up)):
        if not math.isclose(math.hypot(*vector), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise OcctBackendError(f"drawing {label} must be a unit direction")
    if abs(sum(r * u for r, u in zip(right, up))) > 1e-9:
        raise OcctBackendError("drawing right and up must be perpendicular")
    if (isinstance(linear_deflection, bool) or not isinstance(linear_deflection, (int, float))
            or not math.isfinite(linear_deflection) or linear_deflection <= 0.0):
        raise OcctBackendError("drawing linear_deflection must be finite and positive in the shape's unit")
    right = tuple(v / math.hypot(*right) for v in right)
    normal = (right[1] * up[2] - right[2] * up[1],
              right[2] * up[0] - right[0] * up[2],
              right[0] * up[1] - right[1] * up[0])
    # Match gp_Ax2's orthonormal frame exactly, including accepted numeric roundoff.
    normal = tuple(v / math.hypot(*normal) for v in normal)
    up = (normal[1] * right[2] - normal[2] * right[1],
          normal[2] * right[0] - normal[0] * right[2],
          normal[0] * right[1] - normal[1] * right[0])
    return origin, right, up, normal


def _drawing_depth_range(depth_range):
    if depth_range is None:
        return None
    if (not isinstance(depth_range, Sequence) or isinstance(depth_range, (str, bytes)) or len(depth_range) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in depth_range)):
        raise OcctBackendError("drawing depth_range must be two finite depths (near, far)")
    near, far = (float(v) for v in depth_range)
    if not near < far:
        raise OcctBackendError("drawing depth_range must have near < far")
    return near, far


def _drawing_entries(occ, entries: Sequence[StepEntry], object_ids: Sequence[str]) -> tuple[StepEntry, ...]:
    if (not isinstance(object_ids, Sequence) or isinstance(object_ids, (str, bytes)) or not object_ids
            or any(not isinstance(name, str) or not name.strip() for name in object_ids)
            or len(set(object_ids)) != len(object_ids)):
        raise OcctBackendError("drawing object_ids must name at least one unique, non-empty object")
    by_name: dict[str, list[StepEntry]] = {}
    for entry in entries:
        if not isinstance(entry, StepEntry):
            raise OcctBackendError("drawing entries must be StepEntry values")
        if entry.name is not None:
            by_name.setdefault(entry.name, []).append(entry)
    selected = []
    for name in sorted(object_ids):
        matches = by_name.get(name, [])
        if len(matches) != 1:
            raise OcctBackendError(f"drawing object {name!r} is {'unknown' if not matches else 'ambiguous'}")
        entry = matches[0]
        shape = entry.shape
        if (not isinstance(shape, occ.TopoDS.TopoDS_Shape) or shape.IsNull()
                or _count(occ, shape, occ.TopAbs.TopAbs_EDGE) == 0):
            raise OcctBackendError(f"drawing object {name!r} has no non-empty shape")
        if not occ.BRepCheck.BRepCheck_Analyzer(shape).IsValid():
            raise OcctBackendError(f"drawing object {name!r} has an invalid shape")
        selected.append(entry)
    return tuple(selected)


def _depth_clipped_shape(occ, entry: StepEntry, frame, depth_range):
    """The entry's shape restricted to the near/far slab, or None when it lies wholly outside.

    Depth is measured from the drawing origin along the look direction (the
    opposite of ``right cross up``).  A shape wholly inside the slab is used
    as it is; one wholly outside takes no part in the visibility solve; one
    crossing a slab plane is cut exactly (Boolean common with the slab), so
    its cut boundary appears as a drawn edge and the part beyond the plane
    neither draws nor hides.
    """

    origin, right, _, normal = frame
    near, far = depth_range
    look = tuple(-v for v in normal)
    across = (look[1] * right[2] - look[2] * right[1],
              look[2] * right[0] - look[0] * right[2],
              look[0] * right[1] - look[1] * right[0])
    box = occ.Bnd.Bnd_Box()
    occ.BRepBndLib.BRepBndLib.AddOptimal_s(entry.shape, box, False, False)
    if box.IsVoid():
        raise OcctBuildError(f"drawing object {entry.name!r} has no bounds")
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    corners = [(x, y, z) for x in (xmin, xmax) for y in (ymin, ymax) for z in (zmin, zmax)]
    def extent(direction):
        values = [sum((c - o) * d for c, o, d in zip(corner, origin, direction)) for corner in corners]
        return min(values), max(values)
    depth_min, depth_max = extent(look)
    if depth_max < near or depth_min > far:
        return None
    if depth_min >= near and depth_max <= far:
        return entry.shape
    u_min, u_max = extent(right)
    w_min, w_max = extent(across)
    pad = 1.0 + max(u_max - u_min, w_max - w_min)
    corner = tuple(o + near * l + (u_min - pad) * r + (w_min - pad) * a
                   for o, l, r, a in zip(origin, look, right, across))
    axes = occ.gp.gp_Ax2(occ.gp.gp_Pnt(*corner), occ.gp.gp_Dir(*look), occ.gp.gp_Dir(*right))
    slab = occ.BRepPrimAPI.BRepPrimAPI_MakeBox(axes, (u_max - u_min) + 2 * pad, (w_max - w_min) + 2 * pad,
                                               far - near).Shape()
    common = occ.BRepAlgoAPI.BRepAlgoAPI_Common(entry.shape, slab)
    common.Build()
    if not common.IsDone():
        raise OcctBuildError(f"drawing object {entry.name!r}: depth clip failed")
    clipped = common.Shape()
    if clipped.IsNull() or _count(occ, clipped, occ.TopAbs.TopAbs_EDGE) == 0:
        return None
    return clipped


def _drawing_point(point, frame=None):
    """A sampled point in drawing coordinates.

    An HLR result is already expressed in the drawing plane, so it needs no
    frame.  A section is cut in the model's own frame and is measured against
    the drawing's origin and axes, which is what ``frame`` supplies.  A
    perspective drawing supplies its projector's mapping as a callable.
    """

    if frame is None:
        return (float(point.X()), float(point.Y()))
    if callable(frame):
        return frame(point)
    origin, right, up, _ = frame
    delta = tuple(v - o for v, o in zip((point.X(), point.Y(), point.Z()), origin))
    return (sum(v * r for v, r in zip(delta, right)), sum(v * u for v, u in zip(delta, up)))


def _drawing_edge_points(occ, edge, object_id: str, *, frame=None, linear_deflection: float):
    if occ.BRep.BRep_Tool.Degenerated_s(edge):
        return ()
    curve = occ.BRepAdaptor.BRepAdaptor_Curve(edge)
    sample = occ.GCPnts.GCPnts_UniformDeflection(curve, linear_deflection, True)
    if not sample.IsDone():
        raise OcctBuildError(f"drawing object {object_id!r}: edge discretization failed")
    points = []
    for index in range(1, sample.NbPoints() + 1):
        xy = _drawing_point(sample.Value(index), frame)
        if not points or xy != points[-1]:
            points.append(xy)
    return tuple(points)


def _drawing_polylines(occ, shape, object_id: str, kind: str, *, frame=None, linear_deflection: float):
    """Discretize actual B-rep edges; HLR edges already lie in drawing XY."""

    if shape.IsNull():
        return ()
    lines = []
    for item in _explore(occ, shape, occ.TopAbs.TopAbs_EDGE):
        edge = occ.TopoDS.TopoDS.Edge_s(item)
        points = _drawing_edge_points(occ, edge, object_id, frame=frame, linear_deflection=linear_deflection)
        if len(points) > 1:
            lines.append(OcctDrawingPolyline(object_id, kind, min(points, tuple(reversed(points)))))
    return tuple(lines)


def project_occt_lines(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str],
    origin: Sequence[float], right: Sequence[float], up: Sequence[float],
    linear_deflection: float, depth_range: Sequence[float] | None = None,
) -> tuple[OcctDrawingPolyline, ...]:
    """Orthographic sharp edges and silhouettes, with visibility among selected objects.

    Inputs are named shapes, normally from ``read_step``. Origin and all output
    points use that read's CAD Z-up frame and length unit; right/up are unit,
    perpendicular directions. ``right cross up`` points toward the viewer, so
    the look direction is its opposite. ``linear_deflection`` is the maximum
    chord deviation in that same unit (0.0001 for a 0.1 mm drawing from metre
    shapes). Output ``points`` are ``(dot(p - origin, right), dot(p - origin,
    up))``. Nothing is written.

    All selected shapes participate in one exact HLR calculation
    (``HLRBRep_Algo``), then their lines are extracted per object name and
    tagged ``visible`` or ``hidden``; an object entirely behind others may
    have no visible line and still hides nothing less. Coincident front/back
    edges can carry both kinds; draw hidden lines before visible ones.
    Unselected shapes do not hide.

    ``depth_range`` (near, far), measured from origin along the look
    direction, restricts the solve to that slab exactly: shapes wholly
    outside take no part, shapes crossing a plane are cut there (see
    ``_depth_clipped_shape``). ``None`` uses the full depth.
    """

    frame = _drawing_frame(origin, right, up, linear_deflection)
    slab = _drawing_depth_range(depth_range)
    occ = _occt()
    selected = _drawing_entries(occ, entries, object_ids)
    origin, right, _, normal = frame
    try:
        participating = []
        for entry in selected:
            shape = entry.shape if slab is None else _depth_clipped_shape(occ, entry, frame, slab)
            if shape is not None:
                participating.append((entry.name, shape))
        if not participating:
            return ()
        algorithm = occ.HLRBRep.HLRBRep_Algo()
        for _, shape in participating:
            algorithm.Add(shape)
        axis = occ.gp.gp_Ax2(occ.gp.gp_Pnt(*origin), occ.gp.gp_Dir(*normal), occ.gp.gp_Dir(*right))
        algorithm.Projector(occ.HLRAlgo.HLRAlgo_Projector(axis))
        algorithm.Update()
        algorithm.Hide()
        extraction = occ.HLRBRep.HLRBRep_HLRToShape(algorithm)
        lines = []
        for name, shape in participating:
            for kind, methods in (("visible", ("VCompound", "OutLineVCompound")),
                                  ("hidden", ("HCompound", "OutLineHCompound"))):
                for method in methods:
                    lines.extend(_drawing_polylines(occ, getattr(extraction, method)(shape), name, kind,
                                                    linear_deflection=linear_deflection))
    except OcctBackendError:
        raise
    except Exception as exc:
        raise OcctBuildError(f"orthographic projection failed: {exc}") from exc
    return tuple(sorted(set(lines), key=lambda line: (line.object_id, line.kind, line.points)))


__all__ = [
    "OcctDrawingPolyline",
    "OcctDrawingRegion",
    "project_occt_lines",
]
