"""Deterministic narrow-phase checks for continuous linear interfaces.

Broad-phase bounding-box contact proves only that two assemblies meet
*somewhere*.  It cannot prove that a required bearing, seal, or edge is
continuous.  This module computes the exact parameter intervals of every
project-declared required segment that lie within tolerance of its explicitly
paired supporting segments.  It does not sample, infer topology, or choose a
project-specific support relationship.

All coordinates are canonical metres.  The result is an authority-free
``CheckReceiptEnvelope``; callers retain ownership of the architectural
meaning, expected denominator, evidence, tolerance, and persistence path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    finite_number,
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


_COORDINATE_LIMIT_METRES = 1.0e9
_INTERVAL_EPSILON = 1.0e-12
_MAX_PAIR_EVALUATIONS = 262_144


class InterfaceContinuityError(ValueError):
    """A boundary-continuity request is malformed or non-deterministic."""


def _point(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise InterfaceContinuityError(f"{field} must be a three-value tuple")
    result = tuple(float(finite_number(item, field)) for item in value)
    if any(abs(item) > _COORDINATE_LIMIT_METRES for item in result):
        raise InterfaceContinuityError(
            f"{field} exceeds the canonical metre coordinate limit"
        )
    return result[0], result[1], result[2]


@dataclass(frozen=True, slots=True)
class InterfaceBoundarySegment:
    """One straight boundary segment participating in an interface check."""

    segment_ref: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]

    SCHEMA = "InterfaceBoundarySegment@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "segment_ref",
            logical_ref(self.segment_ref, "interface segment_ref"),
        )
        object.__setattr__(self, "start", _point(self.start, "segment start"))
        object.__setattr__(self, "end", _point(self.end, "segment end"))
        if math.dist(self.start, self.end) <= 1.0e-12:
            raise InterfaceContinuityError(
                "interface boundary segment must have non-zero metre length"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "segment_ref": self.segment_ref,
            "start": list(self.start),
            "end": list(self.end),
        }

    @classmethod
    def from_dict(cls, value: object) -> "InterfaceBoundarySegment":
        payload = exact_mapping(
            value,
            {"schema", "segment_ref", "start", "end"},
            "interface boundary segment",
        )
        if payload["schema"] != cls.SCHEMA:
            raise InterfaceContinuityError(
                "unsupported interface boundary segment schema"
            )
        for field in ("start", "end"):
            if not isinstance(payload[field], list):
                raise TypeError(f"interface segment {field} must be a list")
        return cls(
            segment_ref=payload["segment_ref"],
            start=tuple(payload["start"]),
            end=tuple(payload["end"]),
        )


@dataclass(frozen=True, slots=True)
class InterfaceBoundarySupportSet:
    """The only support segments eligible for one required boundary.

    An empty support tuple is a typed unknown, not a pass.  A caller must still
    declare one support set for every expected required boundary so omission of
    the denominator cannot be confused with non-applicability.
    """

    required_segment_ref: str
    supporting_segment_refs: tuple[str, ...]

    SCHEMA = "InterfaceBoundarySupportSet@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_segment_ref",
            logical_ref(
                self.required_segment_ref,
                "interface support-set required_segment_ref",
            ),
        )
        object.__setattr__(
            self,
            "supporting_segment_refs",
            deterministic_refs(
                self.supporting_segment_refs,
                "interface support-set supporting_segment_refs",
                allow_empty=True,
            ),
        )
        if self.required_segment_ref in self.supporting_segment_refs:
            raise InterfaceContinuityError(
                "a required boundary cannot support itself"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "required_segment_ref": self.required_segment_ref,
            "supporting_segment_refs": list(self.supporting_segment_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "InterfaceBoundarySupportSet":
        payload = exact_mapping(
            value,
            {"schema", "required_segment_ref", "supporting_segment_refs"},
            "interface boundary support set",
        )
        if payload["schema"] != cls.SCHEMA:
            raise InterfaceContinuityError(
                "unsupported interface boundary support-set schema"
            )
        if not isinstance(payload["supporting_segment_refs"], list):
            raise TypeError(
                "interface support-set supporting_segment_refs must be a list"
            )
        return cls(
            required_segment_ref=payload["required_segment_ref"],
            supporting_segment_refs=tuple(payload["supporting_segment_refs"]),
        )


def _sub(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(first[index] - second[index] for index in range(3))


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(first[index] * second[index] for index in range(3))


def _scaled_sub(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    scale: float,
) -> tuple[float, float, float]:
    return tuple(first[index] - scale * second[index] for index in range(3))


def _quadratic_sublevel_interval(
    a: float,
    b: float,
    c: float,
    lower: float,
    upper: float,
) -> tuple[float, float] | None:
    """Return where ``a*t^2 + b*t + c <= 0`` inside one regime."""

    coefficient_scale = max(abs(a), abs(b), abs(c), 1.0)
    coefficient_epsilon = 1.0e-14 * coefficient_scale
    if abs(a) <= coefficient_epsilon:
        if abs(b) <= coefficient_epsilon:
            return (lower, upper) if c <= coefficient_epsilon else None
        root = -c / b
        if b > 0.0:
            result = lower, min(upper, root)
        else:
            result = max(lower, root), upper
        return result if result[0] <= result[1] + _INTERVAL_EPSILON else None

    discriminant = b * b - 4.0 * a * c
    discriminant_scale = max(abs(b * b), abs(4.0 * a * c), 1.0)
    if discriminant < -1.0e-13 * discriminant_scale:
        return None
    discriminant = max(0.0, discriminant)
    root_delta = math.sqrt(discriminant)
    root_low = (-b - root_delta) / (2.0 * a)
    root_high = (-b + root_delta) / (2.0 * a)
    result = max(lower, root_low), min(upper, root_high)
    return result if result[0] <= result[1] + _INTERVAL_EPSILON else None


def _coverage_intervals(
    required: InterfaceBoundarySegment,
    support: InterfaceBoundarySegment,
    tolerance: float,
) -> tuple[tuple[float, float], ...]:
    """Exact required-line parameters within a support segment's capsule."""

    p0 = required.start
    direction = _sub(required.end, required.start)
    q0 = support.start
    support_direction = _sub(support.end, support.start)
    offset = _sub(p0, q0)
    support_length_squared = _dot(support_direction, support_direction)
    projection_start = _dot(support_direction, offset) / support_length_squared
    projection_rate = _dot(support_direction, direction) / support_length_squared
    breakpoints = [0.0, 1.0]
    if abs(projection_rate) > 1.0e-15:
        for boundary in (0.0, 1.0):
            parameter = (boundary - projection_start) / projection_rate
            if 0.0 < parameter < 1.0:
                breakpoints.append(parameter)
    breakpoints = sorted(set(breakpoints))

    result: list[tuple[float, float]] = []
    for lower, upper in zip(breakpoints, breakpoints[1:]):
        midpoint = (lower + upper) / 2.0
        support_parameter = projection_start + projection_rate * midpoint
        if support_parameter <= 0.0:
            residual_start = offset
            residual_direction = direction
        elif support_parameter >= 1.0:
            residual_start = _sub(p0, support.end)
            residual_direction = direction
        else:
            residual_start = _scaled_sub(
                offset,
                support_direction,
                projection_start,
            )
            residual_direction = _scaled_sub(
                direction,
                support_direction,
                projection_rate,
            )
        interval = _quadratic_sublevel_interval(
            _dot(residual_direction, residual_direction),
            2.0 * _dot(residual_start, residual_direction),
            _dot(residual_start, residual_start) - tolerance * tolerance,
            lower,
            upper,
        )
        if interval is not None:
            result.append(interval)
    return tuple(result)


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    if not intervals:
        return ()
    merged: list[list[float]] = []
    for lower, upper in sorted(intervals):
        lower = max(0.0, lower)
        upper = min(1.0, upper)
        if not merged or lower > merged[-1][1] + _INTERVAL_EPSILON:
            merged.append([lower, upper])
        else:
            merged[-1][1] = max(merged[-1][1], upper)
    return tuple((item[0], item[1]) for item in merged)


