"""Common deterministic-check receipt envelope.

The envelope reports what was checked and which evidence chain was consumed.
It has no design, mutation, promotion, or canonical-write authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
    text,
)
from archive.archflow.evidence.applicability import ClaimApplicability
from archflow.project.refs import (
    BranchRef,
    require_exact_branch,
    require_same_branch,
)


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class FindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    UNKNOWN = "unknown"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class CheckFinding:
    code: str
    severity: FindingSeverity
    message: str
    subject_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.code, "finding code")
        if not isinstance(self.severity, FindingSeverity):
            raise TypeError("severity must be FindingSeverity")
        text(self.message, "finding message", maximum=4_000)
        deterministic_refs(self.subject_refs, "finding subject_refs")
        deterministic_refs(
            self.evidence_refs,
            "finding evidence_refs",
            allow_empty=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "subject_refs": list(self.subject_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CheckFinding":
        payload = exact_mapping(
            value,
            {
                "code",
                "severity",
                "message",
                "subject_refs",
                "evidence_refs",
            },
            "check finding",
        )
        for field in ("subject_refs", "evidence_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"finding {field} must be a list")
        return cls(
            code=payload["code"],
            severity=FindingSeverity(payload["severity"]),
            message=payload["message"],
            subject_refs=tuple(payload["subject_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class CheckMeasurement:
    measurement_id: str
    subject_ref: str
    name: str
    value: int | float | str | bool
    unit_ref: str | None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.measurement_id, "measurement_id")
        logical_ref(self.subject_ref, "measurement subject_ref")
        identifier(self.name, "measurement name")
        if not isinstance(self.value, (int, float, str, bool)):
            raise TypeError("measurement value has unsupported type")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("measurement value must be finite")
        if isinstance(self.value, str):
            text(self.value, "measurement value")
        if self.unit_ref is not None:
            logical_ref(self.unit_ref, "measurement unit_ref")
        deterministic_refs(
            self.evidence_refs,
            "measurement evidence_refs",
            allow_empty=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "measurement_id": self.measurement_id,
            "subject_ref": self.subject_ref,
            "name": self.name,
            "value": self.value,
            "unit_ref": self.unit_ref,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CheckMeasurement":
        payload = exact_mapping(
            value,
            {
                "measurement_id",
                "subject_ref",
                "name",
                "value",
                "unit_ref",
                "evidence_refs",
            },
            "check measurement",
        )
        if not isinstance(payload["evidence_refs"], list):
            raise TypeError("measurement evidence_refs must be a list")
        unit_ref = payload["unit_ref"]
        if unit_ref is not None and not isinstance(unit_ref, str):
            raise TypeError("measurement unit_ref must be text or None")
        return cls(
            measurement_id=payload["measurement_id"],
            subject_ref=payload["subject_ref"],
            name=payload["name"],
            value=payload["value"],
            unit_ref=unit_ref,
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class CheckReceiptEnvelope:
    check_id: str
    checker_id: str
    checker_version: str
    branch: BranchRef
    scope_digest: str
    subject_refs: tuple[str, ...]
    subject_digest: str
    status: CheckStatus
    claim_refs: tuple[str, ...] = ()
    applicability_refs: tuple[str, ...] = ()
    adoption_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    authority_refs: tuple[str, ...] = ()
    findings: tuple[CheckFinding, ...] = ()
    measurements: tuple[CheckMeasurement, ...] = ()
    coverage_denominator: tuple[str, ...] = ()
    covered_refs: tuple[str, ...] = ()
    revalidation_refs: tuple[str, ...] = ()

    SCHEMA = "CheckReceiptEnvelope@1"

    def __post_init__(self) -> None:
        identifier(self.check_id, "check_id")
        identifier(self.checker_id, "checker_id")
        text(self.checker_version, "checker_version", maximum=100)
        require_exact_branch(self.branch)
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        deterministic_refs(self.subject_refs, "subject_refs")
        object.__setattr__(
            self,
            "subject_digest",
            require_sha256(self.subject_digest, "subject_digest"),
        )
        if not isinstance(self.status, CheckStatus):
            raise TypeError("status must be CheckStatus")
        for field in (
            "claim_refs",
            "applicability_refs",
            "adoption_refs",
            "source_refs",
            "authority_refs",
            "revalidation_refs",
        ):
            deterministic_refs(
                getattr(self, field),
                field,
                allow_empty=True,
            )
        deterministic_refs(
            self.coverage_denominator,
            "coverage_denominator",
        )
        deterministic_refs(
            self.covered_refs,
            "covered_refs",
            allow_empty=True,
        )
        if not set(self.covered_refs) <= set(self.coverage_denominator):
            raise ValueError("covered_refs exceed the coverage denominator")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, CheckFinding) for item in self.findings
        ):
            raise TypeError("findings must be a CheckFinding tuple")
        finding_keys = [
            (item.code, item.subject_refs, item.message) for item in self.findings
        ]
        if len(finding_keys) != len(set(finding_keys)):
            raise ValueError("findings must be unique")
        if not isinstance(self.measurements, tuple) or any(
            not isinstance(item, CheckMeasurement)
            for item in self.measurements
        ):
            raise TypeError("measurements must be a CheckMeasurement tuple")
        measurement_ids = [item.measurement_id for item in self.measurements]
        if measurement_ids != sorted(set(measurement_ids)):
            raise ValueError("measurement ids must be sorted and unique")
        known_subjects = set(self.subject_refs) | set(self.coverage_denominator)
        if any(
            not set(item.subject_refs) <= known_subjects
            for item in self.findings
        ):
            raise ValueError("finding names a subject outside this check")
        if any(
            item.subject_ref not in known_subjects
            for item in self.measurements
        ):
            raise ValueError("measurement names a subject outside this check")
        if bool(self.claim_refs) != bool(self.applicability_refs):
            raise ValueError(
                "claim_refs and applicability_refs must be present together"
            )
        if self.claim_refs and (
            not self.adoption_refs
            or not self.source_refs
            or not self.authority_refs
        ):
            raise ValueError(
                "claim-backed check requires adoption, source, and authority refs"
            )
        errors = any(
            item.severity is FindingSeverity.ERROR for item in self.findings
        )
        unknowns = any(
            item.severity is FindingSeverity.UNKNOWN for item in self.findings
        )
        if self.status is CheckStatus.PASS:
            if errors or unknowns:
                raise ValueError("passing check cannot retain error/unknown findings")
            if self.covered_refs != self.coverage_denominator:
                raise ValueError("passing check must cover its exact denominator")
        elif self.status is CheckStatus.FAIL:
            if not errors:
                raise ValueError("failed check requires an error finding")
        elif self.status is CheckStatus.UNKNOWN:
            if errors or not unknowns:
                raise ValueError(
                    "unknown check requires unknown findings and no errors"
                )
        elif self.status is CheckStatus.NOT_APPLICABLE:
            if errors or unknowns:
                raise ValueError(
                    "not-applicable check cannot retain error/unknown findings"
                )
            if not self.applicability_refs:
                raise ValueError(
                    "not-applicable check requires an applicability record"
                )
            if self.covered_refs or self.measurements:
                raise ValueError(
                    "not-applicable check cannot claim coverage or measurements"
                )

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return f"{self.check_id}-{canonical_digest(self._body_dict())[:20]}"

    def require_applicabilities(
        self,
        applicability: tuple[ClaimApplicability, ...],
    ) -> None:
        if not isinstance(applicability, tuple) or any(
            not isinstance(item, ClaimApplicability) for item in applicability
        ):
            raise TypeError("applicability must be a ClaimApplicability tuple")
        for item in applicability:
            require_same_branch(
                self.branch,
                item.branch,
                field="applicability branch",
            )
            if item.scope_digest != self.scope_digest:
                raise ValueError("applicability crossed the check scope")
        if tuple(sorted(item.ref for item in applicability)) != self.applicability_refs:
            raise ValueError("check applicability refs do not match supplied records")
        if tuple(sorted(item.claim_ref for item in applicability)) != self.claim_refs:
            raise ValueError("check claim refs do not match supplied applicability")
        if not set(
            ref for item in applicability for ref in item.authority_refs
        ) <= set(self.authority_refs):
            raise ValueError("check omitted applicability authority refs")
        if not set(
            ref for item in applicability for ref in item.source_refs
        ) <= set(self.source_refs):
            raise ValueError("check omitted applicability source refs")

    def _body_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "check_id": self.check_id,
            "checker_id": self.checker_id,
            "checker_version": self.checker_version,
            "branch": self.branch.to_dict(),
            "scope_digest": self.scope_digest,
            "subject_refs": list(self.subject_refs),
            "subject_digest": self.subject_digest,
            "status": self.status.value,
            "claim_refs": list(self.claim_refs),
            "applicability_refs": list(self.applicability_refs),
            "adoption_refs": list(self.adoption_refs),
            "source_refs": list(self.source_refs),
            "authority_refs": list(self.authority_refs),
            "findings": [item.to_dict() for item in self.findings],
            "measurements": [item.to_dict() for item in self.measurements],
            "coverage_denominator": list(self.coverage_denominator),
            "covered_refs": list(self.covered_refs),
            "revalidation_refs": list(self.revalidation_refs),
            "design_authority": False,
            "geometry_mutation_authority": False,
            "promotion_authority": False,
            "canonical_write_authority": False,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._body_dict(), "receipt_id": self.receipt_id}

    @classmethod
    def from_dict(cls, value: object) -> "CheckReceiptEnvelope":
        payload = exact_mapping(
            value,
            {
                "schema",
                "receipt_id",
                "check_id",
                "checker_id",
                "checker_version",
                "branch",
                "scope_digest",
                "subject_refs",
                "subject_digest",
                "status",
                "claim_refs",
                "applicability_refs",
                "adoption_refs",
                "source_refs",
                "authority_refs",
                "findings",
                "measurements",
                "coverage_denominator",
                "covered_refs",
                "revalidation_refs",
                "design_authority",
                "geometry_mutation_authority",
                "promotion_authority",
                "canonical_write_authority",
            },
            "check receipt envelope",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("check receipt schema or authority drifted")
        list_fields = (
            "subject_refs",
            "claim_refs",
            "applicability_refs",
            "adoption_refs",
            "source_refs",
            "authority_refs",
            "findings",
            "measurements",
            "coverage_denominator",
            "covered_refs",
            "revalidation_refs",
        )
        for field in list_fields:
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            check_id=payload["check_id"],
            checker_id=payload["checker_id"],
            checker_version=payload["checker_version"],
            branch=BranchRef.from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            subject_refs=tuple(payload["subject_refs"]),
            subject_digest=payload["subject_digest"],
            status=CheckStatus(payload["status"]),
            claim_refs=tuple(payload["claim_refs"]),
            applicability_refs=tuple(payload["applicability_refs"]),
            adoption_refs=tuple(payload["adoption_refs"]),
            source_refs=tuple(payload["source_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
            findings=tuple(
                CheckFinding.from_dict(item) for item in payload["findings"]
            ),
            measurements=tuple(
                CheckMeasurement.from_dict(item)
                for item in payload["measurements"]
            ),
            coverage_denominator=tuple(payload["coverage_denominator"]),
            covered_refs=tuple(payload["covered_refs"]),
            revalidation_refs=tuple(payload["revalidation_refs"]),
        )
        if payload["receipt_id"] != result.receipt_id:
            raise ValueError("check receipt identity drifted")
        return result


__all__ = [
    "CheckFinding",
    "CheckMeasurement",
    "CheckReceiptEnvelope",
    "CheckStatus",
    "FindingSeverity",
]
