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


class IntentDto(BaseModel):
    """The wire form of ``POST /api/intents``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    agent: AgentReadingDto
    proposal: ProposalDto
    timings: IntentTimingsDto
    gestures: list[str] = Field(
        default_factory=list,
        description="the server's own reading of each gesture, in the record's "
        "names, as it was put on the sheet; empty when nothing was drawn",
    )


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
