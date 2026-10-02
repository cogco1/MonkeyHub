"""``GET /api/project``: which project, at which exact version, for which run.

This is the **default-project shortcut** for ``GET /api/projects/{project_id}``:
the same answer, for the one project this process binds. Both go through
``binding_answer`` so there is one shaping of the binding and not two that
could disagree.

``POST /api/project/refresh`` reads the project again (ADR-012): nothing
watches an open project, so a change made outside MonkeyHub is read at open
and when someone asks for it here.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ...binding import bound_project, initialize_modeling, resolve_project
from ...application.projection import project_state
from ..dto.project import (
    REFRESH_MOVED_LIMIT,
    ModelingBaseDto,
    ModelingComponentDto,
    ModelingInitializeDto,
    ModelingInitializeRequestDto,
    ModelingLevelDto,
    ProjectBindingDto,
    ProjectRefreshDto,
    ProjectRefreshRequestDto,
)
from .projects import binding_answer

router = APIRouter(tags=["project"])


@router.post("/project/refresh", response_model=ProjectRefreshDto, response_model_by_alias=True)
def refresh_project(request: Request, body: ProjectRefreshRequestDto) -> ProjectRefreshDto:
    """Read the project again now, and say whether its folder changed outside MonkeyHub.

    The runtime knows its own writes as they happen; a change made anywhere
    else - by hand, by a sync, by a restore, by an agent's command - is read at
    open and here. This walks the project's layout once and compares it with
    the last reading. When it moved, the project index projects again what
    moved, and a commit that changed what clients read moves the revision and
    is announced as ``index.committed``, as any commit; the answer comes once
    the index holds the reading. Conditional reads answer from the new
    reading. It writes nothing to the project.
    """

    binding = resolve_project(request.app.state, body.project_id)
    refreshed = binding.refresh()
    return ProjectRefreshDto(project_id=binding.project_id, changed_outside=refreshed.changed,
                             moved=list(refreshed.moved[:REFRESH_MOVED_LIMIT]), moved_count=len(refreshed.moved))


@router.post("/project/modeling", response_model=ModelingBaseDto | ModelingInitializeDto, response_model_by_alias=True)
def prepare_modeling(
    request: Request,
    body: ModelingInitializeRequestDto,
    base: bool = Query(default=False, description=(
        "Also answer the default base this leaves (stateDigest, sourceStageRef, levels, components, "
        "elementCount) as ModelingBaseDto, so a first proposal needs no GET /api/state before it.")),
) -> ModelingBaseDto | ModelingInitializeDto:
    """Prepare an empty project for its first sketch or massing candidate.

    Existing projects keep their model inputs. With ``base=true`` the answer
    also names the default state it leaves, enough to send a first proposal
    without reading the state again; without it, read GET
    /api/construction/model (or /api/state) and /api/state/frame next. Without
    a real source run omit sourceRunId; studio-projection is a transient
    projection identifier, not a retained candidate.
    """

    binding = resolve_project(request.app.state, body.project_id)
    initialized = initialize_modeling(binding)
    if not base:
        return ModelingInitializeDto(project_id=binding.project_id, initialized=initialized)
    projection = project_state(binding, None, require_view=False)
    return ModelingBaseDto(
        project_id=binding.project_id,
        initialized=initialized,
        state_digest=projection.state_digest,
        source_stage_ref=None if projection.source_stage_ref is None else projection.source_stage_ref.uri,
        levels=[ModelingLevelDto(level_id=entity.entity_id, elevation=entity.fields["elevation"])
                for entity in projection.record.entities_of("Level@1")],
        components=None if projection.components is None else [
            ModelingComponentDto(component_id=component.component_id,
                                 parent_component_id=component.parent_component_id)
            for component in projection.components
        ],
        element_count=len(projection.record.entities_of("Element@1")),
    )


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
