"""The wire form of one proposal: a selection and a sentence in, a typed
operator out.

The selection travels in the request and never in the utterance. That is the
whole reason this DTO has ``targetComponentId`` and ``elementId`` at all: what
the user clicked is a fact the client already knows, and parsing it back out of
prose would be the one place a wrong thing could get changed confidently.

``decisionOperator`` goes out as an opaque ``dict``. It is the kernel's own
``DecisionOperator@2`` payload and this transport does not mirror it field by
field: the client displays it, and anything that needs to read it parses it
with the kernel.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..application.episodes import DeliberationEpisode, EpisodeProposal
from ..application.proposals import PERSISTENCE, Proposal
from .impact import ImpactDto
from .impact import to_dto as impact_dto

# 64 lowercase hex, the form the kernel writes. A wrongly shaped digest is a
# malformed request, not a stale base, and the two must not arrive alike.
STATE_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class ProposalRequestDto(BaseModel):
    """One utterance against one selection, at one exact base."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; a proposal "
        "against any other state is refused as STALE_BASE",
    )
    target_component_id: str = Field(
        alias="targetComponentId",
        min_length=1,
        description="the selected Component@1; selection comes from the "
        "request, never from the utterance",
    )
    element_id: str | None = Field(
        alias="elementId",
        default=None,
        min_length=1,
        description="the selected Element@1, when one was picked; it must "
        "belong to targetComponentId",
    )
    utterance: str = Field(
        min_length=1,
        description="one sentence in the intent grammar; anything else comes "
        "back as BLOCKED_NEEDS_HUMAN with the accepted forms",
    )
    project_id: str | None = Field(
        alias="projectId",
        default=None,
        min_length=1,
        description="the project the client believes it is proposing against; "
        "a different one is refused as PROJECT_MISMATCH",
    )

    def context_refs(self) -> list[str]:
        """The selection as the ``IntentProvider`` port takes it."""

        refs = [
            f"state:{self.state_digest}",
            f"component:{self.target_component_id}",
        ]
        if self.element_id is not None:
            refs.append(f"element:{self.element_id}")
        return refs


class ProposalTargetDto(BaseModel):
    """What the proposal is about, in the record's own identifiers."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    component_id: str = Field(alias="componentId")
    element_id: str | None = Field(alias="elementId")
    ref: str = Field(
        description="the kernel's prefixed ref: entity:<id> or parameter:<key>",
    )
    key: str


class ProposalChangeDto(BaseModel):
    """The number as the record has it, and the number proposed for it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    # Authored quantities keep the type they were written with: a value
    # authored as 3 must not come back as 3.0.
    old: int | float
    new: int | float
    unit: str | None = Field(
        description="the unit the record declares; element params are "
        "unit-less numbers and answer null",
    )


class ProposalDto(BaseModel):
    """The wire form of ``POST /api/proposals`` and ``GET /api/proposals/{id}``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    proposal_id: str = Field(alias="proposalId")
    status: Literal["proposed", "conflict"] = Field(
        description="conflict means the change reaches something the "
        "utterance asked to keep; it is still a proposal, never an execution",
    )
    base_state_digest: str = Field(
        alias="baseStateDigest",
        description="the exact base the operator refuses to run without",
    )
    record_digest: str = Field(alias="recordDigest")
    target: ProposalTargetDto
    change: ProposalChangeDto
    protected: list[str]
    decision_operator: dict[str, Any] = Field(
        alias="decisionOperator",
        description="the kernel's DecisionOperator@2 payload, opaque here",
    )
    impact: ImpactDto
    utterance: str
    persistence: str = Field(
        description="where this proposal lives; it is not version history",
    )
    created_at: str = Field(alias="createdAt")


def to_dto(proposal: Proposal) -> ProposalDto:
    """Shape one proposal for the wire; nothing here is recomputed."""

    return ProposalDto(
        proposal_id=proposal.proposal_id,
        # The application's two statuses are the DTO's two literals; a third
        # would fail here rather than reach a client.
        status=proposal.status,
        base_state_digest=proposal.base_state_digest,
        record_digest=proposal.record_digest,
        target=ProposalTargetDto(
            component_id=proposal.component_id,
            element_id=proposal.element_id,
            ref=proposal.target_ref,
            key=proposal.key,
        ),
        change=ProposalChangeDto(
            old=proposal.old, new=proposal.new, unit=proposal.unit
        ),
        protected=list(proposal.protected),
        decision_operator=proposal.operator.to_dict(),
        impact=impact_dto(proposal.impact),
        utterance=proposal.utterance,
        persistence=PERSISTENCE,
        created_at=proposal.created_at,
    )


# ---- the judgement -----------------------------------------------------------
#
# The wire form of one ``DeliberationEpisode``. It lives beside the proposal it
# is a decision about, because a decision that travelled in its own vocabulary
# would be a second story about the same option.


class ModifiedToDto(BaseModel):
    """What the architect said instead, when the decision was ``modified``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    utterance: str = Field(
        min_length=1,
        description="the sentence the architect replaced the proposal with; "
        "it is re-proposed through the same deterministic path",
    )


