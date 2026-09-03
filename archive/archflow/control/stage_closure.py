"""Composite stage closure compiled from exact typed check receipts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import mapping
from archive.archflow.control.requirements import RequirementBasisMode, StageCheckRequirement, StageRequirementProfile, _branch_from_dict, _text
from archflow.project.refs import BranchRef
from archflow.state.stage_workflow import (
    CompositeStageClosureReceipt,
    StageClosureError,
    StageClosureFinding,
    StageClosureFindingCode,
    StageClosureStatus,
)
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus


def _missing(required: tuple[str, ...], actual: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(required) - set(actual)))


def _receipt_findings(
    requirement: StageCheckRequirement,
    receipt: CheckReceiptEnvelope,
    *,
    profile: StageRequirementProfile,
    subject_digest: str,
) -> tuple[StageClosureFinding, ...]:
    findings: list[StageClosureFinding] = []

    def add(code: StageClosureFindingCode, refs: tuple[str, ...] = ()) -> None:
        findings.append(
            StageClosureFinding(
                code=code,
                requirement_id=requirement.requirement_id,
                receipt_id=receipt.receipt_id,
                refs=refs,
            )
        )

    if receipt.checker_id != requirement.checker_id:
        add(StageClosureFindingCode.CHECKER_MISMATCH)
    if receipt.branch != profile.branch:
        add(StageClosureFindingCode.BRANCH_MISMATCH)
    if receipt.scope_digest != profile.scope_digest:
        add(StageClosureFindingCode.SCOPE_MISMATCH)
    if receipt.subject_digest != subject_digest:
        add(StageClosureFindingCode.SUBJECT_DIGEST_MISMATCH)
    expected_covered_refs = (
        ()
        if receipt.status is CheckStatus.NOT_APPLICABLE
        else requirement.denominator_refs
    )
    if (
        receipt.subject_refs != requirement.denominator_refs
        or receipt.coverage_denominator != requirement.denominator_refs
        or receipt.covered_refs != expected_covered_refs
    ):
        add(StageClosureFindingCode.DENOMINATOR_MISMATCH)

    if requirement.basis_mode is RequirementBasisMode.UNIVERSAL:
        if (
            receipt.claim_refs
            or receipt.applicability_refs
            or receipt.adoption_refs
            or receipt.source_refs
            or receipt.authority_refs
        ):
            add(StageClosureFindingCode.UNIVERSAL_BASIS_CONTAMINATED)
    else:
        missing_claims = _missing(
            requirement.required_claim_refs, receipt.claim_refs
        )
        missing_applicability = _missing(
            requirement.required_applicability_refs,
            receipt.applicability_refs,
        )
        missing_authorities = _missing(
            requirement.required_authority_refs,
            receipt.authority_refs,
        )
        missing_adoptions = _missing(
            requirement.required_adoption_refs,
            receipt.adoption_refs,
        )
        missing_sources = _missing(
            requirement.required_source_refs,
            receipt.source_refs,
        )
        if missing_claims:
            add(StageClosureFindingCode.CLAIM_BINDING_MISSING, missing_claims)
        if missing_applicability:
            add(
                StageClosureFindingCode.APPLICABILITY_BINDING_MISSING,
                missing_applicability,
            )
        if missing_adoptions:
            add(
                StageClosureFindingCode.ADOPTION_BINDING_MISSING,
                missing_adoptions,
            )
        if missing_sources:
            add(
                StageClosureFindingCode.SOURCE_BINDING_MISSING,
                missing_sources,
            )
        if missing_authorities:
            add(
                StageClosureFindingCode.AUTHORITY_BINDING_MISSING,
                missing_authorities,
            )

    if receipt.status is CheckStatus.FAIL:
        add(StageClosureFindingCode.CHECK_FAILED)
    elif receipt.status is CheckStatus.UNKNOWN:
        add(StageClosureFindingCode.CHECK_UNKNOWN)
    elif receipt.status is CheckStatus.NOT_APPLICABLE:
        if not requirement.allow_not_applicable:
            add(StageClosureFindingCode.NOT_APPLICABLE_FORBIDDEN)
    elif receipt.status is not CheckStatus.PASS:
        raise StageClosureError("unsupported check status")
    if receipt.revalidation_refs:
        add(StageClosureFindingCode.REVALIDATION_OPEN, receipt.revalidation_refs)
    return tuple(findings)


def compile_composite_stage_closure(
    profile: StageRequirementProfile,
    *,
    subject_digest: str,
    check_receipts: tuple[CheckReceiptEnvelope, ...],
) -> CompositeStageClosureReceipt:
    """Compile closure; no caller-supplied boolean can satisfy the stage."""

    if not isinstance(profile, StageRequirementProfile):
        raise TypeError("profile must be StageRequirementProfile")
    subject_digest = require_sha256(subject_digest, "subject_digest")
    if not isinstance(check_receipts, tuple):
        raise TypeError("check_receipts must be a tuple")
    if any(not isinstance(item, CheckReceiptEnvelope) for item in check_receipts):
        raise TypeError("check_receipts must contain CheckReceiptEnvelope")

    findings: list[StageClosureFinding] = []
    counts = Counter(item.check_id for item in check_receipts)
    expected = {item.requirement_id: item for item in profile.requirements}
    supplied_ids = set(counts)

    for check_id, count in sorted(counts.items()):
        if count > 1:
            findings.append(
                StageClosureFinding(
                    StageClosureFindingCode.DUPLICATE_CHECK,
                    requirement_id=check_id,
                )
            )
        if check_id not in expected:
            findings.append(
                StageClosureFinding(
                    StageClosureFindingCode.UNEXPECTED_CHECK,
                    requirement_id=check_id,
                )
            )
    for requirement_id in sorted(set(expected) - supplied_ids):
        findings.append(
            StageClosureFinding(
                StageClosureFindingCode.MISSING_CHECK,
                requirement_id=requirement_id,
            )
        )

    by_id: dict[str, CheckReceiptEnvelope] = {}
    for receipt in sorted(check_receipts, key=lambda item: item.receipt_digest):
        by_id.setdefault(receipt.check_id, receipt)
    for requirement_id, requirement in sorted(expected.items()):
        receipt = by_id.get(requirement_id)
        if receipt is None:
            continue
        findings.extend(
            _receipt_findings(
                requirement,
                receipt,
                profile=profile,
                subject_digest=subject_digest,
            )
        )

    ordered_findings = tuple(sorted(set(findings), key=lambda item: item.identity))
    return CompositeStageClosureReceipt(
        profile_id=profile.profile_id,
        profile_digest=profile.profile_digest,
        stage_id=profile.stage_id,
        branch=profile.branch,
        stage_subject_ref=profile.stage_subject_ref,
        subject_digest=subject_digest,
        check_receipt_digests=tuple(
            item.receipt_digest for item in check_receipts
        ),
        findings=ordered_findings,
        status=(
            StageClosureStatus.SATISFIED
            if not ordered_findings
            else StageClosureStatus.OPEN
        ),
    )


__all__ = [
    "CompositeStageClosureReceipt",
    "StageClosureError",
    "StageClosureFinding",
    "StageClosureFindingCode",
    "StageClosureStatus",
    "compile_composite_stage_closure",
]
