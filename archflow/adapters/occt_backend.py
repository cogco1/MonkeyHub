"""In-process OCCT backend behind ``adapters.cad_execution`` (P107, lane A).

The Rhino path translates a compiled program into a script and lets an
external host realize it.  This backend interprets the same
``CompiledGeometryProgram`` directly against Open CASCADE (OCCT) through the
``cadquery-ocp`` binding: every accepted operation kind maps to one kernel
call, the result is measured in process, written to STEP as exact B-rep, and
tessellated for a viewer-readable mesh ``.3dm`` from the same model.

What this module owns is the kernel-facing mechanics only: loading the
binding, the one-time coordinate mapping, building shapes, measuring them,
writing and cold-reading STEP, tessellating, writing the mesh preview, and
extracting named orthographic visible/hidden polylines and cut sections from
the same B-reps (``project_occt_lines``, ``section_occt_lines`` and
``section_occt_regions``).  The export identity, the readback verification
against the analytic predictor and the receipt live in
``adapters.cad_execution``; nothing here knows a project, a run or a
workspace rule.

Coordinate frame.  Program geometry is ``(x, y-up, z-plan)``.  The Rhino
translation writes every point as ``(x, z, y)`` so the saved document is
Z-up; the viewer converts that once.  This backend applies exactly the same
mapping at point construction (``cad_point``), so STEP, preview and the
Rhino ``.3dm`` share one frame and one set of expected bounds.

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

Preview materials.  The mesh preview may carry native ``rhino3dm``
materials for the objects the caller names (an assembly's frame and
glazing): one material per distinct (name, colour, transparency), the
object's ``MaterialSource`` set to the object, so a viewer that reads the
document's material table (the three.js ``Rhino3dmLoader``) renders glass
translucent.  STEP carries no material; both files are written from the
same shapes.

Units.  OCCT's STEP statics assume millimetre internals; they are
initialised only when the first STEP controller exists.  ``_step_units``
therefore initialises the controller and then sets both ``write.step.unit``
and ``xstep.cascade.unit`` to the program's unit, so values are written
unscaled and read back unscaled.  Both statics are process-global and are
consulted at ``Transfer`` time, not when the unit is set: a second export
or readback in another unit that runs between the two leaves the first one
written in the wrong unit (a metre file marked INCH reads back scaled by
0.0254).  ``write_step`` and ``read_step`` therefore hold one shared lock,
``_STEP_LOCK``, from setting the unit through the whole STEP transfer and
write (or read transfer).  Nothing else is serialised: shape building,
measuring and tessellation do not touch the statics.

Requires the optional dependency ``cadquery-ocp`` (``pip install -e
'.[cad-occt]'``).  It is imported lazily: importing this module, or the
execution owner that imports it, never loads OCCT.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from archflow.adapters.cad_program import _params, _physical_ids, _revolve_parameters, lift_to_base_level
from archflow.state.geometry_program import CompiledGeometryProgram


class OcctBackendError(ValueError):
    """The OCCT backend could not do what was asked."""


class OcctUnavailableError(OcctBackendError):
    """The optional ``cadquery-ocp`` binding is not installed."""


class OcctCapabilityError(OcctBackendError):
    """The program names an operation this backend does not realize.

    Raised before any file is written; the caller reports it as a typed
    capability failure rather than silently substituting another executor.
    """

    def __init__(self, op_id: str, kind: str, reason: str) -> None:
        super().__init__(f"{op_id} ({kind}): {reason}")
        self.op_id = op_id
        self.kind = kind
        self.reason = reason


class OcctBuildError(OcctBackendError):
    """OCCT could not build, write or read a shape it was asked for."""


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

_UNIT_TO_STEP: Mapping[str, str] = {
    "millimeter": "MM",
    "meter": "M",
    "inch": "INCH",
    "foot": "FT",
}
_UNIT_TO_RHINO3DM: Mapping[str, str] = {
    "millimeter": "Millimeters",
    "meter": "Meters",
    "inch": "Inches",
    "foot": "Feet",
}

_LOFT_PRECISION = 1e-6
_CLASSIFIER_TOLERANCE = 1e-7

_OCCT: SimpleNamespace | None = None

#: Serialises the STEP unit statics with the transfer that consumes them
#: (see the module docstring).  A redundant controller ``Init_s`` does not
#: reset the statics, so ``_occt`` itself needs no share of this lock.
_STEP_LOCK = threading.Lock()


def _observe_operation(
    observer: Callable[[Mapping[str, Any]], None] | None,
    *,
    phase: str,
    status: str,
    started_at: datetime,
    ended_at: datetime,
    duration_ms: float,
    parent_event_id: str | None,
    details: Mapping[str, Any],
) -> None:
    if observer is None:
        return
    try:
        observer({
            "phase": phase,
            "status": status,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "ended_at": ended_at.isoformat().replace("+00:00", "Z"),
            "duration_ms": round(duration_ms),
            **({"parent_event_id": parent_event_id} if parent_event_id is not None else {}),
            "details": dict(details),
        })
    except (Exception, asyncio.CancelledError):
        # Diagnostics cannot change a kernel result or cause a write to repeat.
        pass


def _occt() -> SimpleNamespace:
    """Load the binding once, silence its transfer statistics, expose what is used."""

    global _OCCT
    if _OCCT is not None:
        return _OCCT
    try:
        ocp = importlib.import_module("OCP")
        modules = {
            name: importlib.import_module(f"OCP.{name}")
            for name in (
                "gp",
                "BRep",
                "BRepAdaptor",
                "BRepAlgoAPI",
                "BRepBndLib",
                "BRepBuilderAPI",
                "BRepCheck",
                "BRepExtrema",
                "BRepClass3d",
                "BRepGProp",
                "BRepMesh",
                "BRepOffsetAPI",
                "BRepPrimAPI",
                "BRepTools",
                "Bnd",
                "GCPnts",
                "GeomAbs",
                "GProp",
                "HLRAlgo",
                "HLRBRep",
                "IFSelect",
                "Interface",
                "Message",
                "Quantity",
                "STEPCAFControl",
                "STEPControl",
                "ShapeAnalysis",
                "ShapeUpgrade",
                "TCollection",
                "TDF",
                "TDataStd",
                "TDocStd",
                "TopAbs",
                "TopExp",
                "TopLoc",
                "TopTools",
                "TopoDS",
                "XCAFDoc",
            )
        }
    except ImportError as exc:
        raise OcctUnavailableError(
            "optional dependency 'cadquery-ocp' is unavailable; install it with "
            "python -m pip install -e '.[cad-occt]'; no Rhino is required or started"
        ) from exc
    namespace = SimpleNamespace(OCP=ocp, **modules)
    # The STEP writer prints transfer statistics at Info level; a kernel
    # library has no business writing to the host's stdout.
    messenger = namespace.Message.Message.DefaultMessenger_s()
    printers = messenger.Printers()
    for index in range(1, printers.Size() + 1):
        printers.Value(index).SetTraceLevel(
            namespace.Message.Message_Gravity.Message_Fail
        )
    namespace.STEPCAFControl.STEPCAFControl_Controller.Init_s()
    _OCCT = namespace
    return namespace


def occt_available() -> bool:
    return importlib.util.find_spec("OCP") is not None


def backend_identity() -> dict[str, object]:
    """Which kernel binding realized the program, as the receipt names it."""

    occ = _occt()
    try:
        binding_version = importlib.metadata.version("cadquery-ocp")
    except importlib.metadata.PackageNotFoundError:
        binding_version = None
    return {
        "kernel": "OCCT",
        "binding": "cadquery-ocp",
        "binding_version": binding_version,
        "ocp_version": getattr(occ.OCP, "__version__", None),
    }


def cad_point(point: Sequence[float]) -> tuple[float, float, float]:
    """Program ``(x, y-up, z-plan)`` to the CAD frame ``(x, z, y)``, applied once."""

    x, y, z = (float(value) for value in point)
    return (x, z, y)


def _gp_point(occ: SimpleNamespace, point: Sequence[float]):
    return occ.gp.gp_Pnt(*cad_point(point))


# ---------------------------------------------------------------- building


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


def build_program_shapes(
    program: CompiledGeometryProgram, *, reusable_shapes: Mapping[str, Any] | None = None,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    observation_parent_id: str | None = None,
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
    """

    if not isinstance(program, CompiledGeometryProgram):
        raise TypeError("program must be CompiledGeometryProgram")
    occ = _occt()
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
    physical = tuple(sorted(_physical_ids(proposal)))
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
            params = _params(operation)
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
                    shape = _build_operation(occ, kind, operation, params, shapes)
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
        operation.kind.value == "loft" and _params(operation).get("cap_ends", True) is False
    ):
        return OPEN_SURFACE
    return CLOSED_SOLID


