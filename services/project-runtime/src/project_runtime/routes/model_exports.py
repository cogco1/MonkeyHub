from typing import Literal
from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from ..application.binding import bound_project
from ..application import model_exports
from ..transport.errors import StudioError

router = APIRouter(tags=["model exports"])


class ExportDTO(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ModelUpload(ExportDTO):
    file_name: str = Field(alias="fileName", min_length=1, max_length=240)
    content_base64: str = Field(alias="contentBase64", max_length=55924056)
    attachment_id: str | None = Field(None, alias="attachmentId")


class ProjectRevision(ExportDTO):
    run_id: str = Field(alias="runId", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    state_digest: str = Field(alias="stateDigest", pattern=r"^[a-f0-9]{64}$")
    asset_sha256: str | None = Field(None, alias="assetSha256", pattern=r"^[a-f0-9]{64}$")


class ModelExportRequest(ExportDTO):
    target_format: Literal["3dm", "skp", "glb", "dwg"] = Field(alias="targetFormat")
    project_revision: ProjectRevision | None = Field(None, alias="projectRevision")
    source_artifact_id: str | None = Field(None, alias="sourceArtifactId", pattern=r"^[a-f0-9]{64}$")
    upload: ModelUpload | None = None


@router.get("/exports/capabilities")
def capabilities():
    return model_exports.capabilities()


@router.post("/exports", status_code=202)
def create_export(request: Request, payload: ModelExportRequest):
    return model_exports.submit(bound_project(request.app.state), request.app.state.jobs, payload)


@router.get("/exports/{export_id}")
def read_export(request: Request, export_id: str):
    result = model_exports.report(bound_project(request.app.state), export_id)
    if result["status"] in ("queued", "running"):
        try:
            job = request.app.state.jobs.for_candidate(export_id)
            result = result | {"jobStatus": job.status, "jobId": job.job_id}
        except StudioError:
            result = result | {"status": "interrupted", "failureReason": "The runtime restarted; this job was not resumed. Submit a new request."}
    return result


@router.get("/exports/{export_id}/bytes")
def download_export(request: Request, export_id: str):
    result, data = model_exports.download(bound_project(request.app.state), export_id)
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{result["fileName"]}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
