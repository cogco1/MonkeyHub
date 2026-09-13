"""The caller-facing compiled-cad-execution boundary, owned by cad_execution.

One registration table, plain request/result values, and the existing native
executors. No repository, host lifecycle unification, or new retained schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Callable, Mapping, Protocol

import archflow.adapters.cad_execution as cad
from archflow.adapters.cad_execution import CadExecutionError, CadProgramBinding
from archflow.adapters.cad_program import CadTranslationError, expected_object_semantics
from archflow.state.geometry_program import CompiledGeometryProgram


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
        cad._strict_workspace(self.speculative_workspace)
        cad._artifact_stem(self.artifact_stem)
        cad._positive_finite(self.readback_tolerance, "readback_tolerance")
        if self.source is not None:
            if self.source.program.proposal.project_id != self.binding.project_id:
                raise CadExecutionError("source program crossed project binding")
            cad.require_sha256(self.source.sha256, "source sha256")


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
            cad._portable_relative_path(artifact.relative_path)
            cad.require_sha256(artifact.sha256, "artifact sha256")
            path = request.speculative_workspace / artifact.relative_path
            cad._strict_child(request.speculative_workspace, path, require_exists=True)
            if succeeded and not artifact.verified:
                raise CadExecutionError("successful CAD artifact lacks verification")
            cad._require_file_digest(path, artifact.sha256, "CAD artifact")


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


class OcctBackend:
    backend_id = "occt"
    record_kind = "seat-occt-execution"
    patch_rebuild = False

    def validate_options(self, options):
        _options(options, {"preview", "powershell_executable"}, self.backend_id)

    def execute(self, request):
        self.validate_options(request.backend_options)
        reuse = {}
        if request.source is not None:
            reuse = dict(prior_program=request.source.program, prior_step=request.source.model,
                         prior_step_sha256=request.source.sha256)
        try:
            receipt = cad.execute_occt_export(request.program, **_native_inputs(request), artifact_stem=request.artifact_stem,
                preview=request.backend_options.get("preview", True), operation_observer=request.operation_observer,
                observation_parent_id=request.observation_parent_id, **reuse)
        except cad.CadCapabilityError as exc:
            return _unsupported(request, self.backend_id, exc)
        result = self.read_receipt(request, receipt.to_dict())
        result.validate(request, self.backend_id)
        return result

    def read_receipt(self, request, payload):
        _check_receipt(request, payload, cad.OcctExecutionReceipt.SCHEMA)
        if payload.get("backend") != cad.backend_identity():
            raise CadExecutionError("retained CAD backend changed")
        verified = payload.get("readback_verified") is True
        artifacts = tuple(CadArtifact(name, row["relative_path"], row["sha256"], row["format"], verified)
                          for name, row in (("exact", payload.get("exact_artifact")), ("preview", payload.get("preview_artifact"))) if row)
        if verified and (not payload.get("exact_artifact") or payload.get("readback") is None or
                         set(payload["readback"]) != set(payload["physical_object_ids"]) or
                         (request.backend_options.get("preview", True) and
                          (not payload.get("preview_artifact") or not payload.get("preview_inspection")))):
            raise CadExecutionError("required CAD artifact/readback missing")
        reused = tuple(payload.get("reused_object_ids", ()))
        return CadExecutionResult(self.backend_id, request.binding, payload["status"], verified, artifacts,
            tuple(payload["physical_object_ids"]), payload["expected_semantics"], payload, payload.get("preview_inspection"),
            tuple(payload["failures"]), "incremental" if reused else self.backend_id, reused,
            {"evidence_tier": payload.get("evidence_tier")})


class RhinoBackend:
    backend_id = "rhino"
    record_kind = "seat-rhino-execution"
    patch_rebuild = True

    def validate_options(self, options):
        _options(options, {"powershell_executable", "timeout_seconds", "runner", "cleanup_runner", "monotonic", "sleeper",
                           "host_wait_seconds", "patch_oracle"}, self.backend_id)

    def execute(self, request):
        self.validate_options(request.backend_options)
        patch = None
        label = "rebuild"
        if request.source is not None:
            if request.source.model.is_symlink():
                raise CadExecutionError("source artifact cannot be a symlink")
            cad._require_file_digest(request.source.model, request.source.sha256, "source artifact")
            patch = cad.RhinoPatchBase(request.source.model, request.source.program)
            label = "patch"
        try:
            plan = cad.prepare_rhino_three_dm_export(request.program, **_native_inputs(request),
                artifact_name=f"{request.artifact_stem}.3dm", patch=patch)
        except CadTranslationError as exc:
            return _unsupported(request, self.backend_id, exc)
        except CadExecutionError as exc:
            if "typed losses" in str(exc):
                return _unsupported(request, self.backend_id, exc)
            raise
        options = {k: v for k, v in request.backend_options.items() if k != "patch_oracle"}
        options.setdefault("powershell_executable", None)
        options.setdefault("timeout_seconds", 900)
        receipt = cad.execute_rhino_three_dm_export(plan, **options)
        result = self.read_receipt(request, receipt.to_dict())
        details = {}
        if plan.patch:
            label = plan.patch.get("mode", label)
            details = {"prior_model": plan.patch["prior_model_path"], "rebuilt_objects": len(plan.patch["rebuilt_op_ids"]),
                       "kept_objects": len(plan.patch["kept_object_ids"])}
        result = replace(result, execution_path=label, details=details)
        result.validate(request, self.backend_id)
        return result

    def read_receipt(self, request, payload):
        _check_receipt(request, payload, cad.RhinoCadExecutionReceipt.SCHEMA)
        verified = payload.get("readback_verified") is True
        inspection = payload.get("inspection")
        if verified and (not inspection or payload.get("cleanup_status") != "confirmed" or
                         not payload.get("host_witness_sha256") or not payload.get("cleanup_witness_sha256") or
                         payload.get("completion_marker_sha256") != payload.get("completion_witness_sha256")):
            raise CadExecutionError("Rhino receipt lacks readback/host/cleanup witnesses")
        artifacts = ()
        if inspection:
            artifacts = (CadArtifact("model", payload["artifact_relative_path"], inspection["file_sha256"], "3dm", verified),)
        semantics = _semantics(request)
        if verified:
            readback = cad.ThreeDmInspection(**{f.name: inspection[f.name] for f in fields(cad.ThreeDmInspection) if f.name in inspection})
            counts = {key: int(row["brep_count"]) for key, row in cad.expected_object_bounds(request.program).items()}
            if cad._rhino_semantic_failures(semantics, readback, counts):
                raise CadExecutionError("Rhino retained readback differs from requested object/semantic identity")
        return CadExecutionResult(self.backend_id, request.binding, payload["status"], verified, artifacts,
            tuple(sorted(semantics["objects"])), semantics, payload, inspection, tuple(payload["failures"]), "rebuild")


def _options(options, allowed, backend_id):
    if options.get("patch_oracle") and "patch_oracle" not in allowed:
        raise CadExecutionError("patch_oracle requires --cad-backend rhino")
    unknown = set(options) - allowed
    if unknown:
        raise CadExecutionError(f"{backend_id} unsupported backend options: {sorted(unknown)}")


# Add an implementation here and declare it under compiled-cad-execution in the
# module registry. The runner uses this same table for selection and execution.
CAD_BACKEND_REGISTRY: dict[str, CadBackend] = {backend.backend_id: backend for backend in (OcctBackend(), RhinoBackend())}


def get_cad_backend(backend_id: str) -> CadBackend:
    try:
        return CAD_BACKEND_REGISTRY[backend_id]
    except KeyError as exc:
        raise CadExecutionError(f"unknown cad_backend {backend_id!r}; registered: {tuple(CAD_BACKEND_REGISTRY)}") from exc


def cad_backend_ids() -> tuple[str, ...]:
    return tuple(CAD_BACKEND_REGISTRY)
