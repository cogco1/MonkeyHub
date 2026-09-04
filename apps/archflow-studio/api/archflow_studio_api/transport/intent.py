"""The wire form of ``POST /api/intents``: an architect's sentence in, the
agent's compiled sentence and the typed proposal it became out.

The proposal half is the same ``ProposalDto`` that ``POST /api/proposals``
answers with — it *is* a proposal made by the deterministic seam. The agent
half is kept apart from it on purpose: ``agent`` says who read the request,
what it compiled and why, so nothing the model said can be mistaken for
something the record answered.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..application.clarification import (
    AuthoredControlDraft,
    CandidateOption,
    PendingIntent,
)
from ..application.gestures import Gesture, GestureHit
from ..application.intent_agent import Compilation
from .proposal import STATE_DIGEST_PATTERN, ProposalDto

Vector3 = tuple[float, float, float]


class GestureHitDto(BaseModel):
    """One object a stroke sample fell on, exactly as the viewer read it.

    The client derives nothing: the user strings and the object name are the
    file's, the world point is where the ray met the mesh. The server resolves
    them through the same pick resolver a click goes through.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    object_name: str | None = Field(alias="objectName", default=None)
    user_strings: dict[str, str] = Field(alias="userStrings", default_factory=dict)
    world: Vector3


class CameraDto(BaseModel):
    """Where the architect stood when they drew: the viewpoint is part of the intent."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    position: Vector3
    target: Vector3
    up: Vector3
    fov: float


class GestureDto(BaseModel):
    """One stroke on the model: circle / arrow / keep / remove.

    ``screen`` is the stroke in canvas pixels, ``camera`` the view it was
    drawn in, ``hits`` the objects under its samples. For an arrow the world
    start/end/direction and its length in model units are the client's
    geometry of the stroke on the model; the server names what it points at.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    kind: Literal["circle", "arrow", "keep", "remove"]
    screen: list[tuple[float, float]] = Field(min_length=1)
    camera: CameraDto
    hits: list[GestureHitDto] = Field(default_factory=list)
    world_start: Vector3 | None = Field(alias="worldStart", default=None)
    world_end: Vector3 | None = Field(alias="worldEnd", default=None)
    world_direction: Vector3 | None = Field(alias="worldDirection", default=None)
    length_model_units: float | None = Field(alias="lengthModelUnits", default=None)


def gesture_from(dto: GestureDto) -> Gesture:
    return Gesture(
        kind=dto.kind,
        hits=tuple(
            GestureHit(
                object_name=hit.object_name,
                user_strings=dict(hit.user_strings),
                world=hit.world,
            )
            for hit in dto.hits
        ),
        world_direction=dto.world_direction,
        length_model_units=dto.length_model_units,
    )


class IntentRequestDto(BaseModel):
    """One request in the architect's words, against the current selection."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; a request "
        "against any other state is refused as STALE_BASE",
    )
    utterance: str = Field(
        min_length=1,
        description="what the architect said, in any words; the agent compiles "
        "it into the grammar or asks",
    )
    target_component_id: str | None = Field(
        alias="targetComponentId",
        default=None,
        min_length=1,
        description="the current selection's component, when there is one; the "
        "agent may keep it or name another the record declares",
    )
    element_id: str | None = Field(
        alias="elementId",
        default=None,
        min_length=1,
        description="the current selection's element, when one was picked",
    )
    project_id: str | None = Field(
        alias="projectId",
        default=None,
        min_length=1,
        description="the project the client believes it is proposing against; "
        "a different one is refused as PROJECT_MISMATCH",
    )
    gestures: list[GestureDto] = Field(
        default_factory=list,
        description="what the architect drew on the model with the words: "
        "circles, arrows, keep and remove marks, with the objects under them; "
        "the server resolves them and reads them beside the sentence",
    )
    continuation_token: str | None = Field(
        alias="continuationToken",
        default=None,
        min_length=1,
        description="the token the server's last clarification answered with, "
        "when this request continues that exchange. It is the whole of the "
        "continuity: the pending intent it names carries the original "
        "utterance, the target resolved so far and what has been rejected, so "
        "no transcript is sent and none is read",
    )


class AgentReadingDto(BaseModel):
    """What the agent said and how it was obtained — the agent's, not the record's."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    provider: str = Field(description="deterministic, codex or anthropic")
    model: str | None = Field(description="the model the provider ran, when it names one")
    compiled_utterance: str = Field(
        alias="compiledUtterance",
        description="the sentence in the grammar the agent produced; for the "
        "deterministic provider it is the request itself",
    )
    why: str = Field(
        description="the agent's own reading of the request, verbatim; empty "
        "for the deterministic provider",
    )
    latency_ms: int = Field(alias="latencyMs")
    prompt_sha256: str | None = Field(
        alias="promptSha256",
        description="digest of the exact prompt the agent was shown; null for "
        "the deterministic provider",
    )
    receipt_id: str | None = Field(
        alias="receiptId",
        default=None,
        description="the ModelInvocationReceipt@2 that signs the call to the "
        "model; null for the deterministic provider, which calls none",
    )
    status: str | None = Field(
        default=None,
        description="how that call ended on the shared contract (success); "
        "null when no model was called",
    )