def _build_operation(
    occ: SimpleNamespace,
    kind: str,
    operation,
    params: Mapping[str, object],
    shapes: Mapping[str, Any],
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
        a0, a1, r0, r1 = _revolve_parameters(params, op_id)
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


def _polygon(occ: SimpleNamespace, points, op_id: str):
    maker = occ.BRepBuilderAPI.BRepBuilderAPI_MakePolygon()
    for point in points:
        maker.Add(_gp_point(occ, point))
    maker.Close()
    if not maker.IsDone():
        raise OcctBuildError(f"{op_id}: profile points do not form a closed polygon")
    return maker.Wire()


def _planar_face(occ: SimpleNamespace, points, op_id: str):
    wire = _polygon(occ, points, op_id)
    maker = occ.BRepBuilderAPI.BRepBuilderAPI_MakeFace(wire, True)
    if not maker.IsDone():
        raise OcctBuildError(f"{op_id}: profile is not a planar simple polygon")
    return maker.Face()


def _shape_list(occ: SimpleNamespace, shapes):
    items = occ.TopTools.TopTools_ListOfShape()
    for shape in shapes:
        items.Append(shape)
    return items


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


def _free_edge_count(occ: SimpleNamespace, shape) -> int:
    """Edges bounding exactly one face: the open boundary of a shell, zero for a closed solid."""

    ancestors = occ.TopTools.TopTools_IndexedDataMapOfShapeListOfShape()
    occ.TopExp.TopExp.MapShapesAndAncestors_s(shape, occ.TopAbs.TopAbs_EDGE, occ.TopAbs.TopAbs_FACE, ancestors)
    return sum(1 for index in range(1, ancestors.Extent() + 1) if ancestors.FindFromIndex(index).Extent() == 1)


def _count(occ: SimpleNamespace, shape, shape_type) -> int:
    explorer = occ.TopExp.TopExp_Explorer(shape, shape_type)
    total = 0
    while explorer.More():
        total += 1
        explorer.Next()
    return total


def _explore(occ: SimpleNamespace, shape, shape_type):
    explorer = occ.TopExp.TopExp_Explorer(shape, shape_type)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


# ---------------------------------------------------------------- measuring


@dataclass(frozen=True, slots=True)
class ShapeMeasure:
    """What the kernel reports about one shape, in the CAD frame.

    ``volume`` is ``None`` for a shape holding no solid: an open surface
    encloses nothing, and ``VolumeProperties`` on it would report the
    signed volume its faces happen to sweep against the origin - a number,
    not a measurement.  ``free_edge_count`` is the open boundary: edges
    bounding exactly one face, zero for every closed solid.
    """

    valid: bool
    solid_count: int
    closed: bool
    face_count: int
    volume: float | None
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    free_edge_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "solid_count": self.solid_count,
            "closed": self.closed,
            "face_count": self.face_count,
            "free_edge_count": self.free_edge_count,
            "volume": self.volume,
            "bbox": {"min": list(self.bbox_min), "max": list(self.bbox_max)},
        }


