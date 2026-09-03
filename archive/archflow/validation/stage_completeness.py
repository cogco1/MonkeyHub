"""Pure architectural stage-completeness and evidence-granularity checks.

Projects supply the typology, stage, required decision families, coverage, and
parameter evidence.  This module owns only deterministic bookkeeping and the
fail-closed rule that topology-only evidence cannot authorize numeric values.
It has no persistence, building catalogue, or typology defaults.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from archflow.contracts.canonical import canonical_digest


class ArchitecturalCompletenessError(ValueError):
    """A completeness requirement or submitted evidence value is malformed."""


class ArchitecturalCompletenessStatus(StrEnum):
    INCOMPLETE = "INCOMPLETE"
    COMPLETE = "COMPLETE"


class ParameterGranularity(StrEnum):
    """The strongest value shape asserted by one parameter claim."""

    TOPOLOGY = "topology"
    NUMERIC_RANGE = "numeric_range"
    EXACT_NUMERIC = "exact_numeric"


class ParameterBasisKind(StrEnum):
    """How a project says a parameter value was established."""

    MEASURED = "measured"
    DERIVED = "derived"
    DECLARED_CANDIDATE = "declared_candidate"
    TOPOLOGY_ONLY = "topology_only"


class ParameterEvidenceIssueReason(StrEnum):
    EXACT_NUMERIC_BASIS_REQUIRED = "exact_numeric_basis_required"
    NUMERIC_RANGE_BASIS_REQUIRED = "numeric_range_basis_required"


_NUMERIC_BASES = frozenset(
    {
        ParameterBasisKind.MEASURED,
        ParameterBasisKind.DERIVED,
        ParameterBasisKind.DECLARED_CANDIDATE,
    }
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    candidate = value.strip()
    if not candidate or len(candidate) > 1_000:
        raise ArchitecturalCompletenessError(
            f"{field} must be bounded non-empty text"
        )
    return candidate


def _text_tuple(
    values: object,
    field: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    normalized = tuple(_text(value, field) for value in values)
    if required and not normalized:
        raise ArchitecturalCompletenessError(f"{field} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ArchitecturalCompletenessError(f"{field} must be unique")
    return tuple(sorted(normalized))


def _typed_tuple(
    values: object,
    expected_type: type,
    field: str,
) -> tuple:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if any(not isinstance(item, expected_type) for item in values):
        raise TypeError(
            f"{field} must contain {expected_type.__name__} values"
        )
    return values


def _finite_values(values: object, field: str) -> tuple[float, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    result = []
    for value in values:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise ArchitecturalCompletenessError(
                f"{field} must contain finite numeric values"
            )
        result.append(float(value))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class StageDecisionRequirements:
    """Caller-owned decision-family denominator for one typology stage."""

    typology_id: str
    stage_id: str
    required_decision_families: tuple[str, ...]

    SCHEMA = "StageDecisionRequirements@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "typology_id",
            _text(self.typology_id, "typology_id"),
        )
        object.__setattr__(self, "stage_id", _text(self.stage_id, "stage_id"))
        object.__setattr__(
            self,
            "required_decision_families",
            _text_tuple(
                self.required_decision_families,
                "required_decision_families",
                required=True,
            ),
        )

    @property
    def requirements_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "typology_id": self.typology_id,
            "stage_id": self.stage_id,
            "required_decision_families": list(
                self.required_decision_families
            ),
            "decision_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class DecisionFamilyCoverage:
    """Evidence that at least one decision in a named family was addressed."""

    family_id: str
    decision_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "DecisionFamilyCoverage@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "family_id", _text(self.family_id, "family_id"))
        object.__setattr__(
            self,
            "decision_refs",
            _text_tuple(self.decision_refs, "decision_refs", required=True),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs", required=True),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "family_id": self.family_id,
            "decision_refs": list(self.decision_refs),
            "evidence_refs": list(self.evidence_refs),
            "coverage_authority": False,
        }


@dataclass(frozen=True, slots=True)
class ParameterEvidence:
    """One parameter claim with explicit value and epistemic granularity."""

    parameter_id: str
    decision_family: str
    granularity: ParameterGranularity
    basis: ParameterBasisKind
    evidence_refs: tuple[str, ...]
    numeric_values: tuple[float, ...] = ()
    unit: str | None = None

    SCHEMA = "ArchitecturalParameterEvidence@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameter_id",
            _text(self.parameter_id, "parameter_id"),
        )
        object.__setattr__(
            self,
            "decision_family",
            _text(self.decision_family, "decision_family"),
        )
        if not isinstance(self.granularity, ParameterGranularity):
            raise TypeError("granularity must be ParameterGranularity")
        if not isinstance(self.basis, ParameterBasisKind):
            raise TypeError("basis must be ParameterBasisKind")
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs", required=True),
        )
        numeric_values = _finite_values(self.numeric_values, "numeric_values")
        object.__setattr__(self, "numeric_values", numeric_values)
        if self.unit is not None:
            object.__setattr__(self, "unit", _text(self.unit, "unit"))

        if self.granularity is ParameterGranularity.TOPOLOGY:
            if numeric_values:
                raise ArchitecturalCompletenessError(
                    "topology evidence cannot carry numeric_values"
                )
        elif self.granularity is ParameterGranularity.NUMERIC_RANGE:
            if len(numeric_values) != 2 or numeric_values[0] > numeric_values[1]:
                raise ArchitecturalCompletenessError(
                    "numeric range evidence requires ordered minimum and maximum"
                )
        elif not numeric_values:
            raise ArchitecturalCompletenessError(
                "exact numeric evidence requires at least one numeric value"
            )

    @property
    def identity(self) -> tuple[str, str]:
        return (self.decision_family, self.parameter_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "parameter_id": self.parameter_id,
            "decision_family": self.decision_family,
            "granularity": self.granularity.value,
            "basis": self.basis.value,
            "numeric_values": list(self.numeric_values),
            "unit": self.unit,
            "evidence_refs": list(self.evidence_refs),
            "parameter_authority": False,
        }


@dataclass(frozen=True, slots=True)
class ParameterEvidenceIssue:
    reason: ParameterEvidenceIssueReason
    parameter_id: str
    decision_family: str
    granularity: ParameterGranularity
    supplied_basis: ParameterBasisKind

    def __post_init__(self) -> None:
        if not isinstance(self.reason, ParameterEvidenceIssueReason):
            raise TypeError("reason must be ParameterEvidenceIssueReason")
        object.__setattr__(
            self,
            "parameter_id",
            _text(self.parameter_id, "parameter_id"),
        )
        object.__setattr__(
            self,
            "decision_family",
            _text(self.decision_family, "decision_family"),
        )
        if not isinstance(self.granularity, ParameterGranularity):
            raise TypeError("granularity must be ParameterGranularity")
        if not isinstance(self.supplied_basis, ParameterBasisKind):
            raise TypeError("supplied_basis must be ParameterBasisKind")

    @property
    def identity(self) -> tuple[str, str, str]:
        return (
            self.decision_family,
            self.parameter_id,
            self.reason.value,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason.value,
            "parameter_id": self.parameter_id,
            "decision_family": self.decision_family,
            "granularity": self.granularity.value,
            "supplied_basis": self.supplied_basis.value,
            "allowed_numeric_bases": sorted(item.value for item in _NUMERIC_BASES),
        }


@dataclass(frozen=True, slots=True)
class ArchitecturalCompletenessReceipt:
    requirements: StageDecisionRequirements
    family_coverage: tuple[DecisionFamilyCoverage, ...]
    parameter_evidence: tuple[ParameterEvidence, ...]
    covered_decision_families: tuple[str, ...]
    missing_decision_families: tuple[str, ...]
    parameter_issues: tuple[ParameterEvidenceIssue, ...]
    compilation_status: ArchitecturalCompletenessStatus

    SCHEMA = "ArchitecturalCompletenessReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.requirements, StageDecisionRequirements):
            raise TypeError("requirements must be StageDecisionRequirements")
        coverage = _typed_tuple(
            self.family_coverage,
            DecisionFamilyCoverage,
            "family_coverage",
        )
        parameters = _typed_tuple(
            self.parameter_evidence,
            ParameterEvidence,
            "parameter_evidence",
        )
        issues = _typed_tuple(
            self.parameter_issues,
            ParameterEvidenceIssue,
            "parameter_issues",
        )
        expected_coverage = tuple(sorted(coverage, key=lambda item: item.family_id))
        expected_parameters = tuple(sorted(parameters, key=lambda item: item.identity))
        expected_issues = tuple(sorted(issues, key=lambda item: item.identity))
        if coverage != expected_coverage:
            raise ArchitecturalCompletenessError(
                "family_coverage must be deterministically ordered"
            )
        if parameters != expected_parameters:
            raise ArchitecturalCompletenessError(
                "parameter_evidence must be deterministically ordered"
            )
        if issues != expected_issues:
            raise ArchitecturalCompletenessError(
                "parameter_issues must be deterministically ordered"
            )
        if len({item.family_id for item in coverage}) != len(coverage):
            raise ArchitecturalCompletenessError(
                "family_coverage must contain one entry per family"
            )
        if len({item.identity for item in parameters}) != len(parameters):
            raise ArchitecturalCompletenessError(
                "parameter_evidence identities must be unique"
            )
        if len({item.identity for item in issues}) != len(issues):
            raise ArchitecturalCompletenessError(
                "parameter_issues identities must be unique"
            )

        covered = _text_tuple(
            self.covered_decision_families,
            "covered_decision_families",
            required=False,
        )
        missing = _text_tuple(
            self.missing_decision_families,
            "missing_decision_families",
            required=False,
        )
        object.__setattr__(self, "covered_decision_families", covered)
        object.__setattr__(self, "missing_decision_families", missing)
        if covered != tuple(item.family_id for item in coverage):
            raise ArchitecturalCompletenessError(
                "covered_decision_families disagree with family_coverage"
            )
        expected_missing = tuple(
            sorted(
                set(self.requirements.required_decision_families) - set(covered)
            )
        )
        if missing != expected_missing:
            raise ArchitecturalCompletenessError(
                "missing_decision_families disagree with requirements"
            )
        expected_issues_from_parameters = tuple(
            sorted(
                (
                    issue
                    for parameter in parameters
                    if parameter.granularity
                    is not ParameterGranularity.TOPOLOGY
                    for issue in (_numeric_issue(parameter),)
                    if issue is not None
                ),
                key=lambda item: item.identity,
            )
        )
        if issues != expected_issues_from_parameters:
            raise ArchitecturalCompletenessError(
                "parameter_issues disagree with parameter evidence granularity"
            )
        if not isinstance(
            self.compilation_status,
            ArchitecturalCompletenessStatus,
        ):
            raise TypeError(
                "compilation_status must be ArchitecturalCompletenessStatus"
            )
        expected_status = (
            ArchitecturalCompletenessStatus.COMPLETE
            if not missing and not issues
            else ArchitecturalCompletenessStatus.INCOMPLETE
        )
        if self.compilation_status is not expected_status:
            raise ArchitecturalCompletenessError(
                "compilation_status disagrees with missing families or issues"
            )

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirements": self.requirements.to_dict(),
            "requirements_digest": self.requirements.requirements_digest,
            "family_coverage": [item.to_dict() for item in self.family_coverage],
            "covered_decision_families": list(self.covered_decision_families),
            "missing_decision_families": list(self.missing_decision_families),
            "parameter_evidence": [
                item.to_dict() for item in self.parameter_evidence
            ],
            "parameter_issues": [item.to_dict() for item in self.parameter_issues],
            "compilation_status": self.compilation_status.value,
            "decision_authority": False,
            "parameter_authority": False,
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }


def _numeric_issue(
    parameter: ParameterEvidence,
) -> ParameterEvidenceIssue | None:
    if parameter.basis in _NUMERIC_BASES:
        return None
    reason = (
        ParameterEvidenceIssueReason.EXACT_NUMERIC_BASIS_REQUIRED
        if parameter.granularity is ParameterGranularity.EXACT_NUMERIC
        else ParameterEvidenceIssueReason.NUMERIC_RANGE_BASIS_REQUIRED
    )
    return ParameterEvidenceIssue(
        reason=reason,
        parameter_id=parameter.parameter_id,
        decision_family=parameter.decision_family,
        granularity=parameter.granularity,
        supplied_basis=parameter.basis,
    )


def compile_architectural_completeness(
    requirements: StageDecisionRequirements,
    *,
    family_coverage: Iterable[DecisionFamilyCoverage],
    parameter_evidence: Iterable[ParameterEvidence] = (),
) -> ArchitecturalCompletenessReceipt:
    """Compile a non-authoritative, fail-closed stage completeness receipt."""

    if not isinstance(requirements, StageDecisionRequirements):
        raise TypeError("requirements must be StageDecisionRequirements")
    coverage = tuple(family_coverage)
    parameters = tuple(parameter_evidence)
    if any(not isinstance(item, DecisionFamilyCoverage) for item in coverage):
        raise TypeError("family_coverage must contain DecisionFamilyCoverage")
    if any(not isinstance(item, ParameterEvidence) for item in parameters):
        raise TypeError("parameter_evidence must contain ParameterEvidence")
    coverage = tuple(sorted(coverage, key=lambda item: item.family_id))
    parameters = tuple(sorted(parameters, key=lambda item: item.identity))
    if len({item.family_id for item in coverage}) != len(coverage):
        raise ArchitecturalCompletenessError(
            "family_coverage must contain one entry per family"
        )
    if len({item.identity for item in parameters}) != len(parameters):
        raise ArchitecturalCompletenessError(
            "parameter_evidence identities must be unique"
        )

    covered = tuple(item.family_id for item in coverage)
    missing = tuple(
        sorted(set(requirements.required_decision_families) - set(covered))
    )
    issues = tuple(
        sorted(
            (
                issue
                for parameter in parameters
                if parameter.granularity is not ParameterGranularity.TOPOLOGY
                for issue in (_numeric_issue(parameter),)
                if issue is not None
            ),
            key=lambda item: item.identity,
        )
    )
    status = (
        ArchitecturalCompletenessStatus.COMPLETE
        if not missing and not issues
        else ArchitecturalCompletenessStatus.INCOMPLETE
    )
    return ArchitecturalCompletenessReceipt(
        requirements=requirements,
        family_coverage=coverage,
        parameter_evidence=parameters,
        covered_decision_families=covered,
        missing_decision_families=missing,
        parameter_issues=issues,
        compilation_status=status,
    )
