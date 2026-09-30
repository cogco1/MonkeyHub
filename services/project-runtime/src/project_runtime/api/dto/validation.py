"""The wire form of one candidate's validation and review readiness.

Three things this shape refuses to let a client do.

It cannot read the receipt as proof of more than was checked: ``validators``
names every gate that ran, ``effectiveChecks`` names the one that had anything
to check, and ``validatorNote`` and ``canonicalFacts`` say in words why the two
lists differ. A client that shows "validation passed" without showing those is
showing a claim the server never made.

It cannot derive review readiness itself. ``reviewReady`` and ``blockedBy``
come from the server; the browser is never given the five clauses to combine on its own, and
``blockedBy`` names the failing ones so a refusal is actionable rather than
merely red.

Review readiness is not issue authority. This route writes nothing and cannot
advance a stage; only ``project.issue`` can issue a run.

And it cannot lose a state: ``relationChecks`` is the candidate's own
three-state block, the same object the candidate readout serves, so nothing
unchecked can be rounded up to held on the way through.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.validation import (
    EFFECTIVE_CHECKS,
    VALIDATOR_NAMES,
    VALIDATOR_NOTE,
    CandidateValidation,
)
from archflow.validation.model import ValidationReceipt

from .candidate import RelationChecksDto, relations_dto
from .project import ProjectVersionDto


class ValidationFindingDto(BaseModel):
    """One finding, in the kernel's own words."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str
    message: str
    severity: str = Field(description="error | warning")


class ValidationReceiptDto(BaseModel):
    """The kernel's receipt, unedited."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    receipt_id: str = Field(alias="receiptId")
    submission_id: str = Field(alias="submissionId")
    submission_digest: str = Field(
        alias="submissionDigest",
        description="the exact submission content these gates examined",
    )
    checked_state: ProjectVersionDto = Field(
        alias="checkedState",
        description="the canonical version the submission was checked against",
    )
    passed: bool
    findings: list[ValidationFindingDto] = Field(
        description="every finding the gates returned; empty is a real answer",
    )


class ValidationDto(BaseModel):
    """The wire form of ``GET /api/candidates/{id}/validation``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    candidate_id: str = Field(alias="candidateId")
    receipt: ValidationReceiptDto
    canonical_facts: str = Field(
        alias="canonicalFacts",
        description="the published references and candidate's exact retained "
        "StateRecord, including declared obligations; source presence does "
        "not mean project conditions were checked",
    )
    validators: list[str] = Field(
        description="every gate that ran, in the order it ran",
    )
    effective_checks: list[str] = Field(
        alias="effectiveChecks",
        description="the gates that had anything to check on this state; "
        "narrower than validators, and never to be shown as equal to it",
    )
    validator_note: str = Field(
        alias="validatorNote",
        description="why the two lists differ, in a line the UI shows verbatim",
    )
    relation_checks: RelationChecksDto = Field(
        alias="relationChecks",
        description="the candidate's own three states, copied not recomputed",
    )
    seat_execution_complete: bool = Field(alias="seatExecutionComplete")
    review_ready: bool = Field(
        alias="reviewReady",
        description="whether all five server-owned review clauses hold; "
        "this never issues a run or advances a stage",
    )
    blocked_by: list[str] = Field(
        alias="blockedBy",
        description="each clause that refused, by name: validation.receipt, "
        "runner.seat_execution_complete, relations.held, "
        "relations.fully_checked, runner.exports_available",
    )
    honesty: list[str] = Field(
        description="what the submission could not carry — a seat whose "
        "program record the studio could not name is said here rather than "
        "dropped quietly; empty is a real answer, not a missing one",
    )


def to_dto(validation: CandidateValidation) -> ValidationDto:
    """Shape one validation for the wire; the receipt travels as it came."""

    return ValidationDto(
        candidate_id=validation.candidate_id,
        receipt=_receipt_dto(validation.receipt),
        canonical_facts=validation.canonical_facts,
        validators=list(VALIDATOR_NAMES),
        effective_checks=list(EFFECTIVE_CHECKS),
        validator_note=VALIDATOR_NOTE,
        relation_checks=relations_dto(validation.relation_checks),
        seat_execution_complete=validation.seat_execution_complete,
        review_ready=validation.review_ready,
        blocked_by=list(validation.blocked_by),
        honesty=list(validation.honesty),
    )


def _receipt_dto(receipt: ValidationReceipt) -> ValidationReceiptDto:
    return ValidationReceiptDto(
        receipt_id=receipt.receipt_id,
        submission_id=receipt.submission_id,
        submission_digest=receipt.submission_digest,
        checked_state=ProjectVersionDto(
            version=receipt.checked_state.version,
            state_sha256=receipt.checked_state.state_sha256,
        ),
        passed=receipt.passed,
        findings=[
            ValidationFindingDto(
                code=finding.code,
                message=finding.message,
                severity=finding.severity.value,
            )
            for finding in receipt.findings
        ],
    )