def measure_shape(shape) -> ShapeMeasure:
    """Validity, solid/shell closure, open boundary, volume (solids only) and tight bounds of one shape."""

    occ = _occt()
    if shape is None or shape.IsNull():
        raise OcctBuildError("cannot measure a null shape")
    valid = bool(occ.BRepCheck.BRepCheck_Analyzer(shape).IsValid())
    solids = tuple(_explore(occ, shape, occ.TopAbs.TopAbs_SOLID))
    shells_in_solids = 0
    closed = bool(solids)
    for solid in solids:
        for shell in _explore(occ, solid, occ.TopAbs.TopAbs_SHELL):
            shells_in_solids += 1
            if not occ.BRep.BRep_Tool.IsClosed_s(shell):
                closed = False
    if _count(occ, shape, occ.TopAbs.TopAbs_SHELL) != shells_in_solids:
        closed = False  # a free shell or face outside every solid
    volume = None
    if solids:
        properties = occ.GProp.GProp_GProps()
        occ.BRepGProp.BRepGProp.VolumeProperties_s(shape, properties)
        volume = float(properties.Mass())
    box = occ.Bnd.Bnd_Box()
    occ.BRepBndLib.BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid():
        raise OcctBuildError("shape has no bounds")
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return ShapeMeasure(
        valid=valid,
        solid_count=len(solids),
        closed=closed,
        face_count=_count(occ, shape, occ.TopAbs.TopAbs_FACE),
        volume=volume,
        bbox_min=(float(xmin), float(ymin), float(zmin)),
        bbox_max=(float(xmax), float(ymax), float(zmax)),
        free_edge_count=_free_edge_count(occ, shape),
    )


