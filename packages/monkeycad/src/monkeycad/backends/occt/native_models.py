"""A native ``.3dm``'s visible geometry as OCCT shapes, read through rhino3dm without a host."""

from __future__ import annotations

import importlib
import math

from monkeycad.backends.occt.errors import (
    OcctBackendError,
    OcctBuildError,
    OcctCapabilityError,
    OcctUnavailableError,
)
from monkeycad.backends.occt.kernel import _explore, _occt
from monkeycad.backends.occt.step import StepEntry


def _native_wire(occ, curve, object_id):
    """Transfer curve geometry without fitting or sampling it."""
    polyline = curve.TryGetPolyline()
    if polyline is not None:
        maker = occ.BRepBuilderAPI.BRepBuilderAPI_MakePolygon()
        points = list(polyline)
        closed = len(points) > 2 and points[0] == points[-1]
        for point in points[:-1] if closed else points:
            maker.Add(occ.gp.gp_Pnt(point.X, point.Y, point.Z))
        if closed:
            maker.Close()
        if not maker.IsDone():
            raise OcctBuildError(f"3DM object {object_id}: invalid polyline")
        return maker.Wire()
    arc = curve.TryGetArc()
    if arc is not None:
        plane = arc.Plane
        axis = occ.gp.gp_Ax2(occ.gp.gp_Pnt(plane.Origin.X, plane.Origin.Y, plane.Origin.Z),
                            occ.gp.gp_Dir(plane.ZAxis.X, plane.ZAxis.Y, plane.ZAxis.Z),
                            occ.gp.gp_Dir(plane.XAxis.X, plane.XAxis.Y, plane.XAxis.Z))
        edge = occ.BRepBuilderAPI.BRepBuilderAPI_MakeEdge(occ.gp.gp_Circ(axis, arc.Radius), arc.StartAngle, arc.EndAngle)
        return occ.BRepBuilderAPI.BRepBuilderAPI_MakeWire(edge.Edge()).Wire()
    nurbs = curve.ToNurbsCurve()
    if nurbs is None or not nurbs.IsValid:
        raise OcctBuildError(f"3DM object {object_id}: curve has no valid NURBS form")
    # OpenNURBS omits two superfluous end knots. Trimmed/clamped curves have
    # the repeated ends below; periodic/non-clamped forms need another transfer.
    knots = list(nurbs.Knots)
    degree = nurbs.Degree
    if knots[:degree] != [knots[0]] * degree or knots[-degree:] != [knots[-1]] * degree:
        raise OcctCapabilityError(object_id, "3dm-curve", "non-clamped NURBS curves are unsupported")
    unique, multiplicities = [], []
    for knot in [knots[0], *knots, knots[-1]]:
        if unique and unique[-1] == knot:
            multiplicities[-1] += 1
        else:
            unique.append(knot)
            multiplicities.append(1)
    poles = occ.TColgp.TColgp_Array1OfPnt(1, len(nurbs.Points))
    weights = occ.TColStd.TColStd_Array1OfReal(1, len(nurbs.Points))
    for index, point in enumerate(nurbs.Points, 1):
        # OpenNURBS stores homogeneous control points, unlike OCCT poles.
        if not math.isfinite(point.W) or point.W <= 0:
            raise OcctBuildError(f"3DM object {object_id}: invalid NURBS weight")
        poles.SetValue(index, occ.gp.gp_Pnt(point.X / point.W, point.Y / point.W, point.Z / point.W))
        weights.SetValue(index, point.W)
    values = occ.TColStd.TColStd_Array1OfReal(1, len(unique))
    mults = occ.TColStd.TColStd_Array1OfInteger(1, len(unique))
    for index, (knot, count) in enumerate(zip(unique, multiplicities), 1):
        values.SetValue(index, knot)
        mults.SetValue(index, count)
    geometry = occ.Geom.Geom_BSplineCurve(poles, weights, values, mults, degree, False)
    edge = occ.BRepBuilderAPI.BRepBuilderAPI_MakeEdge(geometry, nurbs.Domain.T0, nurbs.Domain.T1)
    if not edge.IsDone():
        raise OcctBuildError(f"3DM object {object_id}: NURBS edge transfer failed")
    return occ.BRepBuilderAPI.BRepBuilderAPI_MakeWire(edge.Edge()).Wire()


