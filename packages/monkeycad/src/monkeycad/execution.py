"""The backend-neutral half of CAD execution: the binding an export carries and the interface every backend implements.

``CadProgramBinding`` is the exact P036 record, branch and stage identity one
compiled program is exported under (``RhinoCadProgramBinding`` is the same
class under its historical name; its ``RhinoCadProgramBinding@1`` shape is
unchanged). A ``CadBackend`` executes a ``CadExecutionRequest`` inside a
caller-supplied speculative workspace and answers a plain
``CadExecutionResult``; ``monkeycad.registry`` holds the one table every
backend is registered in. The status and errors, the speculative-workspace
rules, the export provenance and the bounds arithmetic here are shared by the
OCCT, Rhino and Blender backends. Nothing here holds a repository or writes a
project record: the runner retains each backend's native receipt.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol

from archflow.project.refs import BranchRef, ProjectRecordRef, require_identifier
from archflow.state.geometry_program import CompiledGeometryProgram, require_sha256
from monkeycad.program import expected_object_semantics


_MAX_PROCESS_TEXT = 2_000

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


class CadCapabilityError(CadExecutionError):
    """The program names an operation this executor does not realize.

    Raised before anything is written.  There is no fallback to another
    executor: the caller chooses one explicitly.
    """

    def __init__(self, message: str, *, op_id: str, kind: str) -> None:
        super().__init__(message)
        self.op_id = op_id
        self.kind = kind


def _provenance(
    identity,
    extra: Mapping[str, str] | None,
    *,
    export_schema: str,
) -> dict[str, str]:
    """The document user text an export carries: its Rhino or OCCT identity, then the caller's keys."""

    binding = identity.binding
    base = binding.branch.run.base
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


def _portable_relative_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CadExecutionError("artifact path must be portable relative text")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CadExecutionError("artifact path must stay inside workspace")


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


def _artifact_stem(value: str) -> str:
    _portable_relative_path(value)
    path = PurePosixPath(value)
    if len(path.parts) != 1 or value.lower().endswith((".step", ".stp", ".3dm")):
        raise CadExecutionError("artifact_stem must be one portable file stem without a suffix")
    return value


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


@dataclass(frozen=True)
class CadExecutionSource:
    program: CompiledGeometryProgram
    model: Path
    sha256: str


@dataclass(frozen=True)
class CadExecutionRequest:
    program: CompiledGeometryProgram
    binding: CadProgramBinding
    speculative_workspace: Path
    artifact_stem: str
    readback_tolerance: float = 0.003
    provenance: Mapping[str, str] | None = None
    material_by_component: Mapping[str, str] | None = None
    material_colors: Mapping[str, tuple[int, int, int]] | None = None
    layer_by_component: Mapping[str, str] | None = None
    backend_options: Mapping[str, object] = field(default_factory=dict)
    source: CadExecutionSource | None = None
    operation_observer: Callable | None = None
    observation_parent_id: str | None = None

    def __post_init__(self):
        self.binding.bind_program(self.program)
        _strict_workspace(self.speculative_workspace)
        _artifact_stem(self.artifact_stem)
        _positive_finite(self.readback_tolerance, "readback_tolerance")
        if self.source is not None:
            if self.source.program.proposal.project_id != self.binding.project_id:
                raise CadExecutionError("source program crossed project binding")
            require_sha256(self.source.sha256, "source sha256")


@dataclass(frozen=True)
class CadArtifact:
    name: str
    relative_path: str
    sha256: str
    format: str
    verified: bool

    def to_dict(self):
        return {"relative_path": self.relative_path, "sha256": self.sha256, "format": self.format}


@dataclass(frozen=True)
class CadExecutionResult:
    backend_id: str
    binding: CadProgramBinding
    status: str
    readback_verified: bool
    artifacts: tuple[CadArtifact, ...]
    physical_object_ids: tuple[str, ...]
    expected_semantics: dict
    receipt_payload: dict | None
    inspection: dict | None
    failures: tuple[dict, ...]
    execution_path: str
    reused_object_ids: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)

    def validate(self, request: CadExecutionRequest, backend_id: str) -> None:
        request.binding.bind_program(request.program)
        if self.backend_id != backend_id or self.binding != request.binding:
            raise CadExecutionError("CAD result crossed backend/program binding")
        if self.status not in ("succeeded", "failed", "unsupported"):
            raise CadExecutionError("unknown CAD result status")
        succeeded = self.status == "succeeded"
        if succeeded != self.readback_verified or (succeeded and (self.failures or not self.artifacts or self.receipt_payload is None)):
            raise CadExecutionError("CAD success requires artifacts, receipt and verified readback")
        if succeeded:
            expected = _semantics(request)
            if self.physical_object_ids != tuple(sorted(expected["objects"])) or self.expected_semantics != expected:
                raise CadExecutionError("CAD result lost stable object/semantic identity")
        if self.receipt_payload is not None:
            payload = self.receipt_payload
            if ((payload.get("identity") or {}).get("binding") != self.binding.to_dict() or
                    payload.get("status") != self.status or payload.get("readback_verified") != self.readback_verified or
                    tuple(payload.get("failures", ())) != self.failures):
                raise CadExecutionError("CAD result differs from its native receipt")
        if len({a.name for a in self.artifacts}) != len(self.artifacts):
            raise CadExecutionError("CAD artifact names must be unique")
        for artifact in self.artifacts:
            _portable_relative_path(artifact.relative_path)
            require_sha256(artifact.sha256, "artifact sha256")
            path = request.speculative_workspace / artifact.relative_path
            _strict_child(request.speculative_workspace, path, require_exists=True)
            if succeeded and not artifact.verified:
                raise CadExecutionError("successful CAD artifact lacks verification")
            _require_file_digest(path, artifact.sha256, "CAD artifact")


class CadBackend(Protocol):
    backend_id: str
    record_kind: str
    patch_rebuild: bool

    def validate_options(self, options: Mapping[str, object]) -> None: ...
    def execute(self, request: CadExecutionRequest) -> CadExecutionResult: ...
    def read_receipt(self, request: CadExecutionRequest, payload: dict) -> CadExecutionResult: ...


def _semantics(request):
    return expected_object_semantics(request.program, material_by_component=request.material_by_component,
                                    layer_by_component=request.layer_by_component)


def _check_receipt(request, payload, schema):
    if payload.get("schema") != schema or (payload.get("identity") or {}).get("binding") != request.binding.to_dict():
        raise CadExecutionError("retained CAD receipt crossed schema/program binding")


def _unsupported(request, backend_id, error):
    failure = {"code": "cad_execution.unsupported_operation", "detail": str(error)}
    for name in ("op_id", "kind"):
        if hasattr(error, name):
            failure[name] = getattr(error, name)
    return CadExecutionResult(backend_id, request.binding, "unsupported", False, (), (), {}, None, None,
                              (failure,), backend_id)


def _native_inputs(request):
    return dict(binding=request.binding, speculative_workspace=request.speculative_workspace,
                readback_tolerance=request.readback_tolerance, provenance=request.provenance,
                material_by_component=request.material_by_component, material_colors=request.material_colors,
                layer_by_component=request.layer_by_component)


def _options(options, allowed, backend_id):
    if options.get("patch_oracle") and "patch_oracle" not in allowed:
        raise CadExecutionError("patch_oracle requires --cad-backend rhino")
    unknown = set(options) - allowed
    if unknown:
        raise CadExecutionError(f"{backend_id} unsupported backend options: {sorted(unknown)}")
