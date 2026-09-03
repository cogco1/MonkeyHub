"""The wire form of ``POST /api/intents``: an architect's sentence in, the
agent's compiled sentence and the typed proposal it became out.

The proposal half is the same ``ProposalDto`` that ``POST /api/proposals``
answers with — it *is* a proposal made by the deterministic seam. The agent
half is kept apart from it on purpose: ``agent`` says who read the request,
what it compiled and why, so nothing the model said can be mistaken for
something the record answered.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.intent_agent import Compilation
from .proposal import STATE_DIGEST_PATTERN, ProposalDto


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


def agent_dto(compilation: Compilation) -> AgentReadingDto:
    return AgentReadingDto(
        provider=compilation.provider,
        model=compilation.model,
        compiled_utterance=compilation.utterance or "",
        why=compilation.why,
        latency_ms=compilation.latency_ms,
        prompt_sha256=compilation.prompt_sha256,
    )
