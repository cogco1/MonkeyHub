"""The health route: the one answer that must never fail."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..transport.errors import StudioError
from ..transport.health import StudioHealth

router = APIRouter(tags=["health"])


@router.get(
    "/health", response_model=StudioHealth, response_model_by_alias=True,
    response_model_exclude_none=True,
)
def read_health(request: Request) -> StudioHealth:
    """``projectBound`` is whether the binding opens, not a claim about it."""

    try:
        bound_project(request.app.state)
    except StudioError:
        project_bound = False
    else:
        project_bound = True
    return StudioHealth(
        status="ok", project_bound=project_bound,
        server_version=getattr(request.app.state, "server_version", None),
        managed_instance_id=getattr(request.app.state, "managed_instance_id", None),
        process_id=getattr(request.app.state, "process_id", None),
        parent_process_id=getattr(request.app.state, "parent_process_id", None),
        source_revision=getattr(request.app.state, "source_revision", None),
    )
