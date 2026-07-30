"""The only boundary from speculative work to formal review."""

from __future__ import annotations

from dataclasses import dataclass

from archflow.state import (
    ArtifactRef,
    Commitment,
    CommitmentStatus,
    Fact,
    Obligation,
    StateRef,
)


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")


def _require_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple")


@dataclass(frozen=True, slots=True)
class Claim:
    key: str
    value: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.key, "claim key")
        _require_text(self.value, "claim value")
        _require_tuple(self.evidence_refs, "claim evidence_refs")


@dataclass(frozen=True, slots=True)
class CandidateDelta:
    facts_add: tuple[Fact, ...] = ()
    commitments_add: tuple[Commitment, ...] = ()
    obligations_discharge: tuple[str, ...] = ()
    obligations_add: tuple[Obligation, ...] = ()
    artifacts_add: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        for name, values in (
            ("facts_add", self.facts_add),
            ("commitments_add", self.commitments_add),
            ("obligations_discharge", self.obligations_discharge),
            ("obligations_add", self.obligations_add),
            ("artifacts_add", self.artifacts_add),
        ):
            _require_tuple(values, name)
        if len(self.obligations_discharge) != len(set(self.obligations_discharge)):
            raise ValueError("obligations_discharge contains duplicates")
        if any(
            not isinstance(item, Commitment)
            for item in self.commitments_add
        ):
            raise TypeError(
                "commitments_add must contain Commitment values"
            )
        commitment_ids = tuple(
            item.commitment_id for item in self.commitments_add
        )
        if len(commitment_ids) != len(set(commitment_ids)):
            raise ValueError("commitments_add contains duplicate ids")
        if any(
            item.status is not CommitmentStatus.PROPOSED
            for item in self.commitments_add
        ):
            raise ValueError(
                "candidate submissions may add only proposed commitments"
            )


@dataclass(frozen=True, slots=True)
class CandidateSubmission:
    submission_id: str
    base: StateRef
    workspace_id: str
    intent: str
    delta: CandidateDelta
    claims: tuple[Claim, ...]
    evidence_refs: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.submission_id, "submission_id"),
            (self.workspace_id, "workspace_id"),
            (self.intent, "intent"),
        ):
            _require_text(value, name)
        for name, values in (
            ("claims", self.claims),
            ("evidence_refs", self.evidence_refs),
            ("unresolved", self.unresolved),
        ):
            _require_tuple(values, name)
        keys = tuple(claim.key for claim in self.claims)
        if len(keys) != len(set(keys)):
            raise ValueError("claim keys contain duplicates")
