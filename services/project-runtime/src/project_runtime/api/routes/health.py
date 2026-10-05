"""The health route: the one answer that must never fail."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ...binding import bound_project
from ...settings import LOCAL_MODE
from ...errors import StudioError
from ..dto.health import StudioHealth

router = APIRouter(tags=["health"])


@router.get(
    "/health", response_model=StudioHealth, response_model_by_alias=True,
    response_model_exclude_none=True,
)
def read_health(request: Request) -> StudioHealth:
    """``projectBound`` is whether the binding opens, not a claim about it."""

    try:
        binding = bound_project(request.app.state)
    except StudioError:
        binding = None
    # The owned local worker reports its already-open binding, without selecting
    # a reference run. Remote health is anonymous and must not expose its path.
    identity = binding if request.app.state.settings.mode == LOCAL_MODE else None
    initializing = binding is None and request.app.state.settings.sync_automatic
    return StudioHealth(
        replica_initializing=True if initializing else None,
        status="ok", project_bound=binding is not None,
        project_id=identity.project_id if identity is not None else request.app.state.settings.sync_project_id if initializing else None,
        project_dir=str(identity.project_dir) if identity is not None else str(request.app.state.settings.project_dir) if initializing else None,
        server_version=getattr(request.app.state, "server_version", None),
        managed_instance_id=getattr(request.app.state, "managed_instance_id", None),
        process_id=getattr(request.app.state, "process_id", None),
        parent_process_id=getattr(request.app.state, "parent_process_id", None),
        source_revision=getattr(request.app.state, "source_revision", None),
    )
