"""``POST /api/pick/resolve``: what the object the user clicked actually is."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.pick import resolve_pick
from ..application.projection import project_state
from ..transport.pick import PickRequestDto, PickResolutionDto, to_dto

router = APIRouter(tags=["pick"])


@router.post(
    "/pick/resolve",
    response_model=PickResolutionDto,
    response_model_by_alias=True,
)
def resolve(request: Request, pick: PickRequestDto) -> PickResolutionDto:
    """Resolve the pick against the state this project answers with now.

    The projection is taken the same way ``/api/state`` takes it, so the digest
    a pick is checked against is the digest the client was just given.
    """

    projection = project_state(
        bound_project(request.app.state), run_id=pick.source_run_id
    )
    return to_dto(resolve_pick(projection, pick.to_request()))
