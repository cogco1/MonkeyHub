"""``/api/intents``: the architect's sentence, compiled by an agent, typed by
the grammar.

The route does three things in order and nothing else: it asks the process's
intent compiler what the request means against the record sheet; it insists
the answer is a sentence in the grammar (or turns the agent's question into
the same ``BLOCKED_NEEDS_HUMAN`` a human would get); and it hands that
sentence to the deterministic seam exactly as ``POST /api/proposals`` would.
The proposal that comes back is the record's — the agent chose the words, the
grammar and the record chose the number's meaning and the closure.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.intent import DeterministicIntentProvider, parse_utterance
from ..application.intent_agent import (
    DeterministicCompiler,
    IntentCompiler,
    Selection,
    context_refs,
    require_grammatical,
)
from ..application.projection import project_state
from ..application.proposals import proposal_from
from ..transport.errors import StudioError
from ..transport.intent import IntentDto, IntentRequestDto, agent_dto
from ..transport.proposal import to_dto
from .proposals import _require_bound_project

router = APIRouter(tags=["intents"])


@router.post(
    "/intents",
    response_model=IntentDto,
    response_model_by_alias=True,
    status_code=201,
)
def compile_intent(request: Request, body: IntentRequestDto) -> IntentDto:
    """Compile one request against the current selection, then propose it."""

    binding = bound_project(request.app.state)
    _require_bound_project(binding, body.project_id)
    projection = project_state(binding)
    if body.state_digest != projection.state_digest:
        raise StudioError(
            409,
            "STALE_BASE",
            f"the request names state {body.state_digest}, but "
            f"{binding.project_id} is at {projection.state_digest}. Read "
            "/api/state again and ask against the state that answers now.",
        )
    # A sentence already in the grammar is not read by the agent: the grammar
    # is the truth about it, the agent could only re-target it, and the
    # architect who typed an exact sentence gets the same answer, in the same
    # time, as before there was an agent at all.
    compiler: IntentCompiler = (
        DeterministicCompiler()
        if parse_utterance(body.utterance) is not None
        else request.app.state.intent_compiler
    )
    selection = Selection(
        component_id=body.target_component_id, element_id=body.element_id
    )
    compilation = compiler.compile(
        message=body.utterance, selection=selection, projection=projection
    )
    require_grammatical(compilation)
    assert compilation.utterance is not None
    proposal = proposal_from(
        DeterministicIntentProvider(projection).propose(
            session_ref=f"project:{binding.project_id}",
            message=compilation.utterance,
            context_refs=context_refs(body.state_digest, compilation, selection),
        )
    )
    request.app.state.proposals.put(proposal)
    return IntentDto(agent=agent_dto(compilation), proposal=to_dto(proposal))
