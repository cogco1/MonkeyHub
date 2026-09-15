"""Fail-closed Rhino externalization for exact neutral geometry programs.

The adapter writes only a translated script into a caller-supplied speculative
workspace.  It has no project-repository or canonical-write authority.  A
successful process exit is never sufficient: the saved ``.3dm`` must be read
back independently and match the exact P036 program binding, complete physical
denominator, native semantics, instance counts, and expected bounds.
"""

from __future__ import annotations

import hashlib
import importlib
import base64
import json
import math
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Mapping, Sequence

from archflow.adapters.cad_patch import CadPatchError, build_patch_prelude, select_patch_operations
from archflow.adapters.cad_program import (
    LONG_PATH_HELPER_SOURCE,
    CadTranslationError,
    _params,
    _physical_ids,
    _resolved_layer_colors,
    expected_object_bounds,
    expected_object_semantics,
    lift_to_base_level,
    translate_step_import_to_rhino_python,
    translate_to_rhino_python,
)
from archflow.adapters.occt_backend import (
    _observe_operation as _observe_occt_operation,
    _polyline_geometry,
    CLOSED_SOLID,
    CURVE,
    OPEN_SURFACE,
    OcctBackendError,
    OcctCapabilityError,
    OcctDrawingPolyline,
    OcctDrawingRegion,
    OcctUnavailableError,
    PreviewMaterial,
    PreviewObject,
    StepEntry,
    StepObject,
    backend_identity,
    build_program_shapes,
    declared_delivery,
    measure_shape,
    measure_occt_solid_pairs,
    project_occt_lines,
    read_step,
    section_occt_lines,
    section_occt_regions,
    write_preview_three_dm,
    write_step,
)
from archflow.adapters.three_dm_inspector import (
    ThreeDmInspection,
    ThreeDmInspectionError,
    inspect_three_dm,
)
from archflow.project.refs import BranchRef, ProjectRecordRef, require_identifier
from archflow.state.geometry_program import CompiledGeometryProgram
from archflow.state.geometry_program import AssemblyRole, require_sha256
from archflow.contracts.canonical import canonical_digest
from archflow.project.version_refs import register as _register_version_refs


_MAX_PROCESS_TEXT = 2_000
# The ``export_path`` provenance of an export an architect asked for rather
# than one that realizes a seat. The runner's reuse and patch lookups read it
# to leave these alone: a work model is a delivery, never a seat's own output.
WORK_MODEL_EXPORT_PATH = "work-model"
_UNIT_TO_RHINO = {
    "millimeter": ("Millimeters", "Millimeters"),
    "meter": ("Meters", "Meters"),
    "inch": ("Inches", "Inches"),
    "foot": ("Feet", "Feet"),
}
_RESERVED_PROVENANCE = frozenset(
    {
        "project_id",
        "run_id",
        "branch",
        "branch_epoch",
        "stage_id",
        "program_digest",
        "program_record_uri",
        "program_record_sha256",
        "base_version",
        "base_state_sha256",
        "design_state_digest",
        "predecessor_program_digest",
        "length_unit",
        "up_axis",
        "export_schema",
    }
)


class CadExecutionError(ValueError):
    """A Rhino export request is unsafe or under-specified."""


class CadExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CadProgramBinding:
    """Exact P036 record/branch/stage identity for one compiled program."""

    program_ref: ProjectRecordRef
    branch: BranchRef
    stage_id: str
    program_digest: str
    design_state_digest: str
    predecessor_program_digest: str | None

    # Retained receipts bind this serialization; the historical name stays on disk.
    SCHEMA = "RhinoCadProgramBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.program_ref, ProjectRecordRef):
            raise TypeError("program_ref must be ProjectRecordRef")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        require_identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "program_digest",
            require_sha256(self.program_digest, "program_digest"),
        )
        object.__setattr__(
            self,
            "design_state_digest",
            require_sha256(self.design_state_digest, "design_state_digest"),
        )
        if self.predecessor_program_digest is not None:
            object.__setattr__(
                self,
                "predecessor_program_digest",
                require_sha256(
                    self.predecessor_program_digest,
                    "predecessor_program_digest",
                ),
            )
        project_id = self.branch.run.project_id
        run_id = self.branch.run.run_id
        if self.program_ref.project_id != project_id:
            raise CadExecutionError("program record and branch cross projects")
        if self.program_ref.media_type != "application/json":
            raise CadExecutionError(
                "program record must be an application/json P036 record"
            )
        expected_path = (
            f"runs/{run_id}/branches/{self.branch.branch_id}/records/"
            f"{self.stage_id}-geometry-program-{self.program_ref.sha256}.json"
        )
        if self.program_ref.relative_path != expected_path:
            raise CadExecutionError(
                "program record path does not match the exact run/branch/stage/SHA"
            )
        if self.branch.run.base.state_sha256 is None:
            raise CadExecutionError("program binding requires a digest-bound base")

    @property
    def project_id(self) -> str:
        return self.branch.run.project_id

    @property
    def run_id(self) -> str:
        return self.branch.run.run_id

    def bind_program(self, program: CompiledGeometryProgram) -> None:
        """Mechanically reject any program outside this exact P036 identity."""

        if not isinstance(program, CompiledGeometryProgram):
            raise TypeError("program must be CompiledGeometryProgram")
        proposal = program.proposal
        if program.program_digest != self.program_digest:
            raise CadExecutionError("compiled program digest differs from binding")
        if proposal.project_id != self.project_id or proposal.run_id != self.run_id:
            raise CadExecutionError("compiled program crossed project/run binding")
        if proposal.base != self.branch.run.base:
            raise CadExecutionError("compiled program crossed canonical base")
        if proposal.design_state_digest != self.design_state_digest:
            raise CadExecutionError("compiled program crossed design-state binding")
        if proposal.predecessor_program_digest != self.predecessor_program_digest:
            raise CadExecutionError("compiled program crossed predecessor binding")

    def to_dict(self) -> dict[str, object]:
        base = self.branch.run.base
        return {
            "schema": self.SCHEMA,
            "program_ref": {
                "project_id": self.program_ref.project_id,
                "relative_path": self.program_ref.relative_path,
                "sha256": self.program_ref.sha256,
                "media_type": self.program_ref.media_type,
                "uri": self.program_ref.uri,
            },
            "project_id": self.project_id,
            "run_id": self.run_id,
            "branch_id": self.branch.branch_id,
            "branch_epoch": self.branch.epoch,
            "stage_id": self.stage_id,
            "program_digest": self.program_digest,
            "base": {
                "project_id": base.project_id,
                "version": base.version,
                "state_sha256": base.require_digest(),
            },
            "design_state_digest": self.design_state_digest,
            "predecessor_program_digest": self.predecessor_program_digest,
        }


# Compatibility for existing imports and retained receipt construction.
RhinoCadProgramBinding = CadProgramBinding


@dataclass(frozen=True, slots=True)
class RhinoCadExportIdentity:
    """Stable execution identity derived only from the exact program binding."""

    binding: RhinoCadProgramBinding
    length_unit: str
    up_axis: str = "Z-up"

    SCHEMA = "RhinoCadExportIdentity@2"

    def __post_init__(self) -> None:
        if not isinstance(self.binding, RhinoCadProgramBinding):
            raise TypeError("binding must be RhinoCadProgramBinding")
        if self.length_unit not in _UNIT_TO_RHINO:
            raise CadExecutionError(
                "length_unit must be millimeter, meter, inch, or foot"
            )
        if self.up_axis != "Z-up":
            raise CadExecutionError("Rhino CAD externalization requires Z-up")

    @property
    def project_id(self) -> str:
        return self.binding.project_id

    @property
    def run_id(self) -> str:
        return self.binding.run_id

    @property
    def program_digest(self) -> str:
        return self.binding.program_digest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding": self.binding.to_dict(),
            "length_unit": self.length_unit,
            "up_axis": self.up_axis,
        }


@dataclass(frozen=True, slots=True)
class RhinoCadExportPlan:
    """Prepared script and complete deterministic readback denominator."""

    identity: RhinoCadExportIdentity
    workspace: Path
    script_path: Path
    model_path: Path
    completion_marker_path: Path
    host_witness_path: Path
    script_sha256: str
    translation_sha256: str
    validation_denominator_sha256: str
    completion_token: str
    completion_witness_sha256: str
    expected_layer_colors: tuple[tuple[str, tuple[int, int, int]], ...]
    physical_object_ids: tuple[str, ...]
    expected_document_user_text: tuple[tuple[str, str], ...]
    expected_semantics: dict[str, object]
    expected_bounds: dict[str, dict[str, object]]
    expected_object_counts: tuple[tuple[str, int], ...]
    readback_tolerance: float
    patch: dict[str, object] | None = None

    SCHEMA = "RhinoCadExportPlan@4"

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RhinoCadExportIdentity):
            raise TypeError("identity must be RhinoCadExportIdentity")
        for field in (
            "workspace",
            "script_path",
            "model_path",
            "completion_marker_path",
            "host_witness_path",
        ):
            if not isinstance(getattr(self, field), Path):
                raise TypeError(f"{field} must be pathlib.Path")
        _validate_plan_paths(self, require_script=True)
        object.__setattr__(
            self,
            "script_sha256",
            require_sha256(self.script_sha256, "script_sha256"),
        )
        object.__setattr__(
            self,
            "translation_sha256",
            require_sha256(self.translation_sha256, "translation_sha256"),
        )
        object.__setattr__(
            self,
            "validation_denominator_sha256",
            require_sha256(
                self.validation_denominator_sha256,
                "validation_denominator_sha256",
            ),
        )
        object.__setattr__(
            self,
            "completion_token",
            require_sha256(self.completion_token, "completion_token"),
        )
        object.__setattr__(
            self,
            "completion_witness_sha256",
            require_sha256(
                self.completion_witness_sha256,
                "completion_witness_sha256",
            ),
        )
        if self.physical_object_ids != tuple(sorted(set(self.physical_object_ids))):
            raise CadExecutionError("physical_object_ids must be sorted and unique")
        if self.expected_layer_colors != tuple(
            sorted(set(self.expected_layer_colors))
        ):
            raise CadExecutionError("expected_layer_colors must be sorted and unique")
        for layer_path, color in self.expected_layer_colors:
            if not isinstance(layer_path, str) or not layer_path:
                raise CadExecutionError("expected layer paths must be non-empty text")
            if (
                not isinstance(color, tuple)
                or len(color) != 3
                or any(
                    isinstance(channel, bool)
                    or not isinstance(channel, int)
                    or channel < 0
                    or channel > 255
                    for channel in color
                )
            ):
                raise CadExecutionError("expected layer colors must be RGB bytes")
        for value in self.physical_object_ids:
            require_identifier(value, "physical_object_ids")
        if self.expected_document_user_text != tuple(
            sorted(set(self.expected_document_user_text))
        ):
            raise CadExecutionError(
                "expected_document_user_text must be sorted and unique"
            )
        for key, value in self.expected_document_user_text:
            if not isinstance(key, str) or not key.startswith("archflow:"):
                raise CadExecutionError("document keys must use archflow namespace")
            if not isinstance(value, str) or not value:
                raise CadExecutionError("document values must be non-empty text")
        if not isinstance(self.expected_semantics, dict) or set(
            self.expected_semantics
        ) != {"objects", "blocks"}:
            raise CadExecutionError("expected_semantics schema drifted")
        if set(self.expected_semantics["objects"]) != set(
            self.physical_object_ids
        ):
            raise CadExecutionError("semantic and physical denominators differ")
        semantic_layers = {
            row["layer"] for row in self.expected_semantics["objects"].values()
        }
        if {path for path, _ in self.expected_layer_colors} != {
            "archflow",
            *semantic_layers,
        }:
            raise CadExecutionError("layer and semantic denominators differ")
        if not isinstance(self.expected_bounds, dict) or set(
            self.expected_bounds
        ) != set(self.physical_object_ids):
            raise CadExecutionError("bounds and physical denominators differ")
        if self.expected_object_counts != tuple(
            sorted(set(self.expected_object_counts))
        ):
            raise CadExecutionError("expected_object_counts must be sorted and unique")
        if {key for key, _ in self.expected_object_counts} != set(
            self.physical_object_ids
        ) or any(count <= 0 for _, count in self.expected_object_counts):
            raise CadExecutionError("expected_object_counts denominator is invalid")
        tolerance = _positive_finite(self.readback_tolerance, "readback_tolerance")
        object.__setattr__(self, "readback_tolerance", tolerance)
        _validate_plan_denominator(self)
        expected_token = _completion_token(
            identity=self.identity,
            artifact_relative_path=self.artifact_relative_path,
            translation_sha256=self.translation_sha256,
            validation_denominator_sha256=self.validation_denominator_sha256,
        )
        if self.completion_token != expected_token:
            raise CadExecutionError("completion token does not bind the exact plan")
        expected_witness = _sha256_text(
            _marker_text(
                _completion_marker_payload(
                    artifact_relative_path=self.artifact_relative_path,
                    completion_token=self.completion_token,
                    status="succeeded",
                )
            )
        )
        if self.completion_witness_sha256 != expected_witness:
            raise CadExecutionError("completion witness digest does not match the plan")

    @property
    def script_relative_path(self) -> str:
        return self.script_path.relative_to(self.workspace).as_posix()

    @property
    def artifact_relative_path(self) -> str:
        return self.model_path.relative_to(self.workspace).as_posix()

    @property
    def completion_marker_relative_path(self) -> str:
        return self.completion_marker_path.relative_to(self.workspace).as_posix()

    @property
    def host_witness_relative_path(self) -> str:
        return self.host_witness_path.relative_to(self.workspace).as_posix()

    @property
    def plan_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        """Serialize without the machine-local workspace root."""

        return _json_copy(
            {
                "schema": self.SCHEMA,
                "identity": self.identity.to_dict(),
                "script_relative_path": self.script_relative_path,
                "artifact_relative_path": self.artifact_relative_path,
                "completion_marker_relative_path": (
                    self.completion_marker_relative_path
                ),
                "host_witness_relative_path": self.host_witness_relative_path,
                "script_sha256": self.script_sha256,
                "translation_sha256": self.translation_sha256,
                "validation_denominator_sha256": (
                    self.validation_denominator_sha256
                ),
                "completion_token": self.completion_token,
                "completion_witness_sha256": self.completion_witness_sha256,
                "expected_layer_colors": [
                    {"full_path": path, "rgb": list(color)}
                    for path, color in self.expected_layer_colors
                ],
                "physical_object_ids": list(self.physical_object_ids),
                "expected_document_user_text": [
                    {"key": key, "value": value}
                    for key, value in self.expected_document_user_text
                ],
                "expected_semantics": self.expected_semantics,
                "expected_bounds": self.expected_bounds,
                "expected_object_counts": [
                    {"object_id": key, "count": count}
                    for key, count in self.expected_object_counts
                ],
                "readback_tolerance": self.readback_tolerance,
                "patch": _json_copy(self.patch) if self.patch else None,
                "canonical_write_authority": False,
            }
        )


@dataclass(frozen=True, slots=True)
class RhinoCadExecutionReceipt:
    """Process evidence plus independent saved-file readback."""

    status: CadExecutionStatus
    identity: RhinoCadExportIdentity
    adapter_id: str
    executable_name: str
    plan_digest: str
    script_sha256: str
    artifact_relative_path: str
    completion_marker_relative_path: str
    host_witness_relative_path: str
    completion_token: str
    completion_witness_sha256: str
    completion_marker_sha256: str | None
    validation_denominator_sha256: str
    host_witness_sha256: str | None
    cleanup_witness_sha256: str | None
    cleanup_status: str
    process_exit_code: int | None
    stdout_sha256: str | None
    stderr_sha256: str | None
    inspection: dict[str, object] | None
    failures: tuple[dict[str, str], ...]

    SCHEMA = "RhinoCadExecutionReceipt@4"

    def __post_init__(self) -> None:
        if not isinstance(self.status, CadExecutionStatus):
            raise TypeError("status must be CadExecutionStatus")
        if not isinstance(self.identity, RhinoCadExportIdentity):
            raise TypeError("identity must be RhinoCadExportIdentity")
        require_identifier(self.adapter_id, "adapter_id")
        if not isinstance(self.executable_name, str) or not self.executable_name:
            raise CadExecutionError("executable_name must be non-empty text")
        for field in (
            "plan_digest",
            "script_sha256",
            "completion_token",
            "completion_witness_sha256",
            "validation_denominator_sha256",
        ):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        _portable_relative_path(self.artifact_relative_path)
        _portable_relative_path(self.completion_marker_relative_path)
        _portable_relative_path(self.host_witness_relative_path)
        if self.completion_marker_sha256 is not None:
            object.__setattr__(
                self,
                "completion_marker_sha256",
                require_sha256(
                    self.completion_marker_sha256,
                    "completion_marker_sha256",
                ),
            )
        for field in ("host_witness_sha256", "cleanup_witness_sha256"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(
                    self,
                    field,
                    require_sha256(value, field),
                )
        if self.cleanup_status not in (
            "confirmed",
            "cleanup_unverified",
            "not_performed",
        ):
            raise CadExecutionError("cleanup_status is invalid")
        if self.process_exit_code is not None and not isinstance(
            self.process_exit_code, int
        ):
            raise TypeError("process_exit_code must be int or None")
        for field in ("stdout_sha256", "stderr_sha256"):
            value = getattr(self, field)
            if value is not None:
                require_sha256(value, field)
        if self.inspection is not None and not isinstance(self.inspection, dict):
            raise TypeError("inspection must be dict or None")
        if not isinstance(self.failures, tuple) or any(
            not isinstance(item, dict) for item in self.failures
        ):
            raise TypeError("failures must be tuple[dict, ...]")
        succeeded = self.status is CadExecutionStatus.SUCCEEDED
        if succeeded != (not self.failures and self.inspection is not None):
            raise CadExecutionError(
                "successful execution requires verified inspection and no failures"
            )
        if succeeded and self.completion_marker_sha256 != self.completion_witness_sha256:
            raise CadExecutionError(
                "successful execution requires the exact completion marker witness"
            )
        if succeeded and (
            self.host_witness_sha256 is None
            or self.cleanup_witness_sha256 is None
            or self.cleanup_status != "confirmed"
        ):
            raise CadExecutionError(
                "successful execution requires exact host and cleanup witnesses"
            )

    @property
    def readback_verified(self) -> bool:
        return self.status is CadExecutionStatus.SUCCEEDED

    def to_dict(self) -> dict[str, object]:
        return _json_copy(
            {
                "schema": self.SCHEMA,
                "status": self.status.value,
                "identity": self.identity.to_dict(),
                "adapter_id": self.adapter_id,
                "executable_name": self.executable_name,
                "plan_digest": self.plan_digest,
                "script_sha256": self.script_sha256,
                "artifact_relative_path": self.artifact_relative_path,
                "completion_marker_relative_path": (
                    self.completion_marker_relative_path
                ),
                "host_witness_relative_path": self.host_witness_relative_path,
                "completion_token": self.completion_token,
                "completion_witness_sha256": self.completion_witness_sha256,
                "completion_marker_sha256": self.completion_marker_sha256,
                "validation_denominator_sha256": (
                    self.validation_denominator_sha256
                ),
                "host_witness_sha256": self.host_witness_sha256,
                "cleanup_witness_sha256": self.cleanup_witness_sha256,
                "cleanup_status": self.cleanup_status,
                "process_exit_code": self.process_exit_code,
                "stdout_sha256": self.stdout_sha256,
                "stderr_sha256": self.stderr_sha256,
                "inspection": self.inspection,
                "failures": list(self.failures),
                "readback_verified": self.readback_verified,
                "canonical_write_authority": False,
            }
        )


@dataclass(frozen=True)
class RhinoPatchBase:
    """The prior saved document and the program it realized (P103)."""

    prior_model_path: Path
    prior_program: CompiledGeometryProgram

    def __post_init__(self) -> None:
        if not isinstance(self.prior_model_path, Path):
            raise TypeError("prior_model_path must be pathlib.Path")
        if not isinstance(self.prior_program, CompiledGeometryProgram):
            raise TypeError("prior_program must be CompiledGeometryProgram")


@dataclass(frozen=True, slots=True)
class StepImportObject:
    """One named shape of an exported STEP, alone in a file of its own.

    The measures are the kernel's own reading of the shape that was written,
    taken after the single-object file was read back: what a host import has
    to arrive at, and what a silently healed or dropped hole would disagree
    with.
    """

    object_id: str
    file_name: str
    sha256: str
    solid_count: int
    closed: bool
    face_count: int
    volume: float | None
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "object_id": self.object_id,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "solid_count": self.solid_count,
            "closed": self.closed,
            "face_count": self.face_count,
            "volume": self.volume,
            "bbox": {"min": list(self.bbox_min), "max": list(self.bbox_max)},
        }