def classify_point(shape, cad_xyz: Sequence[float]) -> str:
    """``inside`` | ``outside`` | ``boundary`` for one point in the CAD frame."""

    occ = _occt()
    x, y, z = (float(value) for value in cad_xyz)
    classifier = occ.BRepClass3d.BRepClass3d_SolidClassifier(
        shape, occ.gp.gp_Pnt(x, y, z), _CLASSIFIER_TOLERANCE
    )
    state = classifier.State()
    if state == occ.TopAbs.TopAbs_IN:
        return "inside"
    if state == occ.TopAbs.TopAbs_OUT:
        return "outside"
    return "boundary"


def classify_program_point(shape, program_xyz: Sequence[float]) -> str:
    """``classify_point`` for a point stated in program coordinates."""

    return classify_point(shape, cad_point(program_xyz))


# ---------------------------------------------------------------- STEP


@dataclass(frozen=True)
class StepObject:
    """One named solid or surface to write: identity, layer and display colour."""

    object_id: str
    shape: Any
    layer: str
    color: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class StepEntry:
    """One free shape as a cold read of the STEP file found it."""

    name: str | None
    layers: tuple[str, ...]
    color: tuple[int, int, int] | None
    shape: Any




def _step_units(occ: SimpleNamespace, length_unit: str) -> None:
    unit = _UNIT_TO_STEP.get(length_unit)
    if unit is None:
        raise OcctBackendError(f"length unit {length_unit!r} has no STEP unit")
    statics = occ.Interface.Interface_Static
    if not statics.SetCVal_s("write.step.unit", unit) or not statics.SetCVal_s(
        "xstep.cascade.unit", unit
    ):
        raise OcctBuildError("OCCT refused the STEP unit statics")


def write_step(path: Path, objects: Sequence[StepObject], *, length_unit: str) -> None:
    """Write the objects as named, layered, coloured B-rep shapes in the program's unit.

    The name is the object id; the layer is the semantic layer path; the
    colour is the layer colour.  ``archflow:*`` user text has no STEP home
    and travels in the preview and the receipt instead.
    """

    occ = _occt()
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    if not objects:
        raise OcctBuildError("nothing to write")
    # The unit statics are read by Transfer; nothing may change them between
    # here and the end of the write.
    with _STEP_LOCK:
        _step_units(occ, length_unit)
        document = occ.TDocStd.TDocStd_Document(occ.TCollection.TCollection_ExtendedString("XmlXCAF"))
        shape_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        color_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ColorTool_s(document.Main())
        layer_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.LayerTool_s(document.Main())
        for item in objects:
            label = shape_tool.AddShape(item.shape, False, False)
            occ.TDataStd.TDataStd_Name.Set_s(
                label, occ.TCollection.TCollection_ExtendedString(item.object_id)
            )
            layer_tool.SetLayer(label, occ.TCollection.TCollection_ExtendedString(item.layer))
            if item.color is not None:
                red, green, blue = (channel / 255.0 for channel in item.color)
                color_tool.SetColor(
                    label,
                    occ.Quantity.Quantity_Color(red, green, blue, occ.Quantity.Quantity_TOC_RGB),
                    occ.XCAFDoc.XCAFDoc_ColorSurf,
                )
        writer = occ.STEPCAFControl.STEPCAFControl_Writer()
        writer.SetNameMode(True)
        writer.SetLayerMode(True)
        writer.SetColorMode(True)
        if not writer.Transfer(document, occ.STEPControl.STEPControl_AsIs):
            raise OcctBuildError("STEP transfer failed")
        if writer.Write(str(path)) != occ.IFSelect.IFSelect_RetDone:
            raise OcctBuildError("STEP write failed")


