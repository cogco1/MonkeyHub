"""``GET /api/project``: which project, at which exact version, for which run.

This is the **default-project shortcut** for ``GET /api/projects/{project_id}``:
the same answer, for the one project this process binds. Both go through
``binding_answer`` so there is one shaping of the binding and not two that
could disagree.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project, initialize_modeling, resolve_project
from ..transport.project import ProjectBindingDto, ModelingInitializeDto, ModelingInitializeRequestDto
from .projects import binding_answer

router = APIRouter(tags=["project"])


@router.post("/project/modeling", response_model=ModelingInitializeDto, response_model_by_alias=True)
def prepare_modeling(request: Request, body: ModelingInitializeRequestDto) -> ModelingInitializeDto:
    """Prepare an empty project for its first sketch or massing candidate.

    Existing projects keep their model inputs. After this action read GET
    /api/state and /api/state/frame, then use the existing proposal/candidate
    routes. Without a real source run omit sourceRunId; studio-projection is
    a transient projection identifier, not a retained candidate.
    """

    binding = resolve_project(request.app.state, body.project_id)
    return ModelingInitializeDto(project_id=binding.project_id, initialized=initialize_modeling(binding))


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
