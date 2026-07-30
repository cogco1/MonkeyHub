"""Non-authoritative proposals to revise an existing commitment."""

from __future__ import annotations

from dataclasses import dataclass

from archflow.project.refs import BranchRef
from archflow.state.operational_state import require_local_id


_HEX = frozenset("0123456789abcdef")


def _sha256(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    digest = value.lower()
    if len(digest) != 64 or any(char not in _HEX for char in digest):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return digest


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


@dataclass(frozen=True, slots=True)
class CommitmentRevisionProposal:
    """A request for authority, never authority or a lifecycle transition."""

    proposal_id: str
    commitment_id: str
    candidate_id: str
    branch: BranchRef
    base_state_digest: str
    reason_finding_id: str
    required_authority_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_local_id(self.proposal_id, "proposal_id")
        _text(self.commitment_id, "commitment_id")
        require_local_id(self.candidate_id, "candidate_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            _sha256(self.base_state_digest, "base_state_digest"),
        )
        require_local_id(self.reason_finding_id, "reason_finding_id")
        if not isinstance(self.required_authority_ids, tuple) or not (
            self.required_authority_ids
        ):
            raise ValueError(
                "required_authority_ids must be a non-empty tuple"
            )
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")
        for value in (
            *self.required_authority_ids,
            *self.evidence_refs,
        ):
            _text(value, "authority or evidence reference")
        if len(self.required_authority_ids) != len(
            set(self.required_authority_ids)
        ):
            raise ValueError("required_authority_ids contains duplicates")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evidence_refs contains duplicates")

