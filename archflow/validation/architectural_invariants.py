"""Project-neutral architectural invariant checks with claim-bound criteria.

The module owns deterministic mechanics only.  Counts, partitions, expected
angles, symmetry orders, tolerances, and level separations are supplied by a
caller-owned claim-bound profile.  Missing observations remain ``UNKNOWN``;
no checker infers a building typology or accepts a stage.

Each public checker returns ``CheckReceiptEnvelope`` directly.  A controller
can therefore bind ``profile.checker_requirement_refs`` into an exact
``StageCheckRequirement`` without an untyped adapter or caller boolean.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, Iterable

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    finite_number,
    identifier,
    logical_ref,
)
from archflow.project.refs import BranchRef
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "promotion_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "geometry_mutation_authority": False,
    "canonical_write_authority": False,
}


class ArchitecturalInvariantError(ValueError):
    """A profile, requirement, or observation is malformed."""


def _require_no_authority(payload: dict[str, object], field: str) -> None:
    if any(payload.get(key) is not value for key, value in _AUTHORITY_FIELDS.items()):
        raise ArchitecturalInvariantError(f"{field} authority flags changed")


def _non_negative_number(value: object, field: str) -> float:
    result = float(finite_number(value, field))
    if result < 0.0:
        raise ArchitecturalInvariantError(f"{field} must be non-negative")
    return result


def _positive_number(value: object, field: str) -> float:
    result = float(finite_number(value, field))
    if result <= 0.0:
        raise ArchitecturalInvariantError(f"{field} must be positive")
    return result


def _non_negative_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ArchitecturalInvariantError(f"{field} must be a non-negative integer")
    return value


def _positive_integer(value: object, field: str) -> int:
    result = _non_negative_integer(value, field)
    if result == 0:
        raise ArchitecturalInvariantError(f"{field} must be positive")
    return result


def _optional_vector2(
    value: object,
    field: str,
    *,
    non_zero: bool,
) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        raise ArchitecturalInvariantError(f"{field} must be a two-number tuple or None")
    result = (
        float(finite_number(value[0], field)),
        float(finite_number(value[1], field)),
    )
    if non_zero and math.hypot(*result) == 0.0:
        raise ArchitecturalInvariantError(f"{field} must be non-zero")
    return result


def _vector_to_json(value: tuple[float, float] | None) -> list[float] | None:
    return None if value is None else list(value)


def _vector_from_json(
    value: object,
    field: str,
    *,
    non_zero: bool,
) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list or None")
    return _optional_vector2(tuple(value), field, non_zero=non_zero)


def _evidence_union(values: Iterable[tuple[str, ...]]) -> tuple[str, ...]:
    return tuple(sorted({ref for refs in values for ref in refs}))


@dataclass(frozen=True, slots=True)
class ClaimBoundValidationBasis:
    """Exact StageCheckRequirement basis refs supplied by the project."""

    claim_refs: tuple[str, ...]
    applicability_refs: tuple[str, ...]
    adoption_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "ClaimBoundValidationBasis@1"

    def __post_init__(self) -> None:
        for field in (
            "claim_refs",
            "applicability_refs",
            "adoption_refs",
            "source_refs",
            "authority_refs",
        ):
            object.__setattr__(self, field, deterministic_refs(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "basis_mode": "claim_bound",
            "claim_refs": list(self.claim_refs),
            "applicability_refs": list(self.applicability_refs),
            "adoption_refs": list(self.adoption_refs),
            "source_refs": list(self.source_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ClaimBoundValidationBasis":
        payload = exact_mapping(
            value,
            {
                "schema",
                "basis_mode",
                "claim_refs",
                "applicability_refs",
                "adoption_refs",
                "source_refs",
                "authority_refs",
                *_AUTHORITY_FIELDS,
            },
            "claim-bound validation basis",
        )
        _require_no_authority(payload, "claim-bound validation basis")
        if payload["schema"] != cls.SCHEMA or payload["basis_mode"] != "claim_bound":
            raise ArchitecturalInvariantError("claim-bound validation basis drifted")
        for field in (
            "claim_refs",
            "applicability_refs",
            "adoption_refs",
            "source_refs",
            "authority_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            claim_refs=tuple(payload["claim_refs"]),
            applicability_refs=tuple(payload["applicability_refs"]),
            adoption_refs=tuple(payload["adoption_refs"]),
            source_refs=tuple(payload["source_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )
        if result.to_dict() != payload:
            raise ArchitecturalInvariantError("claim-bound validation basis identity changed")
        return result


@dataclass(frozen=True, slots=True)
class _RequirementOutcome:
    findings: tuple[CheckFinding, ...]
    measurements: tuple[CheckMeasurement, ...]


def _finding(
    requirement_ref: str,
    code: str,
    severity: FindingSeverity,
    message: str,
    evidence_refs: tuple[str, ...] = (),
) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=severity,
        message=message,
        subject_refs=(requirement_ref,),
        evidence_refs=evidence_refs,
    )


def _measurement(
    requirement_ref: str,
    name: str,
    value: int | float | str | bool,
    *,
    unit_ref: str | None,
    evidence_refs: tuple[str, ...] = (),
) -> CheckMeasurement:
    return CheckMeasurement(
        measurement_id=(
            f"invariant-{canonical_digest({'requirement_ref': requirement_ref, 'name': name})[:24]}"
        ),
        subject_ref=requirement_ref,
        name=name,
        value=value,
        unit_ref=unit_ref,
        evidence_refs=evidence_refs,
    )


def _check_receipt(
    *,
    check_id: str,
    checker_id: str,
    basis: ClaimBoundValidationBasis,
    denominator_refs: tuple[str, ...],
    outcomes: tuple[_RequirementOutcome, ...],
    observation_evidence_refs: tuple[str, ...],
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    identifier(check_id, "check_id")
    identifier(checker_id, "checker_id")
    denominator_refs = deterministic_refs(denominator_refs, "denominator_refs")
    scope_digest = require_sha256(scope_digest, "scope_digest")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )
    findings = tuple(
        sorted(
            (item for outcome in outcomes for item in outcome.findings),
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )
    measurements = tuple(
        sorted(
            (item for outcome in outcomes for item in outcome.measurements),
            key=lambda item: item.measurement_id,
        )
    )
    if any(item.severity is FindingSeverity.ERROR for item in findings):
        status = CheckStatus.FAIL
    elif any(item.severity is FindingSeverity.UNKNOWN for item in findings):
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    return CheckReceiptEnvelope(
        check_id=check_id,
        checker_id=checker_id,
        checker_version="1.0.0",
        branch=branch,
        scope_digest=scope_digest,
        subject_refs=denominator_refs,
        subject_digest=stage_subject_digest,
        status=status,
        claim_refs=basis.claim_refs,
        applicability_refs=basis.applicability_refs,
        adoption_refs=basis.adoption_refs,
        source_refs=tuple(sorted(set(basis.source_refs) | set(observation_evidence_refs))),
        authority_refs=basis.authority_refs,
        findings=findings,
        measurements=measurements,
        coverage_denominator=denominator_refs,
        # Stage closure interprets coverage independently from PASS/FAIL/UNKNOWN.
        # Every denominator member was evaluated even when evidence stayed unknown.
        covered_refs=denominator_refs,
    )


@dataclass(frozen=True, slots=True)
class PartitionExpectation:
    partition_ref: str
    expected_count: int

    SCHEMA: ClassVar[str] = "PartitionExpectation@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "partition_ref",
            logical_ref(self.partition_ref, "partition_ref"),
        )
        object.__setattr__(
            self,
            "expected_count",
            _non_negative_integer(self.expected_count, "expected_count"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "partition_ref": self.partition_ref,
            "expected_count": self.expected_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> "PartitionExpectation":
        payload = exact_mapping(
            value,
            {"schema", "partition_ref", "expected_count"},
            "partition expectation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalInvariantError("partition expectation schema drifted")
        return cls(
            partition_ref=payload["partition_ref"],
            expected_count=payload["expected_count"],
        )


@dataclass(frozen=True, slots=True)
class ComponentPartitionObservation:
    component_ref: str
    partition_refs: tuple[str, ...] | None
    evidence_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "ComponentPartitionObservation@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_ref",
            logical_ref(self.component_ref, "component_ref"),
        )
        if self.partition_refs is not None:
            object.__setattr__(
                self,
                "partition_refs",
                deterministic_refs(
                    self.partition_refs,
                    "observation partition_refs",
                    allow_empty=True,
                ),
            )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "observation evidence_refs"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_ref": self.component_ref,
            "partition_refs": (
                None if self.partition_refs is None else list(self.partition_refs)
            ),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentPartitionObservation":
        payload = exact_mapping(
            value,
            {"schema", "component_ref", "partition_refs", "evidence_refs"},
            "component partition observation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalInvariantError(
                "component partition observation schema drifted"
            )
        partition_refs = payload["partition_refs"]
        if partition_refs is not None and not isinstance(partition_refs, list):
            raise TypeError("partition_refs must be a list or None")
        if not isinstance(payload["evidence_refs"], list):
            raise TypeError("evidence_refs must be a list")
        return cls(
            component_ref=payload["component_ref"],
            partition_refs=(
                None if partition_refs is None else tuple(partition_refs)
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class ComponentCardinalityPartitionRequirement:
    requirement_id: str
    member_refs: tuple[str, ...]
    expected_total: int
    partitions: tuple[PartitionExpectation, ...]

    SCHEMA: ClassVar[str] = "ComponentCardinalityPartitionRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.requirement_id, "requirement_id")
        object.__setattr__(
            self,
            "member_refs",
            deterministic_refs(self.member_refs, "member_refs", allow_empty=True),
        )
        object.__setattr__(
            self,
            "expected_total",
            _non_negative_integer(self.expected_total, "expected_total"),
        )
        if not isinstance(self.partitions, tuple) or not self.partitions or any(
            not isinstance(item, PartitionExpectation) for item in self.partitions
        ):
            raise TypeError("partitions must contain PartitionExpectation values")
        partitions = tuple(sorted(self.partitions, key=lambda item: item.partition_ref))
        refs = tuple(item.partition_ref for item in partitions)
        if len(refs) != len(set(refs)):
            raise ArchitecturalInvariantError("partition refs must be unique")
        if sum(item.expected_count for item in partitions) != self.expected_total:
            raise ArchitecturalInvariantError(
                "partition expected counts must sum to expected_total"
            )
        object.__setattr__(self, "partitions", partitions)

    @property
    def ref(self) -> str:
        return f"component-cardinality-partition:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "member_refs": list(self.member_refs),
            "expected_total": self.expected_total,
            "partitions": [item.to_dict() for item in self.partitions],
            "exclusive_partition_required": True,
            "project_supplied_values": True,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> "ComponentCardinalityPartitionRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "requirement_id",
                "member_refs",
                "expected_total",
                "partitions",
                "exclusive_partition_required",
                "project_supplied_values",
            },
            "component cardinality partition requirement",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["exclusive_partition_required"] is not True
            or payload["project_supplied_values"] is not True
        ):
            raise ArchitecturalInvariantError(
                "component cardinality partition requirement drifted"
            )
        if not isinstance(payload["member_refs"], list) or not isinstance(
            payload["partitions"], list
        ):
            raise TypeError("requirement members and partitions must be lists")
        return cls(
            requirement_id=payload["requirement_id"],
            member_refs=tuple(payload["member_refs"]),
            expected_total=payload["expected_total"],
            partitions=tuple(
                PartitionExpectation.from_dict(item) for item in payload["partitions"]
            ),
        )


@dataclass(frozen=True, slots=True)
class ComponentCardinalityPartitionProfile:
    profile_id: str
    check_id: str
    basis: ClaimBoundValidationBasis
    requirements: tuple[ComponentCardinalityPartitionRequirement, ...]
    observations: tuple[ComponentPartitionObservation, ...]

    SCHEMA: ClassVar[str] = "ComponentCardinalityPartitionProfile@1"
    CHECKER_ID: ClassVar[str] = "component-cardinality-partition-checker"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        identifier(self.check_id, "check_id")
        if not isinstance(self.basis, ClaimBoundValidationBasis):
            raise TypeError("basis must be ClaimBoundValidationBasis")
        if not isinstance(self.requirements, tuple) or not self.requirements or any(
            not isinstance(item, ComponentCardinalityPartitionRequirement)
            for item in self.requirements
        ):
            raise TypeError(
                "requirements must contain ComponentCardinalityPartitionRequirement values"
            )
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, ComponentPartitionObservation)
            for item in self.observations
        ):
            raise TypeError(
                "observations must contain ComponentPartitionObservation values"
            )
        requirements = tuple(
            sorted(self.requirements, key=lambda item: item.requirement_id)
        )
        observations = tuple(
            sorted(self.observations, key=lambda item: item.component_ref)
        )
        if len({item.requirement_id for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement IDs must be unique")
        if len({item.ref for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement refs must be unique")
        if len({item.component_ref for item in observations}) != len(observations):
            raise ArchitecturalInvariantError("component observations must be unique")
        required_members = {
            ref for requirement in requirements for ref in requirement.member_refs
        }
        orphaned = {
            item.component_ref
            for item in observations
            if item.component_ref not in required_members
        }
        if orphaned:
            raise ArchitecturalInvariantError(
                "component observations fall outside the exact requirement inventory"
            )
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "observations", observations)

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def checker_requirement_refs(self) -> tuple[str, ...]:
        return tuple(sorted(item.ref for item in self.requirements))

    @property
    def observation_evidence_refs(self) -> tuple[str, ...]:
        return _evidence_union(item.evidence_refs for item in self.observations)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "check_id": self.check_id,
            "basis": self.basis.to_dict(),
            "requirements": [item.to_dict() for item in self.requirements],
            "observations": [item.to_dict() for item in self.observations],
            "checker_id": self.CHECKER_ID,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentCardinalityPartitionProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "check_id",
                "basis",
                "requirements",
                "observations",
                "checker_id",
                *_AUTHORITY_FIELDS,
            },
            "component cardinality partition profile",
        )
        _require_no_authority(payload, "component cardinality partition profile")
        if payload["schema"] != cls.SCHEMA or payload["checker_id"] != cls.CHECKER_ID:
            raise ArchitecturalInvariantError(
                "component cardinality partition profile drifted"
            )
        if not isinstance(payload["requirements"], list) or not isinstance(
            payload["observations"], list
        ):
            raise TypeError("profile requirements and observations must be lists")
        result = cls(
            profile_id=payload["profile_id"],
            check_id=payload["check_id"],
            basis=ClaimBoundValidationBasis.from_dict(payload["basis"]),
            requirements=tuple(
                ComponentCardinalityPartitionRequirement.from_dict(item)
                for item in payload["requirements"]
            ),
            observations=tuple(
                ComponentPartitionObservation.from_dict(item)
                for item in payload["observations"]
            ),
        )
        if result.to_dict() != payload:
            raise ArchitecturalInvariantError(
                "component cardinality partition profile identity changed"
            )
        return result


def _check_cardinality_requirement(
    requirement: ComponentCardinalityPartitionRequirement,
    observations: dict[str, ComponentPartitionObservation],
) -> _RequirementOutcome:
    findings: list[CheckFinding] = []
    measurements: list[CheckMeasurement] = []
    requirement_ref = requirement.ref
    expected_partitions = {
        item.partition_ref: item.expected_count for item in requirement.partitions
    }
    partition_counts = {ref: 0 for ref in expected_partitions}
    measurements.append(
        _measurement(
            requirement_ref,
            "expected_total",
            requirement.expected_total,
            unit_ref=None,
        )
    )
    measurements.append(
        _measurement(
            requirement_ref,
            "inventory_total",
            len(requirement.member_refs),
            unit_ref=None,
        )
    )
    if len(requirement.member_refs) != requirement.expected_total:
        findings.append(
            _finding(
                requirement_ref,
                "component-cardinality-mismatch",
                FindingSeverity.ERROR,
                f"inventory contains {len(requirement.member_refs)} members; "
                f"claim-bound expected total is {requirement.expected_total}",
            )
        )

    membership_unknown = False
    for component_ref in requirement.member_refs:
        observation = observations.get(component_ref)
        if observation is None:
            membership_unknown = True
            findings.append(
                _finding(
                    requirement_ref,
                    "component-membership-observation-missing",
                    FindingSeverity.UNKNOWN,
                    f"{component_ref} has no supplied partition observation",
                )
            )
            continue
        if observation.partition_refs is None:
            membership_unknown = True
            findings.append(
                _finding(
                    requirement_ref,
                    "component-membership-unknown",
                    FindingSeverity.UNKNOWN,
                    f"{component_ref} has no resolved partition membership",
                    observation.evidence_refs,
                )
            )
            continue
        if len(observation.partition_refs) != 1:
            findings.append(
                _finding(
                    requirement_ref,
                    "component-exclusive-partition-violated",
                    FindingSeverity.ERROR,
                    f"{component_ref} belongs to {len(observation.partition_refs)} "
                    "partitions; exactly one is required",
                    observation.evidence_refs,
                )
            )
            continue
        partition_ref = observation.partition_refs[0]
        if partition_ref not in expected_partitions:
            findings.append(
                _finding(
                    requirement_ref,
                    "component-partition-unexpected",
                    FindingSeverity.ERROR,
                    f"{component_ref} names unexpected partition {partition_ref}",
                    observation.evidence_refs,
                )
            )
            continue
        partition_counts[partition_ref] += 1

    for expectation in requirement.partitions:
        observed_count = partition_counts[expectation.partition_ref]
        measurements.append(
            _measurement(
                requirement_ref,
                f"partition_count_{canonical_digest(expectation.partition_ref)[:12]}",
                observed_count,
                unit_ref=None,
            )
        )
        if not membership_unknown and observed_count != expectation.expected_count:
            findings.append(
                _finding(
                    requirement_ref,
                    "component-partition-cardinality-mismatch",
                    FindingSeverity.ERROR,
                    f"{expectation.partition_ref} contains {observed_count} members; "
                    f"claim-bound expected count is {expectation.expected_count}",
                )
            )
    return _RequirementOutcome(
        findings=tuple(findings),
        measurements=tuple(measurements),
    )


def check_component_cardinality_partition(
    profile: ComponentCardinalityPartitionProfile,
    *,
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Check exact counts and one-and-only-one partition membership."""

    if not isinstance(profile, ComponentCardinalityPartitionProfile):
        raise TypeError("profile must be ComponentCardinalityPartitionProfile")
    observations = {item.component_ref: item for item in profile.observations}
    outcomes = tuple(
        _check_cardinality_requirement(requirement, observations)
        for requirement in profile.requirements
    )
    return _check_receipt(
        check_id=profile.check_id,
        checker_id=profile.CHECKER_ID,
        basis=profile.basis,
        denominator_refs=profile.checker_requirement_refs,
        outcomes=outcomes,
        observation_evidence_refs=profile.observation_evidence_refs,
        branch=branch,
        scope_digest=scope_digest,
        stage_subject_digest=stage_subject_digest,
    )


