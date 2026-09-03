"""The health route: the first thing a client asks and the last thing to lie."""

from __future__ import annotations

from fastapi import APIRouter

from ..transport.errors import StudioErrorDto
from ..transport.health import StudioHealth

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=StudioHealth,
    response_model_by_alias=True,
    responses={503: {"model": StudioErrorDto}},
    summary="Report service posture and whether a project is bound",
)
def read_health() -> StudioHealth:
    """Answer without touching the project: binding is not this route's job."""

    return StudioHealth(status="ok", project_bound=False)
