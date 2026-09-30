"""Tessellating OCCT shapes and writing the mesh ``.3dm`` preview through rhino3dm.

Preview materials.  The mesh preview may carry native ``rhino3dm``
materials for the objects the caller names (an assembly's frame and
glazing): one material per distinct (name, colour, transparency), the
object's ``MaterialSource`` set to the object, so a viewer that reads the
document's material table (the three.js ``Rhino3dmLoader``) renders glass
translucent.  STEP carries no material; both files are written from the
same shapes.
"""

from __future__ import annotations

import importlib
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from monkeycad.backends.occt.build import CLOSED_SOLID, CURVE, _polyline_geometry
from monkeycad.backends.occt.errors import OcctBackendError, OcctBuildError, OcctUnavailableError
from monkeycad.backends.occt.kernel import _explore, _observe_operation, _occt


_UNIT_TO_RHINO3DM: Mapping[str, str] = {
    "millimeter": "Millimeters",
    "meter": "Meters",
    "inch": "Inches",
    "foot": "Feet",
}


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


def _clean(shape) -> None:
    """Forget a triangulation the shape already holds.

    OCCT keeps a finer mesh when asked for a coarser one, so a model loaded
    once and drawn at 1024 px and then 512 px would draw the 512 px picture
    from the finer mesh. Cleaning first makes the bytes depend on the size
    alone, not on what was drawn before.
    """

    from OCP.BRepTools import BRepTools

    BRepTools.Clean_s(shape)


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
            "python -m pip install -e 'packages/monkeycad[occt]'"
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
    "PreviewMaterial",
    "PreviewObject",
    "tessellate_shape",
    "write_preview_three_dm",
]
