"""``GET /api/project``: which project, at which exact version, for which run."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..transport.project import ProjectBindingDto, project_binding_dto

router = APIRouter(tags=["project"])


@router.get(
    "/project",
    response_model=ProjectBindingDto,
    response_model_by_alias=True,
)
def read_project(request: Request) -> ProjectBindingDto:
    """Bind on first use and state the binding; compute nothing about design."""

    binding = bound_project(request.app.state)
    return project_binding_dto(
        binding,
        binding.reference_run(),
        binding.head(),
        intent_compiler=request.app.state.intent_compiler,
    )
