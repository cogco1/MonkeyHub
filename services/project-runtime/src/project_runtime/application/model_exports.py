"""Conversion jobs and retained reports owned by studio.artifacts/P036."""
from dataclasses import asdict
from io import BytesIO
import base64
import hashlib
from pathlib import PurePath
from uuid import uuid4

from archflow.project.ports import PersistenceArea, PersistenceDestination
from .artifacts import artifact_bytes, list_artifacts, require_model_source, require_complete_model, ModelSource
from .projection import project_state
from archflow.adapters.model_formats import convert, FORMATS
from archflow.adapters.model_providers import ConversionCoordinator, ConversionFailure
from ..transport.errors import StudioError, error_sentence

KIND = "studio-model-export"


def _save(binding, run, report):
    binding.repository.put_json(run=run,
        destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
        record_kind=KIND, payload=report)


def report(binding, export_id):
    rows = [binding.repository.load_json(ref) for ref in binding.record_refs(export_id, kind=KIND)]
    if not rows:
        raise StudioError(404, "EXPORT_NOT_FOUND", "No retained export with that ID.")
    return max(rows, key=lambda r: {"queued": 0, "running": 1, "failed": 2, "succeeded": 2}[r["status"]])


def _object(binding, value, *, role):
    try:
        data = binding.repository.layout.resolve_relative(value["relative_path"]).read_bytes()
    except FileNotFoundError as exc:
        raise StudioError(
            409,
            f"EXPORT_{role.upper()}_MISSING",
            f"The retained {role} artifact is missing.",
        ) from exc
    except OSError as exc:
        raise StudioError(
            409,
            f"EXPORT_{role.upper()}_UNREADABLE",
            f"The retained {role} artifact cannot be read.",
        ) from exc
    if hashlib.sha256(data).hexdigest() != value["sha256"]:
        raise StudioError(409, "EXPORT_DIGEST_MISMATCH", "The retained artifact no longer matches its digest.")
    return data


def download(binding, export_id):
    result = report(binding, export_id)
    if result["status"] != "succeeded":
        raise StudioError(409, "EXPORT_NOT_READY", "The conversion has not succeeded.")
    return result, _object(binding, result["outputArtifact"], role="output")