@dataclass(frozen=True, slots=True)
class StepImportSource:
    """The exported STEP an import-based export reads, split by named shape."""

    step_path: Path
    step_sha256: str
    objects: tuple[StepImportObject, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "step_file_name": self.step_path.name,
            "step_sha256": self.step_sha256,
            "objects": [item.to_dict() for item in self.objects],
        }


def work_model_workspace(speculative_root: Path, *, source_sha256: str) -> Path:
    """A fresh speculative directory for one export attempt, under a named root.

    The caller states the root - the run's own CAD export workspace, from the
    P036 layout - and this only makes one attempt directory inside it. No path
    is inferred from a file's neighbours, and no second workspace convention
    is introduced.

    Attempts are numbered rather than cleaned up: a previous failure keeps its
    script, marker and partial output where they can be read, and never stands
    in the way of the next attempt. Nothing is removed.
    """

    if not isinstance(speculative_root, Path):
        raise TypeError("speculative_root must be pathlib.Path")
    require_sha256(source_sha256, "source_sha256")
    root = _strict_workspace(speculative_root) / "work-model"
    long_path(root).mkdir(parents=True, exist_ok=True)
    stem = source_sha256[:12]
    attempt = 1
    while long_path(root / f"{stem}-{attempt}").exists():
        attempt += 1
    workspace = root / f"{stem}-{attempt}"
    long_path(workspace).mkdir()
    return workspace


def split_step_objects(
    step_path: Path, *, destination: Path, length_unit: str
) -> tuple[StepImportObject, ...]:
    """Write each named shape of one exported STEP into a file of its own.

    The shapes are the ones the kernel reads out of that STEP; nothing is
    recompiled, healed or tessellated, and the same writer that produced the
    source file writes each single-object file. Every file is then read back
    and measured: a shape whose solids, faces, closure, volume or bounds
    disagree with the shape it came from fails here, before any host sees it.

    Identity is the STEP's own shape name. A file whose shapes are unnamed or
    share a name is refused rather than guessed at by order or position.
    """

    if not isinstance(step_path, Path) or not isinstance(destination, Path):
        raise TypeError("step_path and destination must be pathlib.Path")
    if not destination.is_dir():
        raise CadExecutionError(f"the destination directory does not exist: {destination}")
    entries = read_step(long_path(step_path), length_unit=length_unit)
    if not entries:
        raise CadExecutionError(f"{step_path.name} holds no shape to import")
    unnamed = sum(1 for entry in entries if not entry.name)
    if unnamed:
        raise CadExecutionError(
            f"{step_path.name} has {unnamed} unnamed shape(s): an import binds each "
            "file to the object id its shape is named with, and never to import order"
        )
    names = [entry.name for entry in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise CadExecutionError(
            f"{step_path.name} names the same shape more than once: {', '.join(duplicates)}"
        )
    written: list[StepImportObject] = []
    for entry in entries:
        object_id = str(entry.name)
        file_name = f"{_import_file_stem(object_id)}.step"
        target = destination / file_name
        _strict_child(destination, target, require_exists=False)
        if long_path(target).exists():
            raise CadExecutionError(f"speculative import source already exists: {file_name}")
        layer = entry.layers[0] if entry.layers else ""
        write_step(
            long_path(target),
            (StepObject(object_id=object_id, shape=entry.shape, layer=layer, color=entry.color),),
            length_unit=length_unit,
        )
        source_measure = measure_shape(entry.shape)
        cold = read_step(long_path(target), length_unit=length_unit)
        if len(cold) != 1 or cold[0].name != object_id:
            raise CadExecutionError(
                f"{file_name} did not read back as the one shape named {object_id}"
            )
        saved_measure = measure_shape(cold[0].shape)
        _require_same_shape(object_id, source_measure, saved_measure)
        written.append(
            StepImportObject(
                object_id=object_id,
                file_name=file_name,
                sha256=_sha256_bytes(long_path(target).read_bytes()),
                solid_count=saved_measure.solid_count,
                closed=saved_measure.closed,
                face_count=saved_measure.face_count,
                volume=saved_measure.volume,
                bbox_min=saved_measure.bbox_min,
                bbox_max=saved_measure.bbox_max,
            )
        )
    return tuple(sorted(written, key=lambda item: item.object_id))


def _require_file_digest(path: Path, expected: str, subject: str) -> None:
    """The file on disk is the one whose digest was declared, or this fails."""

    require_sha256(expected, f"{subject} sha256")
    try:
        actual = _sha256_bytes(long_path(path).read_bytes())
    except OSError as exc:
        raise CadExecutionError(f"{subject} cannot be read: {exc}") from exc
    if actual != expected:
        raise CadExecutionError(
            f"{subject} is {actual} on disk where {expected} was declared: the file "
            "changed after it was read, and nothing is imported from it"
        )


def verify_work_model_geometry(
    model_path: Path, source: StepImportSource
) -> tuple[dict[str, object], ...]:
    """Read the saved document and compare each object with the shape it came from.

    This is the reader's own second look, outside the host: per named object
    it checks that the geometry is a B-rep (never a mesh), that the solids and
    faces are the ones the STEP shape had - a healed-away opening shows up
    here as missing faces - and that a closed source stayed closed. Volume and
    exact placement are checked inside the host, where mass properties and
    trimmed-surface bounds are available; an openNURBS bounding box read here
    would include untrimmed control surfaces and could refuse a correct file.
    It returns one row per object; a disagreement raises.
    """

    try:
        rhino3dm = importlib.import_module("rhino3dm")
    except ImportError as exc:  # pragma: no cover - the export itself needs it
        raise CadExecutionError("rhino3dm is required to verify a saved work model") from exc
    if not isinstance(source, StepImportSource):
        raise TypeError("source must be StepImportSource")
    model = rhino3dm.File3dm.Read(str(long_path(model_path)))
    if model is None:
        raise CadExecutionError(f"the saved work model cannot be read: {model_path.name}")
    by_name: dict[str, list[object]] = {}
    for item in model.Objects:
        by_name.setdefault(item.Attributes.Name or "", []).append(item.Geometry)
    rows: list[dict[str, object]] = []
    for expected in source.objects:
        found = by_name.get(expected.object_id, [])
        if not found:
            raise CadExecutionError(
                f"{expected.object_id}: the saved work model has no object of that name"
            )
        solids = faces = 0
        for geometry in found:
            kind = type(geometry).__name__
            if kind == "Extrusion":
                brep = geometry.ToBrep(False)
            elif kind == "Brep":
                brep = geometry
            else:
                raise CadExecutionError(
                    f"{expected.object_id}: the saved work model holds {kind} geometry; an "
                    "editable work model carries B-rep surfaces, never a mesh"
                )
            if brep is None:
                raise CadExecutionError(f"{expected.object_id}: saved geometry has no B-rep form")
            faces += len(brep.Faces)
            solids += 1 if brep.IsSolid else 0
        if expected.closed and solids != expected.solid_count:
            raise CadExecutionError(
                f"{expected.object_id}: the exported shape is {expected.solid_count} closed "
                f"solid(s); the saved work model has {solids}"
            )
        if faces != expected.face_count:
            raise CadExecutionError(
                f"{expected.object_id}: the exported shape has {expected.face_count} faces; the "
                f"saved work model has {faces}. An opening or a face was not carried over"
            )
        rows.append({
            "object_id": expected.object_id,
            "objects": len(found),
            "solids": solids,
            "faces": faces,
            "closed": bool(expected.closed and solids == expected.solid_count),
        })
    return tuple(rows)


def _import_file_stem(object_id: str) -> str:
    """A file name that stays inside the workspace and keeps the id readable."""

    stem = "".join(char if (char.isalnum() or char in "-_.") else "-" for char in object_id)
    stem = stem.strip(".-") or "object"
    return f"{stem[:80]}@{_sha256_text(object_id)[:12]}"


def _require_same_shape(object_id: str, source, saved) -> None:
    """The single-object file holds the shape it was written from, or fails."""

    for field in ("solid_count", "face_count", "closed"):
        expected, actual = getattr(source, field), getattr(saved, field)
        if expected != actual:
            raise CadExecutionError(
                f"{object_id}: the single-object STEP has {field}={actual!r} where the "
                f"exported shape has {expected!r}"
            )
    if (source.volume is None) != (saved.volume is None):
        raise CadExecutionError(
            f"{object_id}: the single-object STEP {'has' if saved.volume is not None else 'has no'} "
            "volume where the exported shape does not"
        )
    if source.volume is not None and saved.volume is not None:
        scale = max(abs(source.volume), abs(saved.volume), 1e-9)
        if abs(source.volume - saved.volume) / scale > 1e-6:
            raise CadExecutionError(
                f"{object_id}: the single-object STEP encloses {saved.volume!r} where the "
                f"exported shape encloses {source.volume!r}"
            )
    for corner, expected, actual in (
        ("min", source.bbox_min, saved.bbox_min),
        ("max", source.bbox_max, saved.bbox_max),
    ):
        if any(abs(float(a) - float(b)) > 1e-7 for a, b in zip(expected, actual)):
            raise CadExecutionError(
                f"{object_id}: the single-object STEP bounds {corner} {actual} differ from "
                f"the exported shape's {expected}"
            )


def prepare_rhino_three_dm_export(
    program: CompiledGeometryProgram,
    *,
    binding: RhinoCadProgramBinding,
    speculative_workspace: Path,
    artifact_name: str,
    readback_tolerance: float,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
    patch: RhinoPatchBase | None = None,
    layer_by_component: Mapping[str, str] | None = None,
    step_import: StepImportSource | None = None,
    source_materials: Mapping[str, Mapping[str, object]] | None = None,
) -> RhinoCadExportPlan:
    """Prepare one immutable export plan in an existing explicit workspace.

    With ``patch`` the plan rebuilds only the selected operations on top of
    the prior document's kept objects; the readback denominator is still
    the whole program, so a patch is verified exactly like a rebuild.

    With ``step_import`` the host builds nothing: it reads back the exact
    STEP this program already exported, one named shape per file, and the
    document is the same geometry rather than a second realization of it.
    The denominator is still the whole program, so an imported document is
    verified exactly like a rebuilt one. ``source_materials`` is that
    delivery's own retained material table, per object id, and is what the
    imported objects end up wearing.
    """

    if not isinstance(binding, RhinoCadProgramBinding):
        raise TypeError("binding must be RhinoCadProgramBinding")
    binding.bind_program(program)
    unit = program.proposal.length_unit.value
    identity = RhinoCadExportIdentity(binding=binding, length_unit=unit)
    tolerance = _positive_finite(readback_tolerance, "readback_tolerance")
    workspace = _strict_workspace(speculative_workspace)
    artifact_name = _artifact_name(artifact_name)
    model_path = workspace / artifact_name
    script_path = workspace / f"{Path(artifact_name).stem}.archflow.py"
    completion_marker_path = (
        workspace / f"{Path(artifact_name).stem}.archflow-completion.json"
    )
    host_witness_path = (
        workspace / f"{Path(artifact_name).stem}.archflow-host.json"
    )
    for target in (
        model_path,
        script_path,
        completion_marker_path,
        host_witness_path,
    ):
        _strict_child(workspace, target, require_exists=False)
        if target.exists():
            raise CadExecutionError(f"speculative output already exists: {target.name}")
    supplied = _provenance(identity, provenance)
    selection = None
    patch_prelude = None
    patch_record: dict[str, object] | None = None
    if patch is not None:
        if not isinstance(patch, RhinoPatchBase):
            raise TypeError("patch must be RhinoPatchBase")
        prior_model = patch.prior_model_path.resolve()
        if not prior_model.is_file():
            raise CadExecutionError(f"patch base model does not exist: {prior_model}")
        try:
            selection = select_patch_operations(program, patch.prior_program)
        except CadPatchError as exc:
            raise CadExecutionError(f"patch not expressible: {exc}") from exc
        # an empty selection means the geometry is already in the prior document; the patch then only
        # carries every object over and re-stamps its semantics (bindings, evidence, commitments) — a "restamp"
        patch_prelude = build_patch_prelude(
            selection,
            prior_model_path=prior_model,
            semantics=expected_object_semantics(program, material_by_component=material_by_component, layer_by_component=layer_by_component)["objects"],
        )
        patch_record = {
            **selection.to_dict(),
            "mode": "restamp" if selection.empty else "patch",
            "prior_model_path": str(prior_model),
            "prior_model_sha256": _sha256_bytes(prior_model.read_bytes()),
        }
    if step_import is not None:
        if patch is not None:
            raise CadExecutionError("an imported document has no patch base to build on")
        if not isinstance(step_import, StepImportSource):
            raise TypeError("step_import must be StepImportSource")
        # The digests are what binds this plan to bytes rather than to file
        # names: a source STEP or a single-object file swapped between the
        # split and the export is a different document, and is refused here
        # rather than imported and then described as the one that was asked for.
        _require_file_digest(step_import.step_path, step_import.step_sha256, "the exported STEP")
        for item in step_import.objects:
            source_file = workspace / item.file_name
            _strict_child(workspace, source_file, require_exists=True)
            _require_file_digest(source_file, item.sha256, f"the import source for {item.object_id}")
        # The materials this delivery already wears. The caller passes the
        # table its own export receipt retained, so an imported document keeps
        # the materials that model was actually written with - glass included,
        # with its transparency. Only a receipt that recorded none falls back
        # to deriving them from the program the same way the preview did.
        import_semantics = _json_copy(
            expected_object_semantics(
                program,
                material_by_component=material_by_component,
                layer_by_component=layer_by_component,
            )
        )
        import_layer_colors = dict(
            _resolved_layer_colors(
                {row["layer"] for row in import_semantics["objects"].values()},
                material_by_component=material_by_component,
                material_colors=material_colors,
            )
        )
        translation = translate_step_import_to_rhino_python(
            program,
            step_file_by_object={item.object_id: item.file_name for item in step_import.objects},
            step_sha256_by_object={item.object_id: item.sha256 for item in step_import.objects},
            object_materials=dict(source_materials) if source_materials else {
                object_id: material.to_dict()
                for object_id, material in _preview_materials(
                    program,
                    physical=tuple(sorted(import_semantics["objects"])),
                    semantics=import_semantics,
                    layer_colors=import_layer_colors,
                    material_colors=material_colors,
                ).items()
            },
            provenance=supplied,
            material_by_component=material_by_component,
            material_colors=material_colors,
            layer_by_component=layer_by_component,
        )
    else:
        translation = translate_to_rhino_python(
            program,
            provenance=supplied,
            material_by_component=material_by_component,
            material_colors=material_colors,
            operation_subset=None if selection is None else selection.rebuilt_op_ids,
            layer_by_component=layer_by_component,
        )
    if translation.losses:
        raise CadExecutionError(
            "CAD translation has typed losses and cannot be exported strictly: "
            + json.dumps(translation.losses, sort_keys=True)
        )
    semantics = _json_copy(
        expected_object_semantics(
            program,
            material_by_component=material_by_component,
            layer_by_component=layer_by_component,
        )
    )
    if step_import is not None:
        # A rebuilt document repeats an array as a block definition and its
        # instances; an imported one holds the solids the STEP already carries,
        # one named object per copy. The denominator states the representation
        # this export actually delivers rather than the other one's: the
        # objects, their names, layers, semantics and counts are all still
        # verified, and this is where the count of each is stated.
        semantics["blocks"] = {}
    raw_bounds = expected_object_bounds(program)
    bounds = {
        object_id: _bounds_to_rhino(value)
        for object_id, value in sorted(raw_bounds.items())
    }
    counts = tuple(
        sorted(
            (object_id, int(value["brep_count"]))
            for object_id, value in raw_bounds.items()
        )
    )
    translation_sha256 = _sha256_text(translation.script)
    # a patch names only the rebuilt objects in its script; the denominator stays the whole document
    physical_object_ids = tuple(sorted(translation.physical_object_ids if selection is None else _physical_ids(program.proposal)))
    expected_document_user_text = tuple(
        sorted((f"archflow:{key}", value) for key, value in supplied.items())
    )
    validation_denominator_sha256 = _validation_denominator_digest(
        identity=identity,
        translation_sha256=translation_sha256,
        expected_layer_colors=translation.layer_colors,
        physical_object_ids=physical_object_ids,
        expected_document_user_text=expected_document_user_text,
        expected_semantics=semantics,
        expected_bounds=bounds,
        expected_object_counts=counts,
        readback_tolerance=tolerance,
    )
    completion_token = _completion_token(
        identity=identity,
        artifact_relative_path=artifact_name,
        translation_sha256=translation_sha256,
        validation_denominator_sha256=validation_denominator_sha256,
    )
    success_marker = _completion_marker_payload(
        artifact_relative_path=artifact_name,
        completion_token=completion_token,
        status="succeeded",
    )
    completion_witness_sha256 = _sha256_text(_marker_text(success_marker))
    script = _export_script(
        translation.script,
        artifact_name=artifact_name,
        completion_marker_name=completion_marker_path.name,
        completion_token=completion_token,
        length_unit=unit,
        readback_tolerance=tolerance,
        patch_prelude=patch_prelude,
        source_measures=None if step_import is None else {
            item.object_id: {
                "solid_count": item.solid_count,
                "face_count": item.face_count,
                "closed": item.closed,
                "volume": item.volume,
            }
            for item in step_import.objects
        },
    )
    try:
        with long_path(script_path).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(script)
    except FileExistsError as exc:
        raise CadExecutionError(
            "speculative script appeared before exclusive creation"
        ) from exc
    return RhinoCadExportPlan(
        identity=identity,
        workspace=workspace,
        script_path=script_path,
        model_path=model_path,
        completion_marker_path=completion_marker_path,
        host_witness_path=host_witness_path,
        script_sha256=_sha256_text(script),
        translation_sha256=translation_sha256,
        validation_denominator_sha256=validation_denominator_sha256,
        completion_token=completion_token,
        completion_witness_sha256=completion_witness_sha256,
        expected_layer_colors=translation.layer_colors,
        physical_object_ids=physical_object_ids,
        expected_document_user_text=expected_document_user_text,
        expected_semantics=semantics,
        expected_bounds=bounds,
        expected_object_counts=counts,
        readback_tolerance=tolerance,
        patch=patch_record,
    )


def discover_rhino_executables() -> tuple[Path, ...]:
    """Read-only discovery; never launches or attaches to Rhino."""

    candidates: list[Path] = []
    for version in range(9, 5, -1):
        candidate = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        ) / f"Rhino {version}" / "System" / "Rhino.exe"
        if candidate.is_file() and not candidate.is_symlink():
            candidates.append(candidate.resolve())
    return tuple(candidates)