class IntentTimingsDto(BaseModel):
    """How long the two halves of an intent took, in this process.

    ``compileMs`` is the agent (zero for the deterministic pass-through);
    ``typeMs`` is the grammar, the record and the closure. The fast stage of
    a change is the second number; the first is what an agent costs.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    compile_ms: int = Field(alias="compileMs")
    type_ms: int = Field(alias="typeMs")


Outcome = Literal[
    "COMPILED", "NEEDS_CLARIFICATION", "MISSING_EDITABLE_CONTROL", "UNSUPPORTED"
]
ActionKind = Literal[
    "change_existing_value", "declare_missing_control", "clarify", "unsupported"
]


class CandidateOptionDto(BaseModel):
    """One thing the architect could have meant, as the record has it now.

    A choice a person can make: what it is, what its number is, and where it
    sits. A list of bare identifiers would be the studio asking somebody else to
    do its resolution.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    ref: str = Field(description="how this candidate is written in rejectedCandidates")
    component_id: str = Field(alias="componentId")
    element_id: str | None = Field(alias="elementId")
    key: str | None = Field(description="the numeric field this option would change")
    current_value: float | int | None = Field(
        alias="currentValue", description="what the record holds for it now"
    )
    unit: str | None
    orientation: str | None = Field(
        description="the compass identity the record's own name carries, or null",
    )
    label: str = Field(description="the option as a person reads it")


class PendingIntentDto(BaseModel):
    """The one short-term structure a clarification chain is carried in.

    It is server-side state keyed by ``continuationToken`` and bound to one
    ``stateDigest``. A client sends the token back and nothing else: no
    transcript, no re-derived selection. A ``continuationToken`` of ``null``
    means the exchange is over — the answer is terminal and there is nothing
    left to ask.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    request_id: str = Field(alias="requestId")
    state_digest: str = Field(alias="stateDigest")
    original_utterance: str = Field(
        alias="originalUtterance",
        description="what the architect said when the exchange began; every "
        "later reply is read against it",
    )
    action_kind: ActionKind = Field(alias="actionKind")
    target_component_id: str | None = Field(alias="targetComponentId")
    element_id: str | None = Field(alias="elementId")
    requested_semantic_property: str | None = Field(
        alias="requestedSemanticProperty",
        description="the quality the request is about — height, thickness — "
        "read from the words, not from a field name",
    )
    known_slots: dict[str, str] = Field(alias="knownSlots")
    missing_slots: list[str] = Field(
        alias="missingSlots",
        description="what is still open: target, property, value, orientation",
    )
    candidates: list[CandidateOptionDto]
    rejected_candidates: list[str] = Field(
        alias="rejectedCandidates",
        description="what the architect has refused; authoritative for the "
        "life of this pending intent and never offered again inside it",
    )
    reason_code: str = Field(alias="reasonCode")
    continuation_token: str | None = Field(
        alias="continuationToken",
        description="send this back to continue; null means terminal",
    )
    turn: int = Field(description="which round of this exchange this answer is")


class AuthoredControlDraftDto(BaseModel):
    """A control somebody would have to author, and where it was read from.

    Never written. It is returned so a person can see what the system is
    missing and decide; a later confirm step would turn it into an edit of the
    authored record, and nothing in this round does.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    target_component_id: str = Field(alias="targetComponentId")
    suggested_element_id: str = Field(alias="suggestedElementId")
    semantic_property: str | None = Field(alias="semanticProperty")
    producer: str | None = Field(
        description="the producer the elements around it use, when they agree",
    )
    binding: str | None = Field(
        description="where its base reference would come from",
    )
    unit: str | None = Field(
        description="element params are bare numbers in the record; this seam "
        "converts nothing and states no unit the record does not",
    )
    provenance: list[str] = Field(
        description="exactly which elements and producers this was read from",
    )
    confidence: str = Field(description="how much those readings agreed: high, medium, low")
    dependency_requirements: list[str] = Field(alias="dependencyRequirements")
    suggested_action: str = Field(alias="suggestedAction")


