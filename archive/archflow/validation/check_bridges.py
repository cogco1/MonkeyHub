"""Requirement-first bridges from legacy typed validators to check receipts.

The source receipts predate branch- and scope-bound check envelopes.  A bridge
therefore requires an immutable profile that supplies that missing context and
binds the exact source input before validation.  Loose branch/scope arguments
are intentionally unsupported: the bridge never invents provenance, changes
the declared denominator, or trusts a source summary boolean.
"""

from __future__ import annotations

from dataclasses import dataclass

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier, logical_ref
from archflow.project.refs import BranchRef
from archflow.state.geometry_program import digest_value
from archive.archflow.validation.component_lineage import (
    StageComponentCoverageReceipt,
    StageOperation,
    StageOperationRef,
)
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from archive.archflow.validation.spatial import (
    SpatialCheckStatus,
    SpatialValidationReceipt,
)


_MAX_REFS = 4_096


class CheckReceiptBridgeError(ValueError):
    """A source receipt cannot be bound to its requirement-first profile."""


def _sorted_refs(values: object, field: str) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or not values
        or len(values) > _MAX_REFS
    ):
        raise CheckReceiptBridgeError(
            f"{field} must be a non-empty bounded tuple"
        )
    normalized = tuple(logical_ref(item, field) for item in values)
    if len(normalized) != len(set(normalized)):
        raise CheckReceiptBridgeError(f"{field} contains duplicates")
    return tuple(sorted(normalized))


def _stage_operation_from_dict(value: object) -> StageOperation:
    payload = exact_mapping(
        value,
        {"schema", "component_id", "operation_id", "fingerprint"},
        "stage operation",
    )
    if payload["schema"] != StageOperation.SCHEMA:
        raise CheckReceiptBridgeError("unsupported stage operation schema")
    return StageOperation(
        ref=StageOperationRef(
            component_id=payload["component_id"],
            operation_id=payload["operation_id"],
        ),
        fingerprint=payload["fingerprint"],
    )