def discover_powershell() -> Path | None:
    """The shell that supervises the host, when the caller named none.

    Read-only and ordinary: the two places Windows keeps its own PowerShell,
    in the order an operator would expect. A caller's explicit executable
    always wins; this only answers the case where nothing was configured, so
    a machine that has Rhino and PowerShell can export without being asked to
    fill in a path it did not choose. Nothing is launched here.
    """

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for candidate in (
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7" / "pwsh.exe",
    ):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    return None


def build_rhino_com_powershell_source(
    plan: RhinoCadExportPlan,
    *,
    timeout_seconds: float,
) -> str:
    """Build the STA worker that records its Rhino host before blocking COM."""

    if not isinstance(plan, RhinoCadExportPlan):
        raise TypeError("plan must be RhinoCadExportPlan")
    timeout = _positive_finite(timeout_seconds, "timeout_seconds")
    _validate_plan_paths(plan, require_script=True)
    script_path = str(plan.script_path)
    host_witness_path = str(plan.host_witness_path)
    if any(character in script_path for character in "\x00\r\n()"):
        raise CadExecutionError(
            "Rhino COM script path contains unsafe macro characters"
        )
    if any(character in host_witness_path for character in "\x00\r\n"):
        raise CadExecutionError(
            "Rhino host witness path contains unsafe characters"
        )
    script_literal = _powershell_literal(script_path)
    host_literal = _powershell_literal(host_witness_path)
    token_literal = _powershell_literal(plan.completion_token)
    timeout_literal = format(timeout, ".17g")
    return f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$scriptPath = {script_literal}
$hostWitnessPath = {host_literal}
$completionToken = {token_literal}
$timeoutSeconds = [double]{timeout_literal}
$initDeadline = [DateTime]::UtcNow.AddSeconds([Math]::Min(60.0, $timeoutSeconds))
function Write-ExclusiveUtf8Json([string]$Path, [object]$Payload) {{
  $text = $Payload | ConvertTo-Json -Compress -Depth 4
  $encoding = New-Object System.Text.UTF8Encoding($false)
  $stream = [IO.File]::Open(
    $Path,
    [IO.FileMode]::CreateNew,
    [IO.FileAccess]::Write,
    [IO.FileShare]::None
  )
  try {{
    $bytes = $encoding.GetBytes($text)
    $stream.Write($bytes, 0, $bytes.Length)
  }} finally {{
    $stream.Dispose()
  }}
}}
$baselinePids = @(
  Get-Process -Name 'Rhino' -ErrorAction SilentlyContinue |
    ForEach-Object {{ [int]$_.Id }}
)
$rhino = $null
$result = [ordered]@{{
  schema = 'RhinoComPowerShellWorkerResult@4'
  prog_id = 'Rhino.Application.8'
  initialized = $false
  ownership_status = 'not_observed'
  error_code = $null
  error_detail = $null
}}
try {{
  $type = [Type]::GetTypeFromProgID('Rhino.Application.8', $true)
  $rhino = [Activator]::CreateInstance($type)
  $rhino.Visible = 0
  while ([int]$rhino.IsInitialized() -eq 0) {{
    if ([DateTime]::UtcNow -ge $initDeadline) {{ throw 'RHINO_INIT_TIMEOUT' }}
    Start-Sleep -Milliseconds 100
  }}
  $result.initialized = $true
  $currentPids = @(
    Get-Process -Name 'Rhino' -ErrorAction SilentlyContinue |
      ForEach-Object {{ [int]$_.Id }}
  )
  $newPids = @($currentPids | Where-Object {{ $baselinePids -notcontains $_ }})
  $hostWitness = [ordered]@{{
    schema = 'RhinoCadHostWitness@1'
    completion_token = $completionToken
    ownership_status = 'ambiguous'
    new_pid_count = $newPids.Count
    pid = $null
    executable_path = $null
    start_time_utc_ticks = $null
  }}
  if ($newPids.Count -eq 1) {{
    try {{
      $owned = Get-Process -Id ([int]$newPids[0]) -ErrorAction Stop
      $hostWitness.pid = [int]$owned.Id
      $hostWitness.executable_path = [string]$owned.Path
      $hostWitness.start_time_utc_ticks = [int64]$owned.StartTime.ToUniversalTime().Ticks
      if (-not [string]::IsNullOrWhiteSpace($hostWitness.executable_path) -and
          $hostWitness.start_time_utc_ticks -gt 0) {{
        $hostWitness.ownership_status = 'exact'
      }}
    }} catch {{}}
  }}
  Write-ExclusiveUtf8Json $hostWitnessPath $hostWitness
  $result.ownership_status = $hostWitness.ownership_status
  if ($hostWitness.ownership_status -ne 'exact') {{
    throw 'RHINO_PID_OWNERSHIP_AMBIGUOUS'
  }}
  $macro = '-_RunPythonScript (' + $scriptPath + ')'
  $null = $rhino.RunScript($macro, 0)
}} catch {{
  $result.error_code = 'cad_execution.com_bridge_failed'
  $detail = [string]$_.Exception.Message
  if ($detail.Length -gt 2000) {{ $detail = $detail.Substring(0, 2000) }}
  $result.error_detail = $detail
}} finally {{
  if ($null -ne $rhino) {{
    try {{
      $null = [Runtime.InteropServices.Marshal]::FinalReleaseComObject($rhino)
    }} catch {{}}
    $rhino = $null
  }}
  $result | ConvertTo-Json -Compress -Depth 4
}}
if ($null -ne $result.error_code) {{ exit 1 }}
exit 0
"""


def build_rhino_com_powershell_command(
    plan: RhinoCadExportPlan,
    *,
    powershell_executable: Path,
    timeout_seconds: float,
) -> tuple[str, ...]:
    """Encode the COM bridge so no shell interpolation or launcher file exists."""

    executable = _powershell_executable(powershell_executable)
    source = build_rhino_com_powershell_source(
        plan,
        timeout_seconds=timeout_seconds,
    )
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return (
        str(executable),
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-EncodedCommand",
        encoded,
    )


def _build_rhino_cleanup_powershell_source(
    host_witness: Mapping[str, object],
) -> str:
    pid = int(host_witness["pid"])
    executable_path = str(host_witness["executable_path"])
    start_ticks = int(host_witness["start_time_utc_ticks"])
    token = str(host_witness["completion_token"])
    return f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$expectedPid = [int]{pid}
$expectedPath = {_powershell_literal(executable_path)}
$expectedTicks = [int64]{start_ticks}
$completionToken = {_powershell_literal(token)}
$result = [ordered]@{{
  schema = 'RhinoCadCleanupWitness@1'
  completion_token = $completionToken
  pid = $expectedPid
  status = 'cleanup_unverified'
  identity_matched = $false
  stop_requested = $false
  cleanup_confirmed = $false
  error_detail = $null
}}
try {{
  $process = Get-Process -Id $expectedPid -ErrorAction SilentlyContinue
  if ($null -eq $process) {{
    $result.status = 'already_exited'
    $result.cleanup_confirmed = $true
  }} else {{
    $actualPath = [string]$process.Path
    $actualTicks = [int64]$process.StartTime.ToUniversalTime().Ticks
    if ($actualPath -ieq $expectedPath -and $actualTicks -eq $expectedTicks) {{
      $result.identity_matched = $true
      $result.stop_requested = $true
      Stop-Process -Id $expectedPid -Force -ErrorAction Stop
      $deadline = [DateTime]::UtcNow.AddSeconds(3.0)
      while ((Get-Process -Id $expectedPid -ErrorAction SilentlyContinue) -and
             [DateTime]::UtcNow -lt $deadline) {{
        Start-Sleep -Milliseconds 50
      }}
      if (-not (Get-Process -Id $expectedPid -ErrorAction SilentlyContinue)) {{
        $result.status = 'stopped'
        $result.cleanup_confirmed = $true
      }}
    }} else {{
      $result.status = 'identity_mismatch'
    }}
  }}
}} catch {{
  $detail = [string]$_.Exception.Message
  if ($detail.Length -gt 1000) {{ $detail = $detail.Substring(0, 1000) }}
  $result.error_detail = $detail
}}
$result | ConvertTo-Json -Compress -Depth 3
if ($result.cleanup_confirmed) {{ exit 0 }}
exit 1
"""


def _build_rhino_cleanup_powershell_command(
    *,
    powershell_executable: Path,
    host_witness: Mapping[str, object],
) -> tuple[str, ...]:
    executable = _powershell_executable(powershell_executable)
    source = _build_rhino_cleanup_powershell_source(host_witness)
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return (
        str(executable),
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-EncodedCommand",
        encoded,
    )


#: One Rhino host at a time in this process. A candidate's export and an
#: explicitly requested export are the same machine resource, so they take
#: the same lock here rather than each trusting its own caller's queue: a
#: queue orders the callers it knows about, and this orders every caller.
_RHINO_HOST_LOCK = threading.Lock()


def execute_rhino_three_dm_export(
    plan: RhinoCadExportPlan,
    *,
    powershell_executable: Path,
    timeout_seconds: float = 300.0,
    runner: Callable[..., object] | None = None,
    cleanup_runner: Callable[..., object] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    host_wait_seconds: float = 0.0,
) -> RhinoCadExecutionReceipt:
    """Supervise a blocking COM worker, then independently clean its Rhino.

    Only one export drives the host at a time. ``host_wait_seconds`` is how
    long this call waits for the one already running; the default waits not
    at all and answers ``cad_execution.host_busy`` rather than queueing
    invisibly behind work the caller cannot see.
    """

    if not isinstance(plan, RhinoCadExportPlan):
        raise TypeError("plan must be RhinoCadExportPlan")
    wait = _positive_finite(host_wait_seconds, "host_wait_seconds") if host_wait_seconds else 0.0
    acquired = (
        _RHINO_HOST_LOCK.acquire(timeout=wait) if wait else _RHINO_HOST_LOCK.acquire(blocking=False)
    )
    if not acquired:
        return _failure_receipt(
            plan,
            _powershell_executable(powershell_executable).name,
            "cad_execution.host_busy",
            "another export is driving this machine's Rhino; this one was not started",
        )
    try:
        return _execute_rhino_three_dm_export(
            plan,
            powershell_executable=powershell_executable,
            timeout_seconds=timeout_seconds,
            runner=runner,
            cleanup_runner=cleanup_runner,
            monotonic=monotonic,
            sleeper=sleeper,
        )
    finally:
        _RHINO_HOST_LOCK.release()


def _execute_rhino_three_dm_export(
    plan: RhinoCadExportPlan,
    *,
    powershell_executable: Path,
    timeout_seconds: float = 300.0,
    runner: Callable[..., object] | None = None,
    cleanup_runner: Callable[..., object] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> RhinoCadExecutionReceipt:
    """The supervision itself, with the host already held by the caller."""

    timeout = _positive_finite(timeout_seconds, "timeout_seconds")
    executable = _powershell_executable(powershell_executable)
    try:
        _validate_plan_denominator(plan)
    except CadExecutionError as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.validation_denominator_tampered",
            str(exc),
        )
    try:
        _validate_plan_paths(plan, require_script=True)
    except (CadExecutionError, OSError) as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.path_boundary_failed",
            str(exc),
        )
    try:
        current_script_sha = _sha256_bytes(long_path(plan.script_path).read_bytes())
    except OSError as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.script_unreadable",
            str(exc),
        )
    if current_script_sha != plan.script_sha256:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.script_tampered",
            "prepared script SHA-256 changed before execution",
        )
    if plan.model_path.exists() or plan.model_path.is_symlink():
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.output_exists",
            "3dm target must not exist before execution",
        )
    if plan.completion_marker_path.exists() or plan.completion_marker_path.is_symlink():
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.completion_marker_exists",
            "completion marker must not exist before execution",
        )
    if plan.host_witness_path.exists() or plan.host_witness_path.is_symlink():
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.host_witness_exists",
            "host witness must not exist before execution",
        )
    command = build_rhino_com_powershell_command(
        plan,
        powershell_executable=executable,
        timeout_seconds=timeout,
    )
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = subprocess.CREATE_NO_WINDOW
    factory = subprocess.Popen if runner is None else runner
    try:
        worker = factory(
            command,
            cwd=plan.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except OSError as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.process_error",
            str(exc),
        )
    clock = time.monotonic if monotonic is None else monotonic
    pause = time.sleep if sleeper is None else sleeper
    deadline = clock() + timeout
    timed_out = False
    completion_observed = False
    marker_failure: dict[str, str] | None = None
    host_error: dict[str, str] | None = None
    host_witness: dict[str, object] | None = None
    host_witness_sha256: str | None = None
    while True:
        worker_exit = worker.poll()
        if plan.host_witness_path.exists():
            host_witness, host_witness_sha256, host_error = _read_host_witness(plan)
            if host_error is not None and (
                host_error["code"]
                in (
                    "cad_execution.host_witness_unreadable",
                    "cad_execution.host_witness_malformed",
                )
                and worker_exit is None
            ):
                host_error = None
            elif host_error is not None or (
                host_witness is not None
                and host_witness["ownership_status"] != "exact"
            ):
                break
        if plan.completion_marker_path.exists():
            marker_failure = _read_completion_marker(plan)
            if marker_failure is None or marker_failure["code"] == (
                "cad_execution.rhino_script_failed"
            ):
                completion_observed = True
                break
            if not (
                marker_failure["code"]
                in (
                    "cad_execution.completion_marker_unreadable",
                    "cad_execution.completion_marker_malformed",
                )
                and worker_exit is None
            ):
                break
            marker_failure = None
        if worker_exit is not None:
            break
        remaining = deadline - clock()
        if remaining <= 0:
            timed_out = True
            break
        pause(min(0.1, remaining))
    _terminate_spawned_worker(worker)
    stdout, stderr = _collect_worker_output(worker)
    exit_code = getattr(worker, "returncode", None)
    if exit_code is not None:
        exit_code = int(exit_code)
    if host_witness is None and host_error is None:
        host_witness, host_witness_sha256, host_error = _read_host_witness(plan)
    cleanup_status = "cleanup_unverified"
    cleanup_witness_sha256 = None
    cleanup_detail = "host ownership was not exact"
    if (
        host_error is None
        and host_witness is not None
        and host_witness["ownership_status"] == "exact"
    ):
        (
            cleanup_status,
            cleanup_witness_sha256,
            cleanup_detail,
        ) = _run_exact_rhino_cleanup(
            powershell_executable=executable,
            host_witness=host_witness,
            runner=cleanup_runner,
        )
    common_receipt = {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "host_witness_sha256": host_witness_sha256,
        "cleanup_witness_sha256": cleanup_witness_sha256,
        "cleanup_status": cleanup_status,
    }
    if host_error is not None:
        return _failure_receipt(
            plan,
            executable.name,
            host_error["code"],
            host_error["detail"] + "; cleanup=" + cleanup_detail,
            **common_receipt,
        )
    if host_witness is None:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.host_witness_missing",
            "PowerShell worker did not produce an exact host witness",
            **common_receipt,
        )
    if host_witness["ownership_status"] != "exact":
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.host_ownership_ambiguous",
            "Rhino PID set difference was not exactly one; geometry was not run",
            **common_receipt,
        )
    if timed_out and not completion_observed:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.timeout",
            "Rhino geometry did not produce a completion marker before deadline; "
            "cleanup=" + cleanup_detail,
            **common_receipt,
        )
    if marker_failure is None and not completion_observed:
        marker_failure = _read_completion_marker(plan)
    if marker_failure is not None:
        return _failure_receipt(
            plan,
            executable.name,
            marker_failure["code"],
            _with_com_diagnostics(marker_failure["detail"], stdout),
            completion_marker_sha256=marker_failure.get("marker_sha256"),
            **common_receipt,
        )
    if cleanup_status != "confirmed":
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.cleanup_unverified",
            cleanup_detail,
            completion_marker_sha256=plan.completion_witness_sha256,
            **common_receipt,
        )
    try:
        _strict_child(plan.workspace, plan.model_path, require_exists=True)
        inspection = inspect_three_dm(plan.model_path)
    except (CadExecutionError, ThreeDmInspectionError, OSError) as exc:
        return _failure_receipt(
            plan,
            executable.name,
            "cad_execution.readback_failed",
            _with_com_diagnostics(str(exc), stdout),
            completion_marker_sha256=plan.completion_witness_sha256,
            **common_receipt,
        )
    return verify_rhino_export_readback(
        plan,
        inspection,
        executable_name=executable.name,
        process_exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        completion_marker_sha256=plan.completion_witness_sha256,
        host_witness_sha256=host_witness_sha256,
        cleanup_witness_sha256=cleanup_witness_sha256,
        cleanup_status=cleanup_status,
    )


