"""Project Runtime image attempts; sync handlers keep I/O off the event loop."""
from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..transport.rendering import RenderCapabilitiesDto, RenderJobDto, RenderJobListDto, RenderRequestDto, RenderViewSourceRequestDto
from ..transport.artifacts import SourceDocumentDto, document_dto
from ..application.rendering import save_render_view

router = APIRouter(prefix="/render", tags=["render"])


@router.post("/views", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def retain_render_view(request: Request, payload: RenderViewSourceRequestDto):
    return document_dto(save_render_view(bound_project(request.app.state), payload))


@router.get("/capabilities", response_model=RenderCapabilitiesDto, response_model_by_alias=True)
def render_capabilities(request: Request):
    return RenderCapabilitiesDto(providers=request.app.state.render_jobs.capabilities())


@router.post("/jobs", response_model=RenderJobDto, response_model_by_alias=True, status_code=202)
def create_render_job(request: Request, payload: RenderRequestDto):
    return request.app.state.render_jobs.submit(bound_project(request.app.state), payload)


@router.get("/jobs", response_model=RenderJobListDto, response_model_by_alias=True)
def list_render_jobs(request: Request):
    binding = bound_project(request.app.state)
    return RenderJobListDto(projectId=binding.project_id, jobs=request.app.state.render_jobs.list(binding))


@router.get("/jobs/{job_id}", response_model=RenderJobDto, response_model_by_alias=True)
def get_render_job(request: Request, job_id: str):
    return request.app.state.render_jobs.get(bound_project(request.app.state), job_id)
