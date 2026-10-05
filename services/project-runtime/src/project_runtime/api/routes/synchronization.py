"""A shared project exchanges retained content; Runtime processes compute it."""

import base64

from fastapi import APIRouter, Query, Response
from starlette.requests import Request

from archflow.project.repository import ProjectRepositoryError

from ...authentication import require_actor, read_actor_credentials
from ...binding import bound_project
from ...synchronization import pull_shared_project, push_shared_candidate, transfer_error
from ...settings import SHARED_PROJECT_ROLE
from ...errors import StudioError
from ...sync_cache import SyncDownloadCache
from ..dto.synchronization import (ProjectTransferDto, CandidateTransferDto, SynchronizationDto, SyncFilesDto,
                                   SyncUploadBatchDto, SyncLineDto)

router = APIRouter(prefix="/sync", tags=["synchronization"])


def _shared(request: Request, action: str):
    if request.app.state.settings.service_role != SHARED_PROJECT_ROLE and not request.app.state.settings.team_owner:
        raise StudioError(404, "NOT_FOUND", "This endpoint belongs to a shared project service.")
    binding = bound_project(request.app.state)
    require_actor(request, action, binding.project_id)
    return binding


@router.get("/manifest", response_model=ProjectTransferDto)
def shared_manifest(request: Request) -> ProjectTransferDto:
    binding = _shared(request, "read")
    try:
        return ProjectTransferDto.model_validate(binding.repository.export_sync_transfer(include_contents=False, all_candidates=request.app.state.settings.service_role == SHARED_PROJECT_ROLE))
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc


@router.get("/files", response_class=Response, responses={200: {"content": {"application/octet-stream": {}}}})
def shared_file(request: Request, path: str, sha256: str = Query(pattern=r"^[0-9a-f]{64}$")) -> Response:
    binding = _shared(request, "read")
    allowed = {row["path"]: row["sha256"] for row in binding.repository.export_sync_transfer(all_candidates=request.app.state.settings.service_role == SHARED_PROJECT_ROLE)["files"]}
    if allowed.get(path) != sha256:
        raise StudioError(422, "SYNC_FILE_INVALID", "Only shared retained content can be downloaded.")
    try:
        content = binding.repository.read_transfer_file(path, sha256)
    except (ProjectRepositoryError, FileNotFoundError) as exc:
        raise StudioError(422, "SYNC_FILE_INVALID", "The requested project file or content identity is invalid.") from exc
    return Response(content=content, media_type="application/octet-stream")


@router.post("/candidates", response_model=SynchronizationDto, response_model_by_alias=True)
def receive_candidate(request: Request, payload: CandidateTransferDto) -> SynchronizationDto:
    binding = _shared(request, "propose")
    if payload.project_id != binding.project_id or payload.mode != "candidate" or payload.root_run_id is None:
        raise StudioError(422, "SYNC_TRANSFER_INVALID", "Upload a candidate from this shared project.")
    try:
        transfer = payload.model_dump(exclude={"retained_row"})
        cache = _cache(request)
        for row in transfer["files"]:
            if row["path"] not in transfer["contents"]:
                try:
                    binding.repository.read_transfer_file(row["path"], row["sha256"])
                except (ProjectRepositoryError, FileNotFoundError):
                    transfer["contents"][row["path"]] = cache.encoded(row["sha256"], row["size"])
        binding.repository.import_candidate_transfer(transfer, retained_row=payload.retained_row)
    except ValueError as exc:
        raise StudioError(422, "SYNC_CHUNK_INVALID", "A candidate file is incomplete or corrupt; resume its upload.") from exc
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


def _cache(request: Request) -> SyncDownloadCache:
    return request.app.state.sync_cache


@router.post("/files/batch")
def shared_files(request: Request, payload: SyncFilesDto) -> dict:
    binding = _shared(request, "read")
    if sum(row.length for row in payload.files) > 8 * 1024 * 1024:
        raise StudioError(422, "SYNC_BATCH_TOO_LARGE", "Request at most 8 MiB per batch.")
    allowed = {row["path"]: row["sha256"] for row in binding.repository.export_sync_transfer(all_candidates=request.app.state.settings.service_role == SHARED_PROJECT_ROLE)["files"]}
    result = []
    for row in payload.files:
        if allowed.get(row.path) != row.sha256:
            raise StudioError(422, "SYNC_FILE_INVALID", "Only shared retained content can be downloaded.")
        content = binding.repository.read_transfer_file(row.path, row.sha256)
        if len(content) != row.size or row.offset > row.size:
            raise StudioError(422, "SYNC_FILE_INVALID", "The requested file changed.")
        result.append({**row.model_dump(), "content": base64.b64encode(content[row.offset:row.offset + row.length]).decode("ascii")})
    return {"files": result}


@router.post("/files/missing")
def missing_files(request: Request, payload: SyncFilesDto) -> dict:
    binding = _shared(request, "read")
    cache = _cache(request)
    result = []
    for row in payload.files:
        try:
            content = binding.repository.read_transfer_file(row.path, row.sha256)
            if len(content) != row.size:
                raise ValueError("size mismatch")
        except (ProjectRepositoryError, FileNotFoundError, ValueError):
            result.append({**row.model_dump(), "offset": cache.offset(row.sha256, row.size)})
    return {"files": result}


@router.post("/files/upload")
def upload_files(request: Request, payload: SyncUploadBatchDto) -> dict:
    _shared(request, "propose")
    cache = _cache(request)
    if sum(len(row.content) for row in payload.files) > 12 * 1024 * 1024:
        raise StudioError(422, "SYNC_BATCH_TOO_LARGE", "Upload at most 8 MiB per batch.")
    try:
        for row in payload.files:
            content = base64.b64decode(row.content, validate=True)
            if len(content) > 8 * 1024 * 1024:
                raise ValueError("chunk too large")
            cache.append(row.sha256, row.offset, content, row.size)
    except ValueError as exc:
        raise StudioError(422, "SYNC_CHUNK_INVALID", "The chunk does not match its content position.") from exc
    return {"ok": True}


@router.get("/identity")
def shared_identity(request: Request) -> dict:
    binding = _shared(request, "read")
    actor = require_actor(request, "read", binding.project_id)
    role = "moderator" if actor.allows(binding.project_id, "accept") else "designer" if actor.allows(binding.project_id, "propose") else "viewer"
    return {"actorId": actor.actor_id, "name": actor.display_name or actor.actor_id, "role": role}


@router.get("/lines")
def shared_lines(request: Request) -> dict:
    binding = _shared(request, "read")
    settings = request.app.state.settings
    credentials = read_actor_credentials(settings.actors_file, settings.project_dir)
    return {"lines": [{**binding.repository.sync_actor_line(actor.actor_id, owner_actor_id=settings.project_owner_actor_id),
                       "name": actor.display_name or actor.actor_id}
                      for _, actor in credentials.entries if actor.allows(binding.project_id, "read")]}


@router.put("/line")
def receive_line(request: Request, payload: SyncLineDto) -> dict:
    binding = _shared(request, "propose")
    actor = require_actor(request, "propose", binding.project_id)
    try:
        return binding.repository.receive_sync_actor_line(actor.actor_id,
            {"current": payload.current, "row": payload.row}, expected_revision=payload.expected_revision,
            owner_actor_id=request.app.state.settings.project_owner_actor_id)
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc


@router.get("/status")
def sync_status(request: Request) -> dict:
    worker = getattr(request.app.state, "team_sync", None)
    return worker.snapshot() if worker else {"status": "not-connected", "role": None, "error": None, "totalBytes": 0, "completedBytes": 0}
