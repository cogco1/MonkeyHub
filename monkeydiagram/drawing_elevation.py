"""One model-axis orthographic elevation of a retained exact STEP, frozen through P036.

The A0 drawing slice of ``docs/DRAWING_MODULE_ARCHITECTURE_PLAN.md``, as far
as it is real: a source run's exact STEP (certified by its retained
``OcctExecutionReceipt@1``) is cold-read, every named shape takes part in
one exact hidden-line solve for a frame stated along the model axes
(``adapters.cad_execution.project_occt_lines``), the visible (and, on
request, hidden) polylines are cropped and serialised as one deterministic
SVG whose every polyline names its source physical object, a PNG is
rendered from that SVG (``monkeydiagram.drawing_svg``), and the two files plus
one ``DrawingProjectionReceipt@1`` are retained in a *drawing run* whose
base is the source run's base.

What this module decides and nothing else:

- the source is trusted only through its retained receipt: STEP SHA, CAD
  receipt SHA, run, base, unit and Z-up axis, and the object ids the STEP's
  names must match one for one;
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
values and mints nothing.  There is no view record, job, runner, second
storage or update mechanism here; plans, sections and axonometrics are not
implemented.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
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
    read_step,
)
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

DRAWING_PROJECTION_RECEIPT_SCHEMA = "DrawingProjectionReceipt@1"
SOURCE_RECEIPT_SCHEMA = "OcctExecutionReceipt@1"
ELEVATION_KIND = "model-axis-elevation"
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
    program_digest: str
    stage_id: str
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


def read_elevation_source(repository: FilesystemProjectRepository, source: ElevationSource) -> VerifiedElevationSource:
    """The source receipt, STEP and names, checked against each other; read-only."""

    if not isinstance(source, ElevationSource):
        raise TypeError("source must be ElevationSource")
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


def freeze_model_axis_elevation(
    repository: FilesystemProjectRepository, *, source: ElevationSource, view: ElevationView, drawing_run_id: str,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    parent_event_id: str | None = None,
) -> ElevationDrawing:
    """Project one elevation of the source STEP and retain SVG, PNG and receipt in the drawing run.

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
        run = _drawing_run(repository, drawing_run_id, verified.run)
        destination = PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run.run_id)
        try:
            svg_ref = repository.put_workspace_file(
                run=run, destination=destination, artifact_id=f"{view.name}-svg",
                workspace_relative_path=f"{DOCUMENTATION_WORKSPACE}/{view.name}.svg",
                media_type=SVG_MEDIA_TYPE, source=BytesIO(projection.svg),
            )
            png_ref = repository.put_workspace_file(
                run=run, destination=destination, artifact_id=f"{view.name}-png",
                workspace_relative_path=f"{DOCUMENTATION_WORKSPACE}/{view.name}.png",
                media_type=PNG_MEDIA_TYPE, source=BytesIO(projection.png),
            )
            payload = {
                "schema": DRAWING_PROJECTION_RECEIPT_SCHEMA,
                "project_id": run.project_id,
                "run_id": run.run_id,
                "base": run.base.to_dict(),
                "view": view.to_dict(),
                "unit": verified.length_unit,
                "source": {
                    "run_id": verified.run.run_id,
                    "base": verified.run.base.to_dict(),
                    "stage_id": verified.stage_id,
                    "program_digest": verified.program_digest,
                    "step": {"relative_path": source.step_relative_path, "sha256": source.step_sha256,
                             "media_type": STEP_MEDIA_TYPE},
                    "cad_receipt": {"relative_path": source.cad_receipt_relative_path,
                                    "sha256": source.cad_receipt_sha256},
                    "object_identity": "STEP shape name = CAD receipt physical object id",
                    "physical_object_ids": list(verified.physical_object_ids),
                },
                "projection": {
                    "backend": backend,
                    "algorithm": "HLRBRep_Algo exact hidden-line solve over every listed object, then per-object extraction",
                    "object_count": len(verified.physical_object_ids),
                    **projection.counts(),
                },
                "artifacts": {"svg": _ref_dict(svg_ref), "png": _ref_dict(png_ref)},
            }
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
    """The drawing receipts one run retains, verified as the repository lists them."""

    try:
        run = repository.load_run(drawing_run_id)
        refs = repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id))
    except (ProjectRepositoryError, ValueError) as exc:
        raise DrawingElevationError(f"drawing run {drawing_run_id} cannot be listed: {exc}") from exc
    return tuple(ref for ref in refs if ref.record_kind == DRAWING_PROJECTION_RECEIPT)


__all__ = [
    "DOCUMENTATION_WORKSPACE",
    "DRAWING_PROJECTION_RECEIPT_SCHEMA",
    "ELEVATION_KIND",
    "DrawingElevationError",
    "ElevationDrawing",
    "ElevationProjection",
    "ElevationSource",
    "ElevationView",
    "freeze_model_axis_elevation",
    "list_model_axis_elevations",
    "project_model_axis_elevation",
    "read_model_axis_elevation",
]
