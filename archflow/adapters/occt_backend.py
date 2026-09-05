"""In-process OCCT backend behind ``adapters.cad_execution`` (P107, lane A).

The Rhino path translates a compiled program into a script and lets an
external host realize it.  This backend interprets the same
``CompiledGeometryProgram`` directly against Open CASCADE (OCCT) through the
``cadquery-ocp`` binding: every accepted operation kind maps to one kernel
call, the result is measured in process, written to STEP as exact B-rep, and
tessellated for a viewer-readable mesh ``.3dm`` from the same model.

What this module owns is the kernel-facing mechanics only: loading the
binding, the one-time coordinate mapping, building shapes, measuring them,
writing and cold-reading STEP, tessellating, and writing the mesh preview.
The export identity, the readback verification against the analytic
predictor and the receipt live in ``adapters.cad_execution``; nothing here
knows a project, a run or a workspace rule.

Coordinate frame.  Program geometry is ``(x, y-up, z-plan)``.  The Rhino
translation writes every point as ``(x, z, y)`` so the saved document is
Z-up; the viewer converts that once.  This backend applies exactly the same
mapping at point construction (``cad_point``), so STEP, preview and the
Rhino ``.3dm`` share one frame and one set of expected bounds.

Capability.  Only the operation kinds the initial consumers actually use are
realized: ``solid`` (box), ``extrusion``, polyline ``loft`` with capped ends,
and the three booleans.  Anything else fails ``OcctCapabilityError`` naming
the operation before anything is written.  There is no fallback to Rhino, no
bounding-box stand-in and no mesh pretending to be a B-rep.

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

import importlib
import importlib.metadata
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from archflow.adapters.cad_program import _params, _physical_ids, lift_to_base_level
from archflow.compilers.geometry import CompiledGeometryProgram


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
        "extrusion",
        "loft",
        "boolean_union",
        "boolean_difference",
        "boolean_intersection",
    }
)

_UNSUPPORTED_REASONS: Mapping[str, str] = {
    "curve": "curve objects are not closed-solid deliveries; the OCCT executor writes solids only",
    "transform": "transform has no exact realization (the Rhino translation only copies it)",
    "array": "block instancing is not realized by the OCCT executor yet",
    "radial_array": "block instancing is not realized by the OCCT executor yet",
    "revolve": "revolve is not realized by the OCCT executor yet",
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
                "BRepAlgoAPI",
                "BRepBndLib",
                "BRepBuilderAPI",
                "BRepCheck",
                "BRepClass3d",
                "BRepGProp",
                "BRepMesh",
                "BRepOffsetAPI",
                "BRepPrimAPI",
                "Bnd",
                "GProp",
                "IFSelect",
                "Interface",
                "Message",
                "Quantity",
                "STEPCAFControl",
                "STEPControl",
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


def build_program_shapes(program: CompiledGeometryProgram) -> OcctProgramBuild:
    """Interpret the program in operation order against the kernel.

    Fails ``OcctCapabilityError`` on the first operation outside the
    supported vocabulary and ``OcctBuildError`` when OCCT cannot produce a
    valid shape; nothing is written by this function.
    """

    if not isinstance(program, CompiledGeometryProgram):
        raise TypeError("program must be CompiledGeometryProgram")
    occ = _occt()
    started = time.perf_counter()
    proposal = program.proposal
    operations = {operation.op_id: operation for operation in proposal.operations}
    shapes: dict[str, Any] = {}
    built: dict[str, OcctObjectBuild] = {}
    for op_id in program.operation_order:
        operation = operations[op_id]
        kind = operation.kind.value
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
            shape = _build_operation(occ, kind, operation, params, shapes)
        except OcctBackendError:
            raise
        except Exception as exc:  # OCCT failures surface as Standard_Failure
            raise OcctBuildError(f"{op_id} ({kind}): {exc}") from exc
        _require_built_shape(occ, shape, op_id, kind)
        shapes[output] = shape
        built[output] = OcctObjectBuild(
            object_id=output,
            producer_op=op_id,
            shape=shape,
            hidden=bool(params.get("hidden_for_inspection", False)),
        )
    return OcctProgramBuild(
        objects=built,
        physical_object_ids=tuple(sorted(_physical_ids(proposal))),
        elapsed_seconds=time.perf_counter() - started,
    )


def _build_operation(
    occ: SimpleNamespace,
    kind: str,
    operation,
    params: Mapping[str, object],
    shapes: Mapping[str, Any],
):
    op_id = operation.op_id
    if kind == "solid":
        origin, size = params["origin"], params["size"]
        if any(float(value) <= 0.0 for value in size):
            raise OcctCapabilityError(op_id, kind, "solid size must be positive on every axis")
        far = [float(origin[axis]) + float(size[axis]) for axis in range(3)]
        return occ.BRepPrimAPI.BRepPrimAPI_MakeBox(
            _gp_point(occ, origin), _gp_point(occ, far)
        ).Shape()
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
    if kind == "loft":
        profiles = lift_to_base_level(params["profiles"], params, op_id)
        size = int(params["profile_size"])
        loft_type = params.get("loft_type", "normal")
        profile_basis = params.get("profile_basis", "polyline")
        cap_ends = bool(params.get("cap_ends", True))
        if profile_basis != "polyline":
            raise OcctCapabilityError(op_id, kind, f"profile_basis {profile_basis!r} is not realized; polyline only")
        if loft_type not in ("normal", "straight"):
            raise OcctCapabilityError(op_id, kind, f"loft_type {loft_type!r} is not realized")
        if not cap_ends:
            raise OcctCapabilityError(op_id, kind, "an uncapped loft is not a closed-solid delivery")
        if size < 3 or len(profiles) % size != 0 or len(profiles) // size < 2:
            raise OcctCapabilityError(op_id, kind, "loft needs at least two closed sections of at least three points")
        loft = occ.BRepOffsetAPI.BRepOffsetAPI_ThruSections(
            True, loft_type == "straight", _LOFT_PRECISION
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
    raise OcctCapabilityError(op_id, kind, "operation kind is not realized")


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


def _require_built_shape(occ: SimpleNamespace, shape, op_id: str, kind: str) -> None:
    if shape is None or shape.IsNull():
        raise OcctBuildError(f"{op_id} ({kind}): OCCT produced no shape")
    if _count(occ, shape, occ.TopAbs.TopAbs_SOLID) == 0:
        raise OcctBuildError(f"{op_id} ({kind}): OCCT produced no solid")
    if not occ.BRepCheck.BRepCheck_Analyzer(shape).IsValid():
        raise OcctBuildError(f"{op_id} ({kind}): OCCT produced an invalid shape")


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
    """What the kernel reports about one shape, in the CAD frame."""

    valid: bool
    solid_count: int
    closed: bool
    face_count: int
    volume: float
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "solid_count": self.solid_count,
            "closed": self.closed,
            "face_count": self.face_count,
            "volume": self.volume,
            "bbox": {"min": list(self.bbox_min), "max": list(self.bbox_max)},
        }


def measure_shape(shape) -> ShapeMeasure:
    """Validity, solid/shell closure, volume and tight bounds of one shape."""

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
    properties = occ.GProp.GProp_GProps()
    occ.BRepGProp.BRepGProp.VolumeProperties_s(shape, properties)
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
        volume=float(properties.Mass()),
        bbox_min=(float(xmin), float(ymin), float(zmin)),
        bbox_max=(float(xmax), float(ymax), float(zmax)),
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
    """One named solid to write: identity, layer and display colour."""

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
    """Write the objects as named, layered, coloured solids in the program's unit.

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
        layer_labels = occ.TDF.TDF_LabelSequence()
        layer_tool.GetLayers(label, layer_labels)
        layers = tuple(
            _label_name(occ, layer_labels.Value(position)) or ""
            for position in range(1, layer_labels.Length() + 1)
        )
        color = occ.Quantity.Quantity_Color()
        rgb = None
        if color_tool.GetColor(shape, occ.XCAFDoc.XCAFDoc_ColorSurf, color):
            rgb = tuple(
                int(round(channel * 255.0))
                for channel in (color.Red(), color.Green(), color.Blue())
            )
        entries.append(
            StepEntry(name=_label_name(occ, label), layers=layers, color=rgb, shape=shape)
        )
    return tuple(entries)