def _native_sew(occ, faces, object_id, *, closed, allow_disjoint=False):
    if not faces:
        raise OcctBuildError(f"3DM object {object_id}: no usable faces")
    sewing = occ.BRepBuilderAPI.BRepBuilderAPI_Sewing(1e-7)
    for face in faces:
        sewing.Add(face)
    sewing.Perform()
    shape = sewing.SewedShape()
    if sewing.NbMultipleEdges():
        raise OcctBuildError(f"3DM object {object_id}: non-manifold faces")
    if closed:
        shells = list(_explore(occ, shape, occ.TopAbs.TopAbs_SHELL))
        if not shells or sewing.NbFreeEdges() or (len(shells) != 1 and not allow_disjoint):
            raise OcctCapabilityError(object_id, "3dm-solid", "closed source requires one closed manifold shell")
        solids = []
        for shell in shells:
            solid = occ.BRepBuilderAPI.BRepBuilderAPI_MakeSolid(occ.TopoDS.TopoDS.Shell_s(shell)).Solid()
            if not occ.BRepLib.BRepLib.OrientClosedSolid_s(solid):
                raise OcctBuildError(f"3DM object {object_id}: solid orientation failed")
            if not occ.BRepCheck.BRepCheck_Analyzer(solid).IsValid():
                raise OcctBuildError(f"3DM object {object_id}: invalid closed mesh shell")
            solids.append(solid)
        shape = solids[0]
        if len(solids) > 1:
            # A source Mesh may aggregate separate parts. A shell inside another
            # may instead describe a cavity, so never independently fill it.
            # Sweep bounds first; the kernel only intersects candidate pairs.
            bounded = []
            for solid in solids:
                box = occ.Bnd.Bnd_Box()
                occ.BRepBndLib.BRepBndLib.AddOptimal_s(solid, box, False, False)
                bounded.append((box.Get(), solid))
            bounded.sort(key=lambda item: item[0][0])
            for index, (bounds, left) in enumerate(bounded):
                for other_index in range(index + 1, len(bounded)):
                    other_bounds, right = bounded[other_index]
                    if other_bounds[0] >= bounds[3]:
                        break
                    if any(bounds[axis + 3] <= other_bounds[axis] or other_bounds[axis + 3] <= bounds[axis]
                           for axis in (1, 2)):
                        continue
                    common = occ.BRepAlgoAPI.BRepAlgoAPI_Common(left, right)
                    common.Build()
                    if not common.IsDone() or not occ.BRepCheck.BRepCheck_Analyzer(common.Shape()).IsValid():
                        raise OcctBuildError(f"3DM object {object_id}: mesh shell intersection failed")
                    properties = occ.GProp.GProp_GProps()
                    occ.BRepGProp.BRepGProp.VolumeProperties_s(common.Shape(), properties)
                    volume = abs(float(properties.Mass()))
                    if not math.isfinite(volume) or volume > 0:
                        raise OcctCapabilityError(object_id, "3dm-mesh", "overlapping or contained closed shells are unsupported")
            builder = occ.BRep.BRep_Builder()
            shape = occ.TopoDS.TopoDS_Compound()
            builder.MakeCompound(shape)
            for solid in solids:
                builder.Add(shape, solid)
    unify = occ.ShapeUpgrade.ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    unify.Build()
    return unify.Shape()


