"""Project Runtime image attempts; sync handlers keep I/O off the event loop."""
from fastapi import APIRouter
from starlette.requests import Request

from ...application import camera_passes
from ...application.artifacts import ModelSource
from ...authentication import request_attribution
from ...binding import bound_project
from ...errors import StudioError
from ..dto.rendering import (
    CameraPassRequestDto, RenderCapabilitiesDto, RenderJobDto, RenderJobListDto, RenderViewSourceRequestDto, render_capability_dto,
    render_job_dto,
)
from ..dto.artifacts import SourceDocumentDto, document_dto
from ...application.rendering import RenderRequestDto, save_render_view

router = APIRouter(prefix="/render", tags=["render"])


@router.post("/views", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def retain_render_view(request: Request, payload: RenderViewSourceRequestDto):
    return document_dto(save_render_view(bound_project(request.app.state), payload))


@router.post("/views/passes", status_code=202)
def create_camera_passes(request: Request, payload: CameraPassRequestDto) -> dict:
    """Draw an object-ID, a line and a depth image of one exact model through a perspective camera (#19).

    A background job; read it at ``statusPath``. On success its three PNGs are
    registered as documents beside the model, at up to 4096 pixels on the long
    edge, with the camera, size, ID legend and depth range in their recipes.
    """

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The camera pass names another project.")
    return camera_passes.submit_camera_passes(
        binding, request.app.state.jobs, attribution=request_attribution(request),
        view=None if payload.view is None else payload.view.model_dump(by_alias=True),
        camera=None if payload.camera is None else payload.camera.model_dump(by_alias=True),
        size=payload.size, long_edge=payload.long_edge, id_by=payload.id_by,
        model_source=None if payload.model_source is None else ModelSource.from_dict(
            payload.model_source.model_dump(by_alias=True)),
        source_asset=payload.source_asset, source_stage_ref=payload.source_stage_ref,
        clip=None if payload.clip is None else {"origin": list(payload.clip.origin), "normal": list(payload.clip.normal)},
    )


@router.get("/views/passes/{pass_id}")
def read_camera_passes(request: Request, pass_id: str) -> dict:
    """One camera pass run's latest record: queued, running, succeeded (with its documents) or failed."""

    return camera_passes.pass_report(bound_project(request.app.state), pass_id)


@router.get("/capabilities", response_model=RenderCapabilitiesDto, response_model_by_alias=True)
def render_capabilities(request: Request):
    return RenderCapabilitiesDto(providers=[
        render_capability_dto(capability) for capability in request.app.state.render_jobs.capabilities()])


@router.post("/jobs", response_model=RenderJobDto, response_model_by_alias=True, status_code=202)
def create_render_job(request: Request, payload: RenderRequestDto):
    return render_job_dto(request.app.state.render_jobs.submit(bound_project(request.app.state), payload))


@router.get("/jobs", response_model=RenderJobListDto, response_model_by_alias=True)
def list_render_jobs(request: Request):
    binding = bound_project(request.app.state)
    return RenderJobListDto(projectId=binding.project_id,
                            jobs=[render_job_dto(job) for job in request.app.state.render_jobs.list(binding)])


@router.get("/jobs/{job_id}", response_model=RenderJobDto, response_model_by_alias=True)
def get_render_job(request: Request, job_id: str):
    return render_job_dto(request.app.state.render_jobs.get(bound_project(request.app.state), job_id))