class IntentBlockedDto(BaseModel):
    """The body every refusing outcome of ``POST /api/intents`` answers with.

    The same ``{code, detail}`` every failure has, plus the pending intent it
    belongs to. It is declared here so a client reads the shape from the
    server's own schema rather than hand-writing a mirror of it.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str
    detail: str
    outcome: Outcome
    pending_intent: PendingIntentDto | None = Field(alias="pendingIntent", default=None)
    authored_control_draft: AuthoredControlDraftDto | None = Field(
        alias="authoredControlDraft", default=None
    )
    question: str | None = None
    accepted_forms: list[str] | None = Field(alias="acceptedForms", default=None)


class IntentDto(BaseModel):
    """The wire form of ``POST /api/intents``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    outcome: Outcome = Field(
        description="which of the four closed answers this is; a 201 is always "
        "COMPILED, and the other three arrive as the refusal body",
    )
    agent: AgentReadingDto
    proposal: ProposalDto
    timings: IntentTimingsDto
    gestures: list[str] = Field(
        default_factory=list,
        description="the server's own reading of each gesture, in the record's "
        "names, as it was put on the sheet; empty when nothing was drawn",
    )
    pending_intent: PendingIntentDto = Field(
        alias="pendingIntent",
        description="how the request was resolved: the target the proposal was "
        "made against, what was rejected on the way, and a null "
        "continuationToken, because a compiled request has nothing left to ask",
    )


def candidate_dto(option: CandidateOption) -> CandidateOptionDto:
    return CandidateOptionDto(
        ref=option.ref,
        component_id=option.component_id,
        element_id=option.element_id,
        key=option.key,
        current_value=option.current_value,
        unit=option.unit,
        orientation=option.orientation,
        label=option.label,
    )


def pending_dto(pending: PendingIntent) -> PendingIntentDto:
    """One pending intent for the wire; nothing here is recomputed."""

    return PendingIntentDto(
        request_id=pending.request_id,
        state_digest=pending.state_digest,
        original_utterance=pending.original_utterance,
        action_kind=pending.action_kind,
        target_component_id=pending.target_component_id,
        element_id=pending.element_id,
        requested_semantic_property=pending.requested_semantic_property,
        known_slots=dict(pending.known_slots),
        missing_slots=list(pending.missing_slots),
        candidates=[candidate_dto(option) for option in pending.candidates],
        rejected_candidates=list(pending.rejected_candidates),
        reason_code=pending.reason_code,
        continuation_token=pending.continuation_token,
        turn=pending.turn,
    )


def draft_dto(draft: AuthoredControlDraft) -> AuthoredControlDraftDto:
    return AuthoredControlDraftDto(
        target_component_id=draft.target_component_id,
        suggested_element_id=draft.suggested_element_id,
        semantic_property=draft.semantic_property,
        producer=draft.producer,
        binding=draft.binding,
        unit=draft.unit,
        provenance=list(draft.provenance),
        confidence=draft.confidence,
        dependency_requirements=list(draft.dependency_requirements),
        suggested_action=draft.suggested_action,
    )


def pending_body(pending: PendingIntent) -> dict[str, object]:
    """The pending intent as the error body carries it, camelCase and all."""

    return pending_dto(pending).model_dump(by_alias=True)


def draft_body(draft: AuthoredControlDraft) -> dict[str, object]:
    return draft_dto(draft).model_dump(by_alias=True)


def agent_dto(compilation: Compilation) -> AgentReadingDto:
    # Where there is a receipt it is the authority on how long the call took;
    # ``promptSha256`` stays the digest of the exact bytes that receipt
    # counted as its input.
    receipt = compilation.receipt
    return AgentReadingDto(
        provider=compilation.provider,
        model=compilation.model,
        compiled_utterance=compilation.utterance or "",
        why=compilation.why,
        latency_ms=(
            compilation.latency_ms if receipt is None else receipt.duration_ms
        ),
        prompt_sha256=compilation.prompt_sha256,
        receipt_id=None if receipt is None else receipt.receipt_id,
        status=None if receipt is None else receipt.status.value,
    )
