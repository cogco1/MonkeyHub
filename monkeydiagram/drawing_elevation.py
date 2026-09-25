"""Model-axis elevations and cut plans from retained STEP or registered 3DM through P036.

The A0 drawing slice of ``docs/DRAWING_MODULE_ARCHITECTURE_PLAN.md``, as far
as it is real: a source run's exact STEP (certified by its retained
``OcctExecutionReceipt@1``) or registered 3DM is cold-read. Native 3DM retains
object GUID paths and explicit exact/faceted/approximate geometry quality;
it does not acquire a compiled-program or STEP receipt. Every selected shape takes part in
one exact hidden-line solve for a frame stated along the model axes
(``adapters.cad_execution.project_occt_lines``), the visible (and, on
request, hidden) polylines are cropped and serialised as one deterministic
SVG whose every polyline names its source physical object, a PNG is
rendered from that SVG (``monkeydiagram.drawing_svg``), and the two files plus
one ``DrawingProjectionReceipt@1`` are retained in a *drawing run* whose
base is the source run's base.

What this module decides and nothing else:

- STEP identity is checked against its CAD receipt, run, base, unit and
  Z-up axis, and its names match the certified object ids one for one;
  native models instead verify their registered bytes, run and units,
  preserving any original import identity and conversion warnings;
- the frame is checked to be right-handed and consistent (``look`` is the
  opposite of ``right x up``), the crop window and near/far are finite and
  ordered, and the receipt records exactly what was applied;
- the drawing run is created with ``base = source run base`` or, when it
  exists, is used only if it already carries that base;
- nothing is written until the projection and both renderings succeeded;
  the files and the receipt are read back through the repository before
  the result is reported, and the project's HEAD must be the same after as
  before.

The in-memory projection (``project_model_axis_elevation``) returns plain
values and mints nothing. ``freeze_cut_plan`` adds a real horizontal plane
section and below-cut visibility to that same retention boundary, with
caller-resolved dimensions. ``freeze_section_perspective`` adds a section
perspective: an arbitrary section plane, the kept side drawn in exact
perspective from an eye on the removed side, the cut in poché and true to
scale because the picture plane is the section plane. There is no second
storage, design mutation or update mechanism here; axonometrics are not
implemented.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from archflow.adapters.cad_execution import (
    OcctBackendError,
    OcctDrawingPolyline,
    StepEntry,
    backend_identity,
    project_occt_lines,
    section_occt_lines,
    section_occt_regions,
    read_step,
)
from archflow.adapters.occt_backend import OcctSectionPerspective, project_occt_section_perspective
from monkeydiagram.drawing_svg import (
    PNG_MEDIA_TYPE,
    SVG_MEDIA_TYPE,
    DrawingSvgError,
    drawing_svg,
    render_svg_png,
    svg_objects,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DRAWING_PROJECTION_RECEIPT
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, RunRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from archflow.project.version_refs import (
    register as _register_version_refs,
    register_derived as _register_derived_fields,
)

DRAWING_PROJECTION_RECEIPT_SCHEMA = "DrawingProjectionReceipt@1"
SOURCE_RECEIPT_SCHEMA = "OcctExecutionReceipt@1"
ELEVATION_KIND = "model-axis-elevation"
CUT_PLAN_KIND = "cut-plan"
SECTION_PERSPECTIVE_KIND = "section-perspective"
#: The view kinds one drawing receipt may record; each is frozen by its own function here.
DRAWING_VIEW_KINDS = (ELEVATION_KIND, CUT_PLAN_KIND, SECTION_PERSPECTIVE_KIND)
DOCUMENTATION_WORKSPACE = "documentation"
STEP_MEDIA_TYPE = "model/step"
_STEP_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,159}\.step$")
_UNITS = ("meter", "millimeter", "inch", "foot")
_TOLERANCE = 1e-9


class DrawingElevationError(ValueError):
    """The source, the frame or the drawing run cannot be used as asked; nothing was written."""


@contextmanager
def _observed_stage(observer, phase: str, *, parent_event_id: str | None = None, details=None):
    """Report a real call boundary without changing its value or exception."""

    details = {} if details is None else details
    if observer is None:
        yield details
        return
    started_at, started = datetime.now(timezone.utc).isoformat(), perf_counter()
    event_id = str(uuid4())

    def report(status, *, ended_at=None, duration_ms=None):
        try:
            observer({"event_id": event_id, "parent_event_id": parent_event_id, "phase": phase,
                      "status": status, "started_at": started_at, "ended_at": ended_at,
                      "duration_ms": duration_ms, "details": dict(details)})
        except (Exception, asyncio.CancelledError):
            pass

    report("running")
    status = "succeeded"
    try:
        yield details
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)) else "failed"
        raise
    finally:
        report(status, ended_at=datetime.now(timezone.utc).isoformat(), duration_ms=round((perf_counter() - started) * 1000))


def _vector(value, label: str) -> tuple[float, float, float]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        raise DrawingElevationError(f"{label} must be three finite numbers")
    return tuple(float(v) for v in value)


def _unit(value, label: str) -> tuple[float, float, float]:
    vector = _vector(value, label)
    if not math.isclose(math.hypot(*vector), 1.0, rel_tol=0.0, abs_tol=_TOLERANCE):
        raise DrawingElevationError(f"{label} must be a unit direction")
    return vector


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DrawingElevationError(f"{label} must be a finite number")
    return float(value)


@dataclass(frozen=True, slots=True)
class ElevationSource:
    """The retained exact STEP one drawing is derived from, named by project-relative refs."""

    run_id: str
    step_relative_path: str
    step_sha256: str
    cad_receipt_relative_path: str
    cad_receipt_sha256: str

    def __post_init__(self) -> None:
        try:
            require_identifier(self.run_id, "source run_id")
            receipt = PurePosixPath(ProjectRecordRef("p", self.cad_receipt_relative_path,
                                                     self.cad_receipt_sha256).relative_path)
            # The STEP was written by the CAD adapter into the run's workspace
            # under the stage's own file name (``<stem>@<digest>.step``), which
            # the project ref grammar does not admit; it is located the way the
            # Studio locates certified files: run, workspace, file name.
            if not isinstance(self.step_relative_path, str) or "\\" in self.step_relative_path:
                raise ValueError("step_relative_path must be portable POSIX text")
            step = PurePosixPath(self.step_relative_path)
            if (step.parts[:3] != ("runs", self.run_id, "workspaces") or len(step.parts) != 5
                    or _STEP_FILE_NAME.fullmatch(step.parts[4]) is None or step.as_posix() != self.step_relative_path):
                raise ValueError("the source STEP must be runs/<run>/workspaces/<workspace>/<name>.step")
            require_identifier(step.parts[3], "source workspace")
            digest = ProjectArtifactRef("p", "a", "runs/x", self.step_sha256, STEP_MEDIA_TYPE).sha256
        except (TypeError, ValueError) as exc:
            raise DrawingElevationError(f"elevation source is not well formed: {exc}") from exc
        if receipt.parts[:3] != ("runs", self.run_id, "records") or len(receipt.parts) != 4:
            raise DrawingElevationError("the source CAD receipt must be a record of the source run")
        object.__setattr__(self, "step_sha256", digest)
        object.__setattr__(self, "cad_receipt_sha256", self.cad_receipt_sha256.lower())

    @property
    def step_workspace(self) -> str:
        return PurePosixPath(self.step_relative_path).parts[3]

    @property
    def step_file_name(self) -> str:
        return PurePosixPath(self.step_relative_path).name


@dataclass(frozen=True, slots=True)
class NativeModelSource:
    """An explicitly registered 3DM; no compiled-program or STEP authority is implied."""

    run_id: str
    registration: ProjectRecordRef
    artifact: ProjectArtifactRef


@dataclass(frozen=True, slots=True)
class ElevationView:
    """One orthographic elevation frame along the model axes, in the STEP's CAD Z-up unit.

    ``origin`` is the drawing origin; ``right``/``up`` span the sheet and
    ``look`` must be ``-(right x up)`` (the sheet's normal points at the
    viewer).  ``crop_uv`` is the drawn window in sheet coordinates
    ``(u_min, v_min, u_max, v_max)`` with ``u = dot(p - origin, right)``,
    ``v = dot(p - origin, up)``; ``near_depth``/``far_depth`` bound
    ``dot(p - origin, look)``.  ``hidden_lines`` False draws visible lines
    only; the solve still computes hidden ones and the receipt counts them.
    """

    name: str
    origin: tuple[float, float, float]
    look: tuple[float, float, float]
    right: tuple[float, float, float]
    up: tuple[float, float, float]
    crop_uv: tuple[float, float, float, float]
    near_depth: float
    far_depth: float
    hidden_lines: bool = False
    linear_deflection: float = 0.0001
    scale_denominator: int = 100

    def __post_init__(self) -> None:
        try:
            require_identifier(self.name, "view name")
        except ValueError as exc:
            raise DrawingElevationError(str(exc)) from exc
        object.__setattr__(self, "origin", _vector(self.origin, "origin"))
        object.__setattr__(self, "look", _unit(self.look, "look"))
        object.__setattr__(self, "right", _unit(self.right, "right"))
        object.__setattr__(self, "up", _unit(self.up, "up"))
        if abs(sum(r * u for r, u in zip(self.right, self.up))) > _TOLERANCE:
            raise DrawingElevationError("right and up must be perpendicular")
        normal = _cross(self.right, self.up)
        if any(abs(l + n) > _TOLERANCE for l, n in zip(self.look, normal)):
            raise DrawingElevationError("look must be the opposite of right x up (the sheet faces the viewer)")
        if (not isinstance(self.crop_uv, Sequence) or isinstance(self.crop_uv, (str, bytes))
                or len(self.crop_uv) != 4):
            raise DrawingElevationError("crop_uv must be (u_min, v_min, u_max, v_max)")
        crop = tuple(_finite(v, "crop_uv") for v in self.crop_uv)
        if not (crop[0] < crop[2] and crop[1] < crop[3]):
            raise DrawingElevationError("crop_uv must have u_min < u_max and v_min < v_max")
        object.__setattr__(self, "crop_uv", crop)
        object.__setattr__(self, "near_depth", _finite(self.near_depth, "near_depth"))
        object.__setattr__(self, "far_depth", _finite(self.far_depth, "far_depth"))
        if not self.near_depth < self.far_depth:
            raise DrawingElevationError("near_depth must be less than far_depth")
        if not isinstance(self.hidden_lines, bool):
            raise DrawingElevationError("hidden_lines must be a bool")
        deflection = _finite(self.linear_deflection, "linear_deflection")
        if deflection <= 0.0:
            raise DrawingElevationError("linear_deflection must be positive")
        object.__setattr__(self, "linear_deflection", deflection)
        if (isinstance(self.scale_denominator, bool) or not isinstance(self.scale_denominator, int)
                or self.scale_denominator <= 0):
            raise DrawingElevationError("scale_denominator must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": ELEVATION_KIND,
            "projection": "orthographic",
            "origin": list(self.origin),
            "look": list(self.look),
            "right": list(self.right),
            "up": list(self.up),
            "uv_definition": "u = dot(point - origin, right); v = dot(point - origin, up)",
            "depth_definition": "dot(point - origin, look)",
            "crop_uv": list(self.crop_uv),
            "near_depth": self.near_depth,
            "far_depth": self.far_depth,
            "hidden_lines": self.hidden_lines,
            "linear_deflection": self.linear_deflection,
            "scale": f"1:{self.scale_denominator}",
        }


@dataclass(frozen=True, slots=True)
class ElevationProjection:
    """The in-memory result: every solved polyline, and the SVG and PNG of the cropped drawing."""

    lines: tuple[OcctDrawingPolyline, ...]
    svg: bytes
    png: bytes

    def counts(self) -> dict[str, Any]:
        visible = [line for line in self.lines if line.kind == "visible"]
        hidden = [line for line in self.lines if line.kind == "hidden"]
        return {
            "visible_polylines": len(visible),
            "hidden_polylines": len(hidden),
            "objects_with_visible_lines": len({line.object_id for line in visible}),
            "objects_with_hidden_lines": len({line.object_id for line in hidden}),
            "objects_drawn_in_svg": len(svg_objects(self.svg)),
        }


def project_model_axis_elevation(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str], view: ElevationView, unit: str,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None,
) -> ElevationProjection:
    """Solve, crop and render one elevation of the named shapes; writes nothing.

    Every object in ``object_ids`` takes part in the one visibility solve
    (restricted to the view's near/far slab); the crop then clips the
    result to the sheet window.  The SVG holds visible polylines, plus
    hidden ones when the view asks for them, each naming its object.
    """

    if not isinstance(view, ElevationView):
        raise TypeError("view must be ElevationView")
    if unit not in _UNITS:
        raise DrawingElevationError(f"unit {unit!r} is not a CAD length unit")
    try:
        with _observed_stage(operation_observer, "drawing.hlr", parent_event_id=parent_event_id,
                             details={"scope": "global_visibility", "input_object_ids": sorted(object_ids)}) as observation:
            lines = project_occt_lines(
                entries, object_ids=tuple(object_ids), origin=view.origin, right=view.right, up=view.up,
                linear_deflection=view.linear_deflection, depth_range=(view.near_depth, view.far_depth),
            )
            observation["emitted_object_ids"] = sorted({line.object_id for line in lines})
        with _observed_stage(operation_observer, "drawing.svg", parent_event_id=parent_event_id,
                             details={"input_object_ids": sorted({line.object_id for line in lines})}) as observation:
            svg = drawing_svg(
                lines, crop_uv=view.crop_uv, unit=unit, scale_denominator=view.scale_denominator,
                hidden_lines=view.hidden_lines, title=view.name,
            )
            observation["emitted_object_ids"] = list(svg_objects(svg))
        with _observed_stage(operation_observer, "drawing.png", parent_event_id=parent_event_id):
            png = render_svg_png(svg)
    except (OcctBackendError, DrawingSvgError) as exc:
        raise DrawingElevationError(f"elevation {view.name}: {exc}") from exc
    return ElevationProjection(lines=lines, svg=svg, png=png)


@dataclass(frozen=True, slots=True)
class ElevationDrawing:
    """One retained drawing: its run, receipt and the two files, as read back through the repository."""

    run: RunRef
    receipt_ref: ProjectRecordRef
    receipt: Mapping[str, Any]
    svg_ref: ProjectArtifactRef
    png_ref: ProjectArtifactRef
    svg: bytes
    png: bytes


@dataclass(frozen=True, slots=True)
class VerifiedElevationSource:
    run: RunRef
    receipt: Mapping[str, Any]
    length_unit: str
    program_digest: str | None
    stage_id: str | None
    physical_object_ids: tuple[str, ...]
    entries: tuple[StepEntry, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DrawingElevationError(message)


def _artifact_bytes(repository: FilesystemProjectRepository, ref: ProjectArtifactRef) -> bytes:
    try:
        data = repository.layout.resolve_record(ref).read_bytes()
    except (OSError, ValueError) as exc:
        raise DrawingElevationError(f"cannot read {ref.relative_path}: {exc}") from exc
    _require(hashlib.sha256(data).hexdigest() == ref.sha256, f"{ref.relative_path} does not match its sha256")
    return data


def read_elevation_source(repository: FilesystemProjectRepository, source: ElevationSource | NativeModelSource) -> VerifiedElevationSource:
    """Verify source bytes against their STEP receipt or native registration; read-only."""

    if isinstance(source, NativeModelSource):
        return _read_native_source(repository, source)
    if not isinstance(source, ElevationSource):
        raise TypeError("source must be ElevationSource or NativeModelSource")
    project_id = repository.load_manifest().project_id
    try:
        run = repository.load_run(source.run_id)
        receipt_ref = ProjectRecordRef(project_id, source.cad_receipt_relative_path, source.cad_receipt_sha256)
        _require(receipt_ref.record_kind == "seat-occt-execution", "the source CAD receipt is not a seat-occt-execution record")
        receipt = repository.load_json(receipt_ref)
    except ProjectRepositoryError as exc:
        raise DrawingElevationError(f"source run or CAD receipt cannot be read: {exc}") from exc
    try:
        _require(receipt["schema"] == SOURCE_RECEIPT_SCHEMA, "the source receipt is not an OcctExecutionReceipt@1")
        _require(receipt["status"] == "succeeded" and receipt["readback_verified"] is True,
                 "the source CAD execution did not succeed with a verified readback")
        identity = receipt["identity"]
        length_unit = identity["length_unit"]
        _require(length_unit in _UNITS, f"source length unit {length_unit!r} is not a CAD unit")
        _require(identity["up_axis"] == "Z-up", "the source STEP is not in the CAD Z-up frame")
        binding = identity["binding"]
        _require(binding["project_id"] == project_id and binding["run_id"] == source.run_id,
                 "the source receipt is bound to another project or run")
        _require(binding["base"] == run.base.to_dict(), "the source receipt's base is not the source run's base")
        program_digest = binding["program_digest"]
        stage_id = binding["stage_id"]
        exact = receipt["exact_artifact"]
        _require(exact["exact_brep"] is True, "the source artifact is not exact B-rep")
        _require(exact["sha256"].lower() == source.step_sha256, "the source STEP sha256 is not the one the receipt certifies")
        _require(exact["relative_path"] == PurePosixPath(source.step_relative_path).name,
                 "the source STEP file name is not the one the receipt certifies")
        deliveries = exact["deliveries"]
        physical = tuple(receipt["physical_object_ids"])
        _require(len(physical) == len(set(physical)) and set(physical) == set(deliveries) and bool(physical),
                 "the source receipt's physical object ids and deliveries disagree")
    except (KeyError, TypeError, AttributeError) as exc:
        raise DrawingElevationError(f"the source receipt does not carry the expected contract: {exc!r}") from exc
    step_path = repository.layout.run(source.run_id).workspaces / source.step_workspace / source.step_file_name
    try:
        step_bytes = step_path.read_bytes()
    except OSError as exc:
        raise DrawingElevationError(f"the source STEP cannot be read: {source.step_relative_path}") from exc
    _require(hashlib.sha256(step_bytes).hexdigest() == source.step_sha256,
             "the source STEP's bytes do not match its pinned sha256")
    try:
        entries = read_step(step_path, length_unit=length_unit)
    except OcctBackendError as exc:
        raise DrawingElevationError(f"the source STEP cannot be cold-read: {exc}") from exc
    names = [entry.name for entry in entries]
    _require(all(isinstance(name, str) and name for name in names), "the source STEP holds an unnamed shape")
    _require(len(names) == len(set(names)), "the source STEP holds duplicate shape names")
    missing = sorted(set(physical) - set(names))
    extra = sorted(set(names) - set(physical))
    _require(not missing and not extra,
             f"STEP names and receipt physical object ids differ: missing {missing[:5]}, extra {extra[:5]}")
    return VerifiedElevationSource(run=run, receipt=receipt, length_unit=length_unit, program_digest=program_digest,
                           stage_id=stage_id, physical_object_ids=tuple(sorted(physical)), entries=tuple(entries))


def _read_native_source(repository, source):
    from archflow.adapters.occt_backend import read_three_dm, measure_shape

    project_id = repository.load_manifest().project_id
    run = repository.load_run(source.run_id)
    _require(source.registration.project_id == source.artifact.project_id == project_id,
             "the native model belongs to another project")
    _require(source.registration.record_kind == "studio-model-asset" and
             PurePosixPath(source.registration.relative_path).parts[:3] == ("runs", run.run_id, "records"),
             "the native model must have a registration in its source run")
    payload = repository.load_json(source.registration)
    schema = payload.get("schema")
    external = (schema == "StudioExternalModelAsset@1" or
                (schema == "StudioModelAsset@1" and payload.get("representation") == "external"
                 and payload.get("modelSource") is None))
    _require(schema in {"StudioModelAsset@1", "StudioExternalModelAsset@1"} and payload.get("projectId") == project_id,
             "the native model registration is invalid")
    retained = payload.get("artifact", {})
    _require(all(retained.get(key) == getattr(source.artifact, key)
                 for key in ("relative_path", "sha256", "media_type")),
             "the native model differs from its registered original")
    bound = None if external else payload.get("modelSource")
    _require((not external and isinstance(bound, Mapping) and bound.get("runId") == run.run_id and
              bound.get("assetSha256") == source.artifact.sha256) or
             (external and payload.get("runId") == run.run_id and
              payload.get("assetSha256") == source.artifact.sha256 and
              run.run_id == "studio-model-" + source.artifact.sha256),
             "the native model registration has a different source binding")
    data = _artifact_bytes(repository, source.artifact)
    try:
        entries, unit = read_three_dm(data)
    except OcctBackendError as exc:
        raise DrawingElevationError(str(exc)) from exc
    _require(unit == payload.get("lengthUnit"), "native model units differ from its registration")
    ids = tuple(entry.name for entry in entries)
    _require(bool(ids) and len(set(ids)) == len(ids), "native model object identities are empty or duplicated")
    # Plain readback values for the common frame/section operations, not an execution receipt.
    measured = {entry.name: {"bbox": measure_shape(entry.shape).to_dict()["bbox"]} for entry in entries}
    facts = {"identity": {"length_unit": unit}, "physical_object_ids": ids, "readback": measured, "modelSource": bound}
    source_import = {key: deepcopy(payload[key]) for key in ("sourceArtifact", "sourceFileName", "conversion") if key in payload}
    if source_import:
        facts["sourceImport"] = source_import
    return VerifiedElevationSource(run, facts, unit, None, None, ids, entries)


def _drawing_run(repository: FilesystemProjectRepository, drawing_run_id: str, source_run: RunRef) -> RunRef:
    try:
        require_identifier(drawing_run_id, "drawing_run_id")
    except ValueError as exc:
        raise DrawingElevationError(str(exc)) from exc
    _require(drawing_run_id != source_run.run_id, "the drawing run must not be the source run")
    try:
        if repository.layout.run(drawing_run_id).manifest.exists():
            run = repository.load_run(drawing_run_id)
            _require(run.base == source_run.base,
                     f"drawing run {drawing_run_id} exists with another base; choose another run id")
            return run
        return repository.create_run(drawing_run_id, base=source_run.base)
    except ProjectRepositoryError as exc:
        raise DrawingElevationError(f"drawing run {drawing_run_id} cannot be used: {exc}") from exc


def _ref_dict(ref: ProjectArtifactRef) -> dict[str, str]:
    return {"artifact_id": ref.artifact_id, "relative_path": ref.relative_path,
            "sha256": ref.sha256, "media_type": ref.media_type}


def _artifact_ref(project_id: str, value: Mapping[str, Any]) -> ProjectArtifactRef:
    return ProjectArtifactRef(project_id, value["artifact_id"], value["relative_path"], value["sha256"], value["media_type"])


def _source_binding(source, verified):
    common = {"run_id": verified.run.run_id, "base": verified.run.base.to_dict(),
              "physical_object_ids": list(verified.physical_object_ids)}
    if isinstance(source, NativeModelSource):
        return {**common, "model": _ref_dict(source.artifact),
                "registration": source.registration.to_dict(),
                "object_identity": "3DM object GUID path including block instances",
                "geometry_quality": {entry.name: entry.geometry_quality for entry in verified.entries},
                **({"sourceImport": deepcopy(verified.receipt["sourceImport"])} if "sourceImport" in verified.receipt else {})}
    return {**common, "stage_id": verified.stage_id, "program_digest": verified.program_digest,
            "step": {"relative_path": source.step_relative_path, "sha256": source.step_sha256,
                     "media_type": STEP_MEDIA_TYPE},
            "cad_receipt": {"relative_path": source.cad_receipt_relative_path, "sha256": source.cad_receipt_sha256},
            "object_identity": "STEP shape name = CAD receipt physical object id"}


def _retain_projection(repository, *, source, verified, projection, view, name, drawing_run_id,
                       backend, head_before, projection_details=None, previous_revision_ref=None):
    """The one receipt/artifact boundary for elevation, cut-plan and section-perspective projections."""
    if isinstance(source, NativeModelSource) and verified.receipt.get("modelSource") is None:
        view = {**view, "sourceAsset": {"runId": source.run_id, "assetSha256": source.artifact.sha256}, "follow": "frozen"}
    run = _drawing_run(repository, drawing_run_id, verified.run)
    destination = PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run.run_id)
    try:
        svg_ref = repository.put_workspace_file(
            run=run, destination=destination, artifact_id=f"{name}-svg",
            workspace_relative_path=f"{DOCUMENTATION_WORKSPACE}/{name}.svg",
            media_type=SVG_MEDIA_TYPE, source=BytesIO(projection.svg),
        )
        png_ref = repository.put_workspace_file(
            run=run, destination=destination, artifact_id=f"{name}-png",
            workspace_relative_path=f"{DOCUMENTATION_WORKSPACE}/{name}.png",
            media_type=PNG_MEDIA_TYPE, source=BytesIO(projection.png),
        )
        payload = {
            "schema": DRAWING_PROJECTION_RECEIPT_SCHEMA,
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": run.base.to_dict(),
            "view": view,
            "unit": verified.length_unit,
            "source": _source_binding(source, verified),
            "projection": {
                "backend": backend,
                "algorithm": "HLRBRep_Algo exact hidden-line solve over every listed object, then per-object extraction",
                "object_count": len(verified.physical_object_ids),
                **projection.counts(),
                **(projection_details or {}),
            },
            "artifacts": {"svg": _ref_dict(svg_ref), "png": _ref_dict(png_ref)},
        }
        if previous_revision_ref is not None:
            payload["previousRevisionRef"] = previous_revision_ref
        receipt_ref = repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=DRAWING_PROJECTION_RECEIPT, payload=payload,
        )
    except ProjectRepositoryError as exc:
        raise DrawingElevationError(f"the drawing could not be retained in run {run.run_id}: {exc}") from exc
    drawing = read_model_axis_elevation(repository, receipt_ref)
    _require(drawing.svg == projection.svg and drawing.png == projection.png,
             "the retained drawing files read back differently from what was written")
    _require(repository.read_head() == head_before, "the project's published version changed while drawing")
    return drawing



def plan_dressing_anchors(receipt: Mapping) -> list[dict]:
    """Exact physical-object centres projected in the cut plan's CAD XY frame."""
    semantics = receipt.get("expected_semantics", {}).get("objects", {})
    return [{"objectId": name, "positionUv": [(receipt["readback"][name]["bbox"]["min"][i]
                + receipt["readback"][name]["bbox"]["max"][i]) / 2 for i in (0, 1)]}
            for name in receipt["physical_object_ids"] if name in receipt.get("readback", {}) and semantics.get(name, {}).get("visible", True)]


def resolve_plan_dressing(recipe: Mapping, receipt: Mapping) -> list[dict]:
    """Keep representation intent; missing exact anchors are never rebound."""
    from monkeydiagram.drawing_svg import dressing_assets
    assets = {asset["id"] for asset in dressing_assets()}
    anchors = {row["objectId"]: row["positionUv"] for row in plan_dressing_anchors(receipt)}
    crop = recipe["frame"]["crop_uv"]
    result, seen = [], set()
    for item in recipe.get("dressing", []):
        name = item["id"]
        require_identifier(name, "dressing id")
        _require(name not in seen and item["assetId"] in assets, "dressing needs unique ids and a supported SVG asset")
        seen.add(name)
        position = item["positionUv"]
        _require(len(position) == 2, "dressing positionUv needs two view coordinates")
        u, v = (_finite(value, "dressing coordinate") for value in position)
        size = _finite(item["size"], "dressing size")
        _require(0 < size <= 100000 and isinstance(item.get("flipped", False), bool), "invalid dressing size or flip")
        anchor = item.get("anchorObjectId")
        if anchor is not None and anchor not in anchors:
            result.append({**item, "status": "missing", "resolvedUv": None,
                           "detail": f"Anchor {anchor} is missing or unavailable; this object has not been moved to another anchor."})
            continue
        if anchor is not None:
            u += anchors[anchor][0]
            v += anchors[anchor][1]
        fits = crop[0] <= u-size/2 and u+size/2 <= crop[2] and crop[1] <= v-size/2 and v+size/2 <= crop[3]
        result.append({**item, "status": "resolved" if fits else "outside-view", "resolvedUv": [u, v],
                       "detail": None if fits else "The symbol falls outside the drawing crop; move it or enlarge the crop."})
    return result


def freeze_cut_plan(
    repository: FilesystemProjectRepository, *, source: ElevationSource | NativeModelSource, recipe: Mapping,
    drawing_run_id: str, dimensions: tuple[Mapping, ...] = (), previous_revision_ref: str | None = None,
) -> ElevationDrawing:
    """Retain a horizontal section and the exact below-cut visibility in the existing drawing envelope.

    ``recipe`` retains representation intent; ``dimensions`` contains the application's resolved
    source measurements or explicit unresolved statuses. Neither can change the source model.
    Coordinates are the verified STEP's CAD Z-up frame and length unit, never Program Y-up.
    """
    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    try:
        recipe = deepcopy(dict(recipe))
        _require(recipe.get("kind") == CUT_PLAN_KIND, "the view recipe must be a cut-plan")
        require_identifier(recipe["name"], "cut-plan name")
        frame = recipe["frame"]
        scale = frame["scale"]
        _require(isinstance(scale, str) and re.fullmatch(r"1:[1-9][0-9]*", scale) is not None,
                 "the cut-plan scale must be 1:N")
        view = ElevationView(
            name=recipe["name"], origin=frame["origin"], look=frame["look"], right=frame["right"], up=frame["up"],
            crop_uv=frame["crop_uv"], near_depth=frame["near_depth"], far_depth=frame["far_depth"],
            hidden_lines=frame["hidden_lines"], linear_deflection=frame["linear_deflection"],
            scale_denominator=int(scale[2:]),
        )
        _require(view.right == (1, 0, 0) and view.up == (0, 1, 0) and view.look == (0, 0, -1)
                 and view.near_depth == 0, "a cut-plan must look down CAD -Z from its horizontal cut plane")
        graphics = recipe["graphics"]
        _require(isinstance(graphics, Mapping), "cut-plan graphics must be a mapping")
        resolved = deepcopy(list(dimensions))
        ids = [row["id"] for row in resolved]
        _require(all(isinstance(name, str) and name for name in ids) and len(ids) == len(set(ids)),
                 "cut-plan dimensions must have unique ids")
        _require(all(row["status"] in {"resolved", "missing", "ambiguous", "outside-view", "unverified"} for row in resolved),
                 "cut-plan dimensions need explicit resolution statuses")
    except (KeyError, TypeError, ValueError) as exc:
        raise DrawingElevationError(f"the cut-plan recipe is invalid: {exc}") from exc
    head_before = repository.read_head()
    verified = read_elevation_source(repository, source)
    dressing = resolve_plan_dressing(recipe, verified.receipt)
    hidden = recipe.get("hiddenObjectIds", [])
    if not isinstance(hidden, (list, tuple)) or any(not isinstance(name, str) for name in hidden):
        raise DrawingElevationError("hiddenObjectIds must be a list of physical object ids")
    # Rebuilding keeps authored visibility intent even when its source object was removed.
    # The application validates newly authored selections; this retained recipe reports missing ones.
    unresolved_objects = sorted(set(hidden) - set(verified.physical_object_ids))
    semantics = verified.receipt.get("expected_semantics", {}).get("objects", {})
    source_hidden = {name for name, row in semantics.items() if row.get("visible") is False}
    # Native STEP also retains hidden inspection witnesses such as aperture volumes.
    # They are source evidence, not cut material or occluders in the drawing.
    excluded = set(hidden) | source_hidden
    selected = tuple(name for name in verified.physical_object_ids if name not in excluded)
    _require(bool(selected), "a cut-plan must retain at least one physical object")
    try:
        common = dict(object_ids=selected, origin=view.origin, right=view.right, up=view.up,
                      linear_deflection=view.linear_deflection)
        sections = section_occt_lines(verified.entries, **common)
        regions = section_occt_regions(verified.entries, **common)
        background = project_occt_lines(verified.entries, **common, depth_range=(0, view.far_depth))
        lines = background + sections
        svg = drawing_svg(lines, crop_uv=view.crop_uv, unit=verified.length_unit,
                          scale_denominator=view.scale_denominator, hidden_lines=view.hidden_lines,
                          title=recipe["name"], regions=regions, graphics=graphics, dimensions=resolved, dressing=dressing)
        projection = ElevationProjection(lines=lines, svg=svg, png=render_svg_png(svg))
    except (OcctBackendError, DrawingSvgError) as exc:
        raise DrawingElevationError(f"cut-plan {view.name}: {exc}") from exc
    return _retain_projection(
        repository, source=source, verified=verified, projection=projection, view=recipe, name=view.name,
        drawing_run_id=drawing_run_id, backend=backend_identity(), head_before=head_before,
        projection_details={
            "algorithm": "BRepAlgoAPI_Section on the cut plane; exact below-cut slab and global HLRBRep_Algo visibility",
            "selected_object_ids": list(selected), "section_polylines": len(sections),
            "section_regions": len(regions), "dimensions": resolved, "unresolvedObjectIds": unresolved_objects,
            **({"dressing": dressing} if "dressing" in recipe else {}),
        }, previous_revision_ref=previous_revision_ref,
    )


def freeze_model_axis_elevation(
    repository: FilesystemProjectRepository, *, source: ElevationSource | NativeModelSource, view: ElevationView, drawing_run_id: str,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None,
) -> ElevationDrawing:
    """Project retained STEP or native 3DM and retain SVG, PNG and receipt in the drawing run.

    Refuses before any write when the source does not verify, the frame is
    inconsistent, or the drawing run exists with another base.  A repeat
    with the same source and view writes the same bytes to the same paths
    (the repository accepts identical content) and returns the same refs.
    """

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(view, ElevationView):
        raise TypeError("view must be ElevationView")
    identity = {"view_recipe": {key: value for key, value in view.to_dict().items()
                                if key not in {"uv_definition", "depth_definition"}}}
    if isinstance(source, ElevationSource):
        identity["step_sha256"] = source.step_sha256

    def observe(event):
        operation_observer({**event, "details": {"input_identity": dict(identity), **event.get("details", {})}})

    observer = observe if operation_observer is not None else None
    with _observed_stage(observer, "drawing.load", parent_event_id=parent_event_id) as observation:
        head_before = repository.read_head()
        verified = read_elevation_source(repository, source)
        backend = backend_identity()
        identity.update(backend=backend["binding"], backend_version=backend["binding_version"])
        observation["input_object_ids"] = list(verified.physical_object_ids)
    projection = project_model_axis_elevation(
        verified.entries, object_ids=verified.physical_object_ids, view=view, unit=verified.length_unit,
        operation_observer=observer, parent_event_id=parent_event_id,
    )
    with _observed_stage(observer, "drawing.persist", parent_event_id=parent_event_id) as observation:
        drawing = _retain_projection(
            repository, source=source, verified=verified, projection=projection, view=view.to_dict(), name=view.name,
            drawing_run_id=drawing_run_id, backend=backend, head_before=head_before,
        )
        observation["output_refs"] = [drawing.receipt_ref.uri, drawing.svg_ref.uri, drawing.png_ref.uri]
    return drawing


# ---------------------------------------------------------------- section perspective

DEFAULT_SECTION_FOV_DEG = 55.0
DEFAULT_SECTION_EYE_HEIGHT_M = 1.6
DEFAULT_SECTION_GRAPHICS = {"cutLineMm": 0.5, "visibleLineMm": 0.25, "hatchSpacingMm": 0.5}
#: Metres per CAD length unit, for defaults stated in metres.
UNIT_METRES = {"meter": 1.0, "millimeter": 0.001, "inch": 0.0254, "foot": 0.3048}
_SECTION_MARGIN = 0.05


class SectionPerspectiveError(DrawingElevationError):
    """A section perspective refused by name: ``code`` says which rule failed; nothing was written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _refuse(code: str, message: str):
    raise SectionPerspectiveError(code, message)


def _numbers(value, count: int, label: str) -> tuple[float, ...]:
    """``count`` finite numbers, refused by name when one is not finite."""

    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != count
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
        _refuse("SECTION_REQUEST_INVALID", f"{label} must be {count} numbers")
    if any(not math.isfinite(v) for v in value):
        _refuse("SECTION_VALUE_NOT_FINITE", f"{label} holds a value that is not finite")
    return tuple(float(v) for v in value)


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _refuse("SECTION_REQUEST_INVALID", f"{label} must be a number")
    if not math.isfinite(value):
        _refuse("SECTION_VALUE_NOT_FINITE", f"{label} is not finite")
    return float(value)


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _normalized(vector) -> tuple[float, float, float]:
    length = math.hypot(*vector)
    return tuple(v / length for v in vector)


def _section_plane(section: Mapping[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """A checked canonical section's origin and unit normal, which points to the removed side."""

    if "line" in section:
        (x1, y1), (x2, y2) = section["line"]
        length = math.dist((x1, y1), (x2, y2))
        dx, dy = (x2 - x1) / length + 0.0, (y2 - y1) / length + 0.0
        # Keeping the left of walking the line removes its right: the normal points right.
        return (x1, y1, 0.0), ((dy, -dx + 0.0, 0.0) if section["keep"] == "left" else (-dy + 0.0, dx, 0.0))
    return tuple(section["origin"]), tuple(v + 0.0 for v in _normalized(section["normal"]))


@dataclass(frozen=True, slots=True)
class SectionPerspectiveView:
    """One section perspective request, checked before the model is read; the CAD Z-up frame and STEP unit.

    ``section`` is ``{"line": [[x1, y1], [x2, y2]], "keep": "left" | "right"}``, a
    vertical plane through a plan line keeping the side on the left or right
    of walking from its first point to its second, or ``{"origin": [x, y, z],
    "normal": [nx, ny, nz]}`` whose normal points from the kept side to the
    removed side.  The eye stands on the removed side; nothing there is drawn.

    ``camera`` is explicit, ``{"eye", "target", "up"?, "fovDeg"?}``, or the
    default one-point perspective, ``None`` or ``{"eyeHeight"?, "fovDeg"?,
    "up"?}``: the eye on the removed side ``eyeHeight`` (1.6 m) above the
    lowest cut point, centred on the cut, at the distance that fits the cut's
    width in the horizontal field of view (55 degrees), with the cut's centre
    as target.  ``up`` (+Z) sets the picture's up within the plane.  The
    picture plane is always the section plane, so the cut is true to scale at
    1:``scale_denominator`` and lines along the normal converge at the eye's
    foot, wherever the target is.  The target's image centres the frame,
    whose width is the field of view at the plane and whose height keeps the
    cut's proportions; the default frame is the cut with a 5% margin, and
    moving only the eye moves the vanishing point, not the frame.  ``depth``
    bounds what is kept behind the plane.
    """

    name: str
    section: Mapping[str, Any]
    camera: Mapping[str, Any] | None = None
    depth: float | None = None
    hidden_object_ids: tuple[str, ...] = ()
    scale_denominator: int = 100
    graphics: Mapping[str, Any] | None = None
    linear_deflection: float = 0.0001

    def __post_init__(self) -> None:
        try:
            require_identifier(self.name, "view name")
        except ValueError as exc:
            raise SectionPerspectiveError("SECTION_REQUEST_INVALID", str(exc)) from exc
        deflection = _number(self.linear_deflection, "linear_deflection")
        if deflection <= 0.0:
            _refuse("SECTION_REQUEST_INVALID", "linear_deflection must be positive")
        object.__setattr__(self, "linear_deflection", deflection)
        section = self.section
        if not isinstance(section, Mapping) or set(section) not in ({"line", "keep"}, {"origin", "normal"}):
            _refuse("SECTION_REQUEST_INVALID", "the section is either {line, keep} or {origin, normal}")
        if "line" in section:
            line = section["line"]
            if not isinstance(line, Sequence) or isinstance(line, (str, bytes)) or len(line) != 2:
                _refuse("SECTION_REQUEST_INVALID", "the section line must be two plan points [[x1, y1], [x2, y2]]")
            start, end = (_numbers(point, 2, "a section line point") for point in line)
            if section["keep"] not in ("left", "right"):
                _refuse("SECTION_REQUEST_INVALID", "keep must be left or right of walking along the section line")
            if math.dist(start, end) <= deflection:
                _refuse("SECTION_LINE_DEGENERATE", "the section line has no length; give two distinct plan points")
            canonical = {"line": [list(start), list(end)], "keep": section["keep"]}
        else:
            origin = _numbers(section["origin"], 3, "the section origin")
            raw = _numbers(section["normal"], 3, "the section normal")
            if math.hypot(*raw) <= _TOLERANCE:
                _refuse("SECTION_NORMAL_DEGENERATE", "the section normal has no length")
            canonical = {"origin": list(origin), "normal": list(raw)}
        object.__setattr__(self, "section", canonical)
        origin, normal = _section_plane(canonical)
        camera = {} if self.camera is None else self.camera
        if not isinstance(camera, Mapping) or not set(camera) <= {"eye", "target", "up", "fovDeg", "eyeHeight"}:
            _refuse("SECTION_REQUEST_INVALID", "the camera takes eye, target, up, fovDeg or eyeHeight")
        explicit = "eye" in camera or "target" in camera
        if explicit and not {"eye", "target"} <= set(camera):
            _refuse("SECTION_REQUEST_INVALID", "an explicit camera needs both eye and target")
        if explicit and "eyeHeight" in camera:
            _refuse("SECTION_REQUEST_INVALID", "eyeHeight places the default eye; an explicit camera states its eye")
        resolved: dict[str, Any] = {}
        for key in ("eye", "target", "up"):
            if key in camera:
                resolved[key] = list(_numbers(camera[key], 3, f"the camera {key}"))
        for key in ("fovDeg", "eyeHeight"):
            if key in camera:
                resolved[key] = _number(camera[key], f"the camera {key}")
        fov = resolved.get("fovDeg", DEFAULT_SECTION_FOV_DEG)
        if not 0.0 < fov < 180.0:
            _refuse("SECTION_CAMERA_DEGENERATE", "fovDeg must be between 0 and 180 degrees")
        up = tuple(resolved.get("up", (0.0, 0.0, 1.0)))
        in_plane = tuple(u - _dot(up, normal) * n for u, n in zip(up, normal))
        if math.hypot(*up) <= _TOLERANCE or math.hypot(*in_plane) <= 1e-6 * math.hypot(*up):
            _refuse("SECTION_CAMERA_DEGENERATE", "the camera up must not be parallel to the section normal; "
                                                 "give an up that lies across the section plane")
        if explicit:
            eye, target = resolved["eye"], resolved["target"]
            side = _dot([e - o for e, o in zip(eye, origin)], normal)
            if side < -deflection:
                _refuse("SECTION_EYE_ON_KEPT_SIDE", "the eye stands on the kept side of the section; "
                                                    "stand it on the removed side, where the normal points")
            if side <= deflection:
                _refuse("SECTION_EYE_ON_PLANE", "the eye stands on the section plane; move it onto the removed side")
            if math.dist(eye, target) <= deflection:
                _refuse("SECTION_CAMERA_DEGENERATE", "the eye and the target coincide")
            if _dot([t - e for t, e in zip(target, eye)], normal) >= 0.0:
                _refuse("SECTION_CAMERA_DEGENERATE", "the camera must look through the cut toward the kept side")
        object.__setattr__(self, "camera", resolved or None)
        if self.depth is not None:
            depth = _number(self.depth, "depth")
            if depth <= 0.0:
                _refuse("SECTION_DEPTH_INVALID", "depth must be a positive distance behind the section plane")
            object.__setattr__(self, "depth", depth)
        hidden = self.hidden_object_ids
        if (not isinstance(hidden, (list, tuple)) or any(not isinstance(name, str) or not name for name in hidden)
                or len(set(hidden)) != len(hidden)):
            _refuse("SECTION_REQUEST_INVALID", "hiddenObjectIds must be distinct physical object ids")
        object.__setattr__(self, "hidden_object_ids", tuple(sorted(hidden)))
        if (isinstance(self.scale_denominator, bool) or not isinstance(self.scale_denominator, int)
                or self.scale_denominator <= 0):
            _refuse("SECTION_REQUEST_INVALID", "scale_denominator must be a positive integer")
        graphics = dict(DEFAULT_SECTION_GRAPHICS)
        if self.graphics is not None:
            if not isinstance(self.graphics, Mapping) or not set(self.graphics) <= set(graphics):
                _refuse("SECTION_REQUEST_INVALID", "graphics takes cutLineMm, visibleLineMm and hatchSpacingMm")
            for key, value in self.graphics.items():
                graphics[key] = _number(value, key)
                if graphics[key] <= 0.0:
                    _refuse("SECTION_REQUEST_INVALID", f"{key} must be a positive paper millimetre value")
        object.__setattr__(self, "graphics", graphics)

    def plane(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """The section plane's origin and unit normal (toward the removed side)."""

        return _section_plane(self.section)

    def frame(self) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
        """The drawing frame on the plane: origin, right, up and the normal ``right x up``."""

        origin, normal = self.plane()
        up = tuple((self.camera or {}).get("up", (0.0, 0.0, 1.0)))
        up = _normalized(tuple(u - _dot(up, normal) * n for u, n in zip(up, normal)))
        # Adding 0.0 turns a cross product's negative zeros into zeros for the receipt.
        return (origin, tuple(v + 0.0 for v in _normalized(_cross(up, normal))),
                tuple(v + 0.0 for v in up), tuple(v + 0.0 for v in normal))

    def request(self) -> dict[str, Any]:
        """The canonical request: with the exact source it determines the drawing."""

        return {
            "name": self.name, "section": deepcopy(self.section), "camera": deepcopy(self.camera),
            "depth": self.depth, "hiddenObjectIds": list(self.hidden_object_ids),
            "scale": f"1:{self.scale_denominator}", "graphics": dict(self.graphics),
            "linear_deflection": self.linear_deflection,
        }


@dataclass(frozen=True, slots=True)
class SectionPerspectiveProjection:
    """The in-memory section perspective: the resolved view, the exact solve, and its SVG and PNG."""

    view: Mapping[str, Any]
    perspective: OcctSectionPerspective
    svg: bytes
    png: bytes

    @property
    def lines(self) -> tuple[OcctDrawingPolyline, ...]:
        return self.perspective.lines

    def counts(self) -> dict[str, Any]:
        visible = [line for line in self.lines if line.kind == "visible"]
        return {
            "visible_polylines": len(visible),
            "hidden_polylines": 0,
            "objects_with_visible_lines": len({line.object_id for line in visible}),
            "objects_with_hidden_lines": 0,
            "objects_drawn_in_svg": len(svg_objects(self.svg)),
        }

    def details(self, selected: Sequence[str]) -> dict[str, Any]:
        """What the receipt adds about the camera and the cut."""

        perspective = self.perspective
        return {
            "algorithm": ("BRepAlgoAPI_Common with an oriented box on the kept side, then one exact HLRBRep_Algo "
                          "perspective solve of the kept parts; BRepAlgoAPI_Section of the original solids for the cut"),
            "projector": ("HLRAlgo_Projector(gp_Ax2(foot, normal, right), focus) with the shapes located in the eye's "
                          "camera frame: the eye stands at foot + focus * normal, the picture plane is the section "
                          "plane (true to scale) and a point z behind it maps 1 / (1 + z / focus) toward the foot"),
            "principal_point_uv": list(perspective.principal_point),
            "focus": perspective.focus,
            "selected_object_ids": list(selected),
            "drawn_object_ids": list(perspective.drawn_object_ids),
            "cut_object_ids": list(perspective.cut_object_ids),
            "removed_object_ids": list(perspective.removed_object_ids),
            "section_polylines": sum(line.kind == "section" for line in perspective.lines),
            "section_regions": len(perspective.regions),
        }


def _perspective_image(point, frame, eye, focus) -> tuple[float, float]:
    """Where ``point`` lands in the picture, in drawing coordinates (the projector's own formula)."""

    origin, right, up, normal = frame
    foot = tuple(e - focus * n for e, n in zip(eye, normal))
    delta = [p - f for p, f in zip(point, foot)]
    shrink = 1.0 - _dot(delta, normal) / focus
    return (_dot([f - o for f, o in zip(foot, origin)], right) + _dot(delta, right) / shrink,
            _dot([f - o for f, o in zip(foot, origin)], up) + _dot(delta, up) / shrink)


def project_section_perspective(
    entries: Sequence[StepEntry], *, object_ids: Sequence[str], view: SectionPerspectiveView, unit: str,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None,
) -> SectionPerspectiveProjection:
    """Cut, solve and render one section perspective of the named shapes; writes nothing.

    The plane's section of the selected objects places the default camera
    and must exist: a plane that misses them is refused by name.  The
    resolved view (plane, frame, camera and crop) is returned with the
    drawing so the receipt records exactly what was drawn.
    """

    if not isinstance(view, SectionPerspectiveView):
        raise TypeError("view must be SectionPerspectiveView")
    if unit not in _UNITS:
        raise DrawingElevationError(f"unit {unit!r} is not a CAD length unit")
    frame = view.frame()
    origin, right, up, normal = frame
    try:
        with _observed_stage(operation_observer, "drawing.hlr", parent_event_id=parent_event_id,
                             details={"scope": "global_visibility", "input_object_ids": sorted(object_ids)}) as observation:
            cut = section_occt_lines(entries, object_ids=tuple(object_ids), origin=origin, right=right, up=up,
                                     linear_deflection=view.linear_deflection)
            points = [point for line in cut for point in line.points]
            if not points:
                _refuse("SECTION_PLANE_MISSES_MODEL", "the section plane meets none of the drawn objects; "
                                                      "move it through the model")
            u0, u1 = min(p[0] for p in points), max(p[0] for p in points)
            v0, v1 = min(p[1] for p in points), max(p[1] for p in points)
            margin = _SECTION_MARGIN * max(u1 - u0, v1 - v0)
            # The frame keeps the cut's proportions, margin included, whatever its width.
            aspect = (v1 - v0 + 2.0 * margin) / (u1 - u0 + 2.0 * margin)
            camera = dict(view.camera or {})
            fov = camera.get("fovDeg", DEFAULT_SECTION_FOV_DEG)
            half = math.tan(math.radians(fov) / 2.0)
            if "eye" in camera:
                placement, eye, target = "explicit", tuple(camera["eye"]), tuple(camera["target"])
            else:
                # One-point perspective: the eye's foot is centred on the cut at eye height, and the target is
                # the cut's centre, so the frame is the cut with its margin.
                placement = "default"
                height = camera.get("eyeHeight", DEFAULT_SECTION_EYE_HEIGHT_M / UNIT_METRES[unit])
                distance = ((u1 - u0) / 2.0 + margin) / half
                eye = tuple(o + (u0 + u1) / 2.0 * r + (v0 + height) * w + distance * n
                            for o, r, w, n in zip(origin, right, up, normal))
                target = tuple(o + (u0 + u1) / 2.0 * r + (v0 + v1) / 2.0 * w for o, r, w in zip(origin, right, up))
            perspective = project_occt_section_perspective(
                entries, object_ids=tuple(object_ids), origin=origin, right=right, up=up, eye=eye,
                linear_deflection=view.linear_deflection, depth=view.depth,
            )
            observation["emitted_object_ids"] = sorted({line.object_id for line in perspective.lines})
        width = 2.0 * perspective.focus * half
        centre = _perspective_image(target, frame, eye, perspective.focus)
        crop = (centre[0] - width / 2.0, centre[1] - width * aspect / 2.0,
                centre[0] + width / 2.0, centre[1] + width * aspect / 2.0)
        resolved = {
            "kind": SECTION_PERSPECTIVE_KIND,
            "name": view.name,
            "projection": "perspective",
            "request": view.request(),
            "section": {"origin": list(view.plane()[0]), "normal": list(normal),
                        "kept_side": "dot(point - origin, normal) <= 0", "depth": view.depth},
            "camera": {"placement": placement, "eye": list(eye), "target": list(target),
                       "up": list(camera.get("up", (0.0, 0.0, 1.0))), "fov_deg": fov},
            "origin": list(origin),
            "right": list(right),
            "up": list(up),
            "uv_definition": ("on the section plane u = dot(point - origin, right), v = dot(point - origin, up); "
                              "behind it, the point's perspective image from the eye in the same frame"),
            "picture_plane": "the section plane",
            "crop_uv": list(crop),
            "scale": f"1:{view.scale_denominator}",
            "scale_at": "the section plane",
            "hidden_lines": False,
            "linear_deflection": view.linear_deflection,
            "graphics": dict(view.graphics),
            "hiddenObjectIds": list(view.hidden_object_ids),
        }
        with _observed_stage(operation_observer, "drawing.svg", parent_event_id=parent_event_id,
                             details={"input_object_ids": sorted({line.object_id for line in perspective.lines})}) as observation:
            svg = drawing_svg(
                perspective.lines, crop_uv=crop, unit=unit, scale_denominator=view.scale_denominator,
                hidden_lines=False, title=view.name, regions=perspective.regions, graphics=view.graphics,
                projection=SECTION_PERSPECTIVE_KIND,
            )
            observation["emitted_object_ids"] = list(svg_objects(svg))
        with _observed_stage(operation_observer, "drawing.png", parent_event_id=parent_event_id):
            png = render_svg_png(svg)
    except SectionPerspectiveError:
        raise
    except (OcctBackendError, DrawingSvgError) as exc:
        raise DrawingElevationError(f"section perspective {view.name}: {exc}") from exc
    return SectionPerspectiveProjection(view=resolved, perspective=perspective, svg=svg, png=png)


def section_perspective_objects(verified: VerifiedElevationSource, hidden_object_ids: Sequence[str]) -> tuple[str, ...]:
    """The objects a section perspective cuts and draws: every physical object not hidden.

    Hidden inspection witnesses the source retains (aperture volumes) are
    evidence, not material, as in a cut plan.  An unknown hidden id or
    hiding everything is refused by name.
    """

    unknown = sorted(set(hidden_object_ids) - set(verified.physical_object_ids))
    if unknown:
        _refuse("DRAWING_OBJECT_UNKNOWN", "These physical objects are not in the selected model: " + ", ".join(unknown))
    semantics = verified.receipt.get("expected_semantics", {}).get("objects", {})
    excluded = set(hidden_object_ids) | {name for name, row in semantics.items() if row.get("visible") is False}
    selected = tuple(name for name in verified.physical_object_ids if name not in excluded)
    if not selected:
        _refuse("DRAWING_EMPTY", "Keep at least one physical object in the section perspective.")
    return selected


def freeze_section_perspective(
    repository: FilesystemProjectRepository, *, source: ElevationSource | NativeModelSource, view: SectionPerspectiveView,
    drawing_run_id: str, operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None,
) -> ElevationDrawing:
    """Draw a section perspective of verified STEP or native geometry and retain SVG, PNG and receipt.

    Mirrors ``freeze_model_axis_elevation``: the request was checked when the
    view was made, the source is verified, nothing is written until the
    projection and both renderings succeeded, and ``_retain_projection``
    retains the receipt with the exact plane and camera.  A repeat with the
    same source and view writes the same bytes and returns the same refs.
    """

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(view, SectionPerspectiveView):
        raise TypeError("view must be SectionPerspectiveView")
    identity = {"view_recipe": view.request()}
    if isinstance(source, ElevationSource):
        identity["step_sha256"] = source.step_sha256
    elif isinstance(source, NativeModelSource):
        identity["model_sha256"] = source.artifact.sha256

    def observe(event):
        operation_observer({**event, "details": {"input_identity": dict(identity), **event.get("details", {})}})

    observer = observe if operation_observer is not None else None
    with _observed_stage(observer, "drawing.load", parent_event_id=parent_event_id) as observation:
        head_before = repository.read_head()
        verified = read_elevation_source(repository, source)
        backend = backend_identity()
        identity.update(backend=backend["binding"], backend_version=backend["binding_version"])
        selected = section_perspective_objects(verified, view.hidden_object_ids)
        observation["input_object_ids"] = list(selected)
    projection = project_section_perspective(
        verified.entries, object_ids=selected, view=view, unit=verified.length_unit,
        operation_observer=observer, parent_event_id=parent_event_id,
    )
    with _observed_stage(observer, "drawing.persist", parent_event_id=parent_event_id) as observation:
        drawing = _retain_projection(
            repository, source=source, verified=verified, projection=projection, view=dict(projection.view),
            name=view.name, drawing_run_id=drawing_run_id, backend=backend, head_before=head_before,
            projection_details=projection.details(selected),
        )
        observation["output_refs"] = [drawing.receipt_ref.uri, drawing.svg_ref.uri, drawing.png_ref.uri]
    return drawing


def read_model_axis_elevation(repository: FilesystemProjectRepository, receipt_ref: ProjectRecordRef) -> ElevationDrawing:
    """Cold-read one retained drawing: receipt, SVG and PNG, each verified against its sha256."""

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(receipt_ref, ProjectRecordRef):
        raise TypeError("receipt_ref must be ProjectRecordRef")
    try:
        _require(receipt_ref.record_kind == DRAWING_PROJECTION_RECEIPT, "the record is not a drawing-projection-receipt")
        receipt = repository.load_json(receipt_ref)
        _require(receipt.get("schema") == DRAWING_PROJECTION_RECEIPT_SCHEMA, "the record is not a DrawingProjectionReceipt@1")
        _require(isinstance(receipt.get("view"), Mapping) and receipt["view"].get("kind") in DRAWING_VIEW_KINDS,
                 f"the drawing receipt's view is none of {', '.join(DRAWING_VIEW_KINDS)}")
        run = repository.load_run(receipt["run_id"])
        _require(run.base.to_dict() == receipt["base"] == receipt["source"]["base"],
                 "the drawing run, its receipt and the source disagree on the base")
        svg_ref = _artifact_ref(run.project_id, receipt["artifacts"]["svg"])
        png_ref = _artifact_ref(run.project_id, receipt["artifacts"]["png"])
        for ref in (svg_ref, png_ref):
            _require(PurePosixPath(ref.relative_path).parts[:4] == ("runs", run.run_id, "workspaces", DOCUMENTATION_WORKSPACE),
                     f"{ref.relative_path} is not in the drawing run's documentation workspace")
    except (ProjectRepositoryError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DrawingElevationError):
            raise
        raise DrawingElevationError(f"the drawing receipt cannot be read: {exc!r}") from exc
    return ElevationDrawing(
        run=run, receipt_ref=receipt_ref, receipt=receipt, svg_ref=svg_ref, png_ref=png_ref,
        svg=_artifact_bytes(repository, svg_ref), png=_artifact_bytes(repository, png_ref),
    )


def list_model_axis_elevations(repository: FilesystemProjectRepository, drawing_run_id: str) -> tuple[ProjectRecordRef, ...]:
    """The drawing receipts one run retains, of every view kind, verified as the repository lists them."""

    try:
        run = repository.load_run(drawing_run_id)
        refs = repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id))
    except (ProjectRepositoryError, ValueError) as exc:
        raise DrawingElevationError(f"drawing run {drawing_run_id} cannot be listed: {exc}") from exc
    return tuple(ref for ref in refs if ref.record_kind == DRAWING_PROJECTION_RECEIPT)


