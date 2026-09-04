"""``/api/intents``: the architect's sentence, resolved against the record,
compiled by an agent, typed by the grammar.

The route does four things in order and nothing else. It resumes the pending
intent the request's ``continuationToken`` names, if there is one — that token
is the *whole* of the continuity, and no chat transcript is sent or read. It
asks the resolver what the request is about, which settles the target from the
component tree and an explicit choice rather than from the nearest similar
string. It hands the *resolved* target to the process's intent compiler and
insists the answer is a sentence in the grammar. And it hands that sentence to
the deterministic seam exactly as ``POST /api/proposals`` would.

Four answers and no fifth. ``COMPILED`` is the 201 below; the other three are
refusals carrying the pending intent they belong to, so the next request
continues the same exchange instead of starting a new one that has forgotten
everything. The rule that ends the loop lives in the resolver: a round that
narrows nothing terminates rather than asking again.
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from starlette.requests import Request

from dataclasses import replace

from ..application import clarification
from ..application.binding import bound_project
from ..application.catalog import catalog_of
from ..application.conventions import project_conventions
from ..application.clarification import PendingIntentStore, Resolution
from ..application.gestures import read_gestures
from ..application.intent import (
    DeterministicIntentProvider,
    merge_keep,
    parse_utterance,
)
from ..application.intent_agent import (
    DeterministicCompiler,
    IntentCompiler,
    Selection,
    context_refs,
)
from ..application.projection import project_state
from ..application.proposals import proposal_from
from ..transport.errors import (
    BlockedNeedsHuman,
    MissingEditableControl,
    StudioError,
    UnsupportedRequest,
)
from ..transport.intent import (
    IntentBlockedDto,
    IntentDto,
    IntentRequestDto,
    IntentTimingsDto,
    agent_dto,
    draft_body,
    gesture_from,
    pending_body,
    pending_dto,
)
from ..transport.proposal import ProposalScopeDto, to_dto
from .proposals import _require_bound_project

router = APIRouter(tags=["intents"])


def _refused(
    store: PendingIntentStore, *, token: str | None, resolution: Resolution
) -> StudioError:
    """One of the three refusing outcomes, as the error the route raises.

    The old token is closed whatever happens: a continuation is used once, so a
    reply cannot be replayed against a round that has already moved on. A
    non-terminal answer opens a new one; a terminal answer opens none, and its
    ``continuationToken`` is ``null`` — which is how the client knows to stop
    offering an input box.
    """

    store.close(token)
    if not resolution.terminal:
        store.open(resolution.pending)
    pending = pending_body(resolution.pending)
    if resolution.outcome == clarification.MISSING_EDITABLE_CONTROL:
        return MissingEditableControl(
            resolution.detail,
            pending=pending,
            draft=None if resolution.draft is None else draft_body(resolution.draft),
        )
    if resolution.outcome == clarification.UNSUPPORTED:
        return UnsupportedRequest(resolution.detail, pending=pending)
    return BlockedNeedsHuman(
        resolution.detail,
        question=resolution.question or "",
        accepted_forms=resolution.accepted_forms,
        pending=pending,
    )


@router.post(
    "/intents",
    response_model=IntentDto,
    response_model_by_alias=True,
    status_code=201,
    responses={422: {"model": IntentBlockedDto}},
)
def compile_intent(request: Request, body: IntentRequestDto) -> IntentDto:
    """Compile one request against the resolved target, then propose it."""

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
    store: PendingIntentStore = request.app.state.pending_intents
    # A pending intent is bound to a stateDigest. One opened against a state
    # the project has left is void and is never applied to the state that
    # answers now — the client is told so rather than being answered about a
    # record it was not looking at.
    pending = (
        None
        if body.continuation_token is None
        else store.resume(body.continuation_token, projection.state_digest)
    )
    # What was drawn, read into the record's names before anyone reads the
    # words: a circle with no pick is the selection, a keep mark is a keep
    # clause. Both are the server's, and both are printed back as facts.
    reading = read_gestures(projection, [gesture_from(dto) for dto in body.gestures])
    selection = Selection(
        component_id=body.target_component_id,
        element_id=body.element_id,
        gestures=reading.facts,
    )
    # Three of the four outcomes are reached here, without a model: an action
    # no grammar expresses, a target the record cannot resolve, and a component
    # that has no editable control. The last of those is why the agent is not
    # asked at all in that case — an agent shown the whole record sheet would
    # offer the nearest element whose field happens to share a name.
    # The project's conventions (PROJECT.md: names for people, the compass) and
    # the request's camera let a viewer word become a side; the catalog is the
    # one directory of editable elements and of what the model shows without a row.
    conventions = project_conventions(binding)
    conventions_are_current = conventions.state_digest == projection.state_digest
    camera = (
        body.camera.model_dump()
        if body.camera is not None
        else body.gestures[0].camera.model_dump()
        if body.gestures
        else None
    )
    catalog = catalog_of(binding, projection) if getattr(projection, "state", None) is not None else None
    resolution = clarification.resolve(
        projection,
        utterance=body.utterance,
        selection=selection,
        picked=reading.target,
        has_camera=bool(body.gestures) or camera is not None,
        pending=pending,
        camera=camera,
        compass=conventions.compass if conventions_are_current else None,
        aliases=conventions.aliases if conventions_are_current else {},
        catalog=catalog,
        scope=body.scope,
    )
    if resolution.outcome != clarification.COMPILED:
        raise _refused(store, token=body.continuation_token, resolution=resolution)
    assert resolution.selection is not None
    # A sentence already in the grammar is not read by the agent: the grammar
    # is the truth about it, and the architect who typed an exact sentence gets
    # the same answer, in the same time, as before there was an agent at all.
    # An absolute delta against the one field the request resolved to ("提高 0.1m")
    # is read here, deterministically, into the grammar; a qualitative word is not.
    message = body.utterance
    delta_sentence = clarification.grammar_sentence_for(body.utterance, resolution=resolution, projection=projection)
    if delta_sentence is None and pending is not None:
        # A reply that settles a slot restates nothing. "整个叠层" answers how
        # far and says no number: the change this exchange is about is still
        # the sentence that opened it, which is the whole reason the pending
        # intent keeps ``originalUtterance``. The reading is the same
        # deterministic one — a number, a unit and a direction word, or nothing.
        delta_sentence = clarification.grammar_sentence_for(
            resolution.pending.original_utterance,
            resolution=resolution,
            projection=projection,
        )
    if delta_sentence is not None:
        message = delta_sentence
    compiler: IntentCompiler = (
        DeterministicCompiler()
        if parse_utterance(message) is not None
        else request.app.state.intent_compiler
    )
    compiled_at = time.perf_counter()
    compilation = compiler.compile(
        message=message,
        selection=resolution.selection,
        projection=projection,
    )
    compile_ms = int((time.perf_counter() - compiled_at) * 1000)
    # What the compiler answered, in the same four outcomes. An agent that asks
    # still names a target, and that target is kept: losing it is what made the
    # next round start from nothing.
    resolution = clarification.read_compilation(
        projection, compilation=compilation, resolution=resolution, pending=pending
    )
    if resolution.outcome != clarification.COMPILED:
        raise _refused(store, token=body.continuation_token, resolution=resolution)
    assert compilation.utterance is not None
    if reading.keep_refs:
        compilation = replace(
            compilation,
            utterance=merge_keep(compilation.utterance, reading.keep_refs),
        )
    typed_at = time.perf_counter()
    try:
        parts = DeterministicIntentProvider(projection).propose(
            session_ref=f"project:{binding.project_id}",
            message=compilation.utterance,
            context_refs=context_refs(
                body.state_digest, compilation, resolution.selection
            ),
        )
    except BlockedNeedsHuman as exc:
        # The grammar refuses for reasons only it knows — a locked parameter, a
        # unit it will not convert, an element of another component. Its
        # sentence is the record's and travels verbatim; what it was missing
        # was the exchange it belongs to and whether that is still advancing.
        raise _refused(
            store,
            token=body.continuation_token,
            resolution=clarification.blocked(
                exc.detail,
                question=exc.question,
                resolution=resolution,
                pending=pending,
                accepted_forms=exc.accepted_forms,
            ),
        ) from exc
    proposal = proposal_from(parts)
    # What compiled the words travels with what they became. A sentence
    # already in the grammar was read by no model and carries no receipt.
    # The pending intent travels too: it is what was actually asked — the
    # request id, the slots the resolver filled — and a judgement made about
    # this proposal later has no other way of knowing it.
    proposal = replace(
        proposal,
        compilation_receipt=(
            None if compilation.receipt is None else compilation.receipt.to_dict()
        ),
        pending=resolution.pending,
    )
    request.app.state.proposals.put(proposal)
    type_ms = int((time.perf_counter() - typed_at) * 1000)
    # The exchange is over: the token that got here is spent, and the pending
    # intent that comes back carries a null one.
    store.close(body.continuation_token)
    # How far the exchange settled on, travelling with what it became. The
    # operator moved one scalar whatever the scope says; the ids are what the
    # client shows revalidated, so the closure is not a surprise afterwards.
    settled = clarification.scope_of(resolution.pending)
    return IntentDto(
        outcome=clarification.COMPILED,
        agent=agent_dto(compilation),
        proposal=to_dto(
            proposal,
            scope=(
                None
                if settled is None
                else ProposalScopeDto(scope=settled[0], element_ids=list(settled[1]))
            ),
        ),
        timings=IntentTimingsDto(compile_ms=compile_ms, type_ms=type_ms),
        gestures=list(reading.facts),
        pending_intent=pending_dto(resolution.pending),
    )
