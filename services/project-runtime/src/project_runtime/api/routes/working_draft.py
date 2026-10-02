"""Working positions and local recovery never accept a design Stage."""

from fastapi import APIRouter, Query
from starlette.requests import Request

from ...application.artifacts import ModelSource
from ...authentication import request_attribution
from ...binding import bound_project
from ...application.representation_dependencies import UNAVAILABLE, ReplacementCycle, representation_status
from ...application.working_draft import (
    WorkingDraftDto, read_working_draft, resolve_working_source, retain_local_draft, save_working_draft,
    select_working_draft, working_revision,
)
from ..dto.working_draft import (
    LocalDraftRequestDto, RepresentationStatusDto, WorkingDraftSaveDto, WorkingDraftSelectionDto, WorkingRevisionDto,
    WorkingSourceDto, working_source_dto,
)
from .proposals import _require_bound_project

router = APIRouter(tags=["working-draft"])


@router.get("/working-draft", response_model=WorkingDraftDto)
def read_current_working_draft(request: Request) -> WorkingDraftDto:
    return read_working_draft(bound_project(request.app.state))


@router.get("/working-draft/revision", response_model=WorkingRevisionDto)
def read_working_revision(request: Request) -> WorkingRevisionDto:
    """Poll this, not the whole position: it carries no local draft and takes no project guard."""
    binding = bound_project(request.app.state)
    return WorkingRevisionDto(projectId=binding.project_id, revisionSha256=working_revision(binding))


@router.put("/working-draft", response_model=WorkingDraftDto)
def select_current_working_draft(request: Request, payload: WorkingDraftSelectionDto) -> WorkingDraftDto:
    """Continue: the Working Head follows at once, and a move onto a run records who made it."""
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.projectId)
    message = None if payload.messageSource is None else payload.messageSource.model_dump(by_alias=True)
    answer = select_working_draft(binding, payload.runId, payload.baseRevisionSha256, payload.branchId,
                                  attribution=request_attribution(request), message_source=message,
                                  raw_language=payload.rawLanguage)
    # #575: what the line now moves past goes to the project trash, on the cleaning's own thread.
    retention = getattr(request.app.state, "retention", None)
    if retention is not None and payload.runId is not None:
        retention.after_continue()
    return answer


@router.post("/working-draft/save", response_model=WorkingDraftDto)
def save_current_working_draft(request: Request, payload: WorkingDraftSaveDto) -> WorkingDraftDto:
    """Name a run as a version; the Hub page's own save says the person saved it (savedBy, #575)."""
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.projectId)
    return save_working_draft(binding, payload.runId, payload.baseRevisionSha256, payload.label,
                              saved_by=payload.savedBy)


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


@router.get("/representation-status", response_model=RepresentationStatusDto)
def read_representation_status(
    request: Request,
    runId: str = Query(min_length=1, description="The page's storage run."),
    assetSha256: str = Query(pattern=r"^[0-9a-f]{64}$", description="The registered document's digest."),
    revisionRef: str | None = Query(None, min_length=1, description="The drawing revision; omit for a page without one."),
    pageIndex: int = Query(0, ge=0, description="Zero-based page index."),
) -> RepresentationStatusDto:
    """Whether one exact registered page still shows what its inputs say now, compared with the Working Head (#223).

    The representation-status projection the Worktree Graph and Publish read, asked of the page's owner on
    every read. Replacements that loop are unavailable, as the graph reads them. Nothing is written.
    """
    binding = bound_project(request.app.state)
    try:
        status = representation_status(binding, (runId, assetSha256, revisionRef, pageIndex))
        state, reason = status.state, status.reason
    except ReplacementCycle as exc:
        state, reason = UNAVAILABLE, str(exc)
    return RepresentationStatusDto(projectId=binding.project_id, state=state, reason=reason)
