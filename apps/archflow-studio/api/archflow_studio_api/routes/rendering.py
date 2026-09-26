"""Project Runtime image attempts; sync handlers keep I/O off the event loop."""
from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..transport.rendering import RenderCapabilitiesDto, RenderJobDto, RenderJobListDto, RenderRequestDto, RenderViewSourceRequestDto
from ..transport.artifacts import SourceDocumentDto, document_dto
from ..application.rendering import save_render_view

router = APIRouter(prefix="/render", tags=["render"])

from ..application import geometry_sources, render_scene, mesh_drawings, physical_render
from ..transport.render_scene import SaveScene, SelectGeometry, CyclesRequest, MeshDrawingRequest

@router.get('/scene')
def read_scene(request:Request):
    return render_scene.get_scene(bound_project(request.app.state)) | {'cyclesAvailable':bool(physical_render.executable())}

@router.put('/scene')
def update_scene(request:Request,payload:SaveScene):
    return render_scene.save_scene(bound_project(request.app.state),payload)

@router.get('/geometry')
def scene_geometry(request:Request):
    return geometry_sources.geometry_payload(bound_project(request.app.state))

@router.put('/geometry')
def choose_geometry(request:Request,payload:SelectGeometry):
    return geometry_sources.select_geometry(bound_project(request.app.state),expected_revision=payload.expectedRevision,
        export_id=payload.exportId,run_id=payload.runId,asset_sha256=payload.assetSha256,event_sink=request.app.state.events)

@router.get('/scene/default')
def default_scene(request:Request):
    return render_scene.default_scene(bound_project(request.app.state))

@router.post('/cycles',response_model=RenderJobDto,response_model_by_alias=True,status_code=202)
def submit_cycles(request:Request,payload:CyclesRequest):
    request.app.state.render_jobs.runtime_jobs=request.app.state.jobs
    return physical_render.submit(bound_project(request.app.state),request.app.state.jobs,payload)

@router.get('/drawings')
def scene_drawings(request:Request):
    return mesh_drawings.drawing_list(bound_project(request.app.state))

@router.post('/drawings')
def update_scene_drawings(request:Request,payload:MeshDrawingRequest):
    return mesh_drawings.generate(bound_project(request.app.state),payload)


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
