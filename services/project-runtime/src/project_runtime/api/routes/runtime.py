"""One read-only view for live workers and retained-result recovery, and the project trash (#575)."""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field
from starlette.requests import Request

from archflow.project.repository import ProjectRepositoryError

from ...application.retention import read_trash, restore_trashed
from ...authentication import request_attribution
from ...binding import bound_project
from ...status import inspect_runtime, worktree_graph
from ...errors import StudioError
from ..dto.rendering import render_job_dto
from ..dto.runtime import (
    ProjectTrashDto, RuntimeDto, TrashRestoreDto, TrashRestoreRequestDto, WorktreeGraphDto, runtime_dto, trash_dto,
    worktree_graph_dto,
)
from .proposals import _require_bound_project

router = APIRouter(tags=["runtime"])


@router.get("/runtime", response_model=RuntimeDto, response_model_by_alias=True)
def read_runtime(
    request: Request, limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    run_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]] = Query(default=[], alias="candidateId", max_length=200),
) -> RuntimeDto:
    return runtime_dto(inspect_runtime(bound_project(request.app.state), jobs=request.app.state.jobs,
                                       limit=limit, offset=offset, run_ids=tuple(run_ids)))


@router.get("/worktrees", response_model=WorktreeGraphDto, response_model_by_alias=True)
def read_worktrees(request: Request) -> WorktreeGraphDto:
    """Read-only: the Working Head, running work, other lines and whether they reconcile."""
    binding = bound_project(request.app.state)
    try:
        renders, unread = [render_job_dto(job) for job in request.app.state.render_jobs.list(binding)], None
    except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError) as exc:
        # One unreadable render record must not hide the rest of the project's work.
        renders, unread = None, f"Render results could not be read: {getattr(exc, 'detail', exc)}"
    graph = worktree_graph(binding, jobs=request.app.state.jobs, render_jobs=renders, indexed=True)
    if unread is not None:
        graph = replace(graph, warnings=(*graph.warnings, unread))
    return worktree_graph_dto(graph)


@router.get("/trash", response_model=ProjectTrashDto, response_model_by_alias=True)
def read_project_trash(request: Request) -> ProjectTrashDto:
    """The project trash (#575): superseded drafts and failed attempts moved out whole, restorable until purged."""
    return trash_dto(read_trash(bound_project(request.app.state)))


@router.post("/trash/restore", response_model=TrashRestoreDto, response_model_by_alias=True)
def restore_from_trash(request: Request, payload: TrashRestoreRequestDto) -> TrashRestoreDto:
    """Bring one trashed run back exactly as it left, with any trashed run it was made from; it is never cleaned again."""
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    attribution = request_attribution(request)
    restored = restore_trashed(binding, payload.run_id, now=datetime.now(timezone.utc), actor_id=attribution.actor_id,
                               authenticated=attribution.authenticated, origin=attribution.origin)
    return TrashRestoreDto(project_id=binding.project_id, restored=[entry.run_id for entry in restored],
                           trash=trash_dto(read_trash(binding)))
