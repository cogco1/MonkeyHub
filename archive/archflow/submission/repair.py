"""Traceable repair findings and obligations for speculative iterations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


def _require_text(value: str, field: str, *, maximum: int = 1_000) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")


@dataclass(frozen=True, slots=True)
class RepairFinding:
    code: str
    message: str
    source_receipt_id: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.code, "code"),
            (self.message, "message"),
            (self.source_receipt_id, "source_receipt_id"),
        ):
            _require_text(value, field)
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")
        if len(self.evidence_refs) > 64:
            raise ValueError("evidence_refs cannot exceed 64 items")
        for reference in self.evidence_refs:
            _require_text(reference, "evidence_ref")


@dataclass(frozen=True, slots=True)
class RepairObligation:
    """A design question for the Architect, never an automatic edit."""

    obligation_id: str
    statement: str
    finding_code: str
    source_receipt_id: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.obligation_id, "obligation_id"),
            (self.statement, "statement"),
            (self.finding_code, "finding_code"),
            (self.source_receipt_id, "source_receipt_id"),
        ):
            _require_text(value, field)
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")


def obligations_from_findings(
    findings: tuple[RepairFinding, ...],
) -> tuple[RepairObligation, ...]:
    """Create stable obligation identities while retaining receipt provenance."""

    if not isinstance(findings, tuple):
        raise TypeError("findings must be a tuple")
    obligations: dict[str, RepairObligation] = {}
    for finding in findings:
        if not isinstance(finding, RepairFinding):
            raise TypeError("findings must contain RepairFinding values")
        identity = json.dumps(
            {
                "code": finding.code,
                "message": finding.message,
                "evidence_refs": finding.evidence_refs,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        obligation = RepairObligation(
            obligation_id=f"repair-{digest[:20]}",
            statement=finding.message,
            finding_code=finding.code,
            source_receipt_id=finding.source_receipt_id,
            evidence_refs=finding.evidence_refs,
        )
        obligations.setdefault(obligation.obligation_id, obligation)
    return tuple(sorted(obligations.values(), key=lambda item: item.obligation_id))
