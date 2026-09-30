"""A shared project exchanges retained content; Runtime processes compute it."""

from fastapi import APIRouter, Query, Response
from starlette.requests import Request

from archflow.project.repository import ProjectRepositoryError

from ..application.authentication import require_actor
from ..application.binding import bound_project
from ..application.synchronization import pull_shared_project, push_shared_candidate, transfer_error
from ..settings import SHARED_PROJECT_ROLE
from ..transport.errors import StudioError
from ..transport.synchronization import ProjectTransferDto, SynchronizationDto

router = APIRouter(prefix="/sync", tags=["synchronization"])


def _shared(request: Request, action: str):
    if request.app.state.settings.service_role != SHARED_PROJECT_ROLE:
        raise StudioError(404, "NOT_FOUND", "This endpoint belongs to a shared project service.")
    binding = bound_project(request.app.state)
    require_actor(request, action, binding.project_id)
    return binding


@router.get("/manifest", response_model=ProjectTransferDto)
def shared_manifest(request: Request) -> ProjectTransferDto:
    binding = _shared(request, "read")
    try:
        return ProjectTransferDto.model_validate(binding.repository.export_transfer(include_contents=False))
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc


@router.get("/files", response_class=Response, responses={200: {"content": {"application/octet-stream": {}}}})
def shared_file(request: Request, path: str, sha256: str = Query(pattern=r"^[0-9a-f]{64}$")) -> Response:
    binding = _shared(request, "read")
    try:
        content = binding.repository.read_transfer_file(path, sha256)
    except (ProjectRepositoryError, FileNotFoundError) as exc:
        raise StudioError(422, "SYNC_FILE_INVALID", "The requested project file or content identity is invalid.") from exc
    return Response(content=content, media_type="application/octet-stream")


@router.post("/candidates", response_model=SynchronizationDto, response_model_by_alias=True)
def receive_candidate(request: Request, payload: ProjectTransferDto) -> SynchronizationDto:
    binding = _shared(request, "propose")
    if payload.project_id != binding.project_id or payload.mode != "candidate" or payload.root_run_id is None:
        raise StudioError(422, "SYNC_TRANSFER_INVALID", "Upload a candidate from this shared project.")
    try:
        binding.repository.import_candidate_transfer(payload.model_dump())
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc
    return SynchronizationDto(projectId=binding.project_id, candidateId=payload.root_run_id,
                              filesTransferred=len(payload.contents),
                              bytesTransferred=sum(row.size for row in payload.files if row.path in payload.contents))


@router.post("/pull", response_model=SynchronizationDto, response_model_by_alias=True)
def pull_project(request: Request) -> SynchronizationDto:
    return pull_shared_project(request.app.state)


@router.post("/push", response_model=SynchronizationDto, response_model_by_alias=True)
def push_candidate(request: Request, candidate_id: str = Query(alias="candidateId", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")) -> SynchronizationDto:
    return push_shared_candidate(request.app.state, candidate_id)
