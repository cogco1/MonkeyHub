"""The Rhino implementation of the compiled-cad-execution interface, as registered in ``monkeycad.registry``."""

from __future__ import annotations

from dataclasses import fields, replace

from archflow.state.geometry_program import GeometryBoundsError, expected_object_bounds
from monkeycad.backends.rhino import export
from monkeycad.execution import (
    CadArtifact,
    CadExecutionError,
    CadExecutionResult,
    _check_receipt,
    _native_inputs,
    _options,
    _require_file_digest,
    _semantics,
    _unsupported,
)
from monkeycad.formats.three_dm_inspector import ThreeDmInspection
from monkeycad.program import CadTranslationError


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
            _require_file_digest(request.source.model, request.source.sha256, "source artifact")
            patch = export.RhinoPatchBase(request.source.model, request.source.program)
            label = "patch"
        try:
            plan = export.prepare_rhino_three_dm_export(request.program, **_native_inputs(request),
                artifact_name=f"{request.artifact_stem}.3dm", patch=patch)
        except (CadTranslationError, GeometryBoundsError) as exc:
            return _unsupported(request, self.backend_id, exc)
        except CadExecutionError as exc:
            if "typed losses" in str(exc):
                return _unsupported(request, self.backend_id, exc)
            raise
        options = {k: v for k, v in request.backend_options.items() if k != "patch_oracle"}
        options.setdefault("powershell_executable", None)
        options.setdefault("timeout_seconds", 900)
        receipt = export.execute_rhino_three_dm_export(plan, **options)
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
        _check_receipt(request, payload, export.RhinoCadExecutionReceipt.SCHEMA)
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
            readback = ThreeDmInspection(**{f.name: inspection[f.name] for f in fields(ThreeDmInspection) if f.name in inspection})
            counts = {key: int(row["brep_count"]) for key, row in expected_object_bounds(request.program).items()}
            if export._rhino_semantic_failures(semantics, readback, counts):
                raise CadExecutionError("Rhino retained readback differs from requested object/semantic identity")
        return CadExecutionResult(self.backend_id, request.binding, payload["status"], verified, artifacts,
            tuple(sorted(semantics["objects"])), semantics, payload, inspection, tuple(payload["failures"]), "rebuild")
