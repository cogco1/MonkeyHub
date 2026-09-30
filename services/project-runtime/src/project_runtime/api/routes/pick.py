"""``POST /api/pick/resolve``: what the object the user clicked actually is."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ...binding import bound_project
from ...application.pick import resolve_pick
from ...application.projection import project_state
from ..dto.pick import PickRequestDto, PickResolutionDto, to_dto
from .proposals import _stale_base

router = APIRouter(tags=["pick"])


@router.post(
    "/pick/resolve",
    response_model=PickResolutionDto,
    response_model_by_alias=True,
)
def resolve(request: Request, pick: PickRequestDto) -> PickResolutionDto:
    """Resolve the pick against the state this project answers with now.

    The projection is taken the same way ``/api/state`` takes it, so the digest
    a pick is checked against is the digest the client was just given. A pick
    made on another state is refused naming the run that holds it (#404 F5).
    """

    binding = bound_project(request.app.state)
    projection = project_state(binding, run_id=pick.source_run_id)
    if pick.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, pick.state_digest, "the pick", pick.source_run_id)
    return to_dto(resolve_pick(projection, pick.to_request()))
