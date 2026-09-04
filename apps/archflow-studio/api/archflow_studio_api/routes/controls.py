"""``/api/controls``: the authored control a terminal clarification asks for.

A pending intent that ended in MODEL_VISIBLE_CATALOG_MISSING,
MISSING_ELEMENT_DECLARATION or UNSUPPORTED_ADD_FIELD cannot be answered by
another sentence; it can be answered by declaring the control that is
missing. This route makes that declaration - component, property, the
objects the model shows, the inspection they were read from, a draft row
shape - and holds it in process. Nothing here writes the authored record.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.catalog import catalog_of
from ..application.controls import declare_control
from ..application.projection import project_state
from ..transport.controls import DeclareControlRequestDto, DeclaredControlDto, control_dto
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
    pending = state.pending.get(body.continuation_token)
    if not pending.terminal or pending.target_component_id is None:
        raise StudioError(
            409,
            "CONTROL_NOT_ASKED_FOR",
            f"pending intent {pending.token} ended in {pending.reason_code}, which asks for "
            "an answer, not for a control; declare one only from MODEL_VISIBLE_CATALOG_MISSING, "
            "MISSING_ELEMENT_DECLARATION or UNSUPPORTED_ADD_FIELD.",
        )
    projection = project_state(binding)
    if pending.state_digest != projection.state_digest:
        raise StudioError(
            409,
            "STALE_BASE",
            f"the pending intent was resolved against state {pending.state_digest}, but "
            f"{binding.project_id} is at {projection.state_digest}. Ask again.",
        )
    control = declare_control(
        catalog_of(binding, projection),
        state_digest=projection.state_digest,
        component_id=pending.target_component_id,
        requested_property=pending.requested_property,
        utterance=pending.original_utterance,
        reason_code=pending.reason_code,
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
