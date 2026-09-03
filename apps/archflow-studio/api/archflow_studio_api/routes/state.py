"""``GET /api/state``: the authored State Record, bound and projected."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.projection import project_state
from ..transport.errors import StudioErrorDto
from ..transport.state import StateProjectionDto, to_dto

router = APIRouter(tags=["state"])


@router.get(
    "/state",
    response_model=StateProjectionDto,
    response_model_by_alias=True,
    responses={
        404: {"model": StudioErrorDto},
        # This route takes a query parameter, so FastAPI would otherwise
        # document its own validation shape here; the service answers 422 in
        # the one Studio error shape like everything else.
        422: {"model": StudioErrorDto},
        503: {"model": StudioErrorDto},
    },
    summary="Project the authored State Record against one run",
)
def read_state(
    request: Request,
    run: str | None = Query(
        default=None,
        description=(
            "Answer for this run instead of the one the rule chooses. "
            "The run must exist in the bound project."
        ),
    ),
) -> StateProjectionDto:
    """Ask the kernel; shape the answer. No design question is decided here."""

    return to_dto(project_state(bound_project(request.app.state), run))