def _rhino_semantic_failures(semantics, inspection, expected_counts) -> list[dict[str, str]]:
    """Compare actual Rhino object/instance witnesses with the requested semantics."""
    failures = []
    expected_total = sum(expected_counts.values())
    semantic_objects = semantics["objects"]
    expected_by_op: dict[str, tuple[str, dict[str, str]]] = {}
    for object_id, row in semantic_objects.items():
        user_text = dict(row["user_text"])
        producer = user_text["archflow:producer_op"]
        if producer in expected_by_op:
            failures.append(
                _failure(
                    "cad_execution.ambiguous_producer",
                    f"producer {producer} names more than one physical object",
                )
            )
        expected_by_op[producer] = (object_id, user_text)

    definition_members = {
        object_id
        for definition in inspection.instance_definitions
        for object_id in definition["object_ids"]
    }
    observed_by_op: dict[str, list[dict[str, object]]] = {}
    tagged_top_level = 0
    for row in inspection.object_user_strings:
        if row["object_id"] in definition_members:
            continue
        attribute_pairs = tuple(
            sorted(
                (pair["key"], pair["value"])
                for pair in row.get("attributes", ())
                if pair["key"].startswith("archflow:")
            )
        )
        attributes = dict(attribute_pairs)
        producer = attributes.get("archflow:producer_op")
        if producer is None:
            continue
        tagged_top_level += 1
        geometry_archflow = tuple(
            sorted(
                (pair["key"], pair["value"])
                for pair in row.get("geometry", ())
                if pair["key"].startswith("archflow:")
            )
        )
        observed_by_op.setdefault(producer, []).append(
            {
                "name": row.get("name"),
                "layer_path": row.get("layer_path"),
                "attribute_pairs": attribute_pairs,
                "geometry_archflow": geometry_archflow,
            }
        )
    if tagged_top_level != expected_total:
        failures.append(
            _failure(
                "cad_execution.semantic_witness_count_mismatch",
                "not every top-level object has one semantic witness",
            )
        )
    if set(observed_by_op) != set(expected_by_op):
        failures.append(
            _failure(
                "cad_execution.physical_identity_mismatch",
                "producer operation set differs from physical denominator",
            )
        )
    for producer, (object_id, expected_text) in expected_by_op.items():
        rows = observed_by_op.get(producer, [])
        expected_semantic = semantic_objects[object_id]
        if len(rows) != expected_counts[object_id]:
            failures.append(
                _failure(
                    "cad_execution.physical_multiplicity_mismatch",
                    f"object {object_id} has wrong persisted multiplicity",
                )
            )
        expected_pairs = tuple(sorted(expected_text.items()))
        if any(
            row["attribute_pairs"] != expected_pairs
            or row["geometry_archflow"]
            for row in rows
        ):
            failures.append(
                _failure(
                    "cad_execution.semantic_user_text_mismatch",
                    f"object {object_id} semantic user text differs",
                )
            )
        if any(row["name"] != expected_semantic["name"] for row in rows):
            failures.append(
                _failure(
                    "cad_execution.object_name_mismatch",
                    f"object {object_id} CAD name differs from physical id",
                )
            )
        if any(row["layer_path"] != expected_semantic["layer"] for row in rows):
            failures.append(
                _failure(
                    "cad_execution.object_layer_mismatch",
                    f"object {object_id} layer differs from semantic contract",
                )
            )

    expected_blocks = dict(semantics["blocks"])
    actual_blocks = {
        item["name"]: int(item["reference_count"])
        for item in inspection.instance_definitions
    }
    if actual_blocks != expected_blocks:
        failures.append(
            _failure(
                "cad_execution.block_mismatch",
                "block definitions or reference counts differ",
            )
        )
    expected_instance_total = sum(expected_blocks.values())
    if len(inspection.instance_references) != expected_instance_total:
        failures.append(
            _failure(
                "cad_execution.block_reference_mismatch",
                "instance reference denominator differs",
            )
        )
    if inspection.object_count != (
        inspection.top_level_object_count
        + inspection.instance_definition_member_count
    ):
        failures.append(
            _failure(
                "cad_execution.inspection_count_inconsistent",
                "3dm object accounting is internally inconsistent",
            )
        )

    return failures


def verify_rhino_export_readback(
    plan: RhinoCadExportPlan,
    inspection: ThreeDmInspection,
    *,
    executable_name: str,
    process_exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    completion_marker_sha256: str | None = None,
    host_witness_sha256: str | None = None,
    cleanup_witness_sha256: str | None = None,
    cleanup_status: str = "not_performed",
) -> RhinoCadExecutionReceipt:
    """Validate the full persisted physical, semantic, and bounds denominator."""

    if not isinstance(plan, RhinoCadExportPlan):
        raise TypeError("plan must be RhinoCadExportPlan")
    if not isinstance(inspection, ThreeDmInspection):
        raise TypeError("inspection must be ThreeDmInspection")
    try:
        _validate_plan_denominator(plan)
    except CadExecutionError as exc:
        return _failure_receipt(
            plan,
            executable_name,
            "cad_execution.validation_denominator_tampered",
            str(exc),
            exit_code=process_exit_code,
            stdout=stdout,
            stderr=stderr,
            completion_marker_sha256=completion_marker_sha256,
            host_witness_sha256=host_witness_sha256,
            cleanup_witness_sha256=cleanup_witness_sha256,
            cleanup_status=cleanup_status,
        )
    failures: list[dict[str, str]] = []
    if completion_marker_sha256 != plan.completion_witness_sha256:
        failures.append(
            _failure(
                "cad_execution.completion_witness_mismatch",
                "exact successful completion marker was not witnessed",
            )
        )
    if host_witness_sha256 is None:
        failures.append(
            _failure(
                "cad_execution.host_witness_missing",
                "exact Rhino host witness was not supplied",
            )
        )
    if cleanup_witness_sha256 is None or cleanup_status != "confirmed":
        failures.append(
            _failure(
                "cad_execution.cleanup_unverified",
                "independent exact-host cleanup was not confirmed",
            )
        )
    if not inspection.read_only or inspection.rhino_process_started:
        failures.append(
            _failure(
                "cad_execution.inspector_not_read_only",
                "verification must be independent and read-only",
            )
        )
    actual_archflow_document = tuple(
        sorted(
            (row["key"], row["value"])
            for row in inspection.document_user_strings
            if row["key"].startswith("archflow:")
        )
    )
    if actual_archflow_document != plan.expected_document_user_text:
        failures.append(
            _failure(
                "cad_execution.provenance_mismatch",
                "archflow document user-text key/value set differs from plan",
            )
        )
    expected_unit = _UNIT_TO_RHINO[plan.identity.length_unit][1]
    if inspection.units.get("name") != expected_unit:
        failures.append(
            _failure("cad_execution.unit_mismatch", "saved model units differ")
        )
    expected_layers = dict(plan.expected_layer_colors)
    actual_layers: dict[str, list[dict[str, object]]] = {}
    for row in inspection.layers:
        full_path = row.get("full_path")
        if isinstance(full_path, str) and (
            full_path == "archflow" or full_path.startswith("archflow::")
        ):
            actual_layers.setdefault(full_path, []).append(row)
    if set(actual_layers) != set(expected_layers) or any(
        len(rows) != 1 for rows in actual_layers.values()
    ):
        failures.append(
            _failure(
                "cad_execution.layer_set_mismatch",
                "archflow layer full-path set has missing, duplicate, or extra layers",
            )
        )
    for full_path in sorted(set(expected_layers) & set(actual_layers)):
        if len(actual_layers[full_path]) != 1:
            continue
        actual_rgba = actual_layers[full_path][0].get("color_rgba")
        expected_rgba = [*expected_layers[full_path], 255]
        if actual_rgba != expected_rgba:
            failures.append(
                _failure(
                    "cad_execution.layer_color_mismatch",
                    f"layer {full_path} RGBA differs from translation contract",
                )
            )

    expected_counts = dict(plan.expected_object_counts)
    expected_total = sum(expected_counts.values())
    if inspection.top_level_object_count != expected_total:
        failures.append(
            _failure(
                "cad_execution.object_count_mismatch",
                "top-level object count differs from physical denominator",
            )
        )
    failures.extend(_rhino_semantic_failures(plan.expected_semantics, inspection, expected_counts))

    visible_witnesses = tuple(inspection.visible_bounds_witnesses)
    expected_leaf_witness_count = (
        inspection.top_level_object_count
        - len(inspection.instance_references)
        + inspection.instance_definition_member_count
    )
    witness_ids = [str(row.get("object_id", "")) for row in visible_witnesses]
    if (
        len(visible_witnesses) != expected_leaf_witness_count
        or len(set(witness_ids)) != len(witness_ids)
    ):
        failures.append(
            _failure(
                "cad_execution.visible_bounds_witness_count_mismatch",
                "saved leaf geometry does not have one unique visible-bounds witness",
            )
        )
    for row in visible_witnesses:
        geometry_type = row.get("type")
        source = row.get("source")
        if geometry_type == "Brep" and (
            source
            not in {
                "brep_face_render_mesh_vertices",
                "explicit_trimmed_render_mesh_witnesses",
            }
            or not isinstance(row.get("mesh_face_count"), int)
            or int(row["mesh_face_count"]) <= 0
            or not isinstance(row.get("mesh_vertex_count"), int)
            or int(row["mesh_vertex_count"]) <= 0
        ):
            failures.append(
                _failure(
                    "cad_execution.visible_bounds_witness_invalid",
                    "Brep bounds are not backed by retained face render-mesh vertices",
                )
            )
        if geometry_type == "Extrusion" and source not in {
            "extrusion_render_mesh_vertices",
            "explicit_trimmed_render_mesh_witnesses",
        }:
            failures.append(
                _failure(
                    "cad_execution.visible_bounds_witness_invalid",
                    "Extrusion bounds are not backed by a retained render mesh",
                )
            )

    expected_blocks = dict(plan.expected_semantics["blocks"])
    array_ops = {
        name.removeprefix("archflow-family-") for name in expected_blocks
    }
    direct_ids = {
        object_id
        for object_id, row in plan.expected_semantics["objects"].items()
        if row["user_text"]["archflow:producer_op"] not in array_ops
    }
    named_rows: dict[str, list[dict[str, object]]] = {}
    for row in inspection.named_object_bboxes:
        named_rows.setdefault(str(row["name"]), []).append(row)
    expected_named_counts = dict(plan.expected_object_counts)
    if set(named_rows) != direct_ids or any(
        len(rows) != expected_named_counts.get(object_id, 1)
        for object_id, rows in named_rows.items()
    ):
        failures.append(
            _failure(
                "cad_execution.named_object_mismatch",
                "named direct-object set has missing, duplicate, or extra identities",
            )
        )
    for object_id in sorted(direct_ids & set(named_rows)):
        named_row = named_rows[object_id][0]
        if named_row.get("type") == "Brep" and named_row.get(
            "bbox_source"
        ) not in {
            "brep_face_render_mesh_vertices",
            "explicit_trimmed_render_mesh_witnesses",
        }:
            failures.append(
                _failure(
                    "cad_execution.named_bounds_witness_invalid",
                    f"object {object_id} bounds do not use retained trimmed mesh vertices",
                )
            )
        actual_bbox = named_row["bbox"]
        expected_bbox = plan.expected_bounds[object_id]
        if not _bbox_close(
            actual_bbox,
            expected_bbox,
            plan.readback_tolerance,
        ):
            failures.append(
                _failure(
                    "cad_execution.named_bounds_mismatch",
                    f"object {object_id} bounds differ",
                )
            )
    expected_aggregate = _aggregate_bounds(plan.expected_bounds.values())
    if inspection.aggregate_bbox is None or not _bbox_close(
        inspection.aggregate_bbox,
        expected_aggregate,
        plan.readback_tolerance,
    ):
        failures.append(
            _failure(
                "cad_execution.aggregate_bounds_mismatch",
                "aggregate saved-model bounds differ",
            )
        )

    return RhinoCadExecutionReceipt(
        status=CadExecutionStatus.FAILED if failures else CadExecutionStatus.SUCCEEDED,
        identity=plan.identity,
        adapter_id="rhino-com-powershell",
        executable_name=executable_name,
        plan_digest=plan.plan_digest,
        script_sha256=plan.script_sha256,
        artifact_relative_path=plan.artifact_relative_path,
        completion_marker_relative_path=plan.completion_marker_relative_path,
        host_witness_relative_path=plan.host_witness_relative_path,
        completion_token=plan.completion_token,
        completion_witness_sha256=plan.completion_witness_sha256,
        completion_marker_sha256=completion_marker_sha256,
        validation_denominator_sha256=plan.validation_denominator_sha256,
        host_witness_sha256=host_witness_sha256,
        cleanup_witness_sha256=cleanup_witness_sha256,
        cleanup_status=cleanup_status,
        process_exit_code=process_exit_code,
        stdout_sha256=_sha256_text(stdout),
        stderr_sha256=_sha256_text(stderr),
        inspection=inspection.to_dict(),
        failures=tuple(failures),
    )


def _saved_geometry_check(source_measures: Mapping[str, Mapping[str, object]] | None) -> tuple[str, ...]:
    """The lines that compare the saved document with the shapes it came from.

    Only an import-based export has shapes to compare against, and this is the
    one place where the host can say what the saved geometry actually is: the
    kernel's solid count, face count, closure and volume are checked against
    the objects in the file that was just written, through RhinoCommon's own
    mass properties, before the completion marker exists. A healed-away
    opening or a solid that arrived as a surface fails the export here rather
    than being reported as an exact work model.
    """

    if not source_measures:
        return ()
    return (
        "_source_measures = json.loads("
        + repr(json.dumps({key: dict(value) for key, value in sorted(source_measures.items())}, sort_keys=True))
        + ")",
        "_saved_by_name = {}",
        "for _saved in _final_archive.Objects:",
        "    _saved_by_name.setdefault(_saved.Attributes.Name or '', []).append(_saved.Geometry)",
        "for _oid in sorted(_source_measures):",
        "    _expected = _source_measures[_oid]",
        "    _geometries = _saved_by_name.get(_oid) or []",
        "    if not _geometries: raise Exception('saved work model has no object named ' + _oid)",
        "    _solids = 0",
        "    _faces = 0",
        "    _volume = 0.0",
        "    for _geometry in _geometries:",
        "        if isinstance(_geometry, Rhino.Geometry.Extrusion): _geometry = _geometry.ToBrep(False)",
        "        if not isinstance(_geometry, Rhino.Geometry.Brep):",
        "            raise Exception(_oid + ': saved geometry is ' + type(_geometry).__name__ + ', not a B-rep')",
        "        _faces += _geometry.Faces.Count",
        "        if _geometry.IsSolid: _solids += 1",
        "        _mass = Rhino.Geometry.VolumeMassProperties.Compute(_geometry)",
        "        if _mass is not None: _volume += _mass.Volume",
        "    if _solids != _expected['solid_count']:",
        "        raise Exception(_oid + ': saved solids ' + str(_solids) + ' != ' + str(_expected['solid_count']))",
        "    if _faces != _expected['face_count']:",
        "        raise Exception(_oid + ': saved faces ' + str(_faces) + ' != ' + str(_expected['face_count']))",
        "    if _expected['closed'] and _solids < 1:",
        "        raise Exception(_oid + ': the exported shape is closed; the saved object is not')",
        "    if _expected.get('volume') is not None:",
        "        _allowed = max(abs(_expected['volume']) * 1e-6, 1e-9)",
        "        if abs(_volume - _expected['volume']) > _allowed:",
        "            raise Exception(_oid + ': saved volume ' + str(_volume) + ' != ' + str(_expected['volume']))",
    )