def _native_mesh_shape(occ, mesh, object_id, *, closed=None):
    if mesh is None or not mesh.IsValid or not len(mesh.Faces):
        raise OcctBuildError(f"3DM object {object_id}: no valid saved mesh")
    if mesh.Faces.QuadCount:
        mesh = mesh.Duplicate()
        mesh.Faces.ConvertQuadsToTriangles()
    faces = []
    for a, b, c, d in mesh.Faces:
        if c != d:
            raise OcctBuildError(f"3DM object {object_id}: mesh triangulation failed")
        polygon = occ.BRepBuilderAPI.BRepBuilderAPI_MakePolygon()
        for index in (a, b, c):
            point = mesh.Vertices[index]
            polygon.Add(occ.gp.gp_Pnt(point.X, point.Y, point.Z))
        polygon.Close()
        if not polygon.IsDone():
            raise OcctBuildError(f"3DM object {object_id}: degenerate mesh face")
        face = occ.BRepBuilderAPI.BRepBuilderAPI_MakeFace(polygon.Wire(), True)
        if not face.IsDone():
            raise OcctBuildError(f"3DM object {object_id}: mesh face transfer failed")
        faces.append(face.Face())
    return _native_sew(occ, faces, object_id, closed=mesh.IsClosed if closed is None else closed,
                       allow_disjoint=closed is None)


def _native_planar_brep(occ, rhino, brep, object_id):
    if not all(face.IsPlanar(1e-8) for face in brep.Faces):
        raise OcctCapabilityError(object_id, "3dm-brep", "non-planar BRep requires complete saved render meshes")
    if not all(hasattr(face, "Loops") for face in brep.Faces):
        raise OcctCapabilityError(object_id, "3dm-brep", "trimmed BRep reading requires rhino3dm 8.32 or later")
    faces = []
    for face in brep.Faces:
        loops = list(face.Loops)
        outer = [loop for loop in loops if loop.LoopType == rhino.BrepLoopType.Outer]
        inner = [loop for loop in loops if loop.LoopType == rhino.BrepLoopType.Inner]
        if len(outer) != 1 or len(outer) + len(inner) != len(loops):
            raise OcctCapabilityError(object_id, "3dm-brep", "face needs one outer loop and supported inner loops")
        wires = []
        for loop in [*outer, *inner]:
            wire = occ.BRepBuilderAPI.BRepBuilderAPI_MakeWire()
            for trim in loop.Trims:
                if trim.EdgeIndex < 0:
                    raise OcctCapabilityError(object_id, "3dm-brep", "singular planar trim is unsupported")
                segment = _native_wire(occ, brep.Edges[trim.EdgeIndex], object_id)
                if trim.IsReversed:
                    segment.Reverse()
                wire.Add(segment)
            if not wire.IsDone() or not occ.BRep.BRep_Tool.IsClosed_s(wire.Wire()):
                raise OcctBuildError(f"3DM object {object_id}: incomplete trim loop")
            wires.append(wire.Wire())
        u, v = face.Domain(0), face.Domain(1)
        success, plane = face.FrameAt((u.T0 + u.T1) / 2, (v.T0 + v.T1) / 2)
        if not success:
            raise OcctBuildError(f"3DM object {object_id}: planar face frame failed")
        axis = occ.gp.gp_Ax3(occ.gp.gp_Pnt(plane.Origin.X, plane.Origin.Y, plane.Origin.Z),
                            occ.gp.gp_Dir(plane.ZAxis.X, plane.ZAxis.Y, plane.ZAxis.Z),
                            occ.gp.gp_Dir(plane.XAxis.X, plane.XAxis.Y, plane.XAxis.Z))
        maker = occ.BRepBuilderAPI.BRepBuilderAPI_MakeFace(occ.gp.gp_Pln(axis), wires[0], True)
        for wire in wires[1:]:
            maker.Add(wire)
        if not maker.IsDone():
            raise OcctBuildError(f"3DM object {object_id}: trimmed planar face transfer failed")
        result = maker.Face()
        if face.OrientationIsReversed:
            result.Reverse()
        faces.append(result)
    return _native_sew(occ, faces, object_id, closed=brep.IsSolid)


