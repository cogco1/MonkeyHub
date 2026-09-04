"""``/api/intents``: the architect's sentence, resolved against the catalog,
read by the grammar and - only where the words need it - by an agent, then
typed by the record.

The route does four things in order. It reads the gestures and the pending
intent the sentence continues (a corrected sentence drops what it rejects).
It resolves the target through the catalog: the element named, the object
picked or circled, the component's editable descendants, an alias, a
direction word with a camera. It reads the action: the grammar first, the
deterministic reading of the words second, the agent third - and whatever
the agent answers is admitted only where the catalog agrees. A command is
handed to the deterministic seam exactly as ``POST /api/proposals`` would;
everything else is a ``BLOCKED_NEEDS_HUMAN`` carrying the pending intent, so
the next sentence continues it instead of starting over.
"""

from __future__ import annotations

from dataclasses import replace
import time
from typing import Any, Mapping

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.catalog import Catalog, catalog_of
from ..application.gestures import read_gestures
from ..application.intent import (
    DeterministicIntentProvider,
    merge_keep,
    parse_utterance,
)
from ..application.intent_agent import (
    DETERMINISTIC,
    Compilation,
    DeterministicCompiler,
    Selection,
    context_refs,
)
from ..application.pending import AMBIGUOUS_TARGET, MISSING_PROPERTY, PendingIntent
from ..application.projection import StateProjection, project_state
from ..application.proposals import proposal_from
from ..application.resolver import (
    CHANGE_EXISTING_VALUE,
    CLARIFY,
    DECLARE_MISSING_CONTROL,
    IntentResult,
    ResolvedTarget,
    affirmed_after_negation,
    alias_components,
    negations_in,
    resolve_action,
    side_in,
    resolve_target,
    validate_agent_result,
)
from ..transport.errors import BlockedNeedsHuman, StudioError
from ..transport.intent import (
    IntentDto,
    IntentRequestDto,
    IntentTimingsDto,
    ResolutionDto,
    agent_dto,
    gesture_from,
)
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
    """Resolve one request against the catalog, then propose it or ask."""

    state = request.app.state
    binding = bound_project(state)
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
    catalog = catalog_of(binding, projection)

    # The intent this sentence continues, if any: its target, slots and
    # rejected candidates come along; the new sentence may reject more.
    pending: PendingIntent | None = None
    if body.continuation_token is not None:
        pending = state.pending.get(body.continuation_token)
        if pending.state_digest != projection.state_digest:
            raise StudioError(
                409,
                "PENDING_INTENT_STALE",
                f"pending intent {pending.token} was resolved against state "
                f"{pending.state_digest}; the project is at {projection.state_digest}. Ask again.",
            )
    aliases = _aliases(binding)
    rejected = tuple(dict.fromkeys((*(pending.rejected_candidates if pending else ()), *negations_in(catalog, body.utterance, aliases))))
    if pending is not None and rejected:
        pending = state.pending.reject(pending, rejected)
    subject_text = affirmed_after_negation(body.utterance)

    # What was drawn, read into the record's names before anyone reads the
    # words: a circle with no pick is the selection; a keep mark is a keep
    # clause. Both are the server's, and both are printed back as facts.
    reading = read_gestures(projection, [gesture_from(dto) for dto in body.gestures])
    camera = body.camera.model_dump() if body.camera is not None else (body.gestures[0].camera.model_dump() if body.gestures else None)
    selection = Selection(
        component_id=body.target_component_id or (pending.target_component_id if pending else None),
        element_id=body.element_id or (pending.target_element_id if pending else None),
        gestures=reading.facts,
    )
    if selection.component_id in rejected:
        selection = replace(selection, component_id=None)
    if selection.element_id in rejected:
        selection = replace(selection, element_id=None)
    side_hint = side_in(body.utterance, camera, _compass(binding))
    if pending is not None:
        # A reply that names something ("柱子") resolves by that name; the
        # pending component was the question's, not the answer's. The side
        # the first sentence gave is kept unless the reply gives one.
        if body.target_component_id is None and body.element_id is None and alias_components(catalog, subject_text, aliases):
            selection = replace(selection, component_id=None, element_id=None)
        if side_hint is None:
            side_hint = pending.slots.get("side")

    resolved_at = time.perf_counter()
    target = resolve_target(
        catalog,
        selection=selection,
        utterance=subject_text,
        picked_object=body.picked_object,
        gesture_target=reading.target,
        camera=camera,
        compass=_compass(binding),
        aliases=aliases,
        rejected=rejected,
        side_hint=side_hint,
    )
    # The words carried over from the sentence that asked: a direction or an
    # amount already given is not asked for twice.
    words = _with_slots(body.utterance, pending)
    result = resolve_action(catalog, utterance=words, target=target, length_unit=_length_unit(binding, projection))

    compilation: Compilation | None = None
    compile_ms = 0
    if (
        result.kind == CLARIFY
        and result.reason_code in (AMBIGUOUS_TARGET, MISSING_PROPERTY)
        and target.status in ("resolved", "candidates")
        and parse_utterance(body.utterance) is None
        and not isinstance(state.intent_compiler, DeterministicCompiler)
    ):
        # Only the words the record cannot read reach the agent, with the
        # catalog on its sheet; its closed answer is admitted where the
        # catalog agrees and downgraded to a question where it does not.
        compiled_at = time.perf_counter()
        compilation = state.intent_compiler.compile(
            message=body.utterance,
            selection=replace(selection, catalog=_sheet_catalog(catalog, target)),
            projection=projection,
        )
        compile_ms = int((time.perf_counter() - compiled_at) * 1000)
        answer = compilation.answer if compilation.answer is not None else _legacy_answer(compilation)
        if answer is not None:
            result = validate_agent_result(catalog, utterance=body.utterance, target=target, answer=answer, length_unit=_length_unit(binding, projection))
        elif compilation.status == "question" and compilation.question:
            result = replace(result, question=compilation.question, why=compilation.why)
        elif compilation.status == "compiled" and compilation.utterance is not None:
            result = replace(result, why=f"{compilation.provider} compiled {compilation.utterance!r}, which is not in the grammar")
    type_ms_start = time.perf_counter()

    if result.kind != CHANGE_EXISTING_VALUE:
        opened = state.pending.open(
            state_digest=projection.state_digest,
            artifact_digest=_artifact_digest(projection),
            utterance=body.utterance,
            target_component_id=result.target.component_id,
            target_element_id=result.target.element_id,
            requested_property=result.requested_property,
            missing_slots=result.missing_slots,
            candidates=tuple(item.element_id for item in result.target.candidates),
            reason_code=result.reason_code or "UNSUPPORTED_ACTION",
            question=result.question or "The request could not be read.",
            rejected_candidates=rejected,
            slots={**result.slots, **({"side": side_hint} if side_hint else {})},
            continue_from=pending,
        )
        raise BlockedNeedsHuman(
            _detail(result, compilation),
            question=opened.question,
            pending={**opened.to_dict(), "candidates": [item.to_dict() for item in result.target.candidates], "gestures": list(reading.facts)},
        )

    assert result.utterance is not None
    sentence = result.utterance
    if reading.keep_refs:
        sentence = merge_keep(sentence, reading.keep_refs)
    final_selection = Selection(
        component_id=result.target.component_id,
        element_id=result.target.element_id,
        gestures=reading.facts,
    )
    if compilation is None:
        compilation = Compilation(
            status="compiled",
            provider=DETERMINISTIC,
            model=None,
            utterance=sentence,
            component_id=final_selection.component_id,
            element_id=final_selection.element_id,
            why="",
            question=None,
            latency_ms=int((time.perf_counter() - resolved_at) * 1000),
            prompt_sha256=None,
            raw=None,
        )
    else:
        compilation = replace(compilation, status="compiled", utterance=sentence, component_id=final_selection.component_id, element_id=final_selection.element_id)
    proposal = proposal_from(
        DeterministicIntentProvider(projection).propose(
            session_ref=f"project:{binding.project_id}",
            message=sentence,
            context_refs=context_refs(body.state_digest, compilation, final_selection),
        )
    )
    proposal = replace(
        proposal,
        compilation_receipt=(
            None if compilation.receipt is None else compilation.receipt.to_dict()
        ),
    )
    state.proposals.put(proposal)
    if pending is not None:
        state.pending.close(pending.token)
    type_ms = int((time.perf_counter() - type_ms_start) * 1000)
    return IntentDto(
        agent=agent_dto(compilation),
        proposal=to_dto(proposal),
        timings=IntentTimingsDto(compile_ms=compile_ms, type_ms=type_ms),
        gestures=list(reading.facts),
        resolution=ResolutionDto(
            kind=result.kind,
            target_source=result.target.source,
            target_detail=result.target.detail,
            capability_id=result.capability_id,
            why=result.why,
        ),
    )


