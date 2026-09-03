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