def _native_geometry(occ, rhino, geometry, object_id):
    if not geometry.IsValid:
        raise OcctBuildError(f"3DM object {object_id}: invalid source geometry")
    if isinstance(geometry, rhino.Mesh):
        return _native_mesh_shape(occ, geometry, object_id), "faceted"
    if isinstance(geometry, rhino.Curve):
        return _native_wire(occ, geometry, object_id), "exact"
    if isinstance(geometry, rhino.Extrusion):
        if not geometry.IsMiteredAtStart and not geometry.IsMiteredAtEnd:
            wires = [_native_wire(occ, geometry.Profile3d(index, 0.0), object_id)
                     for index in range(geometry.ProfileCount)]
            if geometry.IsCappedAtBottom != geometry.IsCappedAtTop:
                raise OcctCapabilityError(object_id, "3dm-extrusion", "single-cap extrusion is unsupported")
            if geometry.IsSolid:
                face = occ.BRepBuilderAPI.BRepBuilderAPI_MakeFace(wires[0], True)
                for wire in wires[1:]:
                    face.Add(wire)
                profile = face.Face()
            elif len(wires) == 1:
                profile = wires[0]
            else:
                raise OcctCapabilityError(object_id, "3dm-extrusion", "open multi-profile extrusion is unsupported")
            start, end = geometry.PathStart, geometry.PathEnd
            prism = occ.BRepPrimAPI.BRepPrimAPI_MakePrism(profile, occ.gp.gp_Vec(end.X - start.X, end.Y - start.Y, end.Z - start.Z))
            if not prism.IsDone():
                raise OcctBuildError(f"3DM object {object_id}: extrusion transfer failed")
            return prism.Shape(), "exact"
        mesh = geometry.GetMesh(rhino.MeshType.Render)
        if mesh is not None:
            return _native_mesh_shape(occ, mesh, object_id, closed=geometry.IsSolid), "mesh_approximation"
        geometry = geometry.ToBrep(True)
    elif isinstance(geometry, rhino.Surface):
        # A standalone surface's finite domain is its actual boundary. Never
        # unwrap a BRep face this way: its trim loops remain authoritative.
        geometry = rhino.Brep.CreateFromSurface(geometry)
    if isinstance(geometry, rhino.Brep):
        try:
            return _native_planar_brep(occ, rhino, geometry, object_id), "exact"
        except OcctCapabilityError:
            meshes = [face.GetMesh(rhino.MeshType.Render) for face in geometry.Faces]
            if not meshes or any(mesh is None or not mesh.IsValid or not len(mesh.Faces) for mesh in meshes):
                raise
            joined = rhino.Mesh()
            for mesh in meshes:
                joined.Append(mesh)
            return _native_mesh_shape(occ, joined, object_id, closed=geometry.IsSolid), "mesh_approximation"
    raise OcctCapabilityError(object_id, type(geometry).__name__, "unsupported visible 3DM geometry; no objects were omitted")