def _detail(result: IntentResult, compilation: Compilation | None) -> str:
    head = {
        CLARIFY: "the request needs an answer before it can be typed",
        DECLARE_MISSING_CONTROL: "the request is about something the catalog cannot edit yet",
    }.get(result.kind, "the request is outside what the record can take")
    parts = [head, f"reason {result.reason_code}"]
    if result.why:
        parts.append(result.why)
    if compilation is not None and compilation.why:
        parts.append(f"{compilation.provider} said: {compilation.why}")
    return " · ".join(parts)


def _with_slots(utterance: str, pending: PendingIntent | None) -> str:
    """The sentence with the amount or direction a previous turn already gave."""

    if pending is None or not pending.slots:
        return utterance
    extra: list[str] = []
    from ..application.resolver import amount_in, direction_in

    if amount_in(utterance) is None and "amount" in pending.slots:
        unit = pending.slots.get("unit")
        extra.append(f"{pending.slots['amount']}{unit or ''}")
    if direction_in(utterance) is None and pending.slots.get("direction"):
        extra.append("increase" if pending.slots["direction"] == "increase" else "decrease")
    return utterance if not extra else f"{utterance} {' '.join(extra)}"


def _sheet_catalog(catalog: Catalog, target: ResolvedTarget) -> dict[str, Any]:
    """What the agent may see of the catalog: components, their editable elements, the gaps."""

    components = []
    for node in catalog.components:
        elements = []
        for element in catalog.editable_descendants(node.component_id):
            if element.component_id != node.component_id:
                continue
            elements.append({
                "elementId": element.element_id,
                "capabilities": [
                    {"capabilityId": cap.capability_id, "key": cap.key, "value": cap.value, "unit": cap.unit}
                    for cap in element.capabilities
                    if cap.status == "editable"
                ],
            })
        gap = None
        if not node.descendant_element_ids:
            gap = "MODEL_VISIBLE_CATALOG_MISSING" if node.unbound_object_count else "MISSING_ELEMENT_DECLARATION"
        components.append({
            "componentId": node.component_id,
            "parentId": node.parent_id,
            "elements": elements,
            "gap": gap,
        })
    return {
        "target": {
            "status": target.status,
            "componentId": target.component_id,
            "elementId": target.element_id,
            "candidates": [item.to_dict() for item in target.candidates],
            "detail": target.detail,
        },
        "components": components,
    }