def _coverage_metrics(
    intervals: tuple[tuple[float, float], ...],
) -> tuple[float, float]:
    covered_fraction = sum(max(0.0, high - low) for low, high in intervals)
    cursor = 0.0
    largest_uncovered = 0.0
    for low, high in intervals:
        largest_uncovered = max(largest_uncovered, max(0.0, low - cursor))
        cursor = max(cursor, high)
    largest_uncovered = max(largest_uncovered, max(0.0, 1.0 - cursor))
    return min(1.0, covered_fraction), largest_uncovered


def check_interface_boundary_continuity(
    *,
    check_id: str,
    branch: BranchRef,
    scope_digest: str,
    required_segments: tuple[InterfaceBoundarySegment, ...],
    supporting_segments: tuple[InterfaceBoundarySegment, ...],
    support_sets: tuple[InterfaceBoundarySupportSet, ...],
    expected_required_refs: tuple[str, ...],
    tolerance: float,
    evidence_refs: tuple[str, ...],
    authority_refs: tuple[str, ...],
    stage_subject_digest: str | None = None,
    binding_refs: tuple[str, ...] = (),
) -> CheckReceiptEnvelope:
    """Verify full line coverage over an authority-declared denominator."""

    if not isinstance(required_segments, tuple) or not required_segments:
        raise InterfaceContinuityError("required_segments must not be empty")
    if any(
        not isinstance(item, InterfaceBoundarySegment)
        for item in required_segments
    ):
        raise TypeError("required_segments contains an invalid segment")
    if not isinstance(supporting_segments, tuple) or any(
        not isinstance(item, InterfaceBoundarySegment)
        for item in supporting_segments
    ):
        raise TypeError("supporting_segments contains an invalid segment")
    if not isinstance(support_sets, tuple) or any(
        not isinstance(item, InterfaceBoundarySupportSet) for item in support_sets
    ):
        raise TypeError("support_sets contains an invalid support set")

    required_refs = tuple(item.segment_ref for item in required_segments)
    support_refs = tuple(item.segment_ref for item in supporting_segments)
    deterministic_refs(required_refs, "required segment refs")
    deterministic_refs(support_refs, "support segment refs", allow_empty=True)
    expected_refs = deterministic_refs(
        expected_required_refs,
        "expected required segment refs",
    )
    if required_refs != expected_refs:
        raise InterfaceContinuityError(
            "required boundary denominator differs from expected_required_refs"
        )
    if set(required_refs) & set(support_refs):
        raise InterfaceContinuityError(
            "required and supporting segment identities must be disjoint"
        )

    support_set_refs = tuple(item.required_segment_ref for item in support_sets)
    if support_set_refs != expected_refs:
        raise InterfaceContinuityError(
            "support_sets must cover every expected required ref exactly once"
        )
    assigned_support_refs = tuple(
        sorted(
            {
                support_ref
                for item in support_sets
                for support_ref in item.supporting_segment_refs
            }
        )
    )
    if assigned_support_refs != support_refs:
        raise InterfaceContinuityError(
            "supporting segments must be assigned and none may be orphaned"
        )
    if any(
        not set(item.supporting_segment_refs) <= set(support_refs)
        for item in support_sets
    ):
        raise InterfaceContinuityError("support set refers to an unknown segment")
    pair_evaluations = sum(
        len(item.supporting_segment_refs) for item in support_sets
    )
    if pair_evaluations > _MAX_PAIR_EVALUATIONS:
        raise InterfaceContinuityError(
            "interface boundary request exceeds the deterministic work limit"
        )

    checked_tolerance = float(finite_number(tolerance, "interface tolerance"))
    if checked_tolerance <= 0.0:
        raise InterfaceContinuityError("interface tolerance must be positive")
    deterministic_refs(evidence_refs, "interface evidence_refs")
    deterministic_refs(authority_refs, "interface authority_refs")
    checked_binding_refs = deterministic_refs(
        binding_refs,
        "interface binding_refs",
        allow_empty=True,
    )
    checked_stage_subject_digest = (
        None
        if stage_subject_digest is None
        else require_sha256(stage_subject_digest, "stage_subject_digest")
    )

    subject_payload = {
        "schema": "InterfaceBoundaryContinuitySubject@2",
        "coordinate_unit": "meter",
        "required_segments": [item.to_dict() for item in required_segments],
        "supporting_segments": [item.to_dict() for item in supporting_segments],
        "support_sets": [item.to_dict() for item in support_sets],
        "expected_required_refs": list(expected_refs),
        "tolerance": checked_tolerance,
    }
    required_by_ref = {item.segment_ref: item for item in required_segments}
    support_by_ref = {item.segment_ref: item for item in supporting_segments}
    covered: list[str] = []
    findings: list[CheckFinding] = []
    measurements: list[CheckMeasurement] = []
    saw_unknown = False
    saw_failure = False

    for index, support_set in enumerate(support_sets):
        segment = required_by_ref[support_set.required_segment_ref]
        segment_length = math.dist(segment.start, segment.end)
        measurements.append(
            CheckMeasurement(
                measurement_id=f"segment-{index:04d}-required-length",
                subject_ref=segment.segment_ref,
                name="required_length",
                value=segment_length,
                unit_ref="unit:meter",
                evidence_refs=evidence_refs,
            )
        )
        if not support_set.supporting_segment_refs:
            saw_unknown = True
            findings.append(
                CheckFinding(
                    code="interface-support-unavailable",
                    severity=FindingSeverity.UNKNOWN,
                    message="no eligible supporting boundary segment was supplied",
                    subject_refs=(segment.segment_ref,),
                    evidence_refs=evidence_refs,
                )
            )
            continue

        intervals: list[tuple[float, float]] = []
        for support_ref in support_set.supporting_segment_refs:
            intervals.extend(
                _coverage_intervals(
                    segment,
                    support_by_ref[support_ref],
                    checked_tolerance,
                )
            )
        merged = _merge_intervals(intervals)
        covered_fraction, largest_uncovered = _coverage_metrics(merged)
        measurements.extend(
            (
                CheckMeasurement(
                    measurement_id=f"segment-{index:04d}-covered-fraction",
                    subject_ref=segment.segment_ref,
                    name="covered_parameter_fraction",
                    value=covered_fraction,
                    unit_ref="unit:ratio",
                    evidence_refs=evidence_refs,
                ),
                CheckMeasurement(
                    measurement_id=(
                        f"segment-{index:04d}-largest-uncovered-length"
                    ),
                    subject_ref=segment.segment_ref,
                    name="largest_uncovered_length",
                    value=largest_uncovered * segment_length,
                    unit_ref="unit:meter",
                    evidence_refs=evidence_refs,
                ),
            )
        )
        complete = (
            bool(merged)
            and merged[0][0] <= _INTERVAL_EPSILON
            and merged[-1][1] >= 1.0 - _INTERVAL_EPSILON
            and largest_uncovered <= _INTERVAL_EPSILON
        )
        if complete:
            covered.append(segment.segment_ref)
        else:
            saw_failure = True
            findings.append(
                CheckFinding(
                    code="interface-boundary-discontinuous",
                    severity=FindingSeverity.ERROR,
                    message=(
                        "required boundary is not fully covered by its eligible "
                        "supports within tolerance; largest uncovered "
                        f"length={largest_uncovered * segment_length:.9g} m"
                    ),
                    subject_refs=(segment.segment_ref,),
                    evidence_refs=evidence_refs,
                )
            )

    status = (
        CheckStatus.FAIL
        if saw_failure
        else CheckStatus.UNKNOWN
        if saw_unknown
        else CheckStatus.PASS
    )
    context_bound = bool(checked_binding_refs) or checked_stage_subject_digest is not None
    subject_refs = tuple(
        sorted(set(required_refs + support_refs + checked_binding_refs))
    )
    denominator = subject_refs if context_bound else required_refs
    return CheckReceiptEnvelope(
        check_id=check_id,
        checker_id="interface-boundary-continuity",
        checker_version="2.0",
        branch=branch,
        scope_digest=scope_digest,
        subject_refs=subject_refs,
        subject_digest=(
            checked_stage_subject_digest
            if checked_stage_subject_digest is not None
            else canonical_digest(subject_payload)
        ),
        status=status,
        source_refs=evidence_refs,
        authority_refs=authority_refs,
        findings=tuple(findings),
        measurements=tuple(
            sorted(measurements, key=lambda item: item.measurement_id)
        ),
        coverage_denominator=denominator,
        covered_refs=(denominator if status is CheckStatus.PASS else tuple(covered)),
    )


__all__ = [
    "InterfaceBoundarySegment",
    "InterfaceBoundarySupportSet",
    "InterfaceContinuityError",
    "check_interface_boundary_continuity",
]