@dataclass(frozen=True, slots=True)
class PlanarDirectionObservation:
    frame_ref: str
    direction: tuple[float, float] | None
    evidence_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "PlanarDirectionObservation@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_ref", logical_ref(self.frame_ref, "frame_ref"))
        object.__setattr__(
            self,
            "direction",
            _optional_vector2(self.direction, "direction", non_zero=True),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "direction evidence_refs"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "frame_ref": self.frame_ref,
            "direction": _vector_to_json(self.direction),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "PlanarDirectionObservation":
        payload = exact_mapping(
            value,
            {"schema", "frame_ref", "direction", "evidence_refs"},
            "planar direction observation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalInvariantError("planar direction observation drifted")
        if not isinstance(payload["evidence_refs"], list):
            raise TypeError("direction evidence_refs must be a list")
        return cls(
            frame_ref=payload["frame_ref"],
            direction=_vector_from_json(
                payload["direction"],
                "direction",
                non_zero=True,
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class OrientedFrameAngleRequirement:
    requirement_id: str
    frame_ref: str
    reference_frame_ref: str
    expected_angle_degrees: float
    periodicity_degrees: float
    tolerance_degrees: float

    SCHEMA: ClassVar[str] = "OrientedFrameAngleRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.requirement_id, "requirement_id")
        object.__setattr__(self, "frame_ref", logical_ref(self.frame_ref, "frame_ref"))
        object.__setattr__(
            self,
            "reference_frame_ref",
            logical_ref(self.reference_frame_ref, "reference_frame_ref"),
        )
        if self.frame_ref == self.reference_frame_ref:
            raise ArchitecturalInvariantError(
                "frame_ref and reference_frame_ref must be distinct"
            )
        expected = float(
            finite_number(self.expected_angle_degrees, "expected_angle_degrees")
        )
        periodicity = _positive_number(
            self.periodicity_degrees,
            "periodicity_degrees",
        )
        if periodicity > 360.0:
            raise ArchitecturalInvariantError(
                "periodicity_degrees must not exceed 360"
            )
        tolerance = _non_negative_number(
            self.tolerance_degrees,
            "tolerance_degrees",
        )
        if tolerance >= periodicity / 2.0:
            raise ArchitecturalInvariantError(
                "tolerance_degrees must be smaller than half the periodicity"
            )
        object.__setattr__(self, "expected_angle_degrees", expected % periodicity)
        object.__setattr__(self, "periodicity_degrees", periodicity)
        object.__setattr__(self, "tolerance_degrees", tolerance)

    @property
    def ref(self) -> str:
        return f"oriented-frame-angle:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "frame_ref": self.frame_ref,
            "reference_frame_ref": self.reference_frame_ref,
            "expected_angle_degrees": self.expected_angle_degrees,
            "periodicity_degrees": self.periodicity_degrees,
            "tolerance_degrees": self.tolerance_degrees,
            "project_supplied_values": True,
        }

    @classmethod
    def from_dict(cls, value: object) -> "OrientedFrameAngleRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "requirement_id",
                "frame_ref",
                "reference_frame_ref",
                "expected_angle_degrees",
                "periodicity_degrees",
                "tolerance_degrees",
                "project_supplied_values",
            },
            "oriented frame angle requirement",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["project_supplied_values"] is not True
        ):
            raise ArchitecturalInvariantError("oriented frame angle requirement drifted")
        return cls(
            requirement_id=payload["requirement_id"],
            frame_ref=payload["frame_ref"],
            reference_frame_ref=payload["reference_frame_ref"],
            expected_angle_degrees=payload["expected_angle_degrees"],
            periodicity_degrees=payload["periodicity_degrees"],
            tolerance_degrees=payload["tolerance_degrees"],
        )


@dataclass(frozen=True, slots=True)
class OrientedFrameAngleProfile:
    profile_id: str
    check_id: str
    basis: ClaimBoundValidationBasis
    requirements: tuple[OrientedFrameAngleRequirement, ...]
    observations: tuple[PlanarDirectionObservation, ...]
    angle_unit_ref: str

    SCHEMA: ClassVar[str] = "OrientedFrameAngleProfile@1"
    CHECKER_ID: ClassVar[str] = "oriented-frame-angle-checker"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        identifier(self.check_id, "check_id")
        if not isinstance(self.basis, ClaimBoundValidationBasis):
            raise TypeError("basis must be ClaimBoundValidationBasis")
        if not isinstance(self.requirements, tuple) or not self.requirements or any(
            not isinstance(item, OrientedFrameAngleRequirement)
            for item in self.requirements
        ):
            raise TypeError(
                "requirements must contain OrientedFrameAngleRequirement values"
            )
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, PlanarDirectionObservation)
            for item in self.observations
        ):
            raise TypeError(
                "observations must contain PlanarDirectionObservation values"
            )
        requirements = tuple(
            sorted(self.requirements, key=lambda item: item.requirement_id)
        )
        observations = tuple(sorted(self.observations, key=lambda item: item.frame_ref))
        if len({item.requirement_id for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement IDs must be unique")
        if len({item.ref for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement refs must be unique")
        if len({item.frame_ref for item in observations}) != len(observations):
            raise ArchitecturalInvariantError("frame observations must be unique")
        required_frames = {
            ref
            for requirement in requirements
            for ref in (requirement.frame_ref, requirement.reference_frame_ref)
        }
        if any(item.frame_ref not in required_frames for item in observations):
            raise ArchitecturalInvariantError(
                "frame observations fall outside the exact requirement inventory"
            )
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(
            self,
            "angle_unit_ref",
            logical_ref(self.angle_unit_ref, "angle_unit_ref"),
        )

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def checker_requirement_refs(self) -> tuple[str, ...]:
        return tuple(sorted(item.ref for item in self.requirements))

    @property
    def observation_evidence_refs(self) -> tuple[str, ...]:
        return _evidence_union(item.evidence_refs for item in self.observations)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "check_id": self.check_id,
            "basis": self.basis.to_dict(),
            "requirements": [item.to_dict() for item in self.requirements],
            "observations": [item.to_dict() for item in self.observations],
            "angle_unit_ref": self.angle_unit_ref,
            "checker_id": self.CHECKER_ID,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "OrientedFrameAngleProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "check_id",
                "basis",
                "requirements",
                "observations",
                "angle_unit_ref",
                "checker_id",
                *_AUTHORITY_FIELDS,
            },
            "oriented frame angle profile",
        )
        _require_no_authority(payload, "oriented frame angle profile")
        if payload["schema"] != cls.SCHEMA or payload["checker_id"] != cls.CHECKER_ID:
            raise ArchitecturalInvariantError("oriented frame angle profile drifted")
        if not isinstance(payload["requirements"], list) or not isinstance(
            payload["observations"], list
        ):
            raise TypeError("profile requirements and observations must be lists")
        result = cls(
            profile_id=payload["profile_id"],
            check_id=payload["check_id"],
            basis=ClaimBoundValidationBasis.from_dict(payload["basis"]),
            requirements=tuple(
                OrientedFrameAngleRequirement.from_dict(item)
                for item in payload["requirements"]
            ),
            observations=tuple(
                PlanarDirectionObservation.from_dict(item)
                for item in payload["observations"]
            ),
            angle_unit_ref=payload["angle_unit_ref"],
        )
        if result.to_dict() != payload:
            raise ArchitecturalInvariantError(
                "oriented frame angle profile identity changed"
            )
        return result


def _signed_angle_degrees(
    reference: tuple[float, float],
    direction: tuple[float, float],
) -> float:
    cross = reference[0] * direction[1] - reference[1] * direction[0]
    dot = reference[0] * direction[0] + reference[1] * direction[1]
    return math.degrees(math.atan2(cross, dot)) % 360.0


def _periodic_delta(observed: float, expected: float, periodicity: float) -> float:
    return abs((observed - expected + periodicity / 2.0) % periodicity - periodicity / 2.0)


def _check_oriented_frame_requirement(
    requirement: OrientedFrameAngleRequirement,
    observations: dict[str, PlanarDirectionObservation],
    angle_unit_ref: str,
) -> _RequirementOutcome:
    requirement_ref = requirement.ref
    frame = observations.get(requirement.frame_ref)
    reference = observations.get(requirement.reference_frame_ref)
    evidence_refs = _evidence_union(
        item.evidence_refs for item in (frame, reference) if item is not None
    )
    missing = []
    if frame is None or frame.direction is None:
        missing.append(requirement.frame_ref)
    if reference is None or reference.direction is None:
        missing.append(requirement.reference_frame_ref)
    if missing:
        return _RequirementOutcome(
            findings=(
                _finding(
                    requirement_ref,
                    "oriented-frame-observation-unknown",
                    FindingSeverity.UNKNOWN,
                    "direction observation is missing for " + ", ".join(sorted(missing)),
                    evidence_refs,
                ),
            ),
            measurements=(),
        )
    assert frame is not None and frame.direction is not None
    assert reference is not None and reference.direction is not None
    observed = _signed_angle_degrees(reference.direction, frame.direction)
    error = _periodic_delta(
        observed,
        requirement.expected_angle_degrees,
        requirement.periodicity_degrees,
    )
    findings = ()
    if error > requirement.tolerance_degrees:
        findings = (
            _finding(
                requirement_ref,
                "oriented-frame-angle-out-of-tolerance",
                FindingSeverity.ERROR,
                f"observed angle {observed} differs from claim-bound expected angle "
                f"{requirement.expected_angle_degrees} by {error}, exceeding tolerance "
                f"{requirement.tolerance_degrees}",
                evidence_refs,
            ),
        )
    return _RequirementOutcome(
        findings=findings,
        measurements=(
            _measurement(
                requirement_ref,
                "observed_angle",
                observed,
                unit_ref=angle_unit_ref,
                evidence_refs=evidence_refs,
            ),
            _measurement(
                requirement_ref,
                "periodic_angle_error",
                error,
                unit_ref=angle_unit_ref,
                evidence_refs=evidence_refs,
            ),
        ),
    )


def check_oriented_frame_angle(
    profile: OrientedFrameAngleProfile,
    *,
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Measure frame-to-datum angle using caller-supplied periodicity/tolerance."""

    if not isinstance(profile, OrientedFrameAngleProfile):
        raise TypeError("profile must be OrientedFrameAngleProfile")
    observations = {item.frame_ref: item for item in profile.observations}
    outcomes = tuple(
        _check_oriented_frame_requirement(
            requirement,
            observations,
            profile.angle_unit_ref,
        )
        for requirement in profile.requirements
    )
    return _check_receipt(
        check_id=profile.check_id,
        checker_id=profile.CHECKER_ID,
        basis=profile.basis,
        denominator_refs=profile.checker_requirement_refs,
        outcomes=outcomes,
        observation_evidence_refs=profile.observation_evidence_refs,
        branch=branch,
        scope_digest=scope_digest,
        stage_subject_digest=stage_subject_digest,
    )


@dataclass(frozen=True, slots=True)
class PlanarPoseObservation:
    subject_ref: str
    position: tuple[float, float] | None
    direction: tuple[float, float] | None
    evidence_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "PlanarPoseObservation@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_ref",
            logical_ref(self.subject_ref, "subject_ref"),
        )
        object.__setattr__(
            self,
            "position",
            _optional_vector2(self.position, "position", non_zero=False),
        )
        object.__setattr__(
            self,
            "direction",
            _optional_vector2(self.direction, "direction", non_zero=True),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "pose evidence_refs"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "subject_ref": self.subject_ref,
            "position": _vector_to_json(self.position),
            "direction": _vector_to_json(self.direction),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "PlanarPoseObservation":
        payload = exact_mapping(
            value,
            {"schema", "subject_ref", "position", "direction", "evidence_refs"},
            "planar pose observation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalInvariantError("planar pose observation drifted")
        if not isinstance(payload["evidence_refs"], list):
            raise TypeError("pose evidence_refs must be a list")
        return cls(
            subject_ref=payload["subject_ref"],
            position=_vector_from_json(
                payload["position"],
                "position",
                non_zero=False,
            ),
            direction=_vector_from_json(
                payload["direction"],
                "direction",
                non_zero=True,
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class NfoldRotationalSymmetryRequirement:
    requirement_id: str
    center_ref: str
    member_refs: tuple[str, ...]
    fold_count: int
    position_tolerance: float
    compare_member_directions: bool
    direction_tolerance_degrees: float | None = None

    SCHEMA: ClassVar[str] = "NfoldRotationalSymmetryRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.requirement_id, "requirement_id")
        object.__setattr__(
            self,
            "center_ref",
            logical_ref(self.center_ref, "center_ref"),
        )
        object.__setattr__(
            self,
            "member_refs",
            deterministic_refs(self.member_refs, "member_refs"),
        )
        fold_count = _positive_integer(self.fold_count, "fold_count")
        if fold_count < 2:
            raise ArchitecturalInvariantError("fold_count must be at least two")
        if len(self.member_refs) != fold_count:
            raise ArchitecturalInvariantError(
                "one rotational orbit must name exactly fold_count members"
            )
        if self.center_ref in self.member_refs:
            raise ArchitecturalInvariantError(
                "rotational center must remain outside the member orbit"
            )
        object.__setattr__(self, "fold_count", fold_count)
        object.__setattr__(
            self,
            "position_tolerance",
            _non_negative_number(self.position_tolerance, "position_tolerance"),
        )
        if not isinstance(self.compare_member_directions, bool):
            raise TypeError("compare_member_directions must be bool")
        if self.compare_member_directions:
            if self.direction_tolerance_degrees is None:
                raise ArchitecturalInvariantError(
                    "direction comparison requires a project-supplied tolerance"
                )
            direction_tolerance = _non_negative_number(
                self.direction_tolerance_degrees,
                "direction_tolerance_degrees",
            )
            if direction_tolerance >= 180.0:
                raise ArchitecturalInvariantError(
                    "direction_tolerance_degrees must be smaller than 180"
                )
            object.__setattr__(
                self,
                "direction_tolerance_degrees",
                direction_tolerance,
            )
        elif self.direction_tolerance_degrees is not None:
            raise ArchitecturalInvariantError(
                "direction tolerance is not applicable when direction comparison is false"
            )

    @property
    def ref(self) -> str:
        return f"nfold-rotational-symmetry:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "center_ref": self.center_ref,
            "member_refs": list(self.member_refs),
            "fold_count": self.fold_count,
            "position_tolerance": self.position_tolerance,
            "compare_member_directions": self.compare_member_directions,
            "direction_tolerance_degrees": self.direction_tolerance_degrees,
            "project_supplied_values": True,
        }

    @classmethod
    def from_dict(cls, value: object) -> "NfoldRotationalSymmetryRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "requirement_id",
                "center_ref",
                "member_refs",
                "fold_count",
                "position_tolerance",
                "compare_member_directions",
                "direction_tolerance_degrees",
                "project_supplied_values",
            },
            "n-fold rotational symmetry requirement",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["project_supplied_values"] is not True
        ):
            raise ArchitecturalInvariantError(
                "n-fold rotational symmetry requirement drifted"
            )
        if not isinstance(payload["member_refs"], list):
            raise TypeError("symmetry member_refs must be a list")
        return cls(
            requirement_id=payload["requirement_id"],
            center_ref=payload["center_ref"],
            member_refs=tuple(payload["member_refs"]),
            fold_count=payload["fold_count"],
            position_tolerance=payload["position_tolerance"],
            compare_member_directions=payload["compare_member_directions"],
            direction_tolerance_degrees=payload["direction_tolerance_degrees"],
        )


@dataclass(frozen=True, slots=True)
class NfoldRotationalSymmetryProfile:
    profile_id: str
    check_id: str
    basis: ClaimBoundValidationBasis
    requirements: tuple[NfoldRotationalSymmetryRequirement, ...]
    observations: tuple[PlanarPoseObservation, ...]
    length_unit_ref: str
    angle_unit_ref: str

    SCHEMA: ClassVar[str] = "NfoldRotationalSymmetryProfile@1"
    CHECKER_ID: ClassVar[str] = "nfold-rotational-symmetry-checker"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        identifier(self.check_id, "check_id")
        if not isinstance(self.basis, ClaimBoundValidationBasis):
            raise TypeError("basis must be ClaimBoundValidationBasis")
        if not isinstance(self.requirements, tuple) or not self.requirements or any(
            not isinstance(item, NfoldRotationalSymmetryRequirement)
            for item in self.requirements
        ):
            raise TypeError(
                "requirements must contain NfoldRotationalSymmetryRequirement values"
            )
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, PlanarPoseObservation)
            for item in self.observations
        ):
            raise TypeError("observations must contain PlanarPoseObservation values")
        requirements = tuple(
            sorted(self.requirements, key=lambda item: item.requirement_id)
        )
        observations = tuple(
            sorted(self.observations, key=lambda item: item.subject_ref)
        )
        if len({item.requirement_id for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement IDs must be unique")
        if len({item.ref for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement refs must be unique")
        if len({item.subject_ref for item in observations}) != len(observations):
            raise ArchitecturalInvariantError("pose observations must be unique")
        required_subjects = {
            ref
            for requirement in requirements
            for ref in (requirement.center_ref, *requirement.member_refs)
        }
        if any(item.subject_ref not in required_subjects for item in observations):
            raise ArchitecturalInvariantError(
                "pose observations fall outside the exact requirement inventory"
            )
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(
            self,
            "length_unit_ref",
            logical_ref(self.length_unit_ref, "length_unit_ref"),
        )
        object.__setattr__(
            self,
            "angle_unit_ref",
            logical_ref(self.angle_unit_ref, "angle_unit_ref"),
        )

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def checker_requirement_refs(self) -> tuple[str, ...]:
        return tuple(sorted(item.ref for item in self.requirements))

    @property
    def observation_evidence_refs(self) -> tuple[str, ...]:
        return _evidence_union(item.evidence_refs for item in self.observations)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "check_id": self.check_id,
            "basis": self.basis.to_dict(),
            "requirements": [item.to_dict() for item in self.requirements],
            "observations": [item.to_dict() for item in self.observations],
            "length_unit_ref": self.length_unit_ref,
            "angle_unit_ref": self.angle_unit_ref,
            "checker_id": self.CHECKER_ID,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "NfoldRotationalSymmetryProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "check_id",
                "basis",
                "requirements",
                "observations",
                "length_unit_ref",
                "angle_unit_ref",
                "checker_id",
                *_AUTHORITY_FIELDS,
            },
            "n-fold rotational symmetry profile",
        )
        _require_no_authority(payload, "n-fold rotational symmetry profile")
        if payload["schema"] != cls.SCHEMA or payload["checker_id"] != cls.CHECKER_ID:
            raise ArchitecturalInvariantError(
                "n-fold rotational symmetry profile drifted"
            )
        if not isinstance(payload["requirements"], list) or not isinstance(
            payload["observations"], list
        ):
            raise TypeError("profile requirements and observations must be lists")
        result = cls(
            profile_id=payload["profile_id"],
            check_id=payload["check_id"],
            basis=ClaimBoundValidationBasis.from_dict(payload["basis"]),
            requirements=tuple(
                NfoldRotationalSymmetryRequirement.from_dict(item)
                for item in payload["requirements"]
            ),
            observations=tuple(
                PlanarPoseObservation.from_dict(item)
                for item in payload["observations"]
            ),
            length_unit_ref=payload["length_unit_ref"],
            angle_unit_ref=payload["angle_unit_ref"],
        )
        if result.to_dict() != payload:
            raise ArchitecturalInvariantError(
                "n-fold rotational symmetry profile identity changed"
            )
        return result


def _rotate(vector: tuple[float, float], angle_radians: float) -> tuple[float, float]:
    cosine = math.cos(angle_radians)
    sine = math.sin(angle_radians)
    return (
        vector[0] * cosine - vector[1] * sine,
        vector[0] * sine + vector[1] * cosine,
    )


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _check_rotational_symmetry_requirement(
    requirement: NfoldRotationalSymmetryRequirement,
    observations: dict[str, PlanarPoseObservation],
    *,
    length_unit_ref: str,
    angle_unit_ref: str,
) -> _RequirementOutcome:
    requirement_ref = requirement.ref
    needed_refs = (requirement.center_ref, *requirement.member_refs)
    evidence_refs = _evidence_union(
        observations[ref].evidence_refs for ref in needed_refs if ref in observations
    )
    missing_positions = tuple(
        ref
        for ref in needed_refs
        if ref not in observations or observations[ref].position is None
    )
    if missing_positions:
        return _RequirementOutcome(
            findings=(
                _finding(
                    requirement_ref,
                    "rotational-symmetry-position-unknown",
                    FindingSeverity.UNKNOWN,
                    "planar position is missing for "
                    + ", ".join(sorted(missing_positions)),
                    evidence_refs,
                ),
            ),
            measurements=(),
        )

    center = observations[requirement.center_ref].position
    assert center is not None
    findings: list[CheckFinding] = []
    maximum_position_error = 0.0
    maximum_direction_error = 0.0
    direction_unknown = False
    step_angle = math.tau / requirement.fold_count
    for source_ref in requirement.member_refs:
        source = observations[source_ref]
        assert source.position is not None
        relative = (source.position[0] - center[0], source.position[1] - center[1])
        for step in range(1, requirement.fold_count):
            rotated = _rotate(relative, step * step_angle)
            target_position = (center[0] + rotated[0], center[1] + rotated[1])
            candidates = tuple(
                sorted(
                    (
                        (
                            candidate_ref,
                            _distance(
                                observations[candidate_ref].position,
                                target_position,
                            ),
                        )
                        for candidate_ref in requirement.member_refs
                        if candidate_ref != source_ref
                    ),
                    key=lambda item: (item[1], item[0]),
                )
            )
            matches = tuple(
                item
                for item in candidates
                if item[1] <= requirement.position_tolerance
            )
            if not matches:
                nearest = candidates[0][1] if candidates else math.inf
                findings.append(
                    _finding(
                        requirement_ref,
                        "rotational-symmetry-target-missing",
                        FindingSeverity.ERROR,
                        f"rotating {source_ref} by step {step} finds no unique member; "
                        f"nearest positional error is {nearest}",
                        evidence_refs,
                    )
                )
                continue
            if len(matches) != 1:
                findings.append(
                    _finding(
                        requirement_ref,
                        "rotational-symmetry-target-ambiguous",
                        FindingSeverity.ERROR,
                        f"rotating {source_ref} by step {step} matches "
                        f"{len(matches)} members within tolerance",
                        evidence_refs,
                    )
                )
                continue
            target_ref, position_error = matches[0]
            maximum_position_error = max(maximum_position_error, position_error)
            if not requirement.compare_member_directions:
                continue
            target = observations[target_ref]
            if source.direction is None or target.direction is None:
                direction_unknown = True
                findings.append(
                    _finding(
                        requirement_ref,
                        "rotational-symmetry-direction-unknown",
                        FindingSeverity.UNKNOWN,
                        f"direction comparison lacks a pose for {source_ref} or {target_ref}",
                        tuple(
                            sorted(set(source.evidence_refs) | set(target.evidence_refs))
                        ),
                    )
                )
                continue
            expected_direction = _rotate(source.direction, step * step_angle)
            direction_error = _periodic_delta(
                _signed_angle_degrees(expected_direction, target.direction),
                0.0,
                360.0,
            )
            maximum_direction_error = max(maximum_direction_error, direction_error)
            assert requirement.direction_tolerance_degrees is not None
            if direction_error > requirement.direction_tolerance_degrees:
                findings.append(
                    _finding(
                        requirement_ref,
                        "rotational-symmetry-direction-out-of-tolerance",
                        FindingSeverity.ERROR,
                        f"rotated direction from {source_ref} to {target_ref} differs by "
                        f"{direction_error}, exceeding tolerance "
                        f"{requirement.direction_tolerance_degrees}",
                        tuple(
                            sorted(set(source.evidence_refs) | set(target.evidence_refs))
                        ),
                    )
                )

    measurements = [
        _measurement(
            requirement_ref,
            "maximum_position_error",
            maximum_position_error,
            unit_ref=length_unit_ref,
            evidence_refs=evidence_refs,
        )
    ]
    if requirement.compare_member_directions and not direction_unknown:
        measurements.append(
            _measurement(
                requirement_ref,
                "maximum_direction_error",
                maximum_direction_error,
                unit_ref=angle_unit_ref,
                evidence_refs=evidence_refs,
            )
        )
    return _RequirementOutcome(
        findings=tuple(findings),
        measurements=tuple(measurements),
    )


def check_nfold_rotational_symmetry(
    profile: NfoldRotationalSymmetryProfile,
    *,
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Check positional and optionally directional N-fold rotational orbits."""

    if not isinstance(profile, NfoldRotationalSymmetryProfile):
        raise TypeError("profile must be NfoldRotationalSymmetryProfile")
    observations = {item.subject_ref: item for item in profile.observations}
    outcomes = tuple(
        _check_rotational_symmetry_requirement(
            requirement,
            observations,
            length_unit_ref=profile.length_unit_ref,
            angle_unit_ref=profile.angle_unit_ref,
        )
        for requirement in profile.requirements
    )
    return _check_receipt(
        check_id=profile.check_id,
        checker_id=profile.CHECKER_ID,
        basis=profile.basis,
        denominator_refs=profile.checker_requirement_refs,
        outcomes=outcomes,
        observation_evidence_refs=profile.observation_evidence_refs,
        branch=branch,
        scope_digest=scope_digest,
        stage_subject_digest=stage_subject_digest,
    )


@dataclass(frozen=True, slots=True)
class LevelDatumObservation:
    level_ref: str
    datum_frame_ref: str
    datum: float | None
    evidence_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "LevelDatumObservation@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "level_ref", logical_ref(self.level_ref, "level_ref"))
        object.__setattr__(
            self,
            "datum_frame_ref",
            logical_ref(self.datum_frame_ref, "datum_frame_ref"),
        )
        if self.datum is not None:
            object.__setattr__(self, "datum", float(finite_number(self.datum, "datum")))
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "level datum evidence_refs"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "level_ref": self.level_ref,
            "datum_frame_ref": self.datum_frame_ref,
            "datum": self.datum,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "LevelDatumObservation":
        payload = exact_mapping(
            value,
            {"schema", "level_ref", "datum_frame_ref", "datum", "evidence_refs"},
            "level datum observation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ArchitecturalInvariantError("level datum observation drifted")
        if not isinstance(payload["evidence_refs"], list):
            raise TypeError("level datum evidence_refs must be a list")
        return cls(
            level_ref=payload["level_ref"],
            datum_frame_ref=payload["datum_frame_ref"],
            datum=payload["datum"],
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class LevelStratificationRequirement:
    requirement_id: str
    lower_level_ref: str
    upper_level_ref: str
    datum_frame_ref: str
    minimum_separation: float
    maximum_separation: float | None = None

    SCHEMA: ClassVar[str] = "LevelStratificationRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.requirement_id, "requirement_id")
        object.__setattr__(
            self,
            "lower_level_ref",
            logical_ref(self.lower_level_ref, "lower_level_ref"),
        )
        object.__setattr__(
            self,
            "upper_level_ref",
            logical_ref(self.upper_level_ref, "upper_level_ref"),
        )
        if self.lower_level_ref == self.upper_level_ref:
            raise ArchitecturalInvariantError("lower and upper levels must be distinct")
        object.__setattr__(
            self,
            "datum_frame_ref",
            logical_ref(self.datum_frame_ref, "datum_frame_ref"),
        )
        minimum = _non_negative_number(
            self.minimum_separation,
            "minimum_separation",
        )
        object.__setattr__(self, "minimum_separation", minimum)
        if self.maximum_separation is not None:
            maximum = _non_negative_number(
                self.maximum_separation,
                "maximum_separation",
            )
            if maximum < minimum:
                raise ArchitecturalInvariantError(
                    "maximum_separation must not be below minimum_separation"
                )
            object.__setattr__(self, "maximum_separation", maximum)

    @property
    def ref(self) -> str:
        return f"level-stratification:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "lower_level_ref": self.lower_level_ref,
            "upper_level_ref": self.upper_level_ref,
            "datum_frame_ref": self.datum_frame_ref,
            "minimum_separation": self.minimum_separation,
            "maximum_separation": self.maximum_separation,
            "project_supplied_values": True,
        }

    @classmethod
    def from_dict(cls, value: object) -> "LevelStratificationRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "requirement_id",
                "lower_level_ref",
                "upper_level_ref",
                "datum_frame_ref",
                "minimum_separation",
                "maximum_separation",
                "project_supplied_values",
            },
            "level stratification requirement",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["project_supplied_values"] is not True
        ):
            raise ArchitecturalInvariantError("level stratification requirement drifted")
        return cls(
            requirement_id=payload["requirement_id"],
            lower_level_ref=payload["lower_level_ref"],
            upper_level_ref=payload["upper_level_ref"],
            datum_frame_ref=payload["datum_frame_ref"],
            minimum_separation=payload["minimum_separation"],
            maximum_separation=payload["maximum_separation"],
        )


@dataclass(frozen=True, slots=True)
class LevelStratificationProfile:
    profile_id: str
    check_id: str
    basis: ClaimBoundValidationBasis
    requirements: tuple[LevelStratificationRequirement, ...]
    observations: tuple[LevelDatumObservation, ...]
    length_unit_ref: str

    SCHEMA: ClassVar[str] = "LevelStratificationProfile@1"
    CHECKER_ID: ClassVar[str] = "level-stratification-checker"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        identifier(self.check_id, "check_id")
        if not isinstance(self.basis, ClaimBoundValidationBasis):
            raise TypeError("basis must be ClaimBoundValidationBasis")
        if not isinstance(self.requirements, tuple) or not self.requirements or any(
            not isinstance(item, LevelStratificationRequirement)
            for item in self.requirements
        ):
            raise TypeError(
                "requirements must contain LevelStratificationRequirement values"
            )
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, LevelDatumObservation)
            for item in self.observations
        ):
            raise TypeError("observations must contain LevelDatumObservation values")
        requirements = tuple(
            sorted(self.requirements, key=lambda item: item.requirement_id)
        )
        observations = tuple(
            sorted(self.observations, key=lambda item: item.level_ref)
        )
        if len({item.requirement_id for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement IDs must be unique")
        if len({item.ref for item in requirements}) != len(requirements):
            raise ArchitecturalInvariantError("requirement refs must be unique")
        if len({item.level_ref for item in observations}) != len(observations):
            raise ArchitecturalInvariantError("level observations must be unique")
        required_levels = {
            ref
            for requirement in requirements
            for ref in (requirement.lower_level_ref, requirement.upper_level_ref)
        }
        if any(item.level_ref not in required_levels for item in observations):
            raise ArchitecturalInvariantError(
                "level observations fall outside the exact requirement inventory"
            )
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(
            self,
            "length_unit_ref",
            logical_ref(self.length_unit_ref, "length_unit_ref"),
        )

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def checker_requirement_refs(self) -> tuple[str, ...]:
        return tuple(sorted(item.ref for item in self.requirements))

    @property
    def observation_evidence_refs(self) -> tuple[str, ...]:
        return _evidence_union(item.evidence_refs for item in self.observations)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "check_id": self.check_id,
            "basis": self.basis.to_dict(),
            "requirements": [item.to_dict() for item in self.requirements],
            "observations": [item.to_dict() for item in self.observations],
            "length_unit_ref": self.length_unit_ref,
            "checker_id": self.CHECKER_ID,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "LevelStratificationProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "check_id",
                "basis",
                "requirements",
                "observations",
                "length_unit_ref",
                "checker_id",
                *_AUTHORITY_FIELDS,
            },
            "level stratification profile",
        )
        _require_no_authority(payload, "level stratification profile")
        if payload["schema"] != cls.SCHEMA or payload["checker_id"] != cls.CHECKER_ID:
            raise ArchitecturalInvariantError("level stratification profile drifted")
        if not isinstance(payload["requirements"], list) or not isinstance(
            payload["observations"], list
        ):
            raise TypeError("profile requirements and observations must be lists")
        result = cls(
            profile_id=payload["profile_id"],
            check_id=payload["check_id"],
            basis=ClaimBoundValidationBasis.from_dict(payload["basis"]),
            requirements=tuple(
                LevelStratificationRequirement.from_dict(item)
                for item in payload["requirements"]
            ),
            observations=tuple(
                LevelDatumObservation.from_dict(item)
                for item in payload["observations"]
            ),
            length_unit_ref=payload["length_unit_ref"],
        )
        if result.to_dict() != payload:
            raise ArchitecturalInvariantError(
                "level stratification profile identity changed"
            )
        return result