def read_step(path: Path, *, length_unit: str) -> tuple[StepEntry, ...]:
    """Cold-read a STEP file with a fresh reader: names, layers, colours, shapes.

    Independent of any in-memory shape: the reader sees only the bytes on
    disk, interpreted in the program's unit.
    """

    occ = _occt()
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    # The file's unit is scaled to xstep.cascade.unit by Transfer, not by
    # ReadFile; the static must hold the program's unit until then.
    with _STEP_LOCK:
        _step_units(occ, length_unit)
        reader = occ.STEPCAFControl.STEPCAFControl_Reader()
        reader.SetNameMode(True)
        reader.SetLayerMode(True)
        reader.SetColorMode(True)
        if reader.ReadFile(str(path)) != occ.IFSelect.IFSelect_RetDone:
            raise OcctBuildError("STEP file could not be read")
        document = occ.TDocStd.TDocStd_Document(occ.TCollection.TCollection_ExtendedString("XmlXCAF"))
        if not reader.Transfer(document):
            raise OcctBuildError("STEP transfer into the document failed")
    shape_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    layer_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.LayerTool_s(document.Main())
    color_tool = occ.XCAFDoc.XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    labels = occ.TDF.TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    entries: list[StepEntry] = []
    for index in range(1, labels.Length() + 1):
        label = labels.Value(index)
        shape = shape_tool.GetShape_s(label)
        layers = _layers_of(occ, layer_tool, label)
        rgb = _color_of(occ, color_tool, shape)
        if shape.ShapeType() in (occ.TopAbs.TopAbs_COMPOUND, occ.TopAbs.TopAbs_SHELL):
            # A compound (an array's copies) is written as one named shape,
            # but the reader files its layer and colour on each solid, not
            # on the compound's label.  The entry reports them only when
            # every solid agrees; a mixed compound stays unlayered and is
            # refused by the readback verification.
            # A bounded planar face is cold-read as a shell with attributes
            # on its face. Read that actual metadata, retaining the same
            # all-members-agree requirement as the solid array path.
            members = tuple(_explore(occ, shape, occ.TopAbs.TopAbs_SOLID)) or tuple(_explore(occ, shape, occ.TopAbs.TopAbs_FACE))
            if not layers and members:
                per_member = {_layers_of(occ, layer_tool, member) for member in members}
                if len(per_member) == 1:
                    layers = per_member.pop()
            if rgb is None and members:
                per_member_color = {_color_of(occ, color_tool, member) for member in members}
                if len(per_member_color) == 1:
                    rgb = per_member_color.pop()
        entries.append(
            StepEntry(name=_label_name(occ, label), layers=layers, color=rgb, shape=shape)
        )
    return tuple(entries)


def _layers_of(occ: SimpleNamespace, layer_tool, target) -> tuple[str, ...]:
    """The layer names filed on a label or on a shape, in the reader's order."""

    layer_labels = occ.TDF.TDF_LabelSequence()
    layer_tool.GetLayers(target, layer_labels)
    return tuple(
        _label_name(occ, layer_labels.Value(position)) or ""
        for position in range(1, layer_labels.Length() + 1)
    )


def _color_of(occ: SimpleNamespace, color_tool, shape) -> tuple[int, int, int] | None:
    color = occ.Quantity.Quantity_Color()
    if not color_tool.GetColor(shape, occ.XCAFDoc.XCAFDoc_ColorSurf, color):
        return None
    return tuple(
        int(round(channel * 255.0))
        for channel in (color.Red(), color.Green(), color.Blue())
    )


def _label_name(occ: SimpleNamespace, label) -> str | None:
    attribute = occ.TDataStd.TDataStd_Name()
    if not label.FindAttribute(occ.TDataStd.TDataStd_Name.GetID_s(), attribute):
        return None
    return str(attribute.Get().ToExtString())


