"""Composite stage closure compiled from exact typed check receipts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.control.requirements import (
    RequirementBasisMode,
    StageCheckRequirement,
    StageRequirementProfile,
    _branch_dict,
    _branch_from_dict,
    _mapping,
    _text,
)
from archflow.project.refs import BranchRef
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus


class StageClosureError(ValueError):
    """A composite closure request or retained receipt is malformed."""


class StageClosureStatus(StrEnum):
    OPEN = "OPEN"
    SATISFIED = "SATISFIED"


class StageClosureFindingCode(StrEnum):
    MISSING_CHECK = "missing_check"
    DUPLICATE_CHECK = "duplicate_check"
    UNEXPECTED_CHECK = "unexpected_check"
    CHECKER_MISMATCH = "checker_mismatch"
    BRANCH_MISMATCH = "branch_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    SUBJECT_DIGEST_MISMATCH = "subject_digest_mismatch"
    DENOMINATOR_MISMATCH = "denominator_mismatch"
    CLAIM_BINDING_MISSING = "claim_binding_missing"
    APPLICABILITY_BINDING_MISSING = "applicability_binding_missing"
    ADOPTION_BINDING_MISSING = "adoption_binding_missing"
    SOURCE_BINDING_MISSING = "source_binding_missing"
    AUTHORITY_BINDING_MISSING = "authority_binding_missing"
    UNIVERSAL_BASIS_CONTAMINATED = "universal_basis_contaminated"
    CHECK_FAILED = "check_failed"
    CHECK_UNKNOWN = "check_unknown"
    NOT_APPLICABLE_FORBIDDEN = "not_applicable_forbidden"
    REVALIDATION_OPEN = "revalidation_open"


@dataclass(frozen=True, slots=True)
class StageClosureFinding:
    code: StageClosureFindingCode
    requirement_id: str | None = None
    receipt_id: str | None = None
    refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, StageClosureFindingCode):
            raise TypeError("code must be StageClosureFindingCode")
        if self.requirement_id is not None:
            _text(self.requirement_id, "requirement_id")
        if self.receipt_id is not None:
            _text(self.receipt_id, "receipt_id")
        if not isinstance(self.refs, tuple):
            raise TypeError("refs must be a tuple")
        normalized = tuple(sorted(_text(item, "finding ref") for item in self.refs))
        if len(normalized) != len(set(normalized)):
            raise StageClosureError("finding refs contain duplicates")
        object.__setattr__(self, "refs", normalized)

    @property
    def identity(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            self.code.value,
            self.requirement_id or "",
            self.receipt_id or "",
            self.refs,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "requirement_id": self.requirement_id,
            "receipt_id": self.receipt_id,
            "refs": list(self.refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageClosureFinding":
        payload = _mapping(value, "finding")
        if set(payload) != {"code", "requirement_id", "receipt_id", "refs"}:
            raise StageClosureError("stage closure finding schema drifted")
        raw_refs = payload.get("refs", [])
        if not isinstance(raw_refs, list):
            raise TypeError("finding refs must be a list")
        return cls(
            code=StageClosureFindingCode(payload.get("code")),
            requirement_id=payload.get("requirement_id"),
            receipt_id=payload.get("receipt_id"),
            refs=tuple(raw_refs),
        )


@dataclass(frozen=True, slots=True)
class CompositeStageClosureReceipt:
    profile_id: str
    profile_digest: str
    stage_id: str
    branch: BranchRef
    stage_subject_ref: str
    subject_digest: str
    check_receipt_digests: tuple[str, ...]
    findings: tuple[StageClosureFinding, ...]
    status: StageClosureStatus

    SCHEMA = "CompositeStageClosureReceipt@1"

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        object.__setattr__(
            self,
            "profile_digest",
            require_sha256(self.profile_digest, "profile_digest"),
        )
        _text(self.stage_id, "stage_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        _text(self.stage_subject_ref, "stage_subject_ref")
        object.__setattr__(
            self,
            "subject_digest",
            require_sha256(self.subject_digest, "subject_digest"),
        )
        if not isinstance(self.check_receipt_digests, tuple):
            raise TypeError("check_receipt_digests must be a tuple")
        digests = tuple(
            sorted(
                require_sha256(item, "check_receipt_digest")
                for item in self.check_receipt_digests
            )
        )
        if len(digests) != len(set(digests)):
            raise StageClosureError("check receipt digests contain duplicates")
        object.__setattr__(self, "check_receipt_digests", digests)
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, StageClosureFinding) for item in self.findings
        ):
            raise TypeError("findings must contain StageClosureFinding")
        ordered = tuple(sorted(self.findings, key=lambda item: item.identity))
        if len(ordered) != len(set(item.identity for item in ordered)):
            raise StageClosureError("findings contain duplicates")
        object.__setattr__(self, "findings", ordered)
        if not isinstance(self.status, StageClosureStatus):
            raise TypeError("status must be StageClosureStatus")
        expected = (
            StageClosureStatus.SATISFIED
            if not self.findings
            else StageClosureStatus.OPEN
        )
        if self.status is not expected:
            raise StageClosureError("status disagrees with closure findings")

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "stage_id": self.stage_id,
            "branch": _branch_dict(self.branch),
            "stage_subject_ref": self.stage_subject_ref,
            "subject_digest": self.subject_digest,
            "check_receipt_digests": list(self.check_receipt_digests),
            "findings": [item.to_dict() for item in self.findings],
            "status": self.status.value,
            "stage_acceptance_authority": False,
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def receipt_id(self) -> str:
        return f"composite-stage-closure-{self.receipt_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CompositeStageClosureReceipt":
        payload = _mapping(value, "stage closure receipt")
        expected = {
            "schema",
            "profile_id",
            "profile_digest",
            "stage_id",
            "branch",
            "stage_subject_ref",
            "subject_digest",
            "check_receipt_digests",
            "findings",
            "status",
            "stage_acceptance_authority",
            "design_authority",
            "canonical_write_authority",
            "receipt_id",
            "receipt_digest",
        }
        if set(payload) != expected or payload.get("schema") != cls.SCHEMA:
            raise StageClosureError("unsupported stage closure schema")
        raw_digests = payload.get("check_receipt_digests")
        raw_findings = payload.get("findings")
        if not isinstance(raw_digests, list):
            raise TypeError("check_receipt_digests must be a list")
        if not isinstance(raw_findings, list):
            raise TypeError("findings must be a list")
        receipt = cls(
            profile_id=_text(payload.get("profile_id"), "profile_id"),
            profile_digest=require_sha256(
                payload.get("profile_digest"), "profile_digest"
            ),
            stage_id=_text(payload.get("stage_id"), "stage_id"),
            branch=_branch_from_dict(payload.get("branch")),
            stage_subject_ref=_text(
                payload.get("stage_subject_ref"), "stage_subject_ref"
            ),
            subject_digest=require_sha256(
                payload.get("subject_digest"), "subject_digest"
            ),
            check_receipt_digests=tuple(raw_digests),
            findings=tuple(
                StageClosureFinding.from_dict(item) for item in raw_findings
            ),
            status=StageClosureStatus(payload.get("status")),
        )
        if payload.get("receipt_id") != receipt.receipt_id:
            raise StageClosureError("stage closure receipt identity changed")
        if payload.get("receipt_digest") != receipt.receipt_digest:
            raise StageClosureError("stage closure receipt digest changed")
        return receipt


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