def submit(binding, jobs, payload):
    target = payload.target_format
    selection = sum((payload.upload is not None, payload.source_artifact_id is not None, payload.project_revision is not None))
    if selection != 1:
        raise StudioError(422, "EXPORT_SOURCE_AMBIGUOUS", "Which model do you mean: the current project revision or an uploaded file? Select exactly one source.")
    source_revision, source_artifact, upload = None, None, payload.upload
    if upload is not None:
        name = upload.file_name
        if any(c in name for c in "/\\\r\n\x00") or len(name) > 240:
            raise StudioError(422, "EXPORT_SOURCE_INVALID", "Supply a model filename, not a path.")
        source_format = PurePath(name).suffix.lower().lstrip(".")
        try:
            data = base64.b64decode(upload.content_base64, validate=True)
        except ValueError as exc:
            raise StudioError(422, "EXPORT_SOURCE_INVALID", "Invalid base64 source bytes.") from exc
        if not data or len(data) > 40 * 1024 * 1024:
            raise StudioError(413, "EXPORT_SOURCE_INVALID", "Model sources must contain 1 byte to 40 MiB.")
    elif payload.source_artifact_id is not None:
        originals = [report(binding, run_id) for run_id in binding.run_ids() if run_id.startswith("export-")]
        original = next((r for r in originals if r["sourceArtifact"]["sha256"] == payload.source_artifact_id), None)
        if original is None:
            raise StudioError(404, "EXPORT_SOURCE_NOT_FOUND", "This project has no retained source artifact with that ID.")
        source_artifact = original["sourceArtifact"]
        data = _object(binding, source_artifact, role="source")
        source_format = original["sourceFormat"]
    else:
        revision = payload.project_revision
        projection = project_state(binding, revision.run_id)
        if not projection.reference_state_exact or projection.state_digest != revision.state_digest:
            raise StudioError(409, "EXPORT_REVISION_MISMATCH", "The requested project revision is not the exact retained state.")
        candidates = [a for a in list_artifacts(binding, run_id=revision.run_id).artifacts
                      if a.format == "3dm" and a.available and a.design_state_digest == revision.state_digest]
        if revision.asset_sha256:
            candidates = [a for a in candidates if a.sha256 == revision.asset_sha256]
        valid = []
        for candidate in candidates:
            try:
                require_model_source(binding, ModelSource(revision.run_id, revision.state_digest, candidate.sha256), projection)
                require_complete_model(candidate, projection.reference_receipt or {})
                valid.append(candidate)
            except StudioError:
                continue
        if len(valid) != 1:
            raise StudioError(422, "EXPORT_PROJECT_SOURCE_UNAVAILABLE", "Select one complete model of this exact project revision. A missing, partial or ambiguous projection cannot be exported.")
        _, data = artifact_bytes(binding, valid[0].sha256, run_id=revision.run_id)
        source_format = "3dm"
        source_revision = revision.model_dump(by_alias=True)
    if source_format not in FORMATS:
        raise StudioError(422, "EXPORT_FORMAT_INVALID", "Choose a 3DM, SKP, GLB or DWG source.")
    export_id = "export-" + uuid4().hex
    run = binding.repository.create_run(export_id)
    if source_artifact is None:
        source_artifact = asdict(binding.repository.ingest(run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT), artifact_id="source-model",
            media_type="application/octet-stream", source=BytesIO(data)))
    value = {"schema": "StudioModelExport@1", "exportId": export_id, "projectId": binding.project_id,
             "status": "queued", "sourceFormat": source_format, "targetFormat": target,
             "sourceArtifact": source_artifact, "sourceArtifactId": payload.source_artifact_id or source_artifact["sha256"],
             "sourceProjectRevision": source_revision, "sourceSha256": hashlib.sha256(data).hexdigest(),
             "sourceFileName": upload.file_name if upload else None,
             "sourceAttachmentId": upload.attachment_id if upload else None,
             "converter": [], "sourceUnits": None, "outputUnits": None, "warnings": [],
             "provider": None, "providerVersion": None, "executionMode": None,
             "intermediateFormats": [], "usedIntermediateFormats": False, "losses": None,
             "previewArtifacts": [], "nativeOutputArtifact": None,
             "artifactRoles": {"sourceArtifact": "original", "outputArtifact": "delivery", "previewArtifacts": "preview"},
             "outputValidation": {"status": "not-run", "checks": []},
             "outputArtifact": None, "failureReason": None}
    _save(binding, run, value)

    def work():
        current = value | {"status": "running"}
        _save(binding, run, current)
        try:
            output, details = convert(data, source_format, target)
            current = current | details
            artifact = binding.repository.ingest(run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
                artifact_id="converted-model", media_type="model/gltf-binary" if target == "glb" else "application/octet-stream",
                source=BytesIO(output))
            _save(binding, run, current | details | {"status": "succeeded", "outputArtifact": asdict(artifact),
                "nativeOutputArtifact": asdict(artifact) if target in ("skp", "dwg") else None,
                "outputSha256": artifact.sha256, "downloadPath": f"/api/exports/{export_id}/bytes",
                "fileName": f"model.{target}"})
        except Exception as exc:
            details = exc.report if isinstance(exc, ConversionFailure) else {}
            _save(binding, run, current | details | {"status": "failed", "failureReason": error_sentence(exc)})
            raise
    try:
        job = jobs.submit(candidate_id=export_id, proposal_id=export_id, work=work, kind="export")
    except Exception as exc:
        _save(binding, run, value | {"status": "failed", "failureReason": error_sentence(exc)})
        raise
    return {"exportId": export_id, "jobId": job.job_id, "status": job.status,
            "statusPath": f"/api/exports/{export_id}"}


def capabilities():
    return ConversionCoordinator().capabilities()