def read_three_dm(data: bytes) -> tuple[tuple[StepEntry, ...], str]:
    """Read visible native geometry in its unchanged CAD Z-up coordinates and units.

    No host, writes, execution receipt, bounding-box geometry or untrimmed
    surface substitute. Mesh sources are faceted; saved BRep render meshes
    are explicitly approximate. Any unsupported visible object fails the
    entire read. Definition templates appear only through their instances.
    """
    if not isinstance(data, bytes) or not data:
        raise OcctBuildError("3DM source must be nonempty bytes")
    try:
        rhino = importlib.import_module("rhino3dm")
    except ImportError as exc:
        raise OcctUnavailableError("native 3DM drawings require rhino3dm") from exc
    try:
        model = rhino.File3dm.FromByteArray(data)
    except Exception as exc:
        raise OcctBuildError(f"3DM decoding failed: {exc}") from exc
    if model is None:
        raise OcctBuildError("3DM decoding failed")
    units = {rhino.UnitSystem.Meters: "meter", rhino.UnitSystem.Millimeters: "millimeter",
             rhino.UnitSystem.Inches: "inch", rhino.UnitSystem.Feet: "foot"}.get(model.Settings.ModelUnitSystem)
    if units is None:
        raise OcctCapabilityError("document", "3dm-units", f"unsupported length unit {model.Settings.ModelUnitSystem}")
    occ = _occt()
    objects = {str(item.Attributes.Id): item for item in model.Objects}
    if len(objects) != len(model.Objects):
        raise OcctBuildError("3DM contains duplicate object identities")
    definitions = {str(item.Id): item for item in model.InstanceDefinitions}
    layers = {str(layer.Id): layer for layer in model.Layers}
    templates = {str(object_id) for definition in definitions.values() for object_id in definition.GetObjectIds()}
    entries, cache = [], {}

    def object_layer(item):
        index = item.Attributes.LayerIndex
        return model.Layers.FindIndex(index) if 0 <= index < len(model.Layers) else None

    def visible(item):
        if not item.Attributes.Visible:
            return False
        layer = object_layer(item)
        seen = set()
        while layer is not None:
            if not layer.Visible:
                return False
            key = str(layer.Id)
            if key in seen:
                raise OcctBuildError("3DM layer ancestry contains a cycle")
            seen.add(key)
            layer = layers.get(str(layer.ParentLayerId))
        return True

    def visit(item, path, transform, ancestry):
        if len(path) > 64:
            raise OcctBuildError("3DM block nesting exceeds 64 levels")
        if not visible(item):
            return
        key = str(item.Attributes.Id)
        name = "/".join((*path, key))
        geometry = item.Geometry
        if isinstance(geometry, rhino.InstanceReference):
            definition_id = str(geometry.ParentIdefId)
            if definition_id in ancestry:
                raise OcctBuildError(f"3DM block cycle at {name}")
            definition = definitions.get(definition_id)
            if definition is None:
                raise OcctBuildError(f"3DM block {name}: missing definition")
            children = list(definition.GetObjectIds())
            if not children:
                raise OcctBuildError(f"3DM block {name}: empty definition")
            combined = rhino.Transform.Multiply(transform, geometry.Xform)
            for child_id in children:
                child = objects.get(str(child_id))
                if child is None:
                    raise OcctBuildError(f"3DM block {name}: missing definition object {child_id}")
                visit(child, (*path, key), combined, (*ancestry, definition_id))
            return
        try:
            if key not in cache:
                cache[key] = _native_geometry(occ, rhino, geometry, name)
            shape, quality = cache[key]
            if path:
                values = [[getattr(transform, f"M{row}{col}") for col in range(4)] for row in range(4)]
                if any(not math.isfinite(value) for row in values for value in row) or values[3] != [0, 0, 0, 1]:
                    raise OcctBuildError(f"3DM block {name}: transform must be finite and affine")
                affine = occ.gp.gp_GTrsf()
                for row in range(3):
                    for col in range(4):
                        affine.SetValue(row + 1, col + 1, values[row][col])
                if abs(affine.VectorialPart().Determinant()) < 1e-15:
                    raise OcctBuildError(f"3DM block {name}: singular transform")
                shape = occ.BRepBuilderAPI.BRepBuilderAPI_GTransform(shape, affine, True).Shape()
            if shape.IsNull() or not occ.BRepCheck.BRepCheck_Analyzer(shape).IsValid():
                raise OcctBuildError(f"3DM object {name}: transferred geometry is invalid")
            layer = object_layer(item)
            color = item.Attributes.DrawColor(model) if layer else item.Attributes.ObjectColor
            entries.append(StepEntry(name, (layer.FullPath,) if layer else (), tuple(color[:3]), shape, quality))
        except OcctBackendError:
            raise
        except Exception as exc:
            raise OcctBuildError(f"3DM object {name}: native geometry transfer failed: {exc}") from exc

    for key, item in objects.items():
        if key not in templates and not item.Attributes.IsInstanceDefinitionObject:
            visit(item, (), rhino.Transform.Identity(), ())
    if not entries:
        raise OcctBuildError("3DM contains no supported visible model geometry")
    return tuple(entries), units


__all__ = [
    "read_three_dm",
]