def _artifact_digest(projection: StateProjection) -> str | None:
    receipt = projection.reference.receipt
    if not isinstance(receipt, Mapping):
        return None
    value = receipt.get("design_state_digest")
    return value if isinstance(value, str) else None


def _length_unit(binding: Any, projection: StateProjection) -> str | None:
    """The unit the reference run's export declares, as the resolver's word for it."""

    from ..application.catalog import length_unit_of

    return length_unit_of(binding, projection)


def _aliases(binding: Any) -> Mapping[str, str] | None:
    """Human names for components, from the project's own conventions when it has them."""

    from ..application.conventions import project_conventions

    return project_conventions(binding).aliases


def _compass(binding: Any) -> Mapping[str, tuple[float, float]] | None:
    from ..application.conventions import project_conventions

    return project_conventions(binding).compass


def _legacy_answer(compilation: Compilation) -> dict[str, Any] | None:
    """A compiler that speaks the grammar directly (the deterministic one, a
    scripted one), read as the closed answer so the same validation applies."""

    if compilation.status != "compiled" or compilation.utterance is None:
        return None
    parsed = parse_utterance(compilation.utterance)
    if parsed is None or compilation.element_id is None:
        return None
    key = parsed.field[len("params.") :] if parsed.field.startswith("params.") else parsed.field
    op = "set" if parsed.operation == "set" else parsed.operation
    return {
        "kind": "command",
        "capabilityId": f"entity:{compilation.element_id}#params.{key}",
        "op": op,
        "value": parsed.number,
        "keep": list(parsed.keep),
        "why": compilation.why,
    }
