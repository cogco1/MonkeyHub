"""``GET /api/project``: which project, at which exact version, for which run.

This is the **default-project shortcut** for ``GET /api/projects/{project_id}``:
the same answer, for the one project this process binds. Both go through
``binding_answer`` so there is one shaping of the binding and not two that
could disagree.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..transport.project import ProjectBindingDto
from .projects import binding_answer

router = APIRouter(tags=["project"])


@router.get(
    "/project",
    response_model=ProjectBindingDto,
    response_model_by_alias=True,
)
def read_project(request: Request) -> ProjectBindingDto:
    """Bind on first use and state the binding; compute nothing about design."""

    return binding_answer(
        request.app.state, bound_project(request.app.state)
    )
