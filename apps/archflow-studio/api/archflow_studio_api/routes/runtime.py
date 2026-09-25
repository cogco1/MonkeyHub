"""One read-only view for live workers and retained-result recovery."""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field
from starlette.requests import Request

from archflow.project.repository import ProjectRepositoryError

from ..application.binding import bound_project
from ..application.runtime import inspect_runtime, worktree_graph
from ..transport.errors import StudioError
from ..transport.runtime import RuntimeDto, WorktreeGraphDto, runtime_dto, worktree_graph_dto

router = APIRouter(tags=["runtime"])


@router.get("/runtime", response_model=RuntimeDto, response_model_by_alias=True)
def read_runtime(
    request: Request, limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    candidate_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]] = Query(default=[], alias="candidateId", max_length=200),
) -> RuntimeDto:
    return runtime_dto(inspect_runtime(bound_project(request.app.state), jobs=request.app.state.jobs,
                                       limit=limit, offset=offset, candidate_ids=tuple(candidate_ids)))


@router.get("/worktrees", response_model=WorktreeGraphDto, response_model_by_alias=True)
def read_worktrees(request: Request) -> WorktreeGraphDto:
    """Read-only: the Working Head, running work, other lines and whether they reconcile."""
    binding = bound_project(request.app.state)
    try:
        renders, unread = request.app.state.render_jobs.list(binding), None
    except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError) as exc:
        # One unreadable render record must not hide the rest of the project's work.
        renders, unread = None, f"Render results could not be read: {getattr(exc, 'detail', exc)}"
    graph = worktree_graph(binding, jobs=request.app.state.jobs, render_jobs=renders)
    if unread is not None:
        graph = replace(graph, warnings=(*graph.warnings, unread))
    return worktree_graph_dto(graph)