def _export_script(
    translated_script: str,
    *,
    artifact_name: str,
    completion_marker_name: str,
    completion_token: str,
    length_unit: str,
    readback_tolerance: float,
    patch_prelude: str | None = None,
    source_measures: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    unit_enum = _UNIT_TO_RHINO[length_unit][0]
    mesh_tolerance = _positive_finite(
        readback_tolerance,
        "readback_tolerance",
    ) / 4.0
    mesh_tolerance_literal = format(mesh_tolerance, ".17g")
    success_payload = _completion_marker_payload(
        artifact_relative_path=artifact_name,
        completion_token=completion_token,
        status="succeeded",
    )
    opening = (
        patch_prelude.rstrip("\n")
        if patch_prelude
        else "_existing = rs.AllObjects() or []\nif _existing: rs.DeleteObjects(_existing)"
    )
    body = "\n".join(
        (
            f"Rhino.RhinoDoc.ActiveDoc.AdjustModelUnitSystem(Rhino.UnitSystem.{unit_enum}, False)",
            opening,
            "rs.EnableRedraw(False)",
            translated_script.rstrip("\n"),
            "rs.EnableRedraw(True)",
            "_mesh_type = Rhino.Geometry.MeshType.Render",
            "_mesh_parameters = Rhino.Geometry.MeshingParameters(Rhino.Geometry.MeshingParameters.QualityRenderMesh)",
            "_mesh_parameters.DoublePrecision = True",
            f"_mesh_parameters.Tolerance = {mesh_tolerance_literal}",
            f"_mesh_parameters.MinimumTolerance = {mesh_tolerance_literal}",
            "_archive_meshes = {}",
            "_mesh_objects = {}",
            "for _active_guid in (rs.AllObjects() or []):",
            "    _active_object = Rhino.RhinoDoc.ActiveDoc.Objects.FindId(_active_guid)",
            "    if _active_object is not None:",
            "        _mesh_objects[str(_active_object.Id)] = _active_object",
            "for _instance_definition in Rhino.RhinoDoc.ActiveDoc.InstanceDefinitions:",
            "    if _instance_definition.IsDeleted or _instance_definition.IsReference:",
            "        continue",
            "    for _definition_object in _instance_definition.GetObjects():",
            "        _mesh_objects[str(_definition_object.Id)] = _definition_object",
            "for _rhino_object in sorted(_mesh_objects.values(), key=lambda _item: str(_item.Id)):",
            "    _geometry = _rhino_object.Geometry",
            "    if not isinstance(_geometry, (Rhino.Geometry.Brep, Rhino.Geometry.Extrusion)):",
            "        continue",
            "    _rhino_object.CreateMeshes(_mesh_type, _mesh_parameters, True)",
            "    _retained_meshes = _rhino_object.GetMeshes(_mesh_type)",
            "    _expected_meshes = _geometry.Faces.Count if isinstance(_geometry, Rhino.Geometry.Brep) else 1",
            "    if _retained_meshes is None or len(_retained_meshes) != _expected_meshes:",
            "        raise Exception('retained render-mesh face denominator mismatch')",
            "    _mesh_copies = []",
            "    for _retained_mesh in _retained_meshes:",
            "        if _retained_mesh is None or not _retained_mesh.IsValid or _retained_mesh.Vertices.Count == 0 or _retained_mesh.Faces.Count == 0:",
            "            raise Exception('retained render mesh is missing or invalid')",
            "        _mesh_copies.append(_retained_mesh.DuplicateMesh())",
            "    _archive_meshes[str(_rhino_object.Id)] = _mesh_copies",
            "if not _archive_meshes:",
            "    raise Exception('no meshable document geometry was enumerated')",
            f"_artifact_name = {artifact_name!r}",
            "_output_path = (_script_directory / _artifact_name).resolve()",
            "if _output_path.parent != _script_directory:",
            "    raise Exception('output escaped script workspace')",
            "if _long(_output_path).exists(): raise Exception('output exists')",
            "_raw_path = (_script_directory / (Path(_artifact_name).stem + '.archflow-raw.3dm')).resolve()",
            "if _raw_path.parent != _script_directory or _long(_raw_path).exists():",
            "    raise Exception('raw output path is invalid or occupied')",
            "_write_options = Rhino.FileIO.FileWriteOptions()",
            "_write_options.SuppressDialogBoxes = True",
            "_write_options.IncludeRenderMeshes = True",
            "if not Rhino.RhinoDoc.ActiveDoc.WriteFile(str(_raw_path), _write_options):",
            "    raise Exception('raw 3dm save failed')",
            "_archive = Rhino.FileIO.File3dm.Read(str(_long(_raw_path)))",
            "if _archive is None:",
            "    raise Exception('raw 3dm readback failed inside Rhino')",
            "_archive_sources = {str(_item.Attributes.ObjectId): _item for _item in _archive.Objects}",
            "_initial_archive_object_count = _archive.Objects.Count",
            "_witness_mesh_count = sum(len(_items) for _items in _archive_meshes.values())",
            "for _source_id in sorted(_archive_meshes):",
            "    _source_object = _archive_sources.get(_source_id)",
            "    if _source_object is None:",
            "        raise Exception('archive retained-mesh source identity is missing')",
            "    _saved_meshes = _archive_meshes[_source_id]",
            "    _source_geometry = _source_object.Geometry",
            "    _expected_saved_meshes = _source_geometry.Faces.Count if isinstance(_source_geometry, Rhino.Geometry.Brep) else 1",
            "    if len(_saved_meshes) != _expected_saved_meshes:",
            "        raise Exception('archive retained-mesh source denominator mismatch')",
            "    for _mesh_index, _saved_mesh in enumerate(_saved_meshes):",
            "        _witness_attributes = Rhino.DocObjects.ObjectAttributes()",
            "        _witness_attributes.Name = '__archflow_visible_bounds__:' + _source_id + ':' + format(_mesh_index, '04d')",
            "        _witness_attributes.LayerIndex = _source_object.Attributes.LayerIndex",
            "        _witness_attributes.Visible = False",
            "        _witness_attributes.SetUserString('archflow:visible_bounds_witness_for', _source_id)",
            "        _witness_attributes.SetUserString('archflow:visible_bounds_witness_index', str(_mesh_index))",
            "        _witness_attributes.SetUserString('archflow:visible_bounds_witness_count', str(len(_saved_meshes)))",
            "        _witness_id = _archive.Objects.AddMesh(_saved_mesh, _witness_attributes)",
            "        if str(_witness_id) == '00000000-0000-0000-0000-000000000000':",
            "            raise Exception('failed to add explicit visible-bounds witness mesh')",
            "if _archive.Objects.Count != _initial_archive_object_count + _witness_mesh_count:",
            "    raise Exception('explicit witness mesh archive count mismatch before save')",
            "_archive_options = Rhino.FileIO.File3dmWriteOptions()",
            "_archive_options.Version = 8",
            "_archive_options.SaveRenderMeshes = True",
            "_archive_options.SaveUserData = True",
            "if not _archive.Write(str(_output_path), _archive_options):",
            "    raise Exception('final 3dm save failed')",
            "_final_archive = Rhino.FileIO.File3dm.Read(str(_long(_output_path)))",
            "if _final_archive is None or _final_archive.Objects.Count != _archive.Objects.Count:",
            "    raise Exception('explicit witness mesh archive count mismatch after save')",
            *_saved_geometry_check(source_measures),
            "_long(_raw_path).unlink()",
        )
    )
    indented_body = "\n".join(
        ("    " + line if line else "") for line in body.splitlines()
    )
    return "\n".join(
        (
            "#! python 3",
            "import json",
            "from pathlib import Path",
            "import Rhino",
            "import rhinoscriptsyntax as rs",
            *LONG_PATH_HELPER_SOURCE,
            "_script_directory = Path(__file__).resolve().parent",
            f"_marker_path = _script_directory / {completion_marker_name!r}",
            f"_completion_token = {completion_token!r}",
            "def _write_completion_marker(_payload):",
            "    _text = json.dumps(_payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))",
            # The marker is the one file a failure still has to write, and it
            # sits deepest in the workspace: like every other file call in this
            # script it is made through the extended-length name.
            "    with _long(_marker_path).open('x', encoding='utf-8', newline='\\n') as _stream:",
            "        _stream.write(_text)",
            "try:",
            indented_body,
            "except Exception as _error:",
            "    try:",
            "        _write_completion_marker({",
            "            'schema': 'RhinoCadCompletionMarker@1',",
            f"            'artifact_relative_path': {artifact_name!r},",
            "            'completion_token': _completion_token,",
            "            'status': 'failed',",
            "            'error_type': type(_error).__name__[:128],",
            "            'error_detail': str(_error)[:1000],",
            "        })",
            "    finally:",
            "        raise",
            "else:",
            f"    _write_completion_marker({success_payload!r})",
            "",
        )
    )


def _completion_marker_payload(
    *,
    artifact_relative_path: str,
    completion_token: str,
    status: str,
) -> dict[str, str]:
    return {
        "schema": "RhinoCadCompletionMarker@1",
        "artifact_relative_path": artifact_relative_path,
        "completion_token": completion_token,
        "status": status,
    }


def _completion_token(
    *,
    identity: RhinoCadExportIdentity,
    artifact_relative_path: str,
    translation_sha256: str,
    validation_denominator_sha256: str,
) -> str:
    return canonical_digest(
        {
            "schema": "RhinoCadCompletionToken@1",
            "identity": identity.to_dict(),
            "artifact_relative_path": artifact_relative_path,
            "translation_sha256": translation_sha256,
            "validation_denominator_sha256": validation_denominator_sha256,
        }
    )


def _validation_denominator_digest(
    *,
    identity: RhinoCadExportIdentity,
    translation_sha256: str,
    expected_layer_colors: tuple[tuple[str, tuple[int, int, int]], ...],
    physical_object_ids: tuple[str, ...],
    expected_document_user_text: tuple[tuple[str, str], ...],
    expected_semantics: Mapping[str, object],
    expected_bounds: Mapping[str, object],
    expected_object_counts: tuple[tuple[str, int], ...],
    readback_tolerance: float,
) -> str:
    return canonical_digest(
        {
            "schema": "RhinoCadValidationDenominator@1",
            "identity": identity.to_dict(),
            "translation_sha256": translation_sha256,
            "expected_layer_colors": [
                {"full_path": path, "rgb": list(color)}
                for path, color in expected_layer_colors
            ],
            "physical_object_ids": list(physical_object_ids),
            "expected_document_user_text": [
                {"key": key, "value": value}
                for key, value in expected_document_user_text
            ],
            "expected_semantics": expected_semantics,
            "expected_bounds": expected_bounds,
            "expected_object_counts": [
                {"object_id": object_id, "count": count}
                for object_id, count in expected_object_counts
            ],
            "readback_tolerance": readback_tolerance,
        }
    )


def _validate_plan_denominator(plan: RhinoCadExportPlan) -> None:
    actual = _validation_denominator_digest(
        identity=plan.identity,
        translation_sha256=plan.translation_sha256,
        expected_layer_colors=plan.expected_layer_colors,
        physical_object_ids=plan.physical_object_ids,
        expected_document_user_text=plan.expected_document_user_text,
        expected_semantics=plan.expected_semantics,
        expected_bounds=plan.expected_bounds,
        expected_object_counts=plan.expected_object_counts,
        readback_tolerance=plan.readback_tolerance,
    )
    if actual != plan.validation_denominator_sha256:
        raise CadExecutionError(
            "canonical validation denominator changed after plan preparation"
        )


def _marker_text(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_completion_marker(
    plan: RhinoCadExportPlan,
) -> dict[str, str] | None:
    """Return ``None`` only for the exact successful marker witness."""

    if not long_path(plan.completion_marker_path).exists():
        return _failure(
            "cad_execution.completion_marker_missing",
            "Rhino COM command returned without a completion marker",
        )
    try:
        _strict_child(
            plan.workspace,
            plan.completion_marker_path,
            require_exists=True,
        )
        marker_bytes = long_path(plan.completion_marker_path).read_bytes()
    except (CadExecutionError, OSError) as exc:
        return _failure("cad_execution.completion_marker_unreadable", str(exc))
    marker_sha256 = _sha256_bytes(marker_bytes)
    if len(marker_bytes) > 8_192:
        result = _failure(
            "cad_execution.completion_marker_malformed",
            "completion marker exceeds bounded size",
        )
        result["marker_sha256"] = marker_sha256
        return result
    try:
        payload = json.loads(marker_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result = _failure("cad_execution.completion_marker_malformed", str(exc))
        result["marker_sha256"] = marker_sha256
        return result
    if not isinstance(payload, dict):
        result = _failure(
            "cad_execution.completion_marker_malformed",
            "completion marker must be a JSON object",
        )
        result["marker_sha256"] = marker_sha256
        return result
    shared = {
        "schema": "RhinoCadCompletionMarker@1",
        "artifact_relative_path": plan.artifact_relative_path,
        "completion_token": plan.completion_token,
    }
    if any(payload.get(key) != value for key, value in shared.items()):
        result = _failure(
            "cad_execution.completion_marker_mismatch",
            "completion marker does not bind the exact export plan",
        )
        result["marker_sha256"] = marker_sha256
        return result
    if payload.get("status") == "failed":
        allowed = {*shared, "status", "error_type", "error_detail"}
        if set(payload) != allowed or any(
            not isinstance(payload.get(key), str)
            for key in ("error_type", "error_detail")
        ):
            result = _failure(
                "cad_execution.completion_marker_malformed",
                "failure marker schema drifted",
            )
        else:
            result = _failure(
                "cad_execution.rhino_script_failed",
                f"{str(payload['error_type'])[:128]}: "
                f"{str(payload['error_detail'])[:1000]}",
            )
        result["marker_sha256"] = marker_sha256
        return result
    expected = _completion_marker_payload(
        artifact_relative_path=plan.artifact_relative_path,
        completion_token=plan.completion_token,
        status="succeeded",
    )
    if payload != expected or marker_sha256 != plan.completion_witness_sha256:
        result = _failure(
            "cad_execution.completion_marker_mismatch",
            "successful marker differs from the exact digest witness",
        )
        result["marker_sha256"] = marker_sha256
        return result
    return None


def _read_host_witness(
    plan: RhinoCadExportPlan,
) -> tuple[dict[str, object] | None, str | None, dict[str, str] | None]:
    if not long_path(plan.host_witness_path).exists():
        return (
            None,
            None,
            _failure(
                "cad_execution.host_witness_missing",
                "PowerShell worker did not create a host witness",
            ),
        )
    try:
        _strict_child(plan.workspace, plan.host_witness_path, require_exists=True)
        witness_bytes = long_path(plan.host_witness_path).read_bytes()
    except (CadExecutionError, OSError) as exc:
        return (
            None,
            None,
            _failure("cad_execution.host_witness_unreadable", str(exc)),
        )
    witness_sha256 = _sha256_bytes(witness_bytes)
    if len(witness_bytes) > 8_192:
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_malformed",
                "host witness exceeds bounded size",
            ),
        )
    try:
        payload = json.loads(witness_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return (
            None,
            witness_sha256,
            _failure("cad_execution.host_witness_malformed", str(exc)),
        )
    expected_keys = {
        "schema",
        "completion_token",
        "ownership_status",
        "new_pid_count",
        "pid",
        "executable_path",
        "start_time_utc_ticks",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_malformed",
                "host witness schema drifted",
            ),
        )
    if (
        payload["schema"] != "RhinoCadHostWitness@1"
        or payload["completion_token"] != plan.completion_token
        or payload["ownership_status"] not in ("exact", "ambiguous")
        or not isinstance(payload["new_pid_count"], int)
        or isinstance(payload["new_pid_count"], bool)
        or payload["new_pid_count"] < 0
    ):
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_mismatch",
                "host witness does not bind the exact execution plan",
            ),
        )
    if payload["ownership_status"] == "ambiguous":
        if any(
            payload[field] is not None
            for field in ("pid", "executable_path", "start_time_utc_ticks")
        ):
            return (
                None,
                witness_sha256,
                _failure(
                    "cad_execution.host_witness_malformed",
                    "ambiguous witness cannot assert a Rhino identity",
                ),
            )
        return _json_copy(payload), witness_sha256, None
    executable_path = payload["executable_path"]
    if (
        payload["new_pid_count"] != 1
        or not isinstance(payload["pid"], int)
        or isinstance(payload["pid"], bool)
        or payload["pid"] <= 0
        or not isinstance(executable_path, str)
        or any(character in executable_path for character in "\x00\r\n")
        or not PureWindowsPath(executable_path).is_absolute()
        or PureWindowsPath(executable_path).name.lower() != "rhino.exe"
        or not isinstance(payload["start_time_utc_ticks"], int)
        or isinstance(payload["start_time_utc_ticks"], bool)
        or payload["start_time_utc_ticks"] <= 0
    ):
        return (
            None,
            witness_sha256,
            _failure(
                "cad_execution.host_witness_malformed",
                "exact host witness lacks PID/path/start-time identity",
            ),
        )
    return _json_copy(payload), witness_sha256, None


def _terminate_spawned_worker(worker: object) -> None:
    try:
        if worker.poll() is not None:
            return
        worker.terminate()
        try:
            worker.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=2.0)
    except (OSError, ProcessLookupError, AttributeError):
        return


def _collect_worker_output(worker: object) -> tuple[str, str]:
    try:
        stdout, stderr = worker.communicate(timeout=1.0)
    except (subprocess.TimeoutExpired, AttributeError):
        stdout = getattr(worker, "stdout_text", "")
        stderr = getattr(worker, "stderr_text", "")
    return str(stdout or "")[:8_192], str(stderr or "")[:8_192]


