"""Building a compiled program's operations as exact OCCT shapes.

Capability.  Only the operation kinds the initial consumers actually use are
realized: ``solid`` (box), ``revolve`` (cylinder or conical frustum), ``extrusion``,
``planar_surface`` (one bounded planar face without thickness), retained polyline
``curve`` wires, polyline ``loft`` (capped into a
closed solid, or with ``cap_ends`` false the lofted surface itself, open at
both end sections, for a source that gives a drum or a dome as a surface
without thickness), the three booleans, and the linear ``array`` the
opening solver places repeated windows with (one input, ``count`` real
translated copies at ``i * step`` delivered as one compound).  Anything
else fails ``OcctCapabilityError`` naming the operation before anything is
written.  There is no fallback to Rhino, no bounding-box stand-in and no
mesh pretending to be a B-rep.  Booleans and arrays still consume solids
only; an open surface fed to one fails as a build error, never as a solid.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from archflow.state.geometry_program import (
    CompiledGeometryProgram,
    delivered_object_ids,
    lift_to_base_level,
    operation_parameters,
    revolve_parameters,
)
from monkeycad.backends.occt.boolean import (
    DIFFERENCE_STRATEGIES,
    _ProfileWithHoles,
    _difference_plan,
    _joint_intersection,
    _profile_with_holes,
)
from monkeycad.backends.occt.errors import OcctBackendError, OcctBuildError, OcctCapabilityError
from monkeycad.backends.occt.kernel import (
    _count,
    _explore,
    _free_edge_count,
    _gp_point,
    _observe_operation,
    _occt,
    _planar_face,
    _polygon,
    _shape_list,
    cad_point,
)


#: Operation kinds this backend realizes exactly.  Everything else is a
#: capability failure (see ``_UNSUPPORTED_REASONS``), never a silent drop.
SUPPORTED_OPERATION_KINDS: frozenset[str] = frozenset(
    {
        "solid",
        "curve",
        "revolve",
        "extrusion",
        "planar_surface",
        "loft",
        "boolean_union",
        "boolean_difference",
        "boolean_intersection",
        "array",
    }
)

#: What an operation declares it delivers: a closed solid, or (an uncapped
#: loft or bounded planar face) an open surface without thickness, or a curve.
CLOSED_SOLID = "closed_solid"
OPEN_SURFACE = "open_surface"
CURVE = "curve"

_UNSUPPORTED_REASONS: Mapping[str, str] = {
    "transform": "transform has no exact realization (the Rhino translation only copies it)",
    "radial_array": "radial block instancing is not realized by the OCCT executor yet",
    "sweep": "sweep is not realized by the OCCT executor yet",
    "asset_instance": "asset instances are not realized by the OCCT executor",
}

_LOFT_PRECISION = 1e-6


@dataclass(frozen=True)
class OcctObjectBuild:
    object_id: str
    producer_op: str
    shape: Any
    hidden: bool


@dataclass(frozen=True)
class OcctProgramBuild:
    """Every object the program builds, consumed intermediates included."""

    objects: Mapping[str, OcctObjectBuild]
    physical_object_ids: tuple[str, ...]
    elapsed_seconds: float
    executed_operation_ids: tuple[str, ...] = ()
    recomputed_object_ids: tuple[str, ...] = ()
    reused_object_ids: tuple[str, ...] = ()
    lowering: Mapping[str, str] = field(default_factory=dict)


def build_program_shapes(
    program: CompiledGeometryProgram, *, reusable_shapes: Mapping[str, Any] | None = None,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    observation_parent_id: str | None = None,
    difference_strategy: str = "auto",
) -> OcctProgramBuild:
    """Interpret the program in operation order against the kernel.

    Fails ``OcctCapabilityError`` on the first operation outside the
    supported vocabulary and ``OcctBuildError`` when OCCT cannot produce a
    valid shape; nothing is written by this function. The caller supplies
    already verified, unchanged source shapes. Their producers and unused
    intermediate operations are not evaluated again.

    The optional observer receives one geometry span with actual consumed,
    recomputed, reused and emitted object ids. It does not re-evaluate the
    caller's input-equivalence decision, and observer failures are ignored.

    ``difference_strategy`` chooses how a boolean_difference is realized
    (DIFFERENCE_STRATEGIES); a profile with holes is recorded in ``lowering``.
    """

    if not isinstance(program, CompiledGeometryProgram):
        raise TypeError("program must be CompiledGeometryProgram")
    occ = _occt()
    if difference_strategy not in DIFFERENCE_STRATEGIES:
        raise OcctBackendError(f"unknown difference strategy {difference_strategy!r}")
    lowering: dict[str, str] = {}
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    proposal = program.proposal
    operations = {operation.op_id: operation for operation in proposal.operations}
    shapes: dict[str, Any] = dict(reusable_shapes or {})
    producers = {output: operation.op_id for operation in operations.values() for output in operation.output_object_ids}
    built: dict[str, OcctObjectBuild] = {}
    executed: list[str] = []
    recomputed_ids: list[str] = []
    reused_ids: list[str] = []
    input_ids: set[str] = set()
    physical = delivered_object_ids(proposal)
    status = "failed"
    elapsed = None
    try:
        if set(shapes) - set(producers):
            raise OcctBackendError("reusable shapes name objects outside the program")
        needed: set[str] = set()
        frontier = list(physical)
        while frontier:
            object_id = frontier.pop()
            if object_id in shapes:
                continue
            op_id = producers.get(object_id)
            if op_id is None or op_id in needed:
                continue
            needed.add(op_id)
            frontier.extend(operations[op_id].input_object_ids)
        if not reusable_shapes:
            needed = set(operations)
        for op_id in program.operation_order:
            operation = operations[op_id]
            kind = operation.kind.value
            reused = len(operation.output_object_ids) == 1 and operation.output_object_ids[0] in shapes and op_id not in needed
            if op_id not in needed and not reused:
                continue
            if kind not in SUPPORTED_OPERATION_KINDS:
                raise OcctCapabilityError(
                    op_id,
                    kind,
                    _UNSUPPORTED_REASONS.get(kind, "operation kind is not realized"),
                )
            if len(operation.output_object_ids) != 1:
                raise OcctCapabilityError(
                    op_id, kind, "exactly one output object per operation is realized"
                )
            params = operation_parameters(operation)
            output = operation.output_object_ids[0]
            try:
                if reused:
                    input_ids.add(output)
                    shape = shapes[output]
                    if kind == "curve":
                        # STEP flattens a wire to an edge compound; retain its
                        # edges under one wire so re-export keeps one named layer.
                        shape, _ = _polyline_wire(shape)
                else:
                    executed.append(op_id)
                    input_ids.update(operation.input_object_ids)
                    plan = None
                    if kind == "boolean_difference" and difference_strategy != "boolean":
                        plan, reason = _difference_plan(operation, operations, producers)
                        if plan is None and difference_strategy == "profile_with_holes":
                            raise OcctCapabilityError(op_id, kind, f"profile_with_holes does not apply: {reason}")
                    shape = _build_operation(occ, kind, operation, params, shapes, plan=plan)
                    if plan is not None:
                        lowering[op_id] = "profile_with_holes"
            except OcctBackendError:
                raise
            except Exception as exc:  # OCCT failures surface as Standard_Failure
                raise OcctBuildError(f"{op_id} ({kind}): {exc}") from exc
            _require_built_shape(occ, shape, op_id, kind, delivery=declared_delivery(operation))
            (reused_ids if reused else recomputed_ids).append(output)
            shapes[output] = shape
            built[output] = OcctObjectBuild(
                object_id=output,
                producer_op=op_id,
                shape=shape,
                hidden=bool(params.get("hidden_for_inspection", False)),
            )
        elapsed = time.perf_counter() - started
        result = OcctProgramBuild(
            objects=built,
            physical_object_ids=physical,
            elapsed_seconds=elapsed,
            executed_operation_ids=tuple(executed),
            recomputed_object_ids=tuple(sorted(recomputed_ids)),
            reused_object_ids=tuple(sorted(reused_ids)),
            lowering=dict(sorted(lowering.items())),
        )
        status = "succeeded"
        return result
    finally:
        _observe_operation(
            operation_observer,
            phase="geometry_build",
            status=status,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            duration_ms=1000.0 * (elapsed if elapsed is not None else time.perf_counter() - started),
            parent_event_id=observation_parent_id,
            details={
                "input_identity": {"program_digest": program.program_digest},
                "input_object_ids": sorted(input_ids),
                "recomputed_object_ids": sorted(recomputed_ids),
                "reused_object_ids": sorted(reused_ids),
                "emitted_object_ids": list(physical) if status == "succeeded" else [],
                "execution_path": "occt",
                "scope": "program_geometry",
                "executed_stages": ["build_program_shapes"] if executed else [],
                "cache_status": "hit" if reused_ids and not executed and status == "succeeded"
                    else "partial" if reused_ids else "miss" if executed else "unknown",
            },
        )


def declared_delivery(operation) -> str:
    """The operation's declared solid, surface or curve delivery.

    Read off the operation's own parameters, never off a built shape: a
    ``planar_surface`` and a ``loft`` whose ``cap_ends`` is false are open
    surfaces; a retained curve is a wire; every other realized kind is a closed solid. Readback uses
    the same word to decide which checks a saved object must pass.
    """

    if operation.kind.value == "curve":
        return CURVE
    if operation.kind.value == "planar_surface" or (
        operation.kind.value == "loft" and operation_parameters(operation).get("cap_ends", True) is False
    ):
        return OPEN_SURFACE
    return CLOSED_SOLID


def _build_operation(
    occ: SimpleNamespace,
    kind: str,
    operation,
    params: Mapping[str, object],
    shapes: Mapping[str, Any],
    *,
    plan: _ProfileWithHoles | None = None,
):
    op_id = operation.op_id
    if kind == "curve":
        if params.get("basis", "polyline") != "polyline" or not params.get("retain_for_inspection", False):
            raise OcctCapabilityError(op_id, kind, "only retained polyline curves are realized")
        points = lift_to_base_level(params["points"], params, op_id)
        if len(points) < 2 or any(math.dist(a, b) <= 1e-9 for a, b in zip(points, points[1:])):
            raise OcctBuildError(f"{op_id}: a curve needs distinct consecutive points")
        maker = occ.BRepBuilderAPI.BRepBuilderAPI_MakePolygon()
        for point in points:
            maker.Add(_gp_point(occ, point))
        if not maker.IsDone():
            raise OcctBuildError(f"{op_id}: curve points do not form a wire")
        wire = maker.Wire()
        if _count(occ, wire, occ.TopAbs.TopAbs_EDGE) != len(points) - 1:
            raise OcctBuildError(f"{op_id}: curve contains an edge below the kernel tolerance")
        return wire
    if kind == "solid":
        origin, size = params["origin"], params["size"]
        if any(float(value) <= 0.0 for value in size):
            raise OcctCapabilityError(op_id, kind, "solid size must be positive on every axis")
        far = [float(origin[axis]) + float(size[axis]) for axis in range(3)]
        return occ.BRepPrimAPI.BRepPrimAPI_MakeBox(
            _gp_point(occ, origin), _gp_point(occ, far)
        ).Shape()
    if kind == "planar_surface":
        profile = lift_to_base_level(params["profile"], params, op_id)
        # GeometryOperation already requires an explicitly closed, planar,
        # simple boundary. The polygon builder owns closing the final edge.
        return _planar_face(occ, profile[:-1], op_id)
    if kind == "extrusion":
        profile = lift_to_base_level(params["profile"], params, op_id)
        vector = [float(value) for value in params["vector"]]
        if len(profile) < 3:
            raise OcctCapabilityError(op_id, kind, "an extrusion profile needs at least three points")
        if not any(vector):
            raise OcctCapabilityError(op_id, kind, "an extrusion needs a non-zero vector")
        face = _planar_face(occ, profile, op_id)
        vx, vy, vz = cad_point(vector)
        return occ.BRepPrimAPI.BRepPrimAPI_MakePrism(face, occ.gp.gp_Vec(vx, vy, vz)).Shape()
    if kind == "revolve":
        a0, a1, r0, r1 = revolve_parameters(params, op_id)
        axis = [a1[i] - a0[i] for i in range(3)]
        length = math.sqrt(sum(value * value for value in axis))
        placement = occ.gp.gp_Ax2(_gp_point(occ, a0), occ.gp.gp_Dir(*cad_point(axis)))
        if r0 == r1:
            return occ.BRepPrimAPI.BRepPrimAPI_MakeCylinder(placement, r0, length).Shape()
        return occ.BRepPrimAPI.BRepPrimAPI_MakeCone(placement, r0, r1, length).Shape()
    if kind == "loft":
        profiles = lift_to_base_level(params["profiles"], params, op_id)
        size = int(params["profile_size"])
        loft_type = params.get("loft_type", "normal")
        profile_basis = params.get("profile_basis", "polyline")
        cap_ends = params.get("cap_ends", True)
        closed_profile = params.get("closed_profile", True)
        if profile_basis != "polyline":
            raise OcctCapabilityError(op_id, kind, f"profile_basis {profile_basis!r} is not realized; polyline only")
        if loft_type not in ("normal", "straight"):
            raise OcctCapabilityError(op_id, kind, f"loft_type {loft_type!r} is not realized")
        if not isinstance(cap_ends, bool):
            raise OcctCapabilityError(op_id, kind, f"cap_ends must be true or false, not {cap_ends!r}")
        if closed_profile is not True:
            raise OcctCapabilityError(op_id, kind, "open section profiles are not realized; every section is a closed polygon")
        if size < 3 or len(profiles) % size != 0 or len(profiles) // size < 2:
            raise OcctCapabilityError(op_id, kind, "loft needs at least two closed sections of at least three points")
        # cap_ends is ThruSections' isSolid, as stated: capped sections close
        # into one solid; uncapped ones deliver the lofted surface open at
        # both end sections, with no thickness invented.
        loft = occ.BRepOffsetAPI.BRepOffsetAPI_ThruSections(
            cap_ends, loft_type == "straight", _LOFT_PRECISION
        )
        for start in range(0, len(profiles), size):
            loft.AddWire(_polygon(occ, profiles[start : start + size], op_id))
        loft.Build()
        if not loft.IsDone():
            raise OcctBuildError(f"{op_id} (loft): OCCT could not loft the sections")
        return loft.Shape()
    if kind in ("boolean_union", "boolean_difference", "boolean_intersection"):
        inputs = list(operation.input_object_ids)
        missing = [item for item in inputs if item not in shapes]
        if missing:
            raise OcctBuildError(f"{op_id} ({kind}): inputs not built: {', '.join(missing)}")
        if len(inputs) < 2:
            raise OcctCapabilityError(op_id, kind, "a boolean needs at least two inputs")
        if plan is not None:
            return _profile_with_holes(occ, plan, op_id)
        if kind == "boolean_intersection":
            return _joint_intersection(occ, op_id, inputs, shapes)
        if kind == "boolean_difference":
            base = sorted(inputs)[int(params.get("base_index", 0))]
            arguments = [base]
            tools = [item for item in inputs if item != base]
            algorithm = occ.BRepAlgoAPI.BRepAlgoAPI_Cut()
        else:
            arguments, tools = inputs[:1], inputs[1:]
            algorithm = occ.BRepAlgoAPI.BRepAlgoAPI_Fuse()
        # Fuse(A; B, C) is A ∪ B ∪ C and Cut(A; B, C) is A \ (B ∪ C): with one
        # argument and every other input as a tool, both realize the IR's
        # n-ary meaning.  Common does not (see _joint_intersection).
        algorithm.SetArguments(_shape_list(occ, (shapes[item] for item in arguments)))
        algorithm.SetTools(_shape_list(occ, (shapes[item] for item in tools)))
        algorithm.SetRunParallel(False)
        algorithm.Build()
        # The binding exposes IsDone only; the validity of the result is
        # checked by _require_built_shape right after this returns.
        if not algorithm.IsDone():
            raise OcctBuildError(f"{op_id} ({kind}): OCCT boolean did not complete")
        result = algorithm.Shape()
        if kind == "boolean_union":
            unify = occ.ShapeUpgrade.ShapeUpgrade_UnifySameDomain(result, True, True, True)
            unify.Build()
            result = unify.Shape()
        return result
    if kind == "array":
        return _linear_array(occ, op_id, operation, params, shapes)
    raise OcctCapabilityError(op_id, kind, "operation kind is not realized")


def _linear_array(occ: SimpleNamespace, op_id: str, operation, params: Mapping[str, object], shapes: Mapping[str, Any]):
    """``count`` real copies of the one input, the i-th translated by ``i * step``, as one compound.

    ``step`` is stated in the program frame and converted to the CAD frame
    exactly once, by ``cad_point``, like every other coordinate. The copies
    are independent shapes (``BRepBuilderAPI_Transform`` with copy), so the
    compound holds ``count`` distinct solids per input solid - what the
    analytic predictor counts and the cold read must find.
    """

    inputs = list(operation.input_object_ids)
    if len(inputs) != 1:
        raise OcctCapabilityError(op_id, "array", "a linear array repeats exactly one input object")
    (source,) = inputs
    if source not in shapes:
        raise OcctBuildError(f"{op_id} (array): input not built: {source}")
    count = params.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise OcctCapabilityError(op_id, "array", "count must be a positive integer")
    step = params.get("step")
    if not isinstance(step, (list, tuple)) or len(step) != 3:
        raise OcctCapabilityError(op_id, "array", "step must be a three-component vector")
    step = [float(value) for value in step]
    if any(not math.isfinite(value) for value in step):
        raise OcctCapabilityError(op_id, "array", "step must be finite")
    if count > 1 and not any(step):
        raise OcctCapabilityError(op_id, "array", "coincident copies (a zero step) are not a delivery")
    sx, sy, sz = cad_point(step)
    builder = occ.BRep.BRep_Builder()
    compound = occ.TopoDS.TopoDS_Compound()
    builder.MakeCompound(compound)
    # A boolean result is itself a compound around its solid; the copies are
    # added solid by solid so the delivered compound is flat.  The STEP
    # writer files layers on a compound's direct solids only - a nested
    # compound would lose its layer on the cold read.
    source_solids = tuple(_explore(occ, shapes[source], occ.TopAbs.TopAbs_SOLID))
    if not source_solids:
        raise OcctBuildError(f"{op_id} (array): input {source} holds no solid to repeat")
    for index in range(count):
        transform = occ.gp.gp_Trsf()
        transform.SetTranslation(occ.gp.gp_Vec(sx * index, sy * index, sz * index))
        for solid in source_solids:
            placed = occ.BRepBuilderAPI.BRepBuilderAPI_Transform(solid, transform, True)
            if not placed.IsDone():
                raise OcctBuildError(f"{op_id} (array): OCCT could not place copy {index}")
            builder.Add(compound, placed.Shape())
    return compound


def _require_built_shape(occ: SimpleNamespace, shape, op_id: str, kind: str, *, delivery: str = CLOSED_SOLID) -> None:
    """The shape matches its declared solid, open surface or polyline delivery."""

    if shape is None or shape.IsNull():
        raise OcctBuildError(f"{op_id} ({kind}): OCCT produced no shape")
    solids = _count(occ, shape, occ.TopAbs.TopAbs_SOLID)
    if delivery == CURVE:
        if solids or _count(occ, shape, occ.TopAbs.TopAbs_FACE):
            raise OcctBuildError(f"{op_id} ({kind}): a curve must not contain surfaces or solids")
        _polyline_geometry(shape)
    elif delivery == OPEN_SURFACE:
        if _count(occ, shape, occ.TopAbs.TopAbs_FACE) == 0:
            raise OcctBuildError(f"{op_id} ({kind}): OCCT produced no surface")
        if solids or _free_edge_count(occ, shape) == 0:
            raise OcctBuildError(f"{op_id} ({kind}): the declared surface closed instead of retaining an open boundary")
    elif solids == 0:
        raise OcctBuildError(f"{op_id} ({kind}): OCCT produced no solid")
    if not occ.BRepCheck.BRepCheck_Analyzer(shape).IsValid():
        raise OcctBuildError(f"{op_id} ({kind}): OCCT produced an invalid shape")


def _polyline_wire(shape, tolerance: float = 1e-7):
    """Recover the existing wire topology without replacing its edges.

    STEP writes a wire as separate edges without shared vertices. Reconnect
    only at its numerical tolerance; callers also compare every original point.
    """

    occ = _occt()
    edges = occ.TopTools.TopTools_HSequenceOfShape()
    for raw in _explore(occ, shape, occ.TopAbs.TopAbs_EDGE):
        edge = occ.TopoDS.TopoDS.Edge_s(raw)
        if occ.BRepAdaptor.BRepAdaptor_Curve(edge).GetType() != occ.GeomAbs.GeomAbs_Line:
            raise OcctBuildError("polyline contains a non-linear edge")
        edges.Append(edge)
    if not edges.Length():
        raise OcctBuildError("polyline contains no edges")
    if shape.ShapeType() == occ.TopAbs.TopAbs_WIRE:
        return occ.TopoDS.TopoDS.Wire_s(shape), edges.Length()
    wires = occ.TopTools.TopTools_HSequenceOfShape()
    occ.ShapeAnalysis.ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges, tolerance, False, wires)
    if wires.Length() != 1:
        raise OcctBuildError("polyline edges do not form one connected path")
    return occ.TopoDS.TopoDS.Wire_s(wires.Value(1)), edges.Length()


def _polyline_geometry(shape, tolerance: float = 1e-7) -> dict[str, object]:
    """Measure one complete polyline from its real edges in CAD coordinates."""

    occ = _occt()
    wire, edge_count = _polyline_wire(shape, tolerance)
    explorer = occ.BRepTools.BRepTools_WireExplorer(wire)
    points = []
    last = None
    while explorer.More():
        point = occ.BRep.BRep_Tool.Pnt_s(explorer.CurrentVertex())
        points.append([float(point.X()), float(point.Y()), float(point.Z())])
        last = occ.TopExp.TopExp.LastVertex_s(explorer.Current(), True)
        explorer.Next()
    if len(points) != edge_count or last is None:
        raise OcctBuildError("polyline edge traversal is incomplete")
    point = occ.BRep.BRep_Tool.Pnt_s(last)
    points.append([float(point.X()), float(point.Y()), float(point.Z())])
    properties = occ.GProp.GProp_GProps()
    occ.BRepGProp.BRepGProp.LinearProperties_s(shape, properties)
    length = float(properties.Mass())
    if not math.isfinite(length) or length <= 0:
        raise OcctBuildError("polyline length must be finite and positive")
    return {"curve_points": points, "curve_start": points[0], "curve_end": points[-1], "curve_length": length}


__all__ = [
    "CLOSED_SOLID",
    "CURVE",
    "OPEN_SURFACE",
    "OcctObjectBuild",
    "OcctProgramBuild",
    "SUPPORTED_OPERATION_KINDS",
    "build_program_shapes",
    "declared_delivery",
]
