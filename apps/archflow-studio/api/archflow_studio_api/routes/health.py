"""The health route: the one answer that must never fail."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..transport.errors import StudioError
from ..transport.health import StudioHealth

router = APIRouter(tags=["health"])


@router.get("/health", response_model=StudioHealth, response_model_by_alias=True)
def read_health(request: Request) -> StudioHealth:
    """``projectBound`` is whether the binding opens, not a claim about it."""

    try:
        bound_project(request.app.state)
    except StudioError:
        return StudioHealth(status="ok", project_bound=False)
    return StudioHealth(status="ok", project_bound=True)
