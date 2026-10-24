"""The Working Head's history: the moves it made onto runs, read for the history page (#300).

Moving it stays ``PUT /api/working-draft``; these routes only read.
"""

from fastapi import APIRouter, Query
from starlette.requests import Request

from ...application.working_draft import continue_moves
from ...binding import bound_project
from ..dto.working_draft import ContinueMovesDto, continue_moves_dto

router = APIRouter(tags=["working-design"])


@router.get("/working-design/moves", response_model=ContinueMovesDto)
def read_working_design_moves(request: Request, limit: int = Query(50, ge=1, le=500)) -> ContinueMovesDto:
    """Each move of the Working Head onto a run, newest first, as its design.continued event retained it."""

    binding = bound_project(request.app.state)
    return continue_moves_dto(binding.project_id, continue_moves(binding, limit=limit, indexed=True))
