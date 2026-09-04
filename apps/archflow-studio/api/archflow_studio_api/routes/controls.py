"""``/api/controls``: keep the authored control a terminal clarification asked for.

MISSING_EDITABLE_CONTROL comes back with an authored-control draft and a null
continuation token: no sentence answers it. Confirming the draft here keeps it
as a declared control - component, property, provenance, what the catalog
showed - in process, against the state it was drafted for. Nothing here writes
the authored record.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.catalog import catalog_of
from ..application.controls import declare_control
from ..application.projection import project_state
from ..transport.controls import DeclareControlRequestDto, DeclaredControlDto, control_dto, draft_from_dto
from ..transport.errors import StudioError
from .proposals import _require_bound_project

router = APIRouter(tags=["controls"])


@router.post(
    "/controls",
    response_model=DeclaredControlDto,
    response_model_by_alias=True,
    status_code=201,
)
def declare(request: Request, body: DeclareControlRequestDto) -> DeclaredControlDto:
    state = request.app.state
    binding = bound_project(state)
    _require_bound_project(binding, body.project_id)
    projection = project_state(binding)
    if body.state_digest != projection.state_digest:
        raise StudioError(
            409,
            "STALE_BASE",
            f"the draft was made against state {body.state_digest}, but {binding.project_id} is at "
            f"{projection.state_digest}. Ask again against the state that answers now.",
        )
    draft = draft_from_dto(body.draft)
    declared = {getattr(node, "component_id", None) for node in (getattr(projection, "components", None) or ())} | {e.component_id for e in projection.elements}
    editable: tuple[str, ...] = ()
    if getattr(projection, "state", None) is not None:
        catalog = catalog_of(binding, projection)
        editable = tuple(e.element_id for e in catalog.editable_descendants(draft.target_component_id))
        declared |= {c.component_id for c in catalog.components}
    control = declare_control(
        draft=draft,
        state_digest=projection.state_digest,
        utterance=body.utterance,
        declared_components=declared,
        editable_element_ids=editable,
    )
    state.controls.put(control)
    return control_dto(control)


@router.get(
    "/controls/{control_id}",
    response_model=DeclaredControlDto,
    response_model_by_alias=True,
)
def read_control(request: Request, control_id: str) -> DeclaredControlDto:
    return control_dto(request.app.state.controls.get(control_id))
