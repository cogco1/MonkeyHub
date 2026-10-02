"""The retained model a drawing is derived from, verified before anything is drawn.

A source run's exact STEP (certified by its retained ``OcctExecutionReceipt@1``)
or a registered 3DM is cold-read here. Native 3DM retains object GUID paths and
explicit exact/faceted/approximate geometry quality; it does not acquire a
compiled-program or STEP receipt.

- STEP identity is checked against its CAD receipt, run, base, unit and
  Z-up axis, and its names match the certified object ids one for one;
- native models instead verify their registered bytes, run and units,
  preserving any original import identity and conversion warnings.

Nothing here writes: ``projection.views`` draws the verified shapes and
``drawing_runs`` retains the drawing. ``DrawingElevationError`` is the refusal
all three raise before anything is written.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from monkeycad.backends.occt.errors import OcctBackendError
from monkeycad.backends.occt.step import StepEntry, read_step
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, RunRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError

SOURCE_RECEIPT_SCHEMA = "OcctExecutionReceipt@1"
STEP_MEDIA_TYPE = "model/step"
_STEP_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,159}\.step$")
#: CAD length units accepted by retained drawing sources and views.
DRAWING_LENGTH_UNITS = ("meter", "millimeter", "inch", "foot")


class DrawingElevationError(ValueError):
    """The source, the frame or the drawing run cannot be used as asked; nothing was written."""


def object_semantics(receipt: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Each physical object's component and material, as its CAD receipt's expected semantics state them.

    They are the ``archflow:component`` and ``archflow:material`` user text
    the CAD program gave the object; an object with neither is left out.  A
    native model has no expected semantics, so its drawing names objects only.
    """

    semantics = {}
    for object_id, row in (receipt.get("expected_semantics") or {}).get("objects", {}).items():
        text = row.get("user_text") if isinstance(row, Mapping) else None
        if not isinstance(text, Mapping):
            continue
        values = {key: text.get(f"archflow:{key}") for key in ("component", "material")}
        values = {key: value for key, value in values.items() if isinstance(value, str) and value}
        if values:
            semantics[object_id] = values
    return semantics


def inspection_witness_ids(receipt: Mapping[str, Any]) -> frozenset[str]:
    """Objects the source saved hidden as inspection evidence: aperture volumes, retained voids.

    They are source evidence, not material or occluders, in every drawing.
    """

    objects = (receipt.get("expected_semantics") or {}).get("objects", {})
    return frozenset(name for name, row in objects.items() if isinstance(row, Mapping) and row.get("visible") is False)


def current_object_id(name: str, available) -> str:
    """A retained object reference read against a newer model (#419).

    A wall delivered ``obj-<wall>-cut`` once it had an opening and now always
    delivers ``obj-<wall>``. A retained ``obj-<X>-cut`` that the model lacks
    therefore reads as ``obj-<X>`` when the model has it. Every other name
    stays as written; nothing is rebound to a nearby object.
    """

    if name not in available and name.endswith("-cut") and name[: -len("-cut")] in available:
        return name[: -len("-cut")]
    return name


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
class VerifiedElevationSource:
    run: RunRef
    receipt: Mapping[str, Any]
    length_unit: str
    program_digest: str | None
    stage_id: str | None
    physical_object_ids: tuple[str, ...]
    entries: tuple[StepEntry, ...]


def require_drawing(condition: bool, message: str) -> None:
    """Refuse an invalid source or retained drawing with the shared drawing error."""

    if not condition:
        raise DrawingElevationError(message)


def read_artifact_bytes(repository: FilesystemProjectRepository, ref: ProjectArtifactRef) -> bytes:
    """Read a retained artifact only when its bytes match the pinned SHA-256."""

    try:
        data = repository.layout.resolve_record(ref).read_bytes()
    except (OSError, ValueError) as exc:
        raise DrawingElevationError(f"cannot read {ref.relative_path}: {exc}") from exc
    require_drawing(hashlib.sha256(data).hexdigest() == ref.sha256, f"{ref.relative_path} does not match its sha256")
    return data