@dataclass(frozen=True, slots=True)
class ComponentLineageCheckProfile:
    """Exact branch context and predecessor input for lineage conversion."""

    profile_id: str
    branch: BranchRef
    scope_digest: str
    predecessor_stage_id: str
    successor_stage_id: str
    predecessor_operations: tuple[StageOperation, ...]
    denominator_refs: tuple[str, ...]

    SCHEMA = "ComponentLineageCheckProfile@1"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        require_exact_branch(self.branch)
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        identifier(self.predecessor_stage_id, "predecessor_stage_id")
        identifier(self.successor_stage_id, "successor_stage_id")
        if (
            not isinstance(self.predecessor_operations, tuple)
            or not self.predecessor_operations
            or len(self.predecessor_operations) > _MAX_REFS
        ):
            raise CheckReceiptBridgeError(
                "predecessor_operations must be a non-empty bounded tuple"
            )
        if any(
            not isinstance(item, StageOperation)
            for item in self.predecessor_operations
        ):
            raise TypeError("predecessor_operations must contain StageOperation")
        ordered = tuple(
            sorted(
                self.predecessor_operations,
                key=lambda item: item.identity,
            )
        )
        identities = tuple(item.identity for item in ordered)
        if len(identities) != len(set(identities)):
            raise CheckReceiptBridgeError(
                "predecessor_operations contains duplicate identities"
            )
        object.__setattr__(self, "predecessor_operations", ordered)
        object.__setattr__(
            self,
            "denominator_refs",
            _sorted_refs(self.denominator_refs, "denominator_refs"),
        )

    @property
    def predecessor_denominator_digest(self) -> str:
        return digest_value(
            [item.to_dict() for item in self.predecessor_operations]
        )

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def check_id(self) -> str:
        return f"component-lineage-{self.profile_digest[:24]}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "predecessor_stage_id": self.predecessor_stage_id,
            "successor_stage_id": self.successor_stage_id,
            "predecessor_operations": [
                item.to_dict() for item in self.predecessor_operations
            ],
            "predecessor_denominator_digest": (
                self.predecessor_denominator_digest
            ),
            "denominator_refs": list(self.denominator_refs),
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentLineageCheckProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "branch",
                "scope_digest",
                "predecessor_stage_id",
                "successor_stage_id",
                "predecessor_operations",
                "predecessor_denominator_digest",
                "denominator_refs",
                "design_authority",
                "canonical_write_authority",
            },
            "component lineage check profile",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CheckReceiptBridgeError(
                "unsupported component lineage profile schema"
            )
        if not isinstance(payload["predecessor_operations"], list):
            raise TypeError("predecessor_operations must be a list")
        if not isinstance(payload["denominator_refs"], list):
            raise TypeError("denominator_refs must be a list")
        result = cls(
            profile_id=payload["profile_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            predecessor_stage_id=payload["predecessor_stage_id"],
            successor_stage_id=payload["successor_stage_id"],
            predecessor_operations=tuple(
                _stage_operation_from_dict(item)
                for item in payload["predecessor_operations"]
            ),
            denominator_refs=tuple(payload["denominator_refs"]),
        )
        if (
            payload["predecessor_denominator_digest"]
            != result.predecessor_denominator_digest
        ):
            raise CheckReceiptBridgeError(
                "component lineage predecessor digest drifted"
            )
        return result


@dataclass(frozen=True, slots=True)
class SpatialLayoutCheckProfile:
    """Exact branch context and normalized spatial-validator input digest."""

    profile_id: str
    branch: BranchRef
    scope_digest: str
    stage_id: str
    input_digest: str
    denominator_refs: tuple[str, ...]

    SCHEMA = "SpatialLayoutCheckProfile@1"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        require_exact_branch(self.branch)
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "input_digest",
            require_sha256(self.input_digest, "input_digest"),
        )
        object.__setattr__(
            self,
            "denominator_refs",
            _sorted_refs(self.denominator_refs, "denominator_refs"),
        )

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def check_id(self) -> str:
        return f"spatial-layout-{self.profile_digest[:24]}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_id": self.stage_id,
            "input_digest": self.input_digest,
            "denominator_refs": list(self.denominator_refs),
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SpatialLayoutCheckProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "branch",
                "scope_digest",
                "stage_id",
                "input_digest",
                "denominator_refs",
                "design_authority",
                "canonical_write_authority",
            },
            "spatial layout check profile",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CheckReceiptBridgeError(
                "unsupported spatial layout profile schema"
            )
        if not isinstance(payload["denominator_refs"], list):
            raise TypeError("denominator_refs must be a list")
        return cls(
            profile_id=payload["profile_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_id=payload["stage_id"],
            input_digest=payload["input_digest"],
            denominator_refs=tuple(payload["denominator_refs"]),
        )


def _finding(code: str, message: str, subject_ref: str) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=FindingSeverity.ERROR,
        message=message,
        subject_refs=(subject_ref,),
    )