def measure_occt_solid_pairs(
    entries: Sequence[StepEntry], *, object_pairs: Sequence[tuple[str, str]], length_unit: str,
) -> dict[tuple[str, str], dict[str, object]]:
    """Measure only requested pairs of final named solids, normally cold-read from STEP.

    Distance and common solid volume come from OCCT, in metres and cubic
    metres. A face/edge contact has zero common volume; a positive common
    volume is penetration. Missing, ambiguous or non-solid deliveries stay
    unchecked. In particular, a boolean's consumed operands are not
    reconstructed or compared with its final result.
    """

    to_m = {"meter": 1.0, "millimeter": 0.001, "inch": 0.0254, "foot": 0.3048}.get(length_unit)
    if to_m is None:
        raise OcctBackendError(f"unsupported solid measurement length unit {length_unit!r}")
    occ = _occt()
    by_name: dict[str, list[StepEntry]] = {}
    for entry in entries:
        by_name.setdefault(entry.name, []).append(entry)
    results: dict[tuple[str, str], dict[str, object]] = {}
    for pair in object_pairs:
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                or any(not isinstance(name, str) or not name for name in pair)):
            raise OcctBackendError("solid object pairs must each name two final objects")
        key = tuple(pair)
        row: dict[str, object] = {"status": "unchecked", "distance_m": None, "common_volume_m3": None, "detail": ""}
        results[key] = row
        if pair[0] == pair[1]:
            row["detail"] = f"{pair[0]}: a solid cannot be checked against itself"
            continue
        shapes = []
        try:
            for name in pair:
                matches = by_name.get(name, ())
                if len(matches) != 1:
                    row["detail"] = f"{name}: {'not a final delivered object' if not matches else 'ambiguous final object name'}"
                    break
                shape = matches[0].shape
                measured = measure_shape(shape)
                if not measured.valid or not measured.closed or not measured.solid_count:
                    row["detail"] = f"{name}: not a valid closed solid delivery"
                    break
                shapes.append(shape)
            if len(shapes) != 2:
                continue
            distance = occ.BRepExtrema.BRepExtrema_DistShapeShape(*shapes)
            distance.Perform()
            if not distance.IsDone():
                raise OcctBuildError("minimum solid distance failed")
            common = occ.BRepAlgoAPI.BRepAlgoAPI_Common(*shapes)
            common.Build()
            if not common.IsDone():
                raise OcctBuildError("common solid calculation failed")
            volume = 0.0
            for solid in _explore(occ, common.Shape(), occ.TopAbs.TopAbs_SOLID):
                if not occ.BRepCheck.BRepCheck_Analyzer(solid).IsValid():
                    raise OcctBuildError("common solid is invalid")
                properties = occ.GProp.GProp_GProps()
                occ.BRepGProp.BRepGProp.VolumeProperties_s(solid, properties)
                volume += abs(float(properties.Mass()))
            distance_m = float(distance.Value()) * to_m
            common_volume_m3 = volume * to_m ** 3
            if not math.isfinite(distance_m) or distance_m < 0 or not math.isfinite(common_volume_m3):
                raise OcctBuildError("solid measurement was not finite and non-negative")
            classification = "penetrating" if common_volume_m3 > 0.0 else "contact" if distance_m == 0.0 else "separated"
            row.update(status=classification, distance_m=distance_m, common_volume_m3=common_volume_m3,
                       detail=f"{pair[0]} / {pair[1]}: {classification} (OCCT solid distance and common volume)")
        except Exception as exc:
            row["detail"] = f"{pair[0]} / {pair[1]}: solid measurement unavailable: {exc}"
    return results


# ---------------------------------------------------------------- orthographic drawing lines


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
    the drawing's origin and axes, which is what ``frame`` supplies.
    """

    if frame is None:
        return (float(point.X()), float(point.Y()))
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

# ---------------------------------------------------------------- tessellation and preview


def tessellate_shape(
    shape,
    *,
    linear_deflection: float,
    angular_deflection: float = 0.5,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Triangulate every face; vertices in the CAD frame, outward-wound triangles."""

    occ = _occt()
    if not (isinstance(linear_deflection, (int, float)) and math.isfinite(linear_deflection) and linear_deflection > 0):
        raise OcctBackendError("linear_deflection must be positive and finite")
    occ.BRepMesh.BRepMesh_IncrementalMesh(shape, float(linear_deflection), False, float(angular_deflection), True)
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    for raw_face in _explore(occ, shape, occ.TopAbs.TopAbs_FACE):
        face = occ.TopoDS.TopoDS.Face_s(raw_face)
        location = occ.TopLoc.TopLoc_Location()
        triangulation = occ.BRep.BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            raise OcctBuildError("a face has no triangulation after meshing")
        transform = location.Transformation()
        base = len(vertices)
        for node in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(node).Transformed(transform)
            vertices.append((float(point.X()), float(point.Y()), float(point.Z())))
        reversed_face = face.Orientation() == occ.TopAbs.TopAbs_REVERSED
        for index in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(index).Get()
            corners = (base + a - 1, base + c - 1, base + b - 1) if reversed_face else (base + a - 1, base + b - 1, base + c - 1)
            triangles.append(corners)
    if not vertices or not triangles:
        raise OcctBuildError("tessellation produced no triangles")
    return vertices, triangles


@dataclass(frozen=True, slots=True)
class PreviewMaterial:
    """One native preview material: display name, diffuse colour, openNURBS transparency.

    ``transparency`` is the openNURBS value (0 opaque, 1 invisible); the
    three.js loader renders it as ``opacity = 1 - transparency``.  The
    material also carries ``archflow:material_id`` = ``name`` as user text,
    the key the inspector already reads back.
    """

    name: str
    diffuse: tuple[int, int, int]
    transparency: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise OcctBackendError("preview material name must be non-empty text")
        if (
            not isinstance(self.diffuse, tuple)
            or len(self.diffuse) != 3
            or any(isinstance(c, bool) or not isinstance(c, int) or c < 0 or c > 255 for c in self.diffuse)
        ):
            raise OcctBackendError(f"preview material {self.name}: diffuse must be three 0..255 channels")
        if (
            isinstance(self.transparency, bool)
            or not isinstance(self.transparency, (int, float))
            or not math.isfinite(self.transparency)
            or not 0.0 <= float(self.transparency) < 1.0
        ):
            raise OcctBackendError(f"preview material {self.name}: transparency must be in [0, 1)")
        object.__setattr__(self, "transparency", float(self.transparency))

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "diffuse": list(self.diffuse), "transparency": self.transparency}