def _label_name(occ: SimpleNamespace, label) -> str | None:
    attribute = occ.TDataStd.TDataStd_Name()
    if not label.FindAttribute(occ.TDataStd.TDataStd_Name.GetID_s(), attribute):
        return None
    return str(attribute.Get().ToExtString())


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


@dataclass(frozen=True)
class PreviewObject:
    """One object of the mesh preview: the shape plus the semantics the viewer reads."""

    object_id: str
    shape: Any
    layer: str
    user_text: Mapping[str, str]
    visible: bool = True


def write_preview_three_dm(
    path: Path,
    objects: Sequence[PreviewObject],
    *,
    layer_colors: Mapping[str, tuple[int, int, int]],
    document_user_text: Mapping[str, str],
    length_unit: str,
    linear_deflection: float,
    angular_deflection: float = 0.5,
) -> dict[str, dict[str, int]]:
    """Write a mesh-only ``.3dm`` through rhino3dm from the built shapes.

    Format identity: this is a tessellated render-mesh preview, not a
    NURBS/B-rep delivery.  It carries the object names, nested layer paths
    with their colours, the ``archflow:*`` object user text and the document
    user text, so the viewer treats it exactly like a Rhino-written file.
    Returns per-object mesh vertex and face counts.
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
    counts: dict[str, dict[str, int]] = {}
    for item in objects:
        vertices, triangles = tessellate_shape(
            item.shape,
            linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
        )
        mesh = rhino3dm.Mesh()
        for x, y, z in vertices:
            mesh.Vertices.Add(x, y, z)
        for a, b, c in triangles:
            mesh.Faces.AddFace(a, b, c)
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        if not mesh.IsValid:
            raise OcctBuildError(f"{item.object_id}: preview mesh is invalid")
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = item.object_id
        attributes.LayerIndex = ensure_layer(item.layer)
        attributes.Visible = bool(item.visible)
        for key in sorted(item.user_text):
            attributes.SetUserString(key, item.user_text[key])
        model.Objects.AddMesh(mesh, attributes)
        counts[item.object_id] = {
            "mesh_vertex_count": len(vertices),
            "mesh_face_count": len(triangles),
        }
    for key in sorted(document_user_text):
        model.Strings[key] = document_user_text[key]
    if not model.Write(str(path), 8):
        raise OcctBuildError("preview .3dm write failed")
    return counts


__all__ = [
    "OcctBackendError",
    "OcctBuildError",
    "OcctCapabilityError",
    "OcctObjectBuild",
    "OcctProgramBuild",
    "OcctUnavailableError",
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
    "measure_shape",
    "occt_available",
    "read_step",
    "tessellate_shape",
    "write_preview_three_dm",
    "write_step",
]