def read_elevation_source(repository: FilesystemProjectRepository, source: ElevationSource | NativeModelSource) -> VerifiedElevationSource:
    """Verify source bytes against their STEP receipt or native registration; read-only."""

    if isinstance(source, NativeModelSource):
        return read_native_source(repository, source)
    if not isinstance(source, ElevationSource):
        raise TypeError("source must be ElevationSource or NativeModelSource")
    project_id = repository.load_manifest().project_id
    try:
        run = repository.load_run(source.run_id)
        receipt_ref = ProjectRecordRef(project_id, source.cad_receipt_relative_path, source.cad_receipt_sha256)
        require_drawing(receipt_ref.record_kind == "seat-occt-execution", "the source CAD receipt is not a seat-occt-execution record")
        receipt = repository.load_json(receipt_ref)
    except ProjectRepositoryError as exc:
        raise DrawingElevationError(f"source run or CAD receipt cannot be read: {exc}") from exc
    try:
        require_drawing(receipt["schema"] == SOURCE_RECEIPT_SCHEMA, "the source receipt is not an OcctExecutionReceipt@1")
        require_drawing(receipt["status"] == "succeeded" and receipt["readback_verified"] is True,
                 "the source CAD execution did not succeed with a verified readback")
        identity = receipt["identity"]
        length_unit = identity["length_unit"]
        require_drawing(length_unit in DRAWING_LENGTH_UNITS, f"source length unit {length_unit!r} is not a CAD unit")
        require_drawing(identity["up_axis"] == "Z-up", "the source STEP is not in the CAD Z-up frame")
        binding = identity["binding"]
        require_drawing(binding["project_id"] == project_id and binding["run_id"] == source.run_id,
                 "the source receipt is bound to another project or run")
        require_drawing(binding["base"] == run.base.to_dict(), "the source receipt's base is not the source run's base")
        program_digest = binding["program_digest"]
        stage_id = binding["stage_id"]
        exact = receipt["exact_artifact"]
        require_drawing(exact["exact_brep"] is True, "the source artifact is not exact B-rep")
        require_drawing(exact["sha256"].lower() == source.step_sha256, "the source STEP sha256 is not the one the receipt certifies")
        require_drawing(exact["relative_path"] == PurePosixPath(source.step_relative_path).name,
                 "the source STEP file name is not the one the receipt certifies")
        deliveries = exact["deliveries"]
        physical = tuple(receipt["physical_object_ids"])
        require_drawing(len(physical) == len(set(physical)) and set(physical) == set(deliveries) and bool(physical),
                 "the source receipt's physical object ids and deliveries disagree")
    except (KeyError, TypeError, AttributeError) as exc:
        raise DrawingElevationError(f"the source receipt does not carry the expected contract: {exc!r}") from exc
    step_path = repository.layout.run(source.run_id).workspaces / source.step_workspace / source.step_file_name
    try:
        step_bytes = step_path.read_bytes()
    except OSError as exc:
        raise DrawingElevationError(f"the source STEP cannot be read: {source.step_relative_path}") from exc
    require_drawing(hashlib.sha256(step_bytes).hexdigest() == source.step_sha256,
             "the source STEP's bytes do not match its pinned sha256")
    try:
        entries = read_step(step_path, length_unit=length_unit)
    except OcctBackendError as exc:
        raise DrawingElevationError(f"the source STEP cannot be cold-read: {exc}") from exc
    names = [entry.name for entry in entries]
    require_drawing(all(isinstance(name, str) and name for name in names), "the source STEP holds an unnamed shape")
    require_drawing(len(names) == len(set(names)), "the source STEP holds duplicate shape names")
    missing = sorted(set(physical) - set(names))
    extra = sorted(set(names) - set(physical))
    require_drawing(not missing and not extra,
             f"STEP names and receipt physical object ids differ: missing {missing[:5]}, extra {extra[:5]}")
    return VerifiedElevationSource(run=run, receipt=receipt, length_unit=length_unit, program_digest=program_digest,
                           stage_id=stage_id, physical_object_ids=tuple(sorted(physical)), entries=tuple(entries))


def read_native_source(repository: FilesystemProjectRepository, source: NativeModelSource) -> VerifiedElevationSource:
    """Verify a registered native model and read its shapes without STEP provenance."""

    from monkeycad.backends.occt.measure import measure_shape
    from monkeycad.backends.occt.native_models import read_three_dm

    project_id = repository.load_manifest().project_id
    run = repository.load_run(source.run_id)
    require_drawing(source.registration.project_id == source.artifact.project_id == project_id,
             "the native model belongs to another project")
    require_drawing(source.registration.record_kind == "studio-model-asset" and
             PurePosixPath(source.registration.relative_path).parts[:3] == ("runs", run.run_id, "records"),
             "the native model must have a registration in its source run")
    payload = repository.load_json(source.registration)
    schema = payload.get("schema")
    external = (schema == "StudioExternalModelAsset@1" or
                (schema == "StudioModelAsset@1" and payload.get("representation") == "external"
                 and payload.get("modelSource") is None))
    require_drawing(schema in {"StudioModelAsset@1", "StudioExternalModelAsset@1"} and payload.get("projectId") == project_id,
             "the native model registration is invalid")
    retained = payload.get("artifact", {})
    require_drawing(all(retained.get(key) == getattr(source.artifact, key)
                 for key in ("relative_path", "sha256", "media_type")),
             "the native model differs from its registered original")
    bound = None if external else payload.get("modelSource")
    require_drawing((not external and isinstance(bound, Mapping) and bound.get("runId") == run.run_id and
              bound.get("assetSha256") == source.artifact.sha256) or
             (external and payload.get("runId") == run.run_id and
              payload.get("assetSha256") == source.artifact.sha256 and
              run.run_id == "studio-model-" + source.artifact.sha256),
             "the native model registration has a different source binding")
    data = read_artifact_bytes(repository, source.artifact)
    try:
        entries, unit = read_three_dm(data)
    except OcctBackendError as exc:
        raise DrawingElevationError(str(exc)) from exc
    require_drawing(unit == payload.get("lengthUnit"), "native model units differ from its registration")
    ids = tuple(entry.name for entry in entries)
    require_drawing(bool(ids) and len(set(ids)) == len(ids), "native model object identities are empty or duplicated")
    # Plain readback values for the common frame/section operations, not an execution receipt.
    measured = {entry.name: {"bbox": measure_shape(entry.shape).to_dict()["bbox"]} for entry in entries}
    facts = {"identity": {"length_unit": unit}, "physical_object_ids": ids, "readback": measured, "modelSource": bound}
    source_import = {key: deepcopy(payload[key]) for key in ("sourceArtifact", "sourceFileName", "conversion") if key in payload}
    if source_import:
        facts["sourceImport"] = source_import
    return VerifiedElevationSource(run, facts, unit, None, None, ids, entries)


__all__ = [
    "DRAWING_LENGTH_UNITS",
    "DrawingElevationError",
    "ElevationSource",
    "NativeModelSource",
    "VerifiedElevationSource",
    "current_object_id",
    "inspection_witness_ids",
    "object_semantics",
    "read_artifact_bytes",
    "read_elevation_source",
    "read_native_source",
    "require_drawing",
]