@dataclass(frozen=True)
class PreviewObject:
    """One preview object: the shape and delivery plus the semantics the viewer reads."""

    object_id: str
    shape: Any
    layer: str
    user_text: Mapping[str, str]
    visible: bool = True
    #: A native material of the object's own; None leaves the layer's display colour.
    material: PreviewMaterial | None = None
    delivery: str = CLOSED_SOLID


def write_preview_three_dm(
    path: Path,
    objects: Sequence[PreviewObject],
    *,
    layer_colors: Mapping[str, tuple[int, int, int]],
    document_user_text: Mapping[str, str],
    length_unit: str,
    linear_deflection: float,
    angular_deflection: float = 0.5,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    observation_parent_id: str | None = None,
) -> dict[str, dict[str, int]]:
    """Write tessellated surfaces and native polyline curves through rhino3dm.

    Surfaces are tessellated render meshes; curves retain their native polyline
    geometry. It carries the object names, nested layer paths
    with their colours, the ``archflow:*`` object user text and the document
    user text, so the viewer treats it exactly like a Rhino-written file.
    An object with a ``PreviewMaterial`` is bound to a native material of
    the document's table (``MaterialSource`` = from object); equal materials
    share one table entry. Returns mesh vertex/face or curve point/segment counts.

    Observation separates accumulated tessellator-call time from File3dm.Write;
    mesh construction between calls is outside that accumulated duration.
    Observer failures never change the output or the original write error.
    """

    try:
        rhino3dm = importlib.import_module("rhino3dm")
    except ImportError as exc:
        raise OcctUnavailableError(
            "optional dependency 'rhino3dm' is unavailable; install it with "
            "python -m pip install -e '.[cad-occt]'"
        ) from exc
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    unit_name = _UNIT_TO_RHINO3DM.get(length_unit)
    if unit_name is None:
        raise OcctBackendError(f"length unit {length_unit!r} has no rhino3dm unit")
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = getattr(rhino3dm.UnitSystem, unit_name)
    layer_index: dict[str, int] = {}

    def ensure_layer(full_path: str) -> int:
        if full_path in layer_index:
            return layer_index[full_path]
        segments = full_path.split("::")
        parent_path = "::".join(segments[:-1])
        parent_index = ensure_layer(parent_path) if parent_path else None
        layer = rhino3dm.Layer()
        layer.Name = segments[-1]
        if parent_index is not None:
            layer.ParentLayerId = model.Layers[parent_index].Id
        red, green, blue = layer_colors.get(full_path, (0, 0, 0))
        layer.Color = (int(red), int(green), int(blue), 255)
        index = model.Layers.Add(layer)
        layer_index[full_path] = index
        return index

    for full_path in sorted(layer_colors):
        ensure_layer(full_path)
    material_index: dict[PreviewMaterial, int] = {}

    def ensure_material(material: PreviewMaterial) -> int:
        if material in material_index:
            return material_index[material]
        native = rhino3dm.Material()
        native.Name = material.name
        red, green, blue = material.diffuse
        native.DiffuseColor = (int(red), int(green), int(blue), 255)
        native.Transparency = material.transparency
        native.SetUserString("archflow:material_id", material.name)
        index = model.Materials.Add(native)
        material_index[material] = index
        return index

    for item in objects:
        if item.material is not None:
            ensure_material(item.material)
    counts: dict[str, dict[str, int]] = {}
    program_digest = document_user_text.get("archflow:program_digest")
    input_identity = {"program_digest": program_digest} if program_digest is not None else {}
    mesh_started_at = mesh_ended_at = None
    mesh_seconds = 0.0
    mesh_inputs: list[str] = []
    mesh_outputs: list[str] = []
    mesh_status = "succeeded"
    try:
        for item in objects:
            call_started_at = datetime.now(timezone.utc)
            mesh_started_at = mesh_started_at or call_started_at
            mesh_inputs.append(item.object_id)
            call_started = time.perf_counter()
            try:
                if item.delivery == CURVE:
                    vertices = _polyline_geometry(item.shape)["curve_points"]
                    triangles = []
                else:
                    vertices, triangles = tessellate_shape(
                        item.shape,
                        linear_deflection=linear_deflection,
                        angular_deflection=angular_deflection,
                    )
            except Exception:
                mesh_status = "failed"
                raise
            finally:
                mesh_seconds += time.perf_counter() - call_started
                mesh_ended_at = datetime.now(timezone.utc)
            mesh_outputs.append(item.object_id)
            if item.delivery == CURVE:
                geometry = rhino3dm.PolylineCurve([rhino3dm.Point3d(*point) for point in vertices])
            else:
                geometry = rhino3dm.Mesh()
                for x, y, z in vertices:
                    geometry.Vertices.Add(x, y, z)
                for a, b, c in triangles:
                    geometry.Faces.AddFace(a, b, c)
                geometry.Normals.ComputeNormals()
                geometry.Compact()
            if not geometry.IsValid:
                raise OcctBuildError(f"{item.object_id}: preview geometry is invalid")
            attributes = rhino3dm.ObjectAttributes()
            attributes.Name = item.object_id
            attributes.LayerIndex = ensure_layer(item.layer)
            attributes.Visible = bool(item.visible)
            if item.material is not None:
                attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
                attributes.MaterialIndex = ensure_material(item.material)
            for key in sorted(item.user_text):
                attributes.SetUserString(key, item.user_text[key])
            if item.delivery == CURVE:
                model.Objects.AddCurve(geometry, attributes)
                counts[item.object_id] = {"curve_point_count": len(vertices), "curve_segment_count": len(vertices) - 1}
            else:
                model.Objects.AddMesh(geometry, attributes)
                counts[item.object_id] = {"mesh_vertex_count": len(vertices), "mesh_face_count": len(triangles)}
    finally:
        if mesh_started_at is not None:
            _observe_operation(
                operation_observer,
                phase="tessellation",
                status=mesh_status,
                started_at=mesh_started_at,
                ended_at=mesh_ended_at,
                duration_ms=1000.0 * mesh_seconds,
                parent_event_id=observation_parent_id,
                details={
                    "input_identity": dict(input_identity),
                    "input_object_ids": list(mesh_inputs),
                    "emitted_object_ids": list(mesh_outputs),
                    "execution_path": "occt_tessellation",
                    "scope": "aggregate_active_time",
                    "executed_stages": (["tessellate_shape"] if any(item.delivery != CURVE for item in objects) else [])
                                       + (["polyline_geometry"] if any(item.delivery == CURVE for item in objects) else []),
                    "cache_status": "unknown",
                    "cache_reason": "kernel_mesh_reuse_unobserved",
                },
            )
    for key in sorted(document_user_text):
        model.Strings[key] = document_user_text[key]
    write_started_at = datetime.now(timezone.utc)
    write_started = time.perf_counter()
    write_status = "failed"
    try:
        if not model.Write(str(path), 8):
            raise OcctBuildError("preview .3dm write failed")
        write_status = "succeeded"
    finally:
        write_seconds = time.perf_counter() - write_started
        _observe_operation(
            operation_observer,
            phase="preview_write",
            status=write_status,
            started_at=write_started_at,
            ended_at=datetime.now(timezone.utc),
            duration_ms=1000.0 * write_seconds,
            parent_event_id=observation_parent_id,
            details={
                "input_identity": dict(input_identity),
                "input_object_ids": sorted(counts),
                "emitted_object_ids": sorted(counts) if write_status == "succeeded" else [],
                "execution_path": "file3dm_write",
                "scope": "file_write",
                "executed_stages": ["write_preview_three_dm"],
                "cache_status": "not_applicable",
            },
        )
    return counts


__all__ = [
    "CLOSED_SOLID",
    "OPEN_SURFACE",
    "OcctBackendError",
    "OcctBuildError",
    "OcctCapabilityError",
    "OcctDrawingPolyline",
    "OcctObjectBuild",
    "OcctProgramBuild",
    "OcctUnavailableError",
    "PreviewMaterial",
    "PreviewObject",
    "SUPPORTED_OPERATION_KINDS",
    "ShapeMeasure",
    "StepEntry",
    "StepObject",
    "backend_identity",
    "build_program_shapes",
    "cad_point",
    "classify_point",
    "classify_program_point",
    "declared_delivery",
    "measure_shape",
    "measure_occt_solid_pairs",
    "occt_available",
    "project_occt_lines",
    "read_step",
    "tessellate_shape",
    "write_preview_three_dm",
    "write_step",
]
