"""The only boundary from speculative work to formal review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from archflow.state.model import ArtifactRef, Fact, Obligation, StateRef
from archflow.state.commitments import Commitment, CommitmentStatus


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

    def content_digest(self) -> str:
        """Canonical digest over the complete submission content.

        Validation receipts bind to this digest so promotion can prove the
        gates examined exactly the delta being committed, not merely a
        submission that reused the same id and base.
        """
        payload = {
            "schema": "CandidateSubmissionContent@1",
            "submission_id": self.submission_id,
            "base": [
                self.base.project_id,
                self.base.version,
                self.base.state_sha256,
            ],
            "workspace_id": self.workspace_id,
            "intent": self.intent,
            "delta": {
                "facts_add": [
                    [item.key, item.value, item.source_ref]
                    for item in self.delta.facts_add
                ],
                "commitments_add": [
                    item.to_dict() for item in self.delta.commitments_add
                ],
                "obligations_discharge": list(
                    self.delta.obligations_discharge
                ),
                "obligations_add": [
                    [item.obligation_id, item.statement, item.source_ref]
                    for item in self.delta.obligations_add
                ],
                "artifacts_add": [
                    [
                        item.artifact_id,
                        item.uri,
                        item.media_type,
                        item.sha256,
                    ]
                    for item in self.delta.artifacts_add
                ],
            },
            "claims": [
                [claim.key, claim.value, list(claim.evidence_refs)]
                for claim in self.claims
            ],
            "evidence_refs": list(self.evidence_refs),
            "unresolved": list(self.unresolved),
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