def bridge_component_lineage_receipt(
    profile: ComponentLineageCheckProfile,
    source_receipt: StageComponentCoverageReceipt,
    *,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Convert exact operation coverage without rerunning its validator."""

    if not isinstance(profile, ComponentLineageCheckProfile):
        raise TypeError(
            "source receipt has no branch/scope; an exact "
            "ComponentLineageCheckProfile is required"
        )
    if not isinstance(source_receipt, StageComponentCoverageReceipt):
        raise TypeError("source_receipt must be StageComponentCoverageReceipt")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )
    anchor = profile.denominator_refs[0]
    findings: list[CheckFinding] = []
    stages_exact = (
        source_receipt.predecessor_stage_id
        == profile.predecessor_stage_id
        and source_receipt.successor_stage_id == profile.successor_stage_id
    )
    predecessor_exact = (
        source_receipt.predecessor_operations
        == profile.predecessor_operations
        and source_receipt.predecessor_denominator_digest
        == profile.predecessor_denominator_digest
    )
    if not stages_exact:
        findings.append(
            _finding(
                "source-stage-mismatch",
                "component-lineage receipt does not match profile stage ids",
                anchor,
            )
        )
    if not predecessor_exact:
        findings.append(
            _finding(
                "source-denominator-mismatch",
                "component-lineage receipt changed the exact predecessor input",
                anchor,
            )
        )
    blocking = tuple(
        item for item in source_receipt.operation_coverage if item.blocking
    )
    for item in blocking:
        component_id, operation_id = item.identity
        findings.append(
            _finding(
                "blocking-lineage-operation",
                "component-lineage operation remains blocking: "
                f"{component_id}/{operation_id}",
                anchor,
            )
        )
    findings_tuple = tuple(
        sorted(
            findings,
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )
    status = CheckStatus.FAIL if findings_tuple else CheckStatus.PASS
    covered_refs = (
        profile.denominator_refs
        if stages_exact and predecessor_exact
        else ()
    )
    return CheckReceiptEnvelope(
        check_id=profile.check_id,
        checker_id="component-lineage-validator",
        checker_version="1.0.0",
        branch=profile.branch,
        scope_digest=profile.scope_digest,
        subject_refs=profile.denominator_refs,
        subject_digest=stage_subject_digest,
        status=status,
        findings=findings_tuple,
        measurements=(
            CheckMeasurement(
                measurement_id="blocking-operation-count",
                subject_ref=anchor,
                name="blocking-operation-count",
                value=len(blocking),
                unit_ref="unit:count",
            ),
            CheckMeasurement(
                measurement_id="operation-count",
                subject_ref=anchor,
                name="operation-count",
                value=source_receipt.operation_count,
                unit_ref="unit:count",
            ),
            CheckMeasurement(
                measurement_id="original-receipt-digest",
                subject_ref=anchor,
                name="original-receipt-digest",
                value=source_receipt.receipt_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="predecessor-denominator-digest",
                subject_ref=anchor,
                name="predecessor-denominator-digest",
                value=source_receipt.predecessor_denominator_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="profile-digest",
                subject_ref=anchor,
                name="profile-digest",
                value=profile.profile_digest,
                unit_ref=None,
            ),
        ),
        coverage_denominator=profile.denominator_refs,
        covered_refs=covered_refs,
    )


def bridge_spatial_validation_receipt(
    profile: SpatialLayoutCheckProfile,
    source_receipt: SpatialValidationReceipt,
    *,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Convert individual spatial checks without trusting summary fields."""

    if not isinstance(profile, SpatialLayoutCheckProfile):
        raise TypeError(
            "source receipt has no branch/scope; an exact "
            "SpatialLayoutCheckProfile is required"
        )
    if not isinstance(source_receipt, SpatialValidationReceipt):
        raise TypeError("source_receipt must be SpatialValidationReceipt")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )
    anchor = profile.denominator_refs[0]
    findings: list[CheckFinding] = []
    input_exact = source_receipt.input_digest == profile.input_digest
    if not input_exact:
        findings.append(
            _finding(
                "source-input-mismatch",
                "spatial receipt does not match the profile input digest",
                anchor,
            )
        )
    failed_checks = tuple(
        item
        for item in source_receipt.checks
        if item.status is SpatialCheckStatus.FAILED
    )
    for item in failed_checks:
        findings.append(
            _finding(
                "spatial-check-failed",
                "spatial source check failed: "
                f"{item.kind.value}/{item.check_id}",
                anchor,
            )
        )
    findings_tuple = tuple(
        sorted(
            findings,
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )
    status = CheckStatus.FAIL if findings_tuple else CheckStatus.PASS
    covered_refs = profile.denominator_refs if input_exact else ()
    return CheckReceiptEnvelope(
        check_id=profile.check_id,
        checker_id="spatial-layout-validator",
        checker_version="1.0.0",
        branch=profile.branch,
        scope_digest=profile.scope_digest,
        subject_refs=profile.denominator_refs,
        subject_digest=stage_subject_digest,
        status=status,
        findings=findings_tuple,
        measurements=(
            CheckMeasurement(
                measurement_id="failed-check-count",
                subject_ref=anchor,
                name="failed-check-count",
                value=len(failed_checks),
                unit_ref="unit:count",
            ),
            CheckMeasurement(
                measurement_id="original-input-digest",
                subject_ref=anchor,
                name="original-input-digest",
                value=source_receipt.input_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="original-receipt-digest",
                subject_ref=anchor,
                name="original-receipt-digest",
                value=source_receipt.receipt_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="profile-digest",
                subject_ref=anchor,
                name="profile-digest",
                value=profile.profile_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="source-check-count",
                subject_ref=anchor,
                name="source-check-count",
                value=len(source_receipt.checks),
                unit_ref="unit:count",
            ),
        ),
        coverage_denominator=profile.denominator_refs,
        covered_refs=covered_refs,
    )


__all__ = [
    "CheckReceiptBridgeError",
    "ComponentLineageCheckProfile",
    "SpatialLayoutCheckProfile",
    "bridge_component_lineage_receipt",
    "bridge_spatial_validation_receipt",
]
