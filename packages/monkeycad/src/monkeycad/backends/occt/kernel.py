"""Loading the OCCT binding, the one coordinate mapping, and the shape helpers every OCCT module shares.

Coordinate frame.  Program geometry is ``(x, y-up, z-plan)``.  The Rhino
translation writes every point as ``(x, z, y)`` so the saved document is
Z-up; the viewer converts that once.  This backend applies exactly the same
mapping at point construction (``cad_point``), so STEP, preview and the
Rhino ``.3dm`` share one frame and one set of expected bounds.

Requires the optional dependency ``cadquery-ocp`` (``pip install -e
'packages/monkeycad[occt]'``).  It is imported lazily: importing this module, or any
module of the backend, never loads OCCT.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from monkeycad.backends.occt.errors import OcctBuildError, OcctUnavailableError


_OCCT: SimpleNamespace | None = None


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
                "BRepLib",
                "BRepMesh",
                "BRepOffsetAPI",
                "BRepPrimAPI",
                "BRepTools",
                "Bnd",
                "GCPnts",
                "Geom",
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
                "TColgp",
                "TColStd",
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
            "python -m pip install -e 'packages/monkeycad[occt]'; no Rhino is required or started"
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


__all__ = [
    "backend_identity",
    "cad_point",
    "occt_available",
]
