"""Compile bounded intent observations into authority-safe commitments.

Language interpretation and retrieval happen outside this deterministic
boundary.  This module receives explicit candidate interpretations, preserves
ambiguity, and requires named authority before any proposal becomes active.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

from archflow.state import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    CriterionRef,
    RevisionPolicy,
    transition_commitment,
)
from archflow.contracts.canonical import canonical_digest


class IntentOperator(StrEnum):
    EXACT = "exact"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    TARGET = "target"


class IntentCompilationStatus(StrEnum):
    PROPOSED = "proposed"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"
    ALREADY_LOCKED = "already_locked"


class CommitmentReplacementRequiresRevision(RuntimeError):
    """Later intent conflicts with an authorized locked commitment."""


@dataclass(frozen=True, slots=True)
class IntentTerm:
    parameter_key: str
    operator: IntentOperator
    value: str | int | float
    unit: str | None = None

    def __post_init__(self) -> None:
        _text(self.parameter_key, "parameter_key")
        if not isinstance(self.operator, IntentOperator):
            raise TypeError("operator must be an IntentOperator")
        if isinstance(self.value, bool) or not isinstance(
            self.value,
            (str, int, float),
        ):
            raise TypeError("value must be bounded text or a finite number")
        if isinstance(self.value, str):
            _text(self.value, "value")
        elif isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("numeric value must be finite")
        if self.unit is not None:
            _text(self.unit, "unit")
        if isinstance(self.value, str) and self.unit is not None:
            raise ValueError("text intent values cannot carry a numeric unit")

    def to_dict(self) -> dict[str, object]:
        return {
            "parameter_key": self.parameter_key,
            "operator": self.operator.value,
            "value": self.value,
            "unit": self.unit,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict(), ascii=False)

    @property
    def ref(self) -> str:
        return f"intent-term://sha256/{self.digest}"


@dataclass(frozen=True, slots=True)
class IntentObservation:
    observation_id: str
    raw_text: str
    source_event_ref: str
    authority_id: str
    scope_ref: str
    interpretations: tuple[IntentTerm, ...]
    evidence_refs: tuple[str, ...] = ()
    explicit: bool = True
    strength: CommitmentStrength = CommitmentStrength.HARD
    revision_policy: RevisionPolicy = RevisionPolicy.OWNER_ONLY
    permitted_authority_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.observation_id, "observation_id"),
            (self.raw_text, "raw_text"),
            (self.source_event_ref, "source_event_ref"),
            (self.authority_id, "authority_id"),
            (self.scope_ref, "scope_ref"),
        ):
            _text(value, field)
        _tuple(self.interpretations, "interpretations")
        if any(not isinstance(item, IntentTerm) for item in self.interpretations):
            raise TypeError("interpretations must contain IntentTerm values")
        if len({item.digest for item in self.interpretations}) != len(
            self.interpretations
        ):
            raise ValueError("interpretations contain duplicates")
        keys = {item.parameter_key for item in self.interpretations}
        if len(keys) > 1:
            raise ValueError("one observation may address only one parameter")
        _refs(self.evidence_refs, "evidence_refs")
        if not isinstance(self.explicit, bool):
            raise TypeError("explicit must be boolean")
        if not isinstance(self.strength, CommitmentStrength):
            raise TypeError("strength must be CommitmentStrength")
        if not isinstance(self.revision_policy, RevisionPolicy):
            raise TypeError("revision_policy must be RevisionPolicy")
        _refs(self.permitted_authority_ids, "permitted_authority_ids")
        if (
            self.revision_policy is RevisionPolicy.NAMED_AUTHORITIES
            and not self.permitted_authority_ids
        ):
            raise ValueError(
                "named-authorities policy requires permitted authority ids"
            )
        if (
            self.revision_policy is not RevisionPolicy.NAMED_AUTHORITIES
            and self.permitted_authority_ids
        ):
            raise ValueError(
                "permitted authorities require named-authorities policy"
            )


@dataclass(frozen=True, slots=True)
class CommitmentProposal:
    proposal_id: str
    observation_id: str
    term: IntentTerm
    commitment: Commitment
    requires_confirmation: bool = True

    def __post_init__(self) -> None:
        _text(self.proposal_id, "proposal_id")
        _text(self.observation_id, "observation_id")
        if not isinstance(self.term, IntentTerm):
            raise TypeError("term must be an IntentTerm")
        if not isinstance(self.commitment, Commitment):
            raise TypeError("commitment must be a Commitment")
        if self.commitment.status is not CommitmentStatus.PROPOSED:
            raise ValueError("compiled commitment must remain proposed")
        if not self.requires_confirmation:
            raise ValueError("compiled intent always requires authorization")


@dataclass(frozen=True, slots=True)
class LockedCommitment:
    term: IntentTerm
    commitment: Commitment
    confirmation_event_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.term, IntentTerm):
            raise TypeError("term must be an IntentTerm")
        if not isinstance(self.commitment, Commitment):
            raise TypeError("commitment must be a Commitment")
        if self.commitment.status is not CommitmentStatus.ACTIVE:
            raise ValueError("locked commitment must be active")
        _text(self.confirmation_event_ref, "confirmation_event_ref")


@dataclass(frozen=True, slots=True)
class CommitmentRevision:
    previous: Commitment
    replacement: LockedCommitment
    revision_event_ref: str


@dataclass(frozen=True, slots=True)
class IntentCompilation:
    schema: str
    compilation_id: str
    observation_id: str
    status: IntentCompilationStatus
    proposals: tuple[CommitmentProposal, ...] = ()
    unknown_reasons: tuple[str, ...] = ()
    locked_commitment_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema != "IntentCompilation@1":
            raise ValueError("unsupported intent compilation schema")
        _text(self.compilation_id, "compilation_id")
        _text(self.observation_id, "observation_id")
        if not isinstance(self.status, IntentCompilationStatus):
            raise TypeError("status must be IntentCompilationStatus")
        _tuple(self.proposals, "proposals")
        _refs(self.unknown_reasons, "unknown_reasons")
        if self.locked_commitment_id is not None:
            _text(self.locked_commitment_id, "locked_commitment_id")
        if self.status is IntentCompilationStatus.UNKNOWN:
            if self.proposals or not self.unknown_reasons:
                raise ValueError("unknown compilation must name reasons only")
        elif self.status is IntentCompilationStatus.ALREADY_LOCKED:
            if self.proposals or self.locked_commitment_id is None:
                raise ValueError("already-locked compilation must name its lock")
        elif not self.proposals or self.unknown_reasons:
            raise ValueError("proposed compilation must contain proposals only")


def compile_intent(
    observation: IntentObservation,
    *,
    locked: tuple[LockedCommitment, ...] = (),
) -> IntentCompilation:
    """Compile candidate meanings without choosing among ambiguous meanings."""

    if not isinstance(observation, IntentObservation):
        raise TypeError("observation must be an IntentObservation")
    _tuple(locked, "locked")
    if any(not isinstance(item, LockedCommitment) for item in locked):
        raise TypeError("locked must contain LockedCommitment values")
    if not observation.evidence_refs:
        return _unknown(observation, "intent.evidence_missing")
    if not observation.interpretations:
        return _unknown(observation, "intent.interpretation_missing")

    parameter_key = observation.interpretations[0].parameter_key
    existing = tuple(
        item for item in locked if item.term.parameter_key == parameter_key
    )
    if len(existing) > 1:
        raise ValueError("multiple active locks exist for one parameter")
    if existing:
        current = existing[0]
        candidate_digests = {
            item.digest for item in observation.interpretations
        }
        if candidate_digests == {current.term.digest}:
            identity = {
                "observation_id": observation.observation_id,
                "status": IntentCompilationStatus.ALREADY_LOCKED.value,
                "commitment_id": current.commitment.commitment_id,
            }
            return IntentCompilation(
                schema="IntentCompilation@1",
                compilation_id=f"intent-{canonical_digest(identity, ascii=False)[:20]}",
                observation_id=observation.observation_id,
                status=IntentCompilationStatus.ALREADY_LOCKED,
                locked_commitment_id=current.commitment.commitment_id,
            )
        raise CommitmentReplacementRequiresRevision(
            f"{parameter_key} is locked by "
            f"{current.commitment.commitment_id}; explicit revision required"
        )

    proposals = tuple(
        _proposal(observation, term)
        for term in sorted(
            observation.interpretations,
            key=lambda item: item.digest,
        )
    )
    status = (
        IntentCompilationStatus.PROPOSED
        if observation.explicit and len(proposals) == 1
        else IntentCompilationStatus.AMBIGUOUS
    )
    identity = {
        "observation_id": observation.observation_id,
        "status": status.value,
        "proposal_ids": [item.proposal_id for item in proposals],
    }
    return IntentCompilation(
        schema="IntentCompilation@1",
        compilation_id=f"intent-{canonical_digest(identity, ascii=False)[:20]}",
        observation_id=observation.observation_id,
        status=status,
        proposals=proposals,
    )


def confirm_proposal(
    proposal: CommitmentProposal,
    *,
    actor_authority_id: str,
    confirmation_event_ref: str,
    monitor_state_ref: str,
) -> LockedCommitment:
    """Accept and activate one proposal through the existing authority rules."""

    if not isinstance(proposal, CommitmentProposal):
        raise TypeError("proposal must be a CommitmentProposal")
    _text(confirmation_event_ref, "confirmation_event_ref")
    _text(monitor_state_ref, "monitor_state_ref")
    accepted = transition_commitment(
        proposal.commitment,
        CommitmentStatus.ACCEPTED,
        actor_authority_id=actor_authority_id,
    )
    active = transition_commitment(
        accepted,
        CommitmentStatus.ACTIVE,
        actor_authority_id=actor_authority_id,
        monitor_state_ref=monitor_state_ref,
    )
    return LockedCommitment(
        term=proposal.term,
        commitment=active,
        confirmation_event_ref=confirmation_event_ref,
    )


def revise_locked_commitment(
    current: LockedCommitment,
    replacement_proposal: CommitmentProposal,
    *,
    actor_authority_id: str,
    revision_event_ref: str,
    monitor_state_ref: str,
) -> CommitmentRevision:
    """Apply an explicit authorized revision without overwriting the old object."""

    if not isinstance(current, LockedCommitment):
        raise TypeError("current must be a LockedCommitment")
    if not isinstance(replacement_proposal, CommitmentProposal):
        raise TypeError("replacement_proposal must be a CommitmentProposal")
    _text(revision_event_ref, "revision_event_ref")
    if (
        current.term.parameter_key
        != replacement_proposal.term.parameter_key
    ):
        raise ValueError("revision must address the same parameter")
    successor = replace(
        replacement_proposal.commitment,
        predecessor_id=current.commitment.commitment_id,
        source_event_ref=revision_event_ref,
    )
    revised_previous = transition_commitment(
        current.commitment,
        CommitmentStatus.REVISED,
        actor_authority_id=actor_authority_id,
        successor_id=successor.commitment_id,
    )
    replacement = confirm_proposal(
        replace(replacement_proposal, commitment=successor),
        actor_authority_id=actor_authority_id,
        confirmation_event_ref=revision_event_ref,
        monitor_state_ref=monitor_state_ref,
    )
    return CommitmentRevision(
        previous=revised_previous,
        replacement=replacement,
        revision_event_ref=revision_event_ref,
    )


def _proposal(
    observation: IntentObservation,
    term: IntentTerm,
) -> CommitmentProposal:
    identity = {
        "observation_id": observation.observation_id,
        "term": term.to_dict(),
        "authority_id": observation.authority_id,
        "scope_ref": observation.scope_ref,
        "source_event_ref": observation.source_event_ref,
    }
    digest = canonical_digest(identity, ascii=False)
    commitment_id = f"commitment.intent.{digest[:20]}"
    proposal_id = f"proposal.intent.{digest[:20]}"
    commitment = Commitment(
        commitment_id=commitment_id,
        kind=CommitmentKind.MAINTENANCE,
        strength=observation.strength,
        status=CommitmentStatus.PROPOSED,
        authority_id=observation.authority_id,
        source_event_ref=observation.source_event_ref,
        evidence_refs=observation.evidence_refs,
        scope_refs=(observation.scope_ref, term.ref),
        activation_criterion=CriterionRef(
            criterion_id=f"authority.confirm.{commitment_id}",
            provider_id="authority.confirmation",
            subject_refs=(term.ref,),
        ),
        satisfaction_criterion=CriterionRef(
            criterion_id=term.parameter_key,
            provider_id="validator.parameter",
            subject_refs=(observation.scope_ref, term.ref),
        ),
        revision_policy=observation.revision_policy,
        permitted_authority_ids=observation.permitted_authority_ids,
    )
    return CommitmentProposal(
        proposal_id=proposal_id,
        observation_id=observation.observation_id,
        term=term,
        commitment=commitment,
    )


def _unknown(
    observation: IntentObservation,
    reason: str,
) -> IntentCompilation:
    identity = {
        "observation_id": observation.observation_id,
        "status": IntentCompilationStatus.UNKNOWN.value,
        "reason": reason,
    }
    return IntentCompilation(
        schema="IntentCompilation@1",
        compilation_id=f"intent-{canonical_digest(identity, ascii=False)[:20]}",
        observation_id=observation.observation_id,
        status=IntentCompilationStatus.UNKNOWN,
        unknown_reasons=(reason,),
    )


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1_000:
        raise ValueError(f"{field} must be bounded non-empty text")


def _tuple(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > 64:
        raise ValueError(f"{field} exceeds bounded item count")


def _refs(value: object, field: str) -> None:
    _tuple(value, field)
    for item in value:  # type: ignore[union-attr]
        _text(item, f"{field} item")
    if len(value) != len(set(value)):  # type: ignore[arg-type]
        raise ValueError(f"{field} contains duplicates")


__all__ = [
    "IntentOperator",
    "IntentCompilationStatus",
    "CommitmentReplacementRequiresRevision",
    "IntentTerm",
    "IntentObservation",
    "CommitmentProposal",
    "LockedCommitment",
    "CommitmentRevision",
    "IntentCompilation",
    "compile_intent",
    "confirm_proposal",
    "revise_locked_commitment",
]