def _check_level_stratification_requirement(
    requirement: LevelStratificationRequirement,
    observations: dict[str, LevelDatumObservation],
    length_unit_ref: str,
) -> _RequirementOutcome:
    requirement_ref = requirement.ref
    lower = observations.get(requirement.lower_level_ref)
    upper = observations.get(requirement.upper_level_ref)
    evidence_refs = _evidence_union(
        item.evidence_refs for item in (lower, upper) if item is not None
    )
    findings: list[CheckFinding] = []
    frame_mismatch = False
    for label, observation in (("lower", lower), ("upper", upper)):
        if observation is None:
            findings.append(
                _finding(
                    requirement_ref,
                    "level-datum-observation-missing",
                    FindingSeverity.UNKNOWN,
                    f"{label} level datum observation is missing",
                    evidence_refs,
                )
            )
        elif observation.datum_frame_ref != requirement.datum_frame_ref:
            frame_mismatch = True
            findings.append(
                _finding(
                    requirement_ref,
                    "level-datum-frame-mismatch",
                    FindingSeverity.ERROR,
                    f"{label} level uses {observation.datum_frame_ref}; "
                    f"required datum frame is {requirement.datum_frame_ref}",
                    observation.evidence_refs,
                )
            )
        elif observation.datum is None:
            findings.append(
                _finding(
                    requirement_ref,
                    "level-datum-unknown",
                    FindingSeverity.UNKNOWN,
                    f"{label} level datum is unresolved",
                    observation.evidence_refs,
                )
            )
    if (
        frame_mismatch
        or lower is None
        or upper is None
        or lower.datum is None
        or upper.datum is None
    ):
        return _RequirementOutcome(findings=tuple(findings), measurements=())

    separation = upper.datum - lower.datum
    if separation < requirement.minimum_separation:
        findings.append(
            _finding(
                requirement_ref,
                "level-minimum-separation-unsatisfied",
                FindingSeverity.ERROR,
                f"upper-minus-lower separation {separation} is below claim-bound "
                f"minimum {requirement.minimum_separation}",
                evidence_refs,
            )
        )
    if (
        requirement.maximum_separation is not None
        and separation > requirement.maximum_separation
    ):
        findings.append(
            _finding(
                requirement_ref,
                "level-maximum-separation-exceeded",
                FindingSeverity.ERROR,
                f"upper-minus-lower separation {separation} exceeds claim-bound "
                f"maximum {requirement.maximum_separation}",
                evidence_refs,
            )
        )
    return _RequirementOutcome(
        findings=tuple(findings),
        measurements=(
            _measurement(
                requirement_ref,
                "upper_minus_lower_separation",
                separation,
                unit_ref=length_unit_ref,
                evidence_refs=evidence_refs,
            ),
        ),
    )