def _run_exact_rhino_cleanup(
    *,
    powershell_executable: Path,
    host_witness: Mapping[str, object],
    runner: Callable[..., object] | None,
) -> tuple[str, str | None, str]:
    command = _build_rhino_cleanup_powershell_command(
        powershell_executable=powershell_executable,
        host_witness=host_witness,
    )
    execute = subprocess.run if runner is None else runner
    try:
        completed = execute(
            command,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "cleanup_unverified", None, str(exc)[:1_000]
    stdout = str(getattr(completed, "stdout", "") or "")
    if len(stdout.encode("utf-8", errors="replace")) > 8_192:
        return "cleanup_unverified", None, "cleanup JSON exceeds bounded size"
    try:
        payload = json.loads(stdout.strip())
    except (json.JSONDecodeError, UnicodeError) as exc:
        return "cleanup_unverified", None, ("invalid cleanup JSON: " + str(exc))[:1_000]
    expected_keys = {
        "schema",
        "completion_token",
        "pid",
        "status",
        "identity_matched",
        "stop_requested",
        "cleanup_confirmed",
        "error_detail",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return "cleanup_unverified", None, "cleanup witness schema drifted"
    witness_sha256 = _sha256_text(_marker_text(payload))
    valid = (
        payload["schema"] == "RhinoCadCleanupWitness@1"
        and payload["completion_token"] == host_witness["completion_token"]
        and payload["pid"] == host_witness["pid"]
        and payload["status"] in (
            "already_exited",
            "stopped",
            "identity_mismatch",
            "cleanup_unverified",
        )
        and isinstance(payload["identity_matched"], bool)
        and isinstance(payload["stop_requested"], bool)
        and isinstance(payload["cleanup_confirmed"], bool)
        and (payload["error_detail"] is None or isinstance(payload["error_detail"], str))
    )
    if not valid:
        return "cleanup_unverified", witness_sha256, "cleanup witness values drifted"
    returncode = int(getattr(completed, "returncode", 1))
    if (
        returncode == 0
        and payload["cleanup_confirmed"] is True
        and payload["status"] in ("already_exited", "stopped")
        and (
            payload["status"] == "already_exited"
            or (
                payload["identity_matched"] is True
                and payload["stop_requested"] is True
            )
        )
    ):
        return "confirmed", witness_sha256, str(payload["status"])
    detail = payload["error_detail"] or str(payload["status"])
    return "cleanup_unverified", witness_sha256, str(detail)[:1_000]


def _provenance(
    identity: "RhinoCadExportIdentity | OcctCadExportIdentity",
    extra: Mapping[str, str] | None,
    *,
    export_schema: str | None = None,
) -> dict[str, str]:
    binding = identity.binding
    base = binding.branch.run.base
    if export_schema is None:
        export_schema = RhinoCadExecutionReceipt.SCHEMA
    values = {
        "project_id": binding.project_id,
        "run_id": binding.run_id,
        "branch": binding.branch.branch_id,
        "branch_epoch": str(binding.branch.epoch),
        "stage_id": binding.stage_id,
        "program_digest": binding.program_digest,
        "program_record_uri": binding.program_ref.uri,
        "program_record_sha256": binding.program_ref.sha256,
        "base_version": str(base.version),
        "base_state_sha256": base.require_digest(),
        "design_state_digest": binding.design_state_digest,
        "predecessor_program_digest": (
            binding.predecessor_program_digest or "none"
        ),
        "length_unit": identity.length_unit,
        "up_axis": identity.up_axis,
        "export_schema": export_schema,
    }
    if extra is not None:
        if not isinstance(extra, Mapping):
            raise TypeError("provenance must be a mapping")
        for key, value in extra.items():
            require_identifier(key, "provenance key")
            if key in _RESERVED_PROVENANCE:
                raise CadExecutionError(
                    f"provenance cannot override reserved key: {key}"
                )
            if not isinstance(value, str) or not value:
                raise CadExecutionError("provenance values must be non-empty text")
            values[key] = value
    return values


def _strict_workspace(value: Path) -> Path:
    if not isinstance(value, Path):
        raise TypeError("speculative_workspace must be pathlib.Path")
    if not value.is_absolute():
        raise CadExecutionError("speculative_workspace must be absolute")
    if value.is_symlink():
        raise CadExecutionError("speculative_workspace cannot be a symlink")
    try:
        resolved = value.resolve(strict=True)
    except OSError as exc:
        raise CadExecutionError("speculative_workspace is unavailable") from exc
    if value.absolute() != resolved or not resolved.is_dir():
        raise CadExecutionError(
            "speculative_workspace must be a real existing directory without symlink ancestors"
        )
    return resolved


def _strict_child(workspace: Path, target: Path, *, require_exists: bool) -> None:
    root = _strict_workspace(workspace)
    if not target.is_absolute() or target.parent != root:
        raise CadExecutionError("CAD target escaped the speculative workspace")
    if target.is_symlink():
        raise CadExecutionError("CAD target cannot be a symlink")
    if require_exists:
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise CadExecutionError("required CAD target is unavailable") from exc
        if resolved.parent != root or not resolved.is_file():
            raise CadExecutionError("CAD target containment changed")
    elif target.exists():
        resolved = target.resolve(strict=True)
        if resolved.parent != root:
            raise CadExecutionError("CAD target containment changed")


def _validate_plan_paths(plan: RhinoCadExportPlan, *, require_script: bool) -> None:
    root = _strict_workspace(plan.workspace)
    _strict_child(root, plan.script_path, require_exists=require_script)
    _strict_child(root, plan.model_path, require_exists=False)
    _strict_child(root, plan.completion_marker_path, require_exists=False)
    _strict_child(root, plan.host_witness_path, require_exists=False)
    if plan.script_path.name != f"{plan.model_path.stem}.archflow.py":
        raise CadExecutionError("script/model plan names are inconsistent")
    if plan.completion_marker_path.name != (
        f"{plan.model_path.stem}.archflow-completion.json"
    ):
        raise CadExecutionError("completion marker/model plan names are inconsistent")
    if plan.host_witness_path.name != f"{plan.model_path.stem}.archflow-host.json":
        raise CadExecutionError("host witness/model plan names are inconsistent")
    _artifact_name(plan.model_path.name)


def _artifact_name(value: str) -> str:
    _portable_relative_path(value)
    path = PurePosixPath(value)
    if len(path.parts) != 1 or path.suffix.lower() != ".3dm":
        raise CadExecutionError("artifact_name must be one portable .3dm filename")
    return value


def _portable_relative_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CadExecutionError("artifact path must be portable relative text")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CadExecutionError("artifact path must stay inside workspace")


def _powershell_executable(value: Path) -> Path:
    if not isinstance(value, Path):
        raise TypeError("powershell_executable must be pathlib.Path")
    if value.is_symlink():
        raise CadExecutionError("powershell_executable cannot be a symlink")
    resolved = value.resolve(strict=True)
    if not resolved.is_file() or resolved.name.lower() not in (
        "powershell.exe",
        "pwsh.exe",
    ):
        raise CadExecutionError(
            "powershell_executable must name an existing PowerShell executable"
        )
    return resolved


def _powershell_literal(value: str) -> str:
    if not isinstance(value, str) or any(character in value for character in "\x00\r\n"):
        raise CadExecutionError("PowerShell literal contains unsafe characters")
    return "'" + value.replace("'", "''") + "'"


def _bounds_to_rhino(value: Mapping[str, object]) -> dict[str, object]:
    minimum = value["bbox_min"]
    maximum = value["bbox_max"]
    return {
        "min": [minimum[0], minimum[2], minimum[1]],
        "max": [maximum[0], maximum[2], maximum[1]],
    }


def _aggregate_bounds(values) -> dict[str, list[float]]:
    rows = tuple(values)
    return {
        "min": [min(row["min"][axis] for row in rows) for axis in range(3)],
        "max": [max(row["max"][axis] for row in rows) for axis in range(3)],
    }


def _bbox_close(
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    tolerance: float,
) -> bool:
    try:
        return all(
            abs(float(actual[corner][axis]) - float(expected[corner][axis]))
            <= tolerance
            for corner in ("min", "max")
            for axis in range(3)
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _positive_finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise CadExecutionError(f"{field} must be positive and finite")
    return float(value)


def _failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": str(detail)[:_MAX_PROCESS_TEXT]}


def _with_com_diagnostics(detail: str, stdout: str) -> str:
    """Append only bounded, allow-listed fields from the bridge JSON result."""

    if not isinstance(stdout, str) or not stdout:
        return detail
    payload = None
    for line in reversed(stdout[-8_192:].splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(candidate, dict)
            and candidate.get("schema") == "RhinoComPowerShellWorkerResult@4"
        ):
            payload = candidate
            break
    if payload is None:
        return detail
    error_detail = payload.get("error_detail")
    ownership_status = payload.get("ownership_status")
    parts: list[str] = []
    if isinstance(error_detail, str) and error_detail:
        parts.append("com_error=" + error_detail[:1_000])
    if ownership_status in ("not_observed", "exact", "ambiguous"):
        parts.append("ownership=" + str(ownership_status))
    if not parts:
        return detail
    return (detail + "; " + "; ".join(parts))[:_MAX_PROCESS_TEXT]


def _failure_receipt(
    plan: RhinoCadExportPlan,
    executable_name: str,
    code: str,
    detail: str,
    *,
    exit_code: int | None = None,
    stdout: object = "",
    stderr: object = "",
    completion_marker_sha256: str | None = None,
    host_witness_sha256: str | None = None,
    cleanup_witness_sha256: str | None = None,
    cleanup_status: str = "not_performed",
) -> RhinoCadExecutionReceipt:
    return RhinoCadExecutionReceipt(
        status=CadExecutionStatus.FAILED,
        identity=plan.identity,
        adapter_id="rhino-com-powershell",
        executable_name=executable_name,
        plan_digest=plan.plan_digest,
        script_sha256=plan.script_sha256,
        artifact_relative_path=plan.artifact_relative_path,
        completion_marker_relative_path=plan.completion_marker_relative_path,
        host_witness_relative_path=plan.host_witness_relative_path,
        completion_token=plan.completion_token,
        completion_witness_sha256=plan.completion_witness_sha256,
        completion_marker_sha256=completion_marker_sha256,
        validation_denominator_sha256=plan.validation_denominator_sha256,
        host_witness_sha256=host_witness_sha256,
        cleanup_witness_sha256=cleanup_witness_sha256,
        cleanup_status=cleanup_status,
        process_exit_code=exit_code,
        stdout_sha256=_sha256_text("" if stdout is None else str(stdout)),
        stderr_sha256=_sha256_text("" if stderr is None else str(stderr)),
        inspection=None,
        failures=(_failure(code, detail),),
    )


def long_path(path: Path) -> Path:
    """The same file, named so Windows accepts it past about 260 characters.

    A run's export workspace - project, run id, stage, seat, attempt - reaches
    that length easily, and the ordinary name then fails to open on a host
    whose interpreter does not honour the machine's long-path setting. The
    extended-length form is the identical file.

    Where it is used is decided by what each writer and reader was actually
    observed to accept on a real Rhino host, never by generalizing from one of
    them: Python's own file calls and Rhino's readers (``File3dm.Read``,
    ``FileStp.Read``) take it; ``RhinoDoc.WriteFile`` was seen to refuse it and
    keeps the ordinary absolute name, and the ``File3dm`` archive write - which
    real exports write through on that ordinary name - keeps it too, its
    acceptance of the prefix never having been tested separately. Nothing
    persisted changes either way - receipts keep the ordinary relative
    identities.
    """

    text = os.fspath(path)
    if os.name != "nt":
        return Path(text)
    extended = chr(92) * 2 + "?" + chr(92)
    text = os.path.abspath(text)
    if text.startswith(extended):
        return Path(text)
    if text.startswith(chr(92) * 2):
        return Path(extended + "UNC" + text[1:])
    return Path(extended + text)


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_copy(value: object):
    return json.loads(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


# ---------------------------------------------------------------- OCCT in-process executor (P107)
#
# A second executor of the same CompiledGeometryProgram behind this owner.
# It shares the neutral P036 CadProgramBinding (the historical Rhino name
# remains a compatibility alias), the analytic predictor (expected_object_bounds), the
# semantic denominator (expected_object_semantics) and the workspace rules;
# it does not share the Rhino plan, its completion token, host witness or
# cleanup receipt, because no host process exists.  Evidence tier is
# ``self_measured_cold_read``: the STEP file is re-read from disk by a fresh
# reader and measured; the Rhino gate remains the independent instrument.

OCCT_ADAPTER_ID = "occt-in-process"
OCCT_EVIDENCE_TIER = "self_measured_cold_read"
_STEP_FORMAT = "STEP AP214 (ISO 10303-21)"
_PREVIEW_FORMAT = "3dm render-mesh preview"
_PREVIEW_ANGULAR_DEFLECTION = 0.5

# Preview materials for an assembly's members, by role (P107 lane C).  A
# FRAME member wears the material its component declares (name and display
# colour from the caller's assignment), or, with no declaration, an opaque
# material named after the role in the layer colour darkened by this factor
# so the frame reads against its wall.  GLAZING has no declared vocabulary
# yet: it always takes the documented glass fallback, a light tint with
# non-zero openNURBS transparency.  Objects outside those roles keep the
# layer's display colour, as before.
_FRAME_FALLBACK_SHADE = 0.55
_GLAZING_FALLBACK = PreviewMaterial(name=AssemblyRole.GLAZING.value, diffuse=(150, 200, 225), transparency=0.6)
# openNURBS stores transparency as a double; the readback compares the
# inspector's native value with the declared one within this tolerance.
_PREVIEW_TRANSPARENCY_TOLERANCE = 1.0e-6


def _preview_materials(
    program: CompiledGeometryProgram,
    *,
    physical: tuple[str, ...],
    semantics: Mapping[str, object],
    layer_colors: Mapping[str, tuple[int, int, int]],
    material_colors: Mapping[str, tuple[int, int, int]] | None,
) -> dict[str, PreviewMaterial]:
    """The declared native material each delivered object wears, keyed by object id.

    Every declared component material is carried into the preview. Roles
    come from ``program.proposal.assemblies`` alone, never from an object's
    name: GLAZING keeps its glass fallback and an undeclared FRAME is shaded.
    """

    objects = semantics["objects"]
    materials: dict[str, PreviewMaterial] = {}
    for object_id in physical:
        row = objects[object_id]
        declared = row["user_text"].get("archflow:material")
        if declared:
            layer_color = layer_colors.get(row["layer"], (0, 0, 0))
            color = (material_colors or {}).get(declared, layer_color)
            materials[object_id] = PreviewMaterial(name=declared, diffuse=tuple(int(c) for c in color))
    for assembly in program.proposal.assemblies:
        for object_id in assembly.objects_for(AssemblyRole.GLAZING):
            if object_id in physical:
                materials[object_id] = _GLAZING_FALLBACK
        for object_id in assembly.objects_for(AssemblyRole.FRAME):
            if object_id not in physical or object_id in materials:
                continue
            row = objects[object_id]
            layer_color = layer_colors.get(row["layer"], (0, 0, 0))
            shaded = tuple(int(round(channel * _FRAME_FALLBACK_SHADE)) for channel in layer_color)
            materials[object_id] = PreviewMaterial(name=AssemblyRole.FRAME.value, diffuse=shaded)
    return materials


class CadCapabilityError(CadExecutionError):
    """The program names an operation this executor does not realize.

    Raised before anything is written.  There is no fallback to another
    executor: the caller chooses one explicitly.
    """

    def __init__(self, message: str, *, op_id: str, kind: str) -> None:
        super().__init__(message)
        self.op_id = op_id
        self.kind = kind


@dataclass(frozen=True, slots=True)
class OcctCadExportIdentity:
    """Execution identity of one in-process export: the binding, the unit, the frame."""

    binding: RhinoCadProgramBinding
    length_unit: str
    up_axis: str = "Z-up"

    SCHEMA = "OcctCadExportIdentity@1"
    COORDINATE_FRAME = (
        "program (x, y-up, z-plan) written as CAD (x, z, y): the one Z-up "
        "conversion the Rhino translation applies, applied once here"
    )

    def __post_init__(self) -> None:
        if not isinstance(self.binding, RhinoCadProgramBinding):
            raise TypeError("binding must be RhinoCadProgramBinding")
        if self.length_unit not in _UNIT_TO_RHINO:
            raise CadExecutionError(
                "length_unit must be millimeter, meter, inch, or foot"
            )
        if self.up_axis != "Z-up":
            raise CadExecutionError("OCCT CAD externalization requires Z-up")

    @property
    def project_id(self) -> str:
        return self.binding.project_id

    @property
    def run_id(self) -> str:
        return self.binding.run_id

    @property
    def program_digest(self) -> str:
        return self.binding.program_digest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding": self.binding.to_dict(),
            "length_unit": self.length_unit,
            "up_axis": self.up_axis,
            "coordinate_frame": self.COORDINATE_FRAME,
        }


@dataclass(frozen=True, slots=True)
class OcctExecutionReceipt:
    """What crossed the file boundary and what the cold read found there.

    ``exact_artifact`` names the STEP file (exact B-rep, object names and
    layers); ``preview_artifact`` names the render-mesh/curve ``.3dm`` from
    the same model with viewer semantics. ``readback``
    is the per-object measurement of the STEP file re-read from disk.
    """

    status: CadExecutionStatus
    identity: OcctCadExportIdentity
    adapter_id: str
    backend: dict[str, object]
    evidence_tier: str
    exact_artifact: dict[str, object] | None
    preview_artifact: dict[str, object] | None
    physical_object_ids: tuple[str, ...]
    expected_semantics: dict[str, object]
    expected_bounds: dict[str, dict[str, object]]
    readback: dict[str, dict[str, object]] | None
    preview_inspection: dict[str, object] | None
    readback_tolerance: float
    timings: dict[str, float]
    failures: tuple[dict[str, str], ...]
    reused_object_ids: tuple[str, ...] = ()

    SCHEMA = "OcctExecutionReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.status, CadExecutionStatus):
            raise TypeError("status must be CadExecutionStatus")
        if not isinstance(self.identity, OcctCadExportIdentity):
            raise TypeError("identity must be OcctCadExportIdentity")
        require_identifier(self.adapter_id, "adapter_id")
        if not isinstance(self.backend, dict):
            raise TypeError("backend must be dict")
        if not isinstance(self.reused_object_ids, tuple) or set(self.reused_object_ids) - set(self.physical_object_ids):
            raise CadExecutionError("reused objects must belong to the exported physical denominator")
        if not isinstance(self.evidence_tier, str) or not self.evidence_tier:
            raise CadExecutionError("evidence_tier must be non-empty text")
        for field in ("exact_artifact", "preview_artifact"):
            value = getattr(self, field)
            if value is not None:
                if not isinstance(value, dict):
                    raise TypeError(f"{field} must be dict or None")
                _portable_relative_path(str(value.get("relative_path")))
                require_sha256(str(value.get("sha256")), f"{field} sha256")
        if self.physical_object_ids != tuple(sorted(set(self.physical_object_ids))):
            raise CadExecutionError("physical_object_ids must be sorted and unique")
        for value in self.physical_object_ids:
            require_identifier(value, "physical_object_ids")
        if not isinstance(self.expected_semantics, dict) or set(
            self.expected_semantics
        ) != {"objects", "blocks"}:
            raise CadExecutionError("expected_semantics schema drifted")
        if not isinstance(self.expected_bounds, dict) or set(
            self.expected_bounds
        ) != set(self.physical_object_ids):
            raise CadExecutionError("bounds and physical denominators differ")
        if self.readback is not None and not isinstance(self.readback, dict):
            raise TypeError("readback must be dict or None")
        if self.preview_inspection is not None and not isinstance(
            self.preview_inspection, dict
        ):
            raise TypeError("preview_inspection must be dict or None")
        object.__setattr__(
            self,
            "readback_tolerance",
            _positive_finite(self.readback_tolerance, "readback_tolerance"),
        )
        if not isinstance(self.timings, dict) or any(
            not isinstance(value, float) for value in self.timings.values()
        ):
            raise TypeError("timings must be dict[str, float]")
        if not isinstance(self.failures, tuple) or any(
            not isinstance(item, dict) for item in self.failures
        ):
            raise TypeError("failures must be tuple[dict, ...]")
        succeeded = self.status is CadExecutionStatus.SUCCEEDED
        if succeeded != (
            not self.failures
            and self.exact_artifact is not None
            and self.readback is not None
            and set(self.readback) == set(self.physical_object_ids)
        ):
            raise CadExecutionError(
                "successful execution requires a written STEP file, a complete cold readback and no failures"
            )

    @property
    def readback_verified(self) -> bool:
        return self.status is CadExecutionStatus.SUCCEEDED

    def to_dict(self) -> dict[str, object]:
        return _json_copy(
            {
                "schema": self.SCHEMA,
                "status": self.status.value,
                "identity": self.identity.to_dict(),
                "adapter_id": self.adapter_id,
                "backend": self.backend,
                "evidence_tier": self.evidence_tier,
                "exact_artifact": self.exact_artifact,
                "preview_artifact": self.preview_artifact,
                "physical_object_ids": list(self.physical_object_ids),
                "expected_semantics": self.expected_semantics,
                "expected_bounds": self.expected_bounds,
                "readback": self.readback,
                "preview_inspection": self.preview_inspection,
                "readback_tolerance": self.readback_tolerance,
                "timings": self.timings,
                "failures": list(self.failures),
                "readback_verified": self.readback_verified,
                **({"reused_object_ids": list(self.reused_object_ids)} if self.reused_object_ids else {}),
            }
        )


@contextmanager
def _occt_step(observer, phase: str, *, parent_event_id, timings, timing_key: str, details):
    """Publish the same measured interval retained by the execution receipt."""

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    outcome = {"status": "succeeded", "details": details}
    try:
        yield outcome
    except BaseException:
        outcome["status"] = "failed"
        raise
    finally:
        elapsed = time.perf_counter() - started
        timings[timing_key] = elapsed
        if outcome["status"] != "succeeded":
            for field in ("emitted_object_ids", "output_refs"):
                if field in details:
                    details[field] = []
        _observe_occt_operation(observer, phase=phase, status=outcome["status"],
            started_at=started_at, ended_at=datetime.now(timezone.utc), duration_ms=round(elapsed * 1000),
            parent_event_id=parent_event_id, details=deepcopy(details))


def execute_occt_export(
    program: CompiledGeometryProgram,
    *,
    binding: RhinoCadProgramBinding,
    speculative_workspace: Path,
    artifact_stem: str,
    readback_tolerance: float = 0.003,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
    layer_by_component: Mapping[str, str] | None = None,
    preview: bool = True,
    prior_program: CompiledGeometryProgram | None = None,
    prior_step: Path | None = None,
    prior_step_sha256: str | None = None,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    observation_parent_id: str | None = None,
) -> OcctExecutionReceipt:
    """Realize the bound program, write STEP and a mesh/curve preview, cold-read both.

    Writes ``<artifact_stem>.step`` (exact B-rep, one named shape per
    physical object) and ``<artifact_stem>.preview.3dm`` (render meshes and native curves of
    the same model with the viewer's names, layers, colours and
    ``archflow:*`` user text) into the caller-supplied workspace; neither may
    exist beforehand.  The STEP file is then re-read by a fresh reader and
    every physical object is checked against what its producer operation
    declared: present exactly once under its id, valid, bounds within
    ``readback_tolerance`` of the analytic predictor, on its semantic layer,
    and either exactly the expected number of closed solids (every
    solid operation) or an open surface - no solid, an
    actual open boundary, no volume claimed) or a polyline with its original
    vertices, endpoints and length. The preview is read back
    through ``inspect_three_dm`` and checked against the same denominator.
    No process is started.

    An explicitly supplied prior program and verified STEP may contribute
    unchanged shapes. Only changed geometry is built; the complete current
    model is written and independently read back under the current binding.

    Raises ``CadCapabilityError`` (a ``CadExecutionError``) before writing
    when the program uses an operation this executor does not realize, and
    ``CadExecutionError`` when the binding, workspace or backend is unusable.
    Build, write and readback failures come back as a FAILED receipt.

    Integration recipe (runtime.project_runner._export, once released):

        program_ref = repository.put_json(run=run, destination=branch_destination,
                                          record_kind=stage_geometry_program(stage_id),
                                          payload=program.to_dict())          # P036 first, as today
        binding = CadProgramBinding(program_ref=program_ref, branch=branch, stage_id=stage_id,
                                    program_digest=program.program_digest,
                                    design_state_digest=program.proposal.design_state_digest,
                                    predecessor_program_digest=None)
        receipt = execute_occt_export(program, binding=binding, speculative_workspace=workspace,
                                      artifact_stem=f"{stage_id}@{program.program_digest[:12]}",
                                      readback_tolerance=0.003, provenance={**provenance, "export_path": "occt"})
        repository.put_json(run=run, destination=destination, record_kind=<new occt execution kind>,
                            payload=receipt.to_dict())
        # receipt.preview_artifact["relative_path"] is what the viewer loads;
        # receipt.exact_artifact["relative_path"] is the STEP delivery.
        # The Rhino path stays opt-in behind its own flag and is never
        # launched by create/save/reopen/preview.
    """

    if not isinstance(binding, RhinoCadProgramBinding):
        raise TypeError("binding must be RhinoCadProgramBinding")
    binding.bind_program(program)
    unit = program.proposal.length_unit.value
    identity = OcctCadExportIdentity(binding=binding, length_unit=unit)
    if operation_observer is not None:
        caller_observer = operation_observer

        def observe_program_operation(event):
            caller_observer({**event, "source_ref": event.get("source_ref") or binding.program_ref.uri})

        operation_observer = observe_program_operation
    tolerance = _positive_finite(readback_tolerance, "readback_tolerance")
    workspace = _strict_workspace(speculative_workspace)
    stem = _artifact_stem(artifact_stem)
    step_path = workspace / f"{stem}.step"
    preview_path = workspace / f"{stem}.preview.3dm"
    for target in (step_path, preview_path):
        _strict_child(workspace, target, require_exists=False)
        if target.exists() or target.is_symlink():
            raise CadExecutionError(f"speculative output already exists: {target.name}")
    try:
        semantics = _json_copy(
            expected_object_semantics(
                program,
                material_by_component=material_by_component,
                layer_by_component=layer_by_component,
            )
        )
        raw_bounds = expected_object_bounds(program)
    except CadTranslationError as exc:
        raise CadExecutionError(f"program denominator is not analytically determined: {exc}") from exc
    physical = tuple(sorted(semantics["objects"]))
    if set(raw_bounds) != set(physical):
        raise CadExecutionError("analytic bounds and physical denominators differ")
    bounds = {object_id: _bounds_to_rhino(raw_bounds[object_id]) for object_id in physical}
    counts = {object_id: int(raw_bounds[object_id]["brep_count"]) for object_id in physical}
    deliveries = _declared_deliveries(program, physical)
    curves = {
        operation.output_object_ids[0]: [[x, z, y] for x, y, z in lift_to_base_level(
            _params(operation)["points"], _params(operation), operation.op_id)]
        for operation in program.proposal.operations
        if operation.kind.value == "curve" and operation.output_object_ids[0] in physical
    }
    layer_colors = dict(
        _resolved_layer_colors(
            {row["layer"] for row in semantics["objects"].values()},
            material_by_component=material_by_component,
            material_colors=material_colors,
        )
    )
    supplied = _provenance(identity, provenance, export_schema=OcctExecutionReceipt.SCHEMA)
    document_user_text = {f"archflow:{key}": value for key, value in supplied.items()}
    timings: dict[str, float] = {}
    started = time.perf_counter()

    def failure_receipt(code: str, detail: str, **artifacts) -> OcctExecutionReceipt:
        timings["total_seconds"] = time.perf_counter() - started
        return OcctExecutionReceipt(
            status=CadExecutionStatus.FAILED,
            identity=identity,
            adapter_id=OCCT_ADAPTER_ID,
            backend=backend,
            evidence_tier=OCCT_EVIDENCE_TIER,
            exact_artifact=artifacts.get("exact_artifact"),
            preview_artifact=artifacts.get("preview_artifact"),
            physical_object_ids=physical,
            expected_semantics=semantics,
            expected_bounds=bounds,
            readback=artifacts.get("readback"),
            preview_inspection=artifacts.get("preview_inspection"),
            readback_tolerance=tolerance,
            timings=dict(timings),
            failures=(_failure(code, detail),),
        )

    try:
        with _occt_step(operation_observer, "occt_initialization", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="initialization_seconds",
                        details={"execution_path": "occt", "scope": "kernel_initialization"}):
            backend = backend_identity()
    except OcctUnavailableError as exc:
        raise CadExecutionError(str(exc)) from exc
    try:
        reuse_details = {"input_identity": {"program_digest": program.program_digest,
                         **({"source_program_digest": prior_program.program_digest} if prior_program is not None else {}),
                         **({"source_step_sha256": prior_step_sha256} if prior_step_sha256 is not None else {})},
                         "execution_path": "occt", "scope": "verified_source_shapes"}
        with _occt_step(operation_observer, "occt_reuse_check", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="reuse_check_seconds", details=reuse_details):
            reused_shapes = _reusable_occt_shapes(program, prior_program, prior_step, prior_step_sha256,
                                                  diagnostics=reuse_details)
        build = build_program_shapes(program, **({"reusable_shapes": reused_shapes} if reused_shapes else {}),
                                     operation_observer=operation_observer, observation_parent_id=observation_parent_id)
    except OcctCapabilityError as exc:
        raise CadCapabilityError(
            f"OCCT executor cannot realize {exc.op_id} ({exc.kind}): {exc.reason}",
            op_id=exc.op_id,
            kind=exc.kind,
        ) from exc
    except OcctBackendError as exc:
        return failure_receipt("cad_execution.occt_build_failed", str(exc))
    timings["build_seconds"] = build.elapsed_seconds
    if set(build.physical_object_ids) != set(physical):
        return failure_receipt(
            "cad_execution.physical_identity_mismatch",
            "built physical objects differ from the semantic denominator",
        )

    step_objects = tuple(
        StepObject(
            object_id=object_id,
            shape=build.objects[object_id].shape,
            layer=semantics["objects"][object_id]["layer"],
            color=layer_colors.get(semantics["objects"][object_id]["layer"]),
        )
        for object_id in physical
    )
    try:
        with _occt_step(operation_observer, "step_write", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="step_write_seconds",
                        details={"input_identity": {"program_digest": program.program_digest},
                                 "input_object_ids": list(physical), "emitted_object_ids": list(physical),
                                 "execution_path": "occt", "scope": "step_write_and_hash",
                                 "executed_stages": ["write_step"], "output_refs": [step_path.name]}):
            write_step(step_path, step_objects, length_unit=unit)
            exact_artifact = _exact_artifact(step_path, workspace, deliveries)
    except (OcctBackendError, OSError) as exc:
        return failure_receipt("cad_execution.step_write_failed", str(exc))

    read_error = None
    with _occt_step(operation_observer, "step_readback", parent_event_id=observation_parent_id,
                        timings=timings, timing_key="step_read_seconds",
                        details={"input_identity": {"step_sha256": exact_artifact["sha256"]},
                                 "input_object_ids": list(physical), "execution_path": "occt",
                                 "scope": "cold_read_and_verification", "executed_stages": ["read_step", "verify_step"],
                                 "comparison_refs": [step_path.name]}) as read_span:
        try:
            entries = read_step(step_path, length_unit=unit)
        except (OcctBackendError, OSError) as exc:
            read_error = exc
            read_span["status"] = "failed"
        else:
            readback, failures = _verify_step_readback(
                entries, physical=physical, semantics=semantics, expected_bounds=bounds,
                expected_counts=counts, expected_deliveries=deliveries, expected_curves=curves,
                layer_colors=layer_colors, tolerance=tolerance,
            )
            if failures:
                read_span["status"] = "failed"
    if read_error is not None:
        return failure_receipt(
            "cad_execution.step_readback_failed", str(read_error), exact_artifact=exact_artifact
        )

    preview_artifact = None
    preview_inspection = None
    if preview:
        linear_deflection = tolerance / 4.0
        preview_materials = _preview_materials(
            program,
            physical=physical,
            semantics=semantics,
            layer_colors=layer_colors,
            material_colors=material_colors,
        )
        preview_objects = tuple(
            PreviewObject(
                object_id=object_id,
                shape=build.objects[object_id].shape,
                layer=semantics["objects"][object_id]["layer"],
                user_text=semantics["objects"][object_id]["user_text"],
                visible=semantics["objects"][object_id].get("visible", True) is not False,
                material=preview_materials.get(object_id),
                delivery=deliveries[object_id],
            )
            for object_id in physical
        )
        phase = time.perf_counter()
        try:
            mesh_counts = write_preview_three_dm(
                preview_path,
                preview_objects,
                layer_colors=layer_colors,
                document_user_text=document_user_text,
                length_unit=unit,
                linear_deflection=linear_deflection,
                angular_deflection=_PREVIEW_ANGULAR_DEFLECTION,
                operation_observer=operation_observer,
                observation_parent_id=observation_parent_id,
            )
            preview_artifact = _preview_artifact(
                preview_path, workspace, linear_deflection, mesh_counts, preview_materials
            )
        except (OcctBackendError, OSError) as exc:
            failures.append(_failure("cad_execution.preview_write_failed", str(exc)))
        timings["preview_write_seconds"] = time.perf_counter() - phase
        if preview_artifact is not None:
            with _occt_step(operation_observer, "preview_readback", parent_event_id=observation_parent_id,
                                timings=timings, timing_key="preview_read_seconds",
                                details={"input_identity": {"asset_sha256": preview_artifact["sha256"]},
                                         "input_object_ids": list(physical), "execution_path": "occt",
                                         "scope": "cold_read_and_verification", "executed_stages": ["inspect_three_dm", "verify_preview"],
                                         "comparison_refs": [preview_path.name]}) as preview_span:
                try:
                    _strict_child(workspace, preview_path, require_exists=True)
                    inspection = inspect_three_dm(preview_path)
                except (CadExecutionError, ThreeDmInspectionError, OSError) as exc:
                    failures.append(_failure("cad_execution.preview_readback_failed", str(exc)))
                    preview_span["status"] = "failed"
                else:
                    preview_inspection = inspection.to_dict()
                    preview_failures = _verify_preview_readback(
                        inspection, physical=physical, semantics=semantics,
                        readback_bounds={object_id: row["bbox"] for object_id, row in readback.items() if "bbox" in row},
                        layer_colors=layer_colors, expected_document_user_text=document_user_text,
                        expected_materials=preview_materials, expected_curves=curves, length_unit=unit, tolerance=tolerance,
                    )
                    failures.extend(preview_failures)
                    if preview_failures:
                        preview_span["status"] = "failed"
    timings["total_seconds"] = time.perf_counter() - started
    return OcctExecutionReceipt(
        status=CadExecutionStatus.FAILED if failures else CadExecutionStatus.SUCCEEDED,
        identity=identity,
        adapter_id=OCCT_ADAPTER_ID,
        backend=backend,
        evidence_tier=OCCT_EVIDENCE_TIER,
        exact_artifact=exact_artifact,
        preview_artifact=preview_artifact,
        physical_object_ids=physical,
        expected_semantics=semantics,
        expected_bounds=bounds,
        readback=readback,
        preview_inspection=preview_inspection,
        readback_tolerance=tolerance,
        timings=timings,
        failures=tuple(failures),
        reused_object_ids=tuple(sorted(reused_shapes)),
    )


def _reusable_occt_shapes(program, prior_program, prior_step, prior_step_sha256, *, diagnostics=None) -> dict[str, Any]:
    """Load unchanged physical inputs from the exact source the caller chose."""

    details = diagnostics if diagnostics is not None else {}
    details.update(cache_status="miss", cache_reason="source_not_selected", cache_checks={})
    if prior_program is None and prior_step is None and prior_step_sha256 is None:
        return {}
    if prior_program is None or prior_step is None or prior_step_sha256 is None:
        details.update(cache_status="refused", cache_reason="source_inputs_incomplete", cache_checks={"source_inputs": "missing"})
        raise CadExecutionError("OCCT reuse requires the prior program, STEP and certified digest")
    if program.proposal.length_unit != prior_program.proposal.length_unit:
        details.update(cache_reason="length_unit_changed", cache_checks={"length_unit": "changed"})
        return {}
    source = Path(prior_step)
    if source.is_symlink():
        details.update(cache_status="refused", cache_reason="source_symlink_refused", cache_checks={"artifact": "changed"})
        raise CadExecutionError("OCCT reuse source cannot be a symlink")
    details.update(cache_status="refused", cache_reason="source_artifact_unreadable")
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != prior_step_sha256:
            details.update(cache_reason="source_artifact_changed", cache_checks={"artifact": "changed"})
            raise CadExecutionError("OCCT reuse source differs from its certified STEP")
    details.update(cache_reason="source_step_readback_failed", cache_checks={"artifact": "same"})
    entries = read_step(source, length_unit=program.proposal.length_unit.value)
    by_name = {entry.name: entry.shape for entry in entries}
    if len(by_name) != len(entries) or set(by_name) != set(_physical_ids(prior_program.proposal)):
        details.update(cache_reason="source_object_identity_changed", cache_checks={"artifact": "same", "object_names": "changed"})
        raise CadExecutionError("OCCT reuse source has missing or ambiguous physical objects")
    from .cad_patch import select_patch_operations

    selection = select_patch_operations(program, prior_program)
    # The Rhino patch's kept set excludes the entire connected input closure.
    # OCCT can keep an unchanged final shape even when a changed sibling needs
    # their missing shared intermediate rebuilt from the program.
    unchanged = (set(by_name) & set(_physical_ids(program.proposal))) - set(selection.changed_object_ids)
    details.update(
        cache_status="hit" if unchanged and unchanged == set(_physical_ids(program.proposal)) else "partial" if unchanged else "miss",
        cache_reason="geometry_changed" if selection.changed_object_ids else "objects_added_or_retired"
            if selection.added_object_ids or selection.retired_object_ids else "unchanged_geometry",
        input_equivalent=selection.empty,
        input_object_ids=sorted(by_name), reused_object_ids=sorted(unchanged),
        cache_checks={"length_unit": "same", "source_artifact": "same", "source_object_names": "same",
                      **{f"{name}.{reason}": "changed" for name, reason in selection.reasons.items()},
                      **{f"{name}.geometry": "same" for name in unchanged}},
        comparison_refs=[source.name],
    )
    return {name: by_name[name] for name in unchanged}


def _declared_deliveries(program: CompiledGeometryProgram, physical: tuple[str, ...]) -> dict[str, str]:
    """Per physical object, what its producer operation declared: ``closed_solid`` or ``open_surface``.

    A plain value read off the compiled program's operations; the verifier
    takes it as the denominator for closure, next to the predictor's bounds
    and B-rep counts.
    """

    producers = {
        object_id: operation
        for operation in program.proposal.operations
        for object_id in operation.output_object_ids
    }
    return {object_id: declared_delivery(producers[object_id]) for object_id in physical}


def _exact_artifact(path: Path, workspace: Path, deliveries: Mapping[str, str]) -> dict[str, object]:
    solids = sum(1 for delivery in deliveries.values() if delivery == CLOSED_SOLID)
    surfaces = sum(1 for delivery in deliveries.values() if delivery == OPEN_SURFACE)
    curves = sum(1 for delivery in deliveries.values() if delivery == CURVE)
    geometry = f"exact B-rep in the CAD frame and the program unit: {solids} closed solid object(s)"
    if surfaces:
        geometry += f", {surfaces} open surface object(s) from planar faces or uncapped lofts"
    if curves:
        geometry += f", {curves} polyline curve object(s) without faces or thickness"
    return {
        "format": _STEP_FORMAT,
        "relative_path": path.relative_to(workspace).as_posix(),
        "sha256": _sha256_bytes(long_path(path).read_bytes()),
        "exact_brep": True,
        "carries": [
            "one named shape per physical object (name = object id)",
            "the semantic layer path and its colour per object",
            geometry,
        ],
        "deliveries": {object_id: deliveries[object_id] for object_id in sorted(deliveries)},
        "does_not_carry": ["archflow:* object user text", "archflow:* document user text"],
    }


def _preview_artifact(
    path: Path,
    workspace: Path,
    linear_deflection: float,
    mesh_counts: Mapping[str, Mapping[str, int]],
    materials: Mapping[str, PreviewMaterial],
) -> dict[str, object]:
    artifact: dict[str, object] = {
        "format": _PREVIEW_FORMAT,
        "relative_path": path.relative_to(workspace).as_posix(),
        "sha256": _sha256_bytes(long_path(path).read_bytes()),
        "exact_brep": False,
        "geometry": "render mesh tessellated from the same OCCT model as the STEP file",
        "tessellator": "BRepMesh_IncrementalMesh",
        "linear_deflection": linear_deflection,
        "angular_deflection": _PREVIEW_ANGULAR_DEFLECTION,
        "mesh_counts": {key: dict(value) for key, value in sorted(mesh_counts.items()) if "mesh_face_count" in value},
        "carries": [
            "object names, nested layer paths and layer colours",
            "archflow:* object user text",
            "archflow:* document user text",
        ],
        "note": "not a NURBS/B-rep delivery; the STEP file is the exact geometry",
    }
    curves = {key: dict(value) for key, value in sorted(mesh_counts.items()) if "curve_point_count" in value}
    if curves:
        artifact.update(format="3dm render-mesh and curve preview", curve_counts=curves,
                        geometry="render meshes and native polylines from the same OCCT model as the STEP file",
                        note="Surfaces are render meshes; polylines are native curves. STEP retains the exact model.")
    if materials:
        artifact["carries"].append("native object materials for assembly frame and glazing members")
        artifact["materials"] = {
            object_id: material.to_dict() for object_id, material in sorted(materials.items())
        }
    return artifact


def _artifact_stem(value: str) -> str:
    _portable_relative_path(value)
    path = PurePosixPath(value)
    if len(path.parts) != 1 or value.lower().endswith((".step", ".stp", ".3dm")):
        raise CadExecutionError("artifact_stem must be one portable file stem without a suffix")
    return value


def _verify_step_readback(
    entries,
    *,
    physical: tuple[str, ...],
    semantics: Mapping[str, object],
    expected_bounds: Mapping[str, Mapping[str, object]],
    expected_counts: Mapping[str, int],
    layer_colors: Mapping[str, tuple[int, int, int]],
    tolerance: float,
    expected_deliveries: Mapping[str, str] | None = None,
    expected_curves: Mapping[str, Sequence[Sequence[float]]] | None = None,
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]]]:
    """Measure every named entry of the cold read against the denominator.

    ``expected_deliveries`` says per object whether its producer declared a
    closed solid (the default for every object it does not name) or an open
    surface.  A closed solid must hold exactly ``expected_counts`` solids,
    all closed.  An open surface must hold no solid, at least one face and an
    actual open boundary (free edges); its volume is not a measurement and
    is reported as ``None``. Curves must contain only the declared path's edges,
    checked against its full vertex sequence, endpoints and length.
    """

    failures: list[dict[str, str]] = []
    deliveries = dict(expected_deliveries or {})
    for object_id in physical:
        delivery = deliveries.setdefault(object_id, CLOSED_SOLID)
        if delivery not in (CLOSED_SOLID, OPEN_SURFACE, CURVE):
            raise CadExecutionError(f"{object_id}: unknown declared delivery {delivery!r}")
    by_name: dict[str, list] = {}
    unnamed = 0
    for entry in entries:
        if entry.name is None:
            unnamed += 1
            continue
        by_name.setdefault(entry.name, []).append(entry)
    if unnamed:
        failures.append(
            _failure("cad_execution.step_object_unnamed", f"{unnamed} shape(s) carry no object name")
        )
    missing = sorted(set(physical) - set(by_name))
    extra = sorted(set(by_name) - set(physical))
    if missing or extra:
        failures.append(
            _failure(
                "cad_execution.step_object_set_mismatch",
                f"missing={missing} extra={extra}",
            )
        )
    readback: dict[str, dict[str, object]] = {}
    for object_id in physical:
        rows = by_name.get(object_id, [])
        if len(rows) != 1:
            if rows:
                failures.append(
                    _failure("cad_execution.step_object_duplicate", f"{object_id} appears {len(rows)} times")
                )
            continue
        entry = rows[0]
        try:
            measure = measure_shape(entry.shape)
        except OcctBackendError as exc:
            failures.append(_failure("cad_execution.step_shape_invalid", f"{object_id}: {exc}"))
            continue
        row = measure.to_dict()
        row["name"] = entry.name
        row["layers"] = list(entry.layers)
        row["color"] = list(entry.color) if entry.color is not None else None
        row["declared_delivery"] = deliveries[object_id]
        readback[object_id] = row
        if not measure.valid:
            failures.append(_failure("cad_execution.step_shape_invalid", f"{object_id} is not a valid shape"))
        if deliveries[object_id] == CURVE:
            try:
                if measure.solid_count or measure.face_count:
                    raise OcctBackendError("curve contains a surface or solid")
                row.update(_polyline_geometry(entry.shape))
                if not _curve_matches(row, (expected_curves or {}).get(object_id, ()), tolerance):
                    raise OcctBackendError("curve vertices, endpoints or length differ from the authored path")
            except OcctBackendError as exc:
                failures.append(_failure("cad_execution.step_curve_mismatch", f"{object_id}: {exc}"))
        elif deliveries[object_id] == OPEN_SURFACE:
            if measure.solid_count or measure.closed or measure.free_edge_count == 0 or measure.face_count == 0:
                failures.append(
                    _failure(
                        "cad_execution.step_not_open_surface",
                        f"{object_id} was declared an open surface but holds {measure.solid_count} solid(s), "
                        f"{measure.face_count} face(s) and {measure.free_edge_count} free edge(s)",
                    )
                )
            if measure.volume is not None:
                failures.append(_failure("cad_execution.step_not_open_surface", f"{object_id} reports a volume for an open surface"))
        else:
            if measure.solid_count != expected_counts[object_id]:
                failures.append(
                    _failure(
                        "cad_execution.step_solid_count_mismatch",
                        f"{object_id} has {measure.solid_count} solid(s), expected {expected_counts[object_id]}",
                    )
                )
            if not measure.closed:
                failures.append(_failure("cad_execution.step_not_closed_solid", f"{object_id} is not a closed solid"))
        if not _bbox_close(row["bbox"], expected_bounds[object_id], tolerance):
            failures.append(_failure("cad_execution.named_bounds_mismatch", f"object {object_id} bounds differ"))
        expected_layer = semantics["objects"][object_id]["layer"]
        if tuple(entry.layers) != (expected_layer,):
            failures.append(
                _failure("cad_execution.object_layer_mismatch", f"object {object_id} layer differs from semantic contract")
            )
        expected_color = layer_colors.get(expected_layer)
        if expected_color is not None and (
            entry.color is None
            or any(abs(int(a) - int(b)) > 1 for a, b in zip(entry.color, expected_color))
        ):
            failures.append(
                _failure("cad_execution.layer_color_mismatch", f"object {object_id} colour differs from layer contract")
            )
    if set(readback) == set(physical) and physical:
        aggregate = _aggregate_bounds(readback[object_id]["bbox"] for object_id in physical)
        if not _bbox_close(aggregate, _aggregate_bounds(expected_bounds.values()), tolerance):
            failures.append(
                _failure("cad_execution.aggregate_bounds_mismatch", "aggregate STEP bounds differ")
            )
    return readback, failures


def _curve_matches(actual: Mapping[str, object], expected: Sequence[Sequence[float]], tolerance: float) -> bool:
    points = actual.get("curve_points", ())
    length = actual.get("curve_length")
    if not expected or len(points) != len(expected) or not isinstance(length, (int, float)):
        return False
    expected_length = sum(math.dist(a, b) for a, b in zip(expected, expected[1:]))
    return abs(length - expected_length) <= tolerance and any(
        all(math.dist(a, b) <= tolerance for a, b in zip(points, ordered))
        for ordered in (expected, tuple(reversed(expected)))
    )


def _verify_preview_readback(
    inspection: ThreeDmInspection,
    *,
    physical: tuple[str, ...],
    semantics: Mapping[str, object],
    readback_bounds: Mapping[str, Mapping[str, object]],
    layer_colors: Mapping[str, tuple[int, int, int]],
    expected_document_user_text: Mapping[str, str],
    length_unit: str,
    tolerance: float,
    expected_materials: Mapping[str, PreviewMaterial] | None = None,
    expected_curves: Mapping[str, Sequence[Sequence[float]]] | None = None,
) -> list[dict[str, str]]:
    """The preview carries the same identities and mesh/curve geometry as the STEP."""

    failures: list[dict[str, str]] = []
    if not inspection.read_only or inspection.rhino_process_started:
        failures.append(
            _failure("cad_execution.inspector_not_read_only", "verification must be independent and read-only")
        )
    if inspection.units.get("name") != _UNIT_TO_RHINO[length_unit][1]:
        failures.append(_failure("cad_execution.unit_mismatch", "preview units differ"))
    actual_document = {
        row["key"]: row["value"]
        for row in inspection.document_user_strings
        if str(row["key"]).startswith("archflow:")
    }
    if actual_document != dict(expected_document_user_text):
        failures.append(
            _failure("cad_execution.provenance_mismatch", "archflow document user-text key/value set differs")
        )
    actual_layers: dict[str, list] = {}
    for row in inspection.layers:
        actual_layers.setdefault(str(row.get("full_path")), []).append(row)
    for full_path, color in sorted(layer_colors.items()):
        rows = actual_layers.get(full_path, [])
        if len(rows) != 1:
            failures.append(_failure("cad_execution.layer_set_mismatch", f"layer {full_path} is missing or duplicated"))
            continue
        if rows[0].get("color_rgba") != [*color, 255]:
            failures.append(_failure("cad_execution.layer_color_mismatch", f"layer {full_path} RGBA differs"))
    if inspection.top_level_object_count != len(physical):
        failures.append(
            _failure("cad_execution.object_count_mismatch", "preview object count differs from physical denominator")
        )
    named: dict[str, list] = {}
    for row in inspection.named_object_bboxes:
        named.setdefault(str(row["name"]), []).append(row)
    if set(named) != set(physical) or any(len(rows) != 1 for rows in named.values()):
        failures.append(
            _failure("cad_execution.named_object_mismatch", "preview named-object set has missing, duplicate, or extra identities")
        )
    for object_id in sorted(set(named) & set(physical)):
        row = named[object_id][0]
        if len(named[object_id]) != 1:
            continue
        curve = (expected_curves or {}).get(object_id)
        if curve is not None:
            analysis = [item for item in inspection.object_geometry_analysis if item["name"] == object_id]
            if row.get("type") != "Curve" or len(analysis) != 1 or not _curve_matches(analysis[0], curve, tolerance):
                failures.append(_failure("cad_execution.preview_curve_mismatch", f"object {object_id} curve vertices, endpoints or length differ"))
        elif row.get("type") != "Mesh":
            failures.append(_failure("cad_execution.preview_not_mesh", f"object {object_id} is not a preview mesh"))
        expected = readback_bounds.get(object_id)
        if expected is None or not _bbox_close(row["bbox"], expected, tolerance):
            failures.append(_failure("cad_execution.named_bounds_mismatch", f"preview object {object_id} bounds differ from STEP"))
    observed: dict[str, list] = {}
    for row in inspection.object_user_strings:
        if row.get("is_instance_definition_object"):
            continue
        observed.setdefault(str(row.get("name")), []).append(row)
    for object_id in physical:
        expected_semantic = semantics["objects"][object_id]
        rows = observed.get(object_id, [])
        if len(rows) != 1:
            failures.append(_failure("cad_execution.semantic_witness_count_mismatch", f"object {object_id} has {len(rows)} semantic witnesses"))
            continue
        pairs = {
            pair["key"]: pair["value"]
            for pair in rows[0].get("attributes", ())
            if str(pair["key"]).startswith("archflow:")
        }
        if pairs != dict(expected_semantic["user_text"]):
            failures.append(_failure("cad_execution.semantic_user_text_mismatch", f"object {object_id} semantic user text differs"))
        if rows[0].get("layer_path") != expected_semantic["layer"]:
            failures.append(_failure("cad_execution.object_layer_mismatch", f"preview object {object_id} layer differs"))
    bindings: dict[str, list] = {}
    for row in inspection.object_material_bindings:
        if row.get("is_instance_definition_object"):
            continue
        bindings.setdefault(str(row.get("name")), []).append(row)
    for object_id, material in sorted((expected_materials or {}).items()):
        rows = bindings.get(object_id, [])
        transparency = rows[0].get("material_transparency") if len(rows) == 1 else None
        bound = (
            len(rows) == 1
            and rows[0].get("material_source") == "MaterialFromObject"
            and rows[0].get("material_name") == material.name
            and rows[0].get("archflow_material_id") == material.name
            and rows[0].get("material_diffuse_color_rgba") == [*material.diffuse, 255]
            and isinstance(transparency, (int, float))
            and not isinstance(transparency, bool)
            and abs(float(transparency) - material.transparency) <= _PREVIEW_TRANSPARENCY_TOLERANCE
        )
        if not bound:
            failures.append(
                _failure("cad_execution.preview_material_mismatch", f"preview object {object_id} does not wear material {material.name}")
            )
    return failures


def patch_composed_three_dm(
    base_3dm: bytes,
    *,
    prior_program: CompiledGeometryProgram,
    program: CompiledGeometryProgram,
    replacement_3dm: bytes,
) -> bytes:
    """Replace changed native objects inside an existing composed display model.

    The caller verifies the source binding and retains the returned bytes. This
    function neither writes files nor executes CAD. Patch selection comes from
    the existing compiled-program diff; imported objects and unchanged native
    objects stay in the original File3dm, including their attributes, cached
    meshes, instance definitions and document tables. Existing imported geometry
    is not revalidated as newly generated geometry.

    ``replacement_3dm`` is the native export of ``program``. Only its selected
    physical objects are imported. Its mesh/Brep objects are supported; a changed
    native block instance is refused, never silently stripped of its definition.
    Preserved block instances in the base need no reconstruction. This is a
    display-model composition, not an exact STEP export of the imported assets.
    A replacement without a native material keeps the existing object's native
    material, or a new object's unambiguous component material. Explicit donor
    materials take precedence; inherited materials retain their PBR and textures.
    New native objects must use built-in linetypes: rhino3dm's custom-linetype
    table wrappers cannot safely be released on the supported Windows runtime.
    """

    import rhino3dm

    selection = select_patch_operations(program, prior_program)

    def read(data: bytes, label: str):
        if not isinstance(data, bytes) or not data:
            raise CadPatchError(f"{label} must contain 3DM bytes")
        try:
            model = rhino3dm.File3dm.FromByteArray(data)
        except Exception as exc:
            raise CadPatchError(f"{label} is not a readable 3DM") from exc
        if model is None:
            raise CadPatchError(f"{label} is not a readable 3DM")
        return model

    base = read(base_3dm, "composed base")
    donor = read(replacement_3dm, "native replacement")
    unit = getattr(rhino3dm.UnitSystem, _UNIT_TO_RHINO[program.proposal.length_unit.value][0])
    if donor.Settings.ModelUnitSystem != unit:
        raise CadPatchError("native replacement must use the program's length unit")
    base_unit = base.Settings.ModelUnitSystem
    unit_scale = rhino3dm.UnitSystem.UnitScale(unit, base_unit)
    if base_unit.name in {"None", "Unset", "CustomUnits"} or not math.isfinite(unit_scale) or unit_scale <= 0:
        raise CadPatchError("composed base must declare a convertible model length unit")
    scale_transform = rhino3dm.Transform.Scale(rhino3dm.Point3d(0, 0, 0), unit_scale)

    def physical(model):
        by_name = {}
        for item in model.Objects:
            if not item.Attributes.IsInstanceDefinitionObject:
                by_name.setdefault(item.Attributes.Name, []).append(item)
        return by_name

    def require_native_identity(objects, names, label):
        for name in sorted(names):
            matches = objects[name]
            if len(matches) != 1:
                raise CadPatchError(f"{label} has ambiguous native object {name}: {len(matches)} top-level objects")
            if matches[0].Attributes.GetUserString("archflow:object_ref") != f"cad-object:{name}":
                raise CadPatchError(f"{label} native object has a missing or mismatched object_ref: {name}")

    base_objects = physical(base)
    donor_objects = physical(donor)
    prior_names = set(_physical_ids(prior_program.proposal))
    new_names = set(_physical_ids(program.proposal))
    missing = prior_names - base_objects.keys()
    if missing:
        raise CadPatchError(f"composed base is missing native objects: {sorted(missing)}")
    require_native_identity(base_objects, prior_names, "composed base")
    collisions = (new_names - prior_names) & base_objects.keys()
    if collisions:
        raise CadPatchError(f"new native names collide with preserved objects: {sorted(collisions)}")
    replacement_names = new_names - set(selection.kept_object_ids)
    missing = replacement_names - donor_objects.keys()
    if missing:
        raise CadPatchError(f"native replacement is missing patch objects: {sorted(missing)}")
    require_native_identity(donor_objects, replacement_names, "native replacement")
    replacements = [item for name in sorted(replacement_names) for item in donor_objects[name]]
    for item in replacements:
        if isinstance(item.Geometry, rhino3dm.InstanceReference):
            raise CadPatchError(f"native replacement block needs its definition: {item.Attributes.Name}")
        if not item.Geometry.IsValid:
            raise CadPatchError(f"native replacement geometry is invalid: {item.Attributes.Name}")
    if selection.empty:
        return base_3dm

    def native_material_index(model, attributes):
        if attributes.MaterialSource == rhino3dm.ObjectMaterialSource.MaterialFromObject:
            index = attributes.MaterialIndex
        elif attributes.MaterialSource == rhino3dm.ObjectMaterialSource.MaterialFromLayer:
            layer = model.Layers.FindIndex(attributes.LayerIndex)
            index = -1 if layer is None else layer.RenderMaterialIndex
        else:
            return None
        return index if index >= 0 and model.Materials.FindIndex(index) is not None else None

    source_materials = {}
    component_materials: dict[str, set[int]] = {}
    for name in prior_names:
        attributes = base_objects[name][0].Attributes
        index = native_material_index(base, attributes)
        if index is None:
            continue
        source_materials[name] = index
        component = attributes.GetUserString("archflow:component")
        if component:
            component_materials.setdefault(component, set()).add(index)

    inherited_materials = {}
    for item in replacements:
        attributes = item.Attributes
        if native_material_index(donor, attributes) is not None:
            continue
        index = source_materials.get(attributes.Name)
        if index is None:
            component = attributes.GetUserString("archflow:component")
            candidates = component_materials.get(component, set())
            if len(candidates) == 1:
                index = next(iter(candidates))
        if index is not None:
            inherited_materials[attributes.Name] = index

    # Table indices belong to a document. Copy only tables the replacement uses;
    # never change an existing base layer or material while adding native objects.
    materials: dict[int, int] = {}
    groups: dict[int, int] = {}
    layers: dict[int, int] = {}
    donor_layers = {layer.Index: layer for layer in donor.Layers}
    donor_layer_ids = {layer.Id: layer.Index for layer in donor.Layers}
    base_layer_paths = {layer.FullPath: layer.Index for layer in base.Layers}
    donor_layer_paths = {layer.Index: layer.FullPath for layer in donor.Layers}

    def copy_material(index: int) -> int:
        if index < 0:
            return index
        if index not in materials:
            source = donor.Materials.FindIndex(index)
            if source is None:
                raise CadPatchError(f"replacement material is missing: {index}")
            materials[index] = base.Materials.Add(source)
        return materials[index]

    def copy_linetype(index: int) -> int:
        if index < 0:
            return index
        raise CadPatchError("custom replacement linetypes require a native CAD export")

    def copy_layer(index: int) -> int:
        if index not in layers:
            source = donor_layers.get(index)
            if source is None:
                raise CadPatchError(f"replacement layer is missing: {index}")
            path = donor_layer_paths[index]
            if path in base_layer_paths:
                layers[index] = base_layer_paths[path]
            else:
                parent = donor_layer_ids.get(source.ParentLayerId)
                if parent is not None:
                    source.ParentLayerId = base.Layers.FindIndex(copy_layer(parent)).Id
                source.RenderMaterialIndex = copy_material(source.RenderMaterialIndex)
                source.LinetypeIndex = copy_linetype(source.LinetypeIndex)
                layers[index] = base.Layers.Add(source)
                base_layer_paths[path] = layers[index]
        return layers[index]

    def copy_group(index: int) -> int:
        if index not in groups:
            source = donor.Groups.FindIndex(index)
            if source is None:
                raise CadPatchError(f"replacement group is missing: {index}")
            base.Groups.Add(source)
            groups[index] = max(item.Index for item in base.Groups)
        return groups[index]

    deleted = {
        item.Attributes.Id
        for name in selection.delete_object_names
        for item in base_objects.get(name, ())
    }
    preserved = {item.Attributes.Id for item in base.Objects} - deleted
    for object_id in deleted:
        base.Objects.Delete(object_id)
    imported = set()
    for item in replacements:
        attributes = item.Attributes
        donor_material = native_material_index(donor, attributes)
        attributes.LayerIndex = copy_layer(attributes.LayerIndex)
        inherited = inherited_materials.get(attributes.Name)
        if inherited is not None:
            attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            attributes.MaterialIndex = inherited
            material = base.Materials.FindIndex(inherited)
            logical = material.GetUserString("archflow:material_id") or material.GetUserString("archflow:material")
            if logical:
                attributes.SetUserString("archflow:material", logical)
        elif donor_material is not None:
            attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
            attributes.MaterialIndex = copy_material(donor_material)
        else:
            attributes.MaterialIndex = copy_material(attributes.MaterialIndex)
        attributes.LinetypeIndex = copy_linetype(attributes.LinetypeIndex)
        old_groups = attributes.GetGroupList2()
        attributes.RemoveFromAllGroups()
        for group in old_groups:
            attributes.AddToGroup(copy_group(group))
        geometry = item.Geometry.Duplicate()
        if unit_scale != 1.0 and not geometry.Transform(scale_transform):
            raise CadPatchError(f"native replacement could not be converted to the base unit: {attributes.Name}")
        imported.add(base.Objects.Add(geometry, attributes))

    encoded = base64.b64decode(base.Encode())
    reopened = read(encoded, "composed result")
    if {item.Attributes.Id for item in reopened.Objects} != preserved | imported:
        raise CadPatchError("composed result did not preserve its objects")
    result_objects = physical(reopened)
    if any(name not in result_objects for name in new_names) or any(
        name in result_objects for name in prior_names - new_names
    ):
        raise CadPatchError("composed result does not contain the native patch outputs")
    return encoded


__all__ = [
    "CadCapabilityError",
    "CadExecutionError",
    "CadExecutionStatus",
    "CadProgramBinding",
    "OCCT_ADAPTER_ID",
    "OCCT_EVIDENCE_TIER",
    "OcctBackendError",
    "OcctCadExportIdentity",
    "OcctDrawingPolyline",
    "OcctExecutionReceipt",
    "measure_occt_solid_pairs",
    "StepEntry",
    "WORK_MODEL_EXPORT_PATH",
    "StepImportObject",
    "StepImportSource",
    "backend_identity",
    "project_occt_lines",
    "read_step",
    "long_path",
    "split_step_objects",
    "work_model_workspace",
    "verify_work_model_geometry",
    "RhinoCadExecutionReceipt",
    "RhinoCadExportIdentity",
    "RhinoCadExportPlan",
    "RhinoCadProgramBinding",
    "build_rhino_com_powershell_command",
    "build_rhino_com_powershell_source",
    "discover_rhino_executables",
    "execute_occt_export",
    "execute_rhino_three_dm_export",
    "prepare_rhino_three_dm_export",
    "patch_composed_three_dm",
    "verify_rhino_export_readback",
]


# A CAD execution receipt keeps the canonical base twice: once as the flat
# scalar the exported file's own metadata carries, and once as the exact
# reference in the binding the receipt was produced from.
VERSION_REF_POINTERS = {
    schema: ("/metadata/base_state_sha256", "/binding/base")
    for schema in (
        "RhinoCadExecutionReceipt@4",
        "OcctExecutionReceipt@1",
        "BlenderExecutionReceipt@1",
    )
}

_register_version_refs(VERSION_REF_POINTERS)
