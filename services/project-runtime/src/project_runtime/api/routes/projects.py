"""``/api/projects``: the general, project-scoped form of the boundary.

Every resource in the protocol is addressed under a project. The general form
is ``/api/projects/{project_id}/…`` and the unscoped paths this API has served
since round 1 — ``/api/state``, ``/api/pick/resolve``, ``/api/proposals`` — are
the **default-project shortcut** for it: they mean the one project this process
binds. The shortcut is not deprecated and not a second API; it is the same
resources with the project segment left off.

Only the two listing routes are scoped today, because duplicating a dozen
routes under a prefix that resolves to the same binding would be a second copy
of the API to keep honest. What matters now is that the project segment has one
resolver — ``resolve_project`` — so a server that binds several projects fills
that in, mounts the scoped prefix, and no client changes.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.datastructures import State
from starlette.requests import Request

from ..application.binding import ProjectBinding, bound_project, resolve_project
from ..transport.project import (
    ProjectBindingDto,
    ProjectListDto,
    project_binding_dto,
    project_list_dto,
)

router = APIRouter(tags=["projects"])


def binding_answer(state: State, binding: ProjectBinding) -> ProjectBindingDto:
    """The binding summary, shaped once for both the general form and the
    shortcut, so ``/api/project`` can never drift from
    ``/api/projects/{id}``."""

    return project_binding_dto(
        binding,
        binding.reference_run(),
        binding.head(),
        intent_compiler=state.intent_compiler,
    )


@router.get(
    "/projects",
    response_model=ProjectListDto,
    response_model_by_alias=True,
)
def read_projects(request: Request) -> ProjectListDto:
    """Which projects this server binds, and which one the shortcut means."""

    return project_list_dto(bound_project(request.app.state))


@router.get(
    "/projects/{project_id}",
    response_model=ProjectBindingDto,
    response_model_by_alias=True,
)
def read_project_by_id(request: Request, project_id: str) -> ProjectBindingDto:
    """One named project's binding: the same answer ``GET /api/project`` gives."""

    return binding_answer(
        request.app.state, resolve_project(request.app.state, project_id)
    )