def check_level_stratification(
    profile: LevelStratificationProfile,
    *,
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Check ordered level relations in one explicit datum frame."""

    if not isinstance(profile, LevelStratificationProfile):
        raise TypeError("profile must be LevelStratificationProfile")
    observations = {item.level_ref: item for item in profile.observations}
    outcomes = tuple(
        _check_level_stratification_requirement(
            requirement,
            observations,
            profile.length_unit_ref,
        )
        for requirement in profile.requirements
    )
    return _check_receipt(
        check_id=profile.check_id,
        checker_id=profile.CHECKER_ID,
        basis=profile.basis,
        denominator_refs=profile.checker_requirement_refs,
        outcomes=outcomes,
        observation_evidence_refs=profile.observation_evidence_refs,
        branch=branch,
        scope_digest=scope_digest,
        stage_subject_digest=stage_subject_digest,
    )


validate_component_cardinality_partition = check_component_cardinality_partition
validate_oriented_frame_angle = check_oriented_frame_angle
validate_nfold_rotational_symmetry = check_nfold_rotational_symmetry
validate_level_stratification = check_level_stratification


__all__ = [
    "ArchitecturalInvariantError",
    "ClaimBoundValidationBasis",
    "ComponentCardinalityPartitionProfile",
    "ComponentCardinalityPartitionRequirement",
    "ComponentPartitionObservation",
    "LevelDatumObservation",
    "LevelStratificationProfile",
    "LevelStratificationRequirement",
    "NfoldRotationalSymmetryProfile",
    "NfoldRotationalSymmetryRequirement",
    "OrientedFrameAngleProfile",
    "OrientedFrameAngleRequirement",
    "PartitionExpectation",
    "PlanarDirectionObservation",
    "PlanarPoseObservation",
    "check_component_cardinality_partition",
    "check_level_stratification",
    "check_nfold_rotational_symmetry",
    "check_oriented_frame_angle",
    "validate_component_cardinality_partition",
    "validate_level_stratification",
    "validate_nfold_rotational_symmetry",
    "validate_oriented_frame_angle",
]
