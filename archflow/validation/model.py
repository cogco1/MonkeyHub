"""Validation receipt contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from archflow.state import StateRef


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    message: str
    severity: Severity = Severity.ERROR
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationReceipt:
    receipt_id: str
    submission_id: str
    checked_state: StateRef
    passed: bool
    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        has_error = any(item.severity is Severity.ERROR for item in self.findings)
        if self.passed == has_error:
            raise ValueError("passed must be true exactly when no error finding exists")
