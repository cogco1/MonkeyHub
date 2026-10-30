"""The Working Head's history and its moves: the moves it made onto runs, and Switch with its Undo (#300).

The history page (2-6) and the attention executor share these. Switch is Continue
(``PUT /api/working-draft``) answered with the Undo that puts back what it replaced.
"""

from fastapi import APIRouter, Query
from starlette.requests import Request

from ...application.working_draft import (
    HeadEntry, WorkingDraftDto, continue_moves, switch_working_design, undo_working_design,
)
from ...authentication import agent_chat, request_attribution
from ...binding import bound_project
from ..dto.working_draft import (
    ContinueMovesDto, WorkingDesignSwitchDto, WorkingDesignSwitchRequestDto, WorkingDesignUndoRequestDto,
    continue_moves_dto, switch_dto,
)
from .episodes import require_design_loop
from .proposals import _require_bound_project

router = APIRouter(tags=["working-design"])


@router.get("/working-design/moves", response_model=ContinueMovesDto)
def read_working_design_moves(request: Request, limit: int = Query(50, ge=1, le=500)) -> ContinueMovesDto:
    """The moves that brought the Working Head where it stands, newest first, as their design.continued events retained them."""

    binding = bound_project(request.app.state)
    moves, warnings = continue_moves(binding, limit=limit)
    return continue_moves_dto(binding.project_id, moves, warnings)


def _after_move(request: Request) -> None:
    # #575: what the line now moves past goes to the project trash, on the cleaning's own thread.
    retention = getattr(request.app.state, "retention", None)
    if retention is not None:
        retention.after_continue()


@router.post("/working-design/switch", response_model=WorkingDesignSwitchDto)
def switch_working_design_route(request: Request, payload: WorkingDesignSwitchRequestDto) -> WorkingDesignSwitchDto:
    """Move the Working Head onto a run, as Continue does, and say how to put back what it replaced.

    Refused with ``DESIGN_TREE_UNSYNCED_EDITS`` while Modeling holds edits not yet recorded on the current source.
    """

    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.projectId)
    message = None if payload.messageSource is None else payload.messageSource.model_dump(by_alias=True)
    if agent_chat(request) is not None or message is not None:
        # The Agent's own Continue: while the design loop is enforced it lands only through POST /api/revisions.
        require_design_loop(request, "The Agent continues the working design")
    position, undo = switch_working_design(binding, payload.runId, payload.baseRevisionSha256, payload.branchId,
                                           attribution=request_attribution(request), message_source=message,
                                           raw_language=payload.rawLanguage)
    _after_move(request)
    return switch_dto(position, undo)


@router.post("/working-design/undo", response_model=WorkingDraftDto)
def undo_working_design_route(request: Request, payload: WorkingDesignUndoRequestDto) -> WorkingDraftDto:
    """Put back the Current one Switch replaced, while Current still stands on the run that Switch moved it to.

    Current moved on: ``DESIGN_TREE_UNDO_MOVED`` with ``currentRunId``. Already put back: the position, unchanged.
    """

    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.projectId)
    if agent_chat(request) is not None:
        require_design_loop(request, "The Agent continues the working design")
    position = undo_working_design(binding, HeadEntry(payload.previous.runId, payload.previous.branchId),
                                   payload.continued, attribution=request_attribution(request))
    _after_move(request)
    return position
