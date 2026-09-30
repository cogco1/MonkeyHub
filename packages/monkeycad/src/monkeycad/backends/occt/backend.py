"""The OCCT implementation of the compiled-cad-execution interface, as registered in ``monkeycad.registry``."""

from __future__ import annotations

from monkeycad.backends.occt import export
from monkeycad.backends.occt.kernel import backend_identity
from monkeycad.execution import (
    CadArtifact,
    CadCapabilityError,
    CadExecutionError,
    CadExecutionResult,
    _check_receipt,
    _native_inputs,
    _options,
    _unsupported,
)


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
            receipt = export.execute_occt_export(request.program, **_native_inputs(request), artifact_stem=request.artifact_stem,
                preview=request.backend_options.get("preview", True), operation_observer=request.operation_observer,
                observation_parent_id=request.observation_parent_id, **reuse)
        except CadCapabilityError as exc:
            return _unsupported(request, self.backend_id, exc)
        result = self.read_receipt(request, receipt.to_dict())
        result.validate(request, self.backend_id)
        return result

    def read_receipt(self, request, payload):
        _check_receipt(request, payload, export.OcctExecutionReceipt.SCHEMA)
        if payload.get("backend") != backend_identity():
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
