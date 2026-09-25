"""Working positions and local recovery never accept a design Stage."""

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.artifacts import ModelSource
from ..application.binding import bound_project
from ..application.working_draft import (
    read_working_draft, resolve_working_source, retain_local_draft, save_working_draft, select_working_draft,
)
from ..transport.working_draft import (
    LocalDraftRequestDto, WorkingDraftDto, WorkingDraftSaveDto, WorkingDraftSelectionDto, WorkingSourceDto,
    working_source_dto,
)
from .proposals import _require_bound_project

router = APIRouter(tags=["working-draft"])


@router.get("/working-draft", response_model=WorkingDraftDto)
def read_current_working_draft(request: Request) -> WorkingDraftDto:
    return read_working_draft(bound_project(request.app.state))


@router.put("/working-draft", response_model=WorkingDraftDto)
def select_current_working_draft(request: Request, payload: WorkingDraftSelectionDto) -> WorkingDraftDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.projectId)
    return select_working_draft(binding, payload.runId, payload.baseRevisionSha256, payload.branchId)


@router.post("/working-draft/save", response_model=WorkingDraftDto)
def save_current_working_draft(request: Request, payload: WorkingDraftSaveDto) -> WorkingDraftDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.projectId)
    return save_working_draft(binding, payload.runId, payload.baseRevisionSha256, payload.label)


@router.put("/working-draft/local", response_model=WorkingDraftDto)
def retain_local_working_draft(request: Request, payload: LocalDraftRequestDto) -> WorkingDraftDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.projectId)
    return retain_local_draft(binding, payload.draft, payload.baseRevisionSha256, payload.expectedSource)


@router.get("/working-source", response_model=WorkingSourceDto)
def read_working_source(
    request: Request,
    workspace: str = Query("modeling", description="modeling, drawing, render or board."),
    policy: str = Query("live", description="live follows the Working Head; frozen keeps the exact pinned model."),
    runId: str | None = Query(None, description="Frozen pin: the exact retained run."),
    stateDigest: str | None = Query(None, description="Frozen pin: that run's exact state digest."),
    assetSha256: str | None = Query(None, description="Frozen pin: the exact model bytes."),
) -> WorkingSourceDto:
    """The current working source one workspace follows, resolved from retained facts only."""
    pin = (runId, stateDigest, assetSha256)
    pinned = ModelSource(*pin) if all(pin) else None
    return working_source_dto(resolve_working_source(bound_project(request.app.state), workspace,
                                                     policy=policy, pinned=pinned))
