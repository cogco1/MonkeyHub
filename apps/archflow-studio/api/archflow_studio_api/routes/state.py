"""``GET /api/state``: the authored State Record, bound and projected."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.projection import project_state
from ..transport.state import StateProjectionDto, to_dto

router = APIRouter(tags=["state"])


@router.get(
    "/state",
    response_model=StateProjectionDto,
    response_model_by_alias=True,
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
    """Ask the kernel; shape the answer. No design question is decided here.

    This is the one route served a record whose developed-design view the
    kernel refused: what such a record *declares* is still its own answer, and
    the refusal travels as ``componentTreeError`` and an honesty line rather
    than as a blank screen. Every route that would have to stand on that view
    refuses instead.
    """

    return to_dto(
        project_state(bound_project(request.app.state), run, require_view=False)
    )
