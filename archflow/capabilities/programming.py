"""Read-only, state-responsive input boundary for programming experts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from archflow.project.refs import require_identifier
from archflow.state.design_brief import (
    BriefSlot,
    BriefSlotStatus,
    DesignBrief,
)
from archflow.state.operational_state import (
    require_local_id,
    require_logical_ref,
)


@dataclass(frozen=True, slots=True)
class ProgrammingSnapshot:
    """Detached evidence and obligations; no writer or design authority."""

    project_id: str
    run_id: str
    base_state_sha256: str
    brief_digest: str
    obligation_topics: tuple[str, ...]
    claim_refs: tuple[str, ...]
    constraint_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "ProgrammingSnapshot@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        _sha256(self.base_state_sha256, "base_state_sha256")
        _sha256(self.brief_digest, "brief_digest")
        _topics(
            self.obligation_topics,
            "obligation_topics",
        )
        _refs(self.claim_refs, "claim_refs", allow_empty=True)
        _refs(
            self.constraint_refs,
            "constraint_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def snapshot_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base_state_sha256,
            "brief_digest": self.brief_digest,
            "obligation_topics": list(self.obligation_topics),
            "claim_refs": list(self.claim_refs),
            "constraint_refs": list(self.constraint_refs),
            "evidence_refs": list(self.evidence_refs),
            "read_only": True,
            "generation_authority": False,
        }


@dataclass(frozen=True, slots=True)
class ProgrammingAdviceReceipt:
    """Detached advice provenance; proposals still require compilation."""

    advisor_id: str
    snapshot_digest: str
    proposal_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]

    SCHEMA = "ProgrammingAdviceReceipt@1"

    def __post_init__(self) -> None:
        require_local_id(self.advisor_id, "advisor_id")
        _sha256(self.snapshot_digest, "snapshot_digest")
        _refs(self.proposal_refs, "proposal_refs")
        _refs(self.evidence_refs, "evidence_refs")
        _refs(
            self.assumption_refs,
            "assumption_refs",
            allow_empty=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "advisor_id": self.advisor_id,
            "snapshot_digest": self.snapshot_digest,
            "proposal_refs": list(self.proposal_refs),
            "evidence_refs": list(self.evidence_refs),
            "assumption_refs": list(self.assumption_refs),
            "read_only": True,
            "accepted": False,
        }


def build_programming_snapshot(
    brief: DesignBrief,
    *,
    obligation_topics: tuple[str, ...] | None = None,
) -> ProgrammingSnapshot:
    """Expose current topics; the returned set is not an execution schedule.

    Before a DesignProgram exists, every generic program question is open even
    when the brief supplies evidence for it.  On later passes the caller
    supplies the exact topics from the current operational obligations.
    """

    if not isinstance(brief, DesignBrief):
        raise TypeError("brief must be DesignBrief")
    status_by_slot = {item.slot: item.status for item in brief.slots}
    if obligation_topics is None:
        topics = {
            "program.functions",
            "program.relationships",
            "program.capacity",
            "program.area",
        }
        if status_by_slot[BriefSlot.SIZE] is not BriefSlotStatus.SUPPORTED:
            topics.add("program.scale-scenarios")
    else:
        _topics(
            obligation_topics,
            "obligation_topics",
        )
        topics = set(obligation_topics)
    return ProgrammingSnapshot(
        project_id=brief.project_id,
        run_id=brief.run_id,
        base_state_sha256=brief.base.require_digest(),
        brief_digest=brief.brief_digest,
        obligation_topics=tuple(sorted(topics)),
        claim_refs=tuple(sorted(item.ref for item in brief.claims)),
        constraint_refs=tuple(
            sorted(
                f"brief-constraint:{item.proposal_id}"
                for item in brief.constraint_proposals
            )
        ),
        evidence_refs=tuple(sorted(brief.evidence_refs)),
    )


def _refs(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if not value and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in value:
        require_logical_ref(item, field)
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


def _topics(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    for item in value:
        require_local_id(item, field)
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


def _sha256(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
