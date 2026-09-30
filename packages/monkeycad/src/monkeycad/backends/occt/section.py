"""Plane sections of named shapes, and the section perspective whose picture plane is the cut."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from monkeycad.backends.occt.errors import OcctBackendError, OcctBuildError
from monkeycad.backends.occt.kernel import _count, _explore, _occt
from monkeycad.backends.occt.projection import (
    OcctDrawingPolyline,
    OcctDrawingRegion,
    _depth_clipped_shape,
    _drawing_edge_points,
    _drawing_entries,
    _drawing_frame,
    _drawing_point,
    _drawing_polylines,
)
from monkeycad.backends.occt.step import StepEntry


def _plane_section(occ, shape, object_id: str, plane):
    try:
        section = occ.BRepAlgoAPI.BRepAlgoAPI_Section(shape, plane, False)
        section.Build()
        if not section.IsDone():
            raise OcctBuildError(f"drawing object {object_id!r}: plane section failed")
        return section.Shape()
    except OcctBackendError:
        raise
    except Exception as exc:
        raise OcctBuildError(f"drawing object {object_id!r}: plane section failed: {exc}") from exc


def section_occt_lines(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str],
    origin: Sequence[float], right: Sequence[float], up: Sequence[float],
    linear_deflection: float,
) -> tuple[OcctDrawingPolyline, ...]:
    """Intersect named shapes with the plane through origin spanned by right/up.

    Frame, output units and chord deviation follow ``project_occt_lines``;
    every returned line has kind ``section``. A valid shape missed by the
    plane contributes no lines; unknown, ambiguous and empty selected objects
    are errors. These are cut edges, not projected background geometry.
    """

    frame = _drawing_frame(origin, right, up, linear_deflection)
    occ = _occt()
    selected = _drawing_entries(occ, entries, object_ids)
    origin, _, _, normal = frame
    plane = occ.gp.gp_Pln(occ.gp.gp_Pnt(*origin), occ.gp.gp_Dir(*normal))
    lines = []
    for entry in selected:
        try:
            section = _plane_section(occ, entry.shape, entry.name, plane)
            lines.extend(_drawing_polylines(occ, section, entry.name, "section",
                                             frame=frame, linear_deflection=linear_deflection))
        except OcctBackendError:
            raise
        except Exception as exc:
            raise OcctBuildError(f"drawing object {entry.name!r}: plane section failed: {exc}") from exc
    return tuple(sorted(set(lines), key=lambda line: (line.object_id, line.kind, line.points)))


def _section_loops(occ, section, object_id: str, *, frame, linear_deflection: float):
    edges = occ.TopTools.TopTools_HSequenceOfShape()
    for item in _explore(occ, section, occ.TopAbs.TopAbs_EDGE):
        edge = occ.TopoDS.TopoDS.Edge_s(item)
        if not occ.BRep.BRep_Tool.Degenerated_s(edge):
            edges.Append(edge)
    wires = occ.TopTools.TopTools_HSequenceOfShape()
    # Shared topology only: proximity must never turn an open cut into material.
    occ.ShapeAnalysis.ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges, 0.0, True, wires)
    loops = []
    for index in range(1, wires.Length() + 1):
        wire = occ.TopoDS.TopoDS.Wire_s(wires.Value(index))
        if not occ.BRep.BRep_Tool.IsClosed_s(wire):
            continue
        explorer = occ.BRepTools.BRepTools_WireExplorer(wire)
        points = []
        visited = 0
        while explorer.More():
            edge = explorer.Current()
            segment = list(_drawing_edge_points(occ, edge, object_id, frame=frame,
                                                linear_deflection=linear_deflection))
            if edge.Orientation() == occ.TopAbs.TopAbs_REVERSED:
                segment.reverse()
            if len(segment) < 2:
                raise OcctBuildError(f"drawing object {object_id!r}: section edge has no drawable extent")
            # Sampled curve endpoints can differ by roundoff; use their shared
            # B-rep vertices, whose connectivity has already proved closure.
            for position, vertex in ((0, occ.TopExp.TopExp.FirstVertex_s(edge, True)),
                                     (-1, occ.TopExp.TopExp.LastVertex_s(edge, True))):
                segment[position] = _drawing_point(occ.BRep.BRep_Tool.Pnt_s(vertex), frame)
            if points and points[-1] != segment[0]:
                raise OcctBuildError(f"drawing object {object_id!r}: section wire is not connected")
            points.extend(segment[1:] if points else segment)
            visited += 1
            explorer.Next()
        if visited != _count(occ, wire, occ.TopAbs.TopAbs_EDGE) or not points or points[0] != points[-1]:
            raise OcctBuildError(f"drawing object {object_id!r}: section wire traversal is incomplete")
        if len(set(points)) < 3:
            continue
        ring = tuple(points[:-1])
        first = min(ring)
        rotations = []
        for direction in (ring, tuple(reversed(ring))):
            for start, point in enumerate(direction):
                if point == first:
                    rotations.append(direction[start:] + direction[:start])
        ring = min(rotations)
        loops.append(ring + (ring[0],))
    return tuple(sorted(set(loops)))


def section_occt_regions(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str],
    origin: Sequence[float], right: Sequence[float], up: Sequence[float],
    linear_deflection: float,
) -> tuple[OcctDrawingRegion, ...]:
    """Closed material sections in the same frame and units as ``section_occt_lines``.

    Each value contains one solid's actual closed cut boundaries, including
    holes and disconnected pieces. Fill its loops together using even-odd;
    fill separate values separately, even when their object_id is the same.
    Open surfaces and shells contribute no material, and missed or tangent
    cuts with no closed boundary contribute no region. Open cut edges are
    never joined by proximity or closed with an invented segment.
    """

    frame = _drawing_frame(origin, right, up, linear_deflection)
    occ = _occt()
    selected = _drawing_entries(occ, entries, object_ids)
    origin, _, _, normal = frame
    plane = occ.gp.gp_Pln(occ.gp.gp_Pnt(*origin), occ.gp.gp_Dir(*normal))
    regions = []
    for entry in selected:
        try:
            for solid in _explore(occ, entry.shape, occ.TopAbs.TopAbs_SOLID):
                section = _plane_section(occ, solid, entry.name, plane)
                loops = _section_loops(occ, section, entry.name, frame=frame,
                                       linear_deflection=linear_deflection)
                if loops:
                    regions.append(OcctDrawingRegion(entry.name, loops))
        except OcctBackendError:
            raise
        except Exception as exc:
            raise OcctBuildError(f"drawing object {entry.name!r}: section regions failed: {exc}") from exc
    return tuple(sorted(set(regions), key=lambda region: (region.object_id, region.loops)))


@dataclass(frozen=True, slots=True)
class OcctSectionPerspective:
    """One section perspective in the section plane's drawing frame; nothing is written.

    ``lines`` holds the ``visible`` edges and silhouettes of what lies behind
    the cut and the ``section`` edges where the plane meets each object;
    ``regions`` holds each solid's closed cut boundaries, filled with even-odd.
    ``principal_point`` is the foot of the eye on the plane: every line
    perpendicular to the plane converges there.  ``focus`` is the eye's
    distance from the plane.  ``drawn_object_ids`` took part in the visibility
    solve, ``cut_object_ids`` were met by the plane, and ``removed_object_ids``
    lie wholly on the eye's side (or beyond the depth) and are not drawn.
    """

    lines: tuple[OcctDrawingPolyline, ...]
    regions: tuple[OcctDrawingRegion, ...]
    principal_point: tuple[float, float]
    focus: float
    drawn_object_ids: tuple[str, ...]
    cut_object_ids: tuple[str, ...]
    removed_object_ids: tuple[str, ...]


def _perspective_camera(occ, frame, eye, tolerance: float):
    """The eye's camera frame, focus and principal point for a picture plane equal to the drawing plane.

    OCCT's ``HLRAlgo_Projector(gp_Ax2(P, N, X), focus)`` puts the eye at
    ``P + focus * N`` and projects onto the plane through ``P`` perpendicular
    to ``N``, with ``X`` and ``N x X`` as its 2D axes: a point on that plane
    maps to its own coordinates about ``P``, and a point at distance ``z``
    beyond it maps ``1 / (1 + z / focus)`` of the way, toward ``P``.  With
    ``P`` the eye's foot on the drawing plane, the plane is drawn true to
    scale and lines perpendicular to it converge at ``P``.

    ``HLRBRep_Algo`` misjudges perspective visibility and silhouettes when
    that projector frame is not the identity (cadquery-ocp 7.9: back faces on
    one side of ``P`` stay visible, and a cylinder's silhouettes land off its
    tangent lines).  The solve therefore runs on the shapes located in the
    camera frame (``to_camera``: ``P`` at the origin, ``X`` right, ``Z``
    toward the eye), an exact rigid move, with the identity-frame projector.
    """

    origin, right, up, normal = frame
    if (not isinstance(eye, Sequence) or isinstance(eye, (str, bytes)) or len(eye) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in eye)):
        raise OcctBackendError("the eye must be three finite numbers")
    eye = tuple(float(v) for v in eye)
    focus = sum((e - o) * n for e, o, n in zip(eye, origin, normal))
    if not focus > tolerance:
        raise OcctBackendError("the eye must stand off the section plane on its removed side (along right x up)")
    foot = tuple(e - focus * n for e, n in zip(eye, normal))
    principal = (sum((f - o) * r for f, o, r in zip(foot, origin, right)),
                 sum((f - o) * u for f, o, u in zip(foot, origin, up)))
    to_camera = occ.gp.gp_Trsf()
    to_camera.SetTransformation(occ.gp.gp_Ax3(occ.gp.gp_Pnt(*foot), occ.gp.gp_Dir(*normal), occ.gp.gp_Dir(*right)))
    identity = occ.gp.gp_Ax2(occ.gp.gp_Pnt(0.0, 0.0, 0.0), occ.gp.gp_Dir(0.0, 0.0, 1.0), occ.gp.gp_Dir(1.0, 0.0, 0.0))
    return to_camera, occ.HLRAlgo.HLRAlgo_Projector(identity, focus), focus, principal


def project_occt_section_perspective(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str],
    origin: Sequence[float], right: Sequence[float], up: Sequence[float],
    eye: Sequence[float], linear_deflection: float, depth: float | None = None,
) -> OcctSectionPerspective:
    """Cut the named shapes with a plane and draw what remains in exact perspective from ``eye``.

    The section plane passes through ``origin`` and is spanned by the unit,
    perpendicular ``right`` and ``up``; ``right cross up`` points to the
    removed side, where the eye must stand.  Frame, units and chord
    deviation follow ``project_occt_lines``.  Nothing is written.

    Every selected shape is cut exactly: the part on the eye's side is
    discarded by a Boolean common with an oriented box on the kept side
    (``_depth_clipped_shape`` with near depth 0, bounded by ``depth`` when
    given), so nothing on the removed side draws or hides.  The kept parts
    take part in one exact ``HLRBRep_Algo`` solve with the perspective
    projector of ``_perspective_camera``, whose picture plane is the
    section plane; visible sharp edges and silhouettes are extracted per
    object and hidden lines are not.  The plane's sections of the original
    shapes (``section`` lines and each solid's closed regions, as in
    ``section_occt_lines`` and ``section_occt_regions``) are mapped through
    the same camera move and projector.

    Output ``points`` are drawing coordinates: on the plane ``u = dot(p -
    origin, right)`` and ``v = dot(p - origin, up)``, and behind it the
    perspective image in that same frame.  The cut is therefore true to
    scale and independent of where the eye stands.
    """

    frame = _drawing_frame(origin, right, up, linear_deflection)
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, (int, float))
                              or not math.isfinite(depth) or depth <= 0.0):
        raise OcctBackendError("the section depth must be finite and positive")
    occ = _occt()
    selected = _drawing_entries(occ, entries, object_ids)
    origin, right, up, normal = frame
    to_camera, projector, focus, principal = _perspective_camera(occ, frame, eye, linear_deflection)
    look = tuple(-v for v in normal)

    def depth_extent(entry):
        box = occ.Bnd.Bnd_Box()
        occ.BRepBndLib.BRepBndLib.AddOptimal_s(entry.shape, box, False, False)
        if box.IsVoid():
            raise OcctBuildError(f"drawing object {entry.name!r} has no bounds")
        xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
        values = [sum((c - o) * l for c, o, l in zip((x, y, z), origin, look))
                  for x in (xmin, xmax) for y in (ymin, ymax) for z in (zmin, zmax)]
        return min(values), max(values)

    try:
        extents = {entry.name: depth_extent(entry) for entry in selected}
        # Without a depth the kept box reaches past everything selected; it only cuts.
        far = float(depth) if depth is not None else max(1.0, 1.0 + 1.01 * max(high for _, high in extents.values()))
        participating = []
        for entry in selected:
            shape = _depth_clipped_shape(occ, entry, frame, (0.0, far))
            if shape is not None:
                participating.append((entry.name, shape))
        pu, pv = principal
        camera_location = occ.TopLoc.TopLoc_Location(to_camera)

        def hlr_point(point):
            return (float(point.X()) + pu, float(point.Y()) + pv)

        def model_point(point):
            image = occ.gp.gp_Pnt2d()
            projector.Project(point.Transformed(to_camera), image)
            return (float(image.X()) + pu, float(image.Y()) + pv)

        lines = []
        if participating:
            algorithm = occ.HLRBRep.HLRBRep_Algo()
            located = [(name, shape.Moved(camera_location)) for name, shape in participating]
            for _, shape in located:
                algorithm.Add(shape)
            algorithm.Projector(projector)
            algorithm.Update()
            algorithm.Hide()
            extraction = occ.HLRBRep.HLRBRep_HLRToShape(algorithm)
            for name, shape in located:
                for method in ("VCompound", "OutLineVCompound"):
                    lines.extend(_drawing_polylines(occ, getattr(extraction, method)(shape), name, "visible",
                                                    frame=hlr_point, linear_deflection=linear_deflection))
        plane = occ.gp.gp_Pln(occ.gp.gp_Pnt(*origin), occ.gp.gp_Dir(*normal))
        regions = []
        for entry in selected:
            low, high = extents[entry.name]
            if high < 0.0 or low > 0.0:
                continue  # the plane cannot meet it
            section = _plane_section(occ, entry.shape, entry.name, plane)
            lines.extend(_drawing_polylines(occ, section, entry.name, "section",
                                            frame=model_point, linear_deflection=linear_deflection))
            for solid in _explore(occ, entry.shape, occ.TopAbs.TopAbs_SOLID):
                loops = _section_loops(occ, _plane_section(occ, solid, entry.name, plane), entry.name,
                                       frame=model_point, linear_deflection=linear_deflection)
                if loops:
                    regions.append(OcctDrawingRegion(entry.name, loops))
    except OcctBackendError:
        raise
    except Exception as exc:
        raise OcctBuildError(f"section perspective failed: {exc}") from exc
    drawn = tuple(sorted(name for name, _ in participating))
    return OcctSectionPerspective(
        lines=tuple(sorted(set(lines), key=lambda line: (line.object_id, line.kind, line.points))),
        regions=tuple(sorted(set(regions), key=lambda region: (region.object_id, region.loops))),
        principal_point=principal,
        focus=focus,
        drawn_object_ids=drawn,
        cut_object_ids=tuple(sorted({line.object_id for line in lines if line.kind == "section"})),
        removed_object_ids=tuple(sorted({entry.name for entry in selected} - set(drawn))),
    )


__all__ = [
    "OcctSectionPerspective",
    "project_occt_section_perspective",
    "section_occt_lines",
    "section_occt_regions",
]