class ProposalDecisionRequestDto(BaseModel):
    """``POST /api/proposals/{id}/decision``: what was decided, and why.

    ``accepted`` is deliberately not a decision this route takes. A proposal is
    accepted by being run — ``POST /api/proposals/{id}/candidate`` — and an
    acceptance that left no run would be a judgement about a building nobody
    built.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    decision: Literal["rejected", "modified"] = Field(
        description="rejected closes the option; modified closes it and "
        "re-proposes modifiedTo in its place",
    )
    reason: str | None = Field(
        default=None,
        min_length=1,
        description="the architect's own sentence for the decision, kept "
        "verbatim; null when none was given",
    )
    modified_to: ModifiedToDto | None = Field(
        alias="modifiedTo",
        default=None,
        description="required when decision is modified, refused otherwise",
    )


class EpisodeChangeDto(BaseModel):
    """The number the record had, and the number the option proposed."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    key: str
    old: int | float
    new: int | float


class EpisodeProposalDto(BaseModel):
    """One option that was on the table, and what became of it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    proposal_id: str = Field(alias="proposalId")
    target: str
    change: EpisodeChangeDto
    closure: list[str] = Field(
        description="every ref this option would invalidate, as the "
        "proposal's own closure had it",
    )
    decision: Literal["accepted", "rejected", "modified"]
    reason: str | None
    modified_to: dict[str, Any] | None = Field(alias="modifiedTo")


class EpisodeIntentDto(BaseModel):
    """What was asked, as the studio resolved it — not the chat log."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    utterance: str
    target_component_id: str = Field(alias="targetComponentId")
    element_id: str | None = Field(alias="elementId")
    requested_property: str | None = Field(alias="requestedProperty")
    known_slots: dict[str, str] = Field(alias="knownSlots")
    request_id: str | None = Field(
        alias="requestId",
        description="the pending intent this request belonged to, when it "
        "came through a clarification chain",
    )


class EpisodeDto(BaseModel):
    """One retained judgement: the intent, the options, and the run."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    episode_id: str = Field(alias="episodeId")
    project_id: str = Field(alias="projectId")
    state_digest: str = Field(alias="stateDigest")
    intent: EpisodeIntentDto
    proposals: list[EpisodeProposalDto]
    protected: list[str]
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    validation_refs: list[str] = Field(alias="validationRefs")
    produced_run: str | None = Field(
        alias="producedRun",
        description="the candidate run the accepted proposal made; null "
        "while the judgement has met no run",
    )
    chosen_scope: str | None = Field(alias="chosenScope")
    created_at: str = Field(alias="createdAt")
    persistence: str = Field(
        description="run:<id> once the judgement is in a run's records, and "
        "the in-memory sentence while it is only this process's",
    )


def episode_proposal_dto(item: EpisodeProposal) -> EpisodeProposalDto:
    return EpisodeProposalDto(
        proposal_id=item.proposal_id,
        target=item.target,
        change=EpisodeChangeDto(
            key=item.change.key, old=item.change.old, new=item.change.new
        ),
        closure=list(item.closure),
        # The application's three decisions are the DTO's three literals; a
        # fourth would fail here rather than reach a client.
        decision=item.decision,
        reason=item.reason,
        modified_to=(
            None if item.modified_to is None else dict(item.modified_to)
        ),
    )


def episode_dto(episode: DeliberationEpisode) -> EpisodeDto:
    """Shape one judgement for the wire; nothing here is recomputed."""

    return EpisodeDto(
        episode_id=episode.episode_id,
        project_id=episode.project_id,
        state_digest=episode.state_digest,
        intent=EpisodeIntentDto(
            utterance=episode.intent.utterance,
            target_component_id=episode.intent.target_component_id,
            element_id=episode.intent.element_id,
            requested_property=episode.intent.requested_property,
            known_slots=dict(episode.intent.known_slots),
            request_id=episode.intent.request_id,
        ),
        proposals=[episode_proposal_dto(item) for item in episode.proposals],
        protected=list(episode.protected),
        evidence_refs=list(episode.evidence_refs),
        validation_refs=list(episode.validation_refs),
        produced_run=episode.produced_run,
        chosen_scope=episode.chosen_scope,
        created_at=episode.created_at,
        persistence=episode.persistence,
    )