__all__ = [
    "CUT_PLAN_KIND",
    "DEFAULT_SECTION_EYE_HEIGHT_M",
    "DEFAULT_SECTION_FOV_DEG",
    "DOCUMENTATION_WORKSPACE",
    "DRAWING_PROJECTION_RECEIPT_SCHEMA",
    "DRAWING_VIEW_KINDS",
    "ELEVATION_KIND",
    "SECTION_PERSPECTIVE_KIND",
    "UNIT_METRES",
    "DrawingElevationError",
    "ElevationDrawing",
    "ElevationProjection",
    "ElevationSource",
    "NativeModelSource",
    "ElevationView",
    "SectionPerspectiveError",
    "SectionPerspectiveProjection",
    "SectionPerspectiveView",
    "freeze_model_axis_elevation",
    "freeze_cut_plan",
    "freeze_section_perspective",
    "plan_dressing_anchors",
    "resolve_plan_dressing",
    "list_model_axis_elevations",
    "project_model_axis_elevation",
    "project_section_perspective",
    "read_model_axis_elevation",
    "section_perspective_objects",
]


# A drawing receipt names the canonical version its run was based on, and the
# base of the verified CAD run its geometry was projected from. It derives
# nothing from either.
VERSION_REF_POINTERS = {"DrawingProjectionReceipt@1": ("/base", "/source/base")}

_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("DrawingProjectionReceipt@1", ())
