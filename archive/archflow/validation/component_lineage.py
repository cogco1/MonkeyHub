"""Pure coverage accounting across an exact pair of design stages.

The predecessor operation set is the denominator.  A changed-operation
allowlist is deliberately not an input because it cannot prove what happened
to unchanged or deferred semantic work.  Every predecessor ``component_id`` /
``operation_id`` pair must instead resolve to exactly one typed disposition:

* ``REFINED`` -- successor operation lineage plus evidence or revalidation;
* ``VERIFIED_UNCHANGED`` -- an identical successor fingerprint plus explicit
  relational revalidation; or
* ``PARKED_WITH_REASON`` -- a bounded reason, evidence, and blocking decision.

This module returns immutable values and a deterministic digest.  It has no
persistence, project-path, stage-acceptance, or canonical-write authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from archflow.contracts.fields import exact_mapping
from archflow.project.refs import require_identifier
from archflow.state.geometry_program import require_sha256
from archflow.contracts.canonical import canonical_digest
from archflow.state.operational_state import require_logical_ref


_MAX_OPERATIONS = 4_096
_MAX_REFS = 4_096
_MAX_REASON_CHARS = 1_000


class StageComponentCoverageError(ValueError):
    """The submitted coverage set is incomplete, contradictory, or unknown."""


class OperationDisposition(StrEnum):
    REFINED = "REFINED"
    VERIFIED_UNCHANGED = "VERIFIED_UNCHANGED"
    PARKED_WITH_REASON = "PARKED_WITH_REASON"


class StageComponentCoverageStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


def _typed_tuple(
    value: object,
    expected_type: type,
    field: str,
    *,
    allow_empty: bool,
    max_items: int = _MAX_OPERATIONS,
) -> tuple:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > max_items or (not allow_empty and not value):
        raise StageComponentCoverageError(f"{field} has an invalid item count")
    if any(not isinstance(item, expected_type) for item in value):
        raise TypeError(f"{field} must contain {expected_type.__name__} values")
    return value


def _sorted_logical_refs(
    value: object,
    field: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_REFS or (not allow_empty and not value):
        raise StageComponentCoverageError(f"{field} has an invalid item count")
    normalized = tuple(require_logical_ref(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise StageComponentCoverageError(f"{field} contains duplicates")
    return tuple(sorted(normalized))


def _sorted_operation_ids(
    value: object,
    field: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_OPERATIONS or (not allow_empty and not value):
        raise StageComponentCoverageError(f"{field} has an invalid item count")
    normalized = tuple(require_identifier(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise StageComponentCoverageError(f"{field} contains duplicates")
    return tuple(sorted(normalized))


def _reason(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_REASON_CHARS:
        raise StageComponentCoverageError(
            f"{field} must be bounded non-empty text"
        )
    return normalized


def _identity_preview(identities: set[tuple[str, str]]) -> str:
    rendered = [f"{component_id}/{operation_id}" for component_id, operation_id in sorted(identities)]
    if len(rendered) > 8:
        rendered = [*rendered[:8], f"...+{len(identities) - 8}"]
    return ", ".join(rendered)


@dataclass(frozen=True, slots=True)
class StageOperationRef:
    """Portable semantic operation identity within one stage snapshot."""

    component_id: str
    operation_id: str

    SCHEMA = "StageOperationRef@1"

    def __post_init__(self) -> None:
        require_identifier(self.component_id, "component_id")
        require_identifier(self.operation_id, "operation_id")

    @property
    def identity(self) -> tuple[str, str]:
        return self.component_id, self.operation_id

    def to_dict(self) -> dict[str, str]:
        return {
            "component_id": self.component_id,
            "operation_id": self.operation_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageOperationRef":
        payload = exact_mapping(
            value,
            {"component_id", "operation_id"},
            "stage operation ref",
        )
        result = cls(
            component_id=payload["component_id"],
            operation_id=payload["operation_id"],
        )
        if result.to_dict() != payload:
            raise StageComponentCoverageError(
                "stage operation ref is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class StageOperation:
    """One exact predecessor or successor operation fingerprint."""

    ref: StageOperationRef
    fingerprint: str

    SCHEMA = "StageOperation@1"

    def __post_init__(self) -> None:
        if not isinstance(self.ref, StageOperationRef):
            raise TypeError("ref must be a StageOperationRef")
        object.__setattr__(
            self,
            "fingerprint",
            require_sha256(self.fingerprint, "operation fingerprint"),
        )

    @property
    def identity(self) -> tuple[str, str]:
        return self.ref.identity

    @property
    def component_id(self) -> str:
        return self.ref.component_id

    @property
    def operation_id(self) -> str:
        return self.ref.operation_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            **self.ref.to_dict(),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageOperation":
        payload = exact_mapping(
            value,
            {"schema", "component_id", "operation_id", "fingerprint"},
            "stage operation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageComponentCoverageError(
                "unsupported stage operation schema"
            )
        result = cls(
            ref=StageOperationRef.from_dict(
                {
                    "component_id": payload["component_id"],
                    "operation_id": payload["operation_id"],
                }
            ),
            fingerprint=payload["fingerprint"],
        )
        if result.to_dict() != payload:
            raise StageComponentCoverageError(
                "stage operation is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class OperationLineageResolution:
    """Caller-supplied resolution from one predecessor to exact successors."""

    predecessor_ref: StageOperationRef
    successor_refs: tuple[StageOperationRef, ...]
    lineage_refs: tuple[str, ...]

    SCHEMA = "OperationLineageResolution@1"

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor_ref, StageOperationRef):
            raise TypeError("predecessor_ref must be a StageOperationRef")
        successor_refs = _typed_tuple(
            self.successor_refs,
            StageOperationRef,
            "successor_refs",
            allow_empty=False,
        )
        if len({item.identity for item in successor_refs}) != len(successor_refs):
            raise StageComponentCoverageError("successor_refs contains duplicates")
        object.__setattr__(
            self,
            "successor_refs",
            tuple(sorted(successor_refs, key=lambda item: item.identity)),
        )
        object.__setattr__(
            self,
            "lineage_refs",
            _sorted_logical_refs(
                self.lineage_refs,
                "lineage_refs",
                allow_empty=False,
            ),
        )

    @property
    def identity(self) -> tuple[str, str]:
        return self.predecessor_ref.identity

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor": self.predecessor_ref.to_dict(),
            "successors": [item.to_dict() for item in self.successor_refs],
            "lineage_refs": list(self.lineage_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "OperationLineageResolution":
        payload = exact_mapping(
            value,
            {"schema", "predecessor", "successors", "lineage_refs"},
            "operation lineage resolution",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageComponentCoverageError(
                "unsupported operation lineage resolution schema"
            )
        if not isinstance(payload["successors"], list):
            raise TypeError("successors must be a list")
        if not isinstance(payload["lineage_refs"], list):
            raise TypeError("lineage_refs must be a list")
        result = cls(
            predecessor_ref=StageOperationRef.from_dict(
                payload["predecessor"]
            ),
            successor_refs=tuple(
                StageOperationRef.from_dict(item)
                for item in payload["successors"]
            ),
            lineage_refs=tuple(payload["lineage_refs"]),
        )
        if result.to_dict() != payload:
            raise StageComponentCoverageError(
                "operation lineage resolution is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class PredecessorOperationDisposition:
    """The asserted disposition for exactly one predecessor operation."""

    predecessor_ref: StageOperationRef
    disposition: OperationDisposition
    evidence_refs: tuple[str, ...] = ()
    relational_revalidation_refs: tuple[str, ...] = ()
    parked_reason: str | None = None
    blocking: bool | None = None

    SCHEMA = "PredecessorOperationDisposition@1"

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor_ref, StageOperationRef):
            raise TypeError("predecessor_ref must be a StageOperationRef")
        if not isinstance(self.disposition, OperationDisposition):
            raise TypeError("disposition must be an OperationDisposition")
        object.__setattr__(
            self,
            "evidence_refs",
            _sorted_logical_refs(
                self.evidence_refs,
                "evidence_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "relational_revalidation_refs",
            _sorted_logical_refs(
                self.relational_revalidation_refs,
                "relational_revalidation_refs",
                allow_empty=True,
            ),
        )

        if self.disposition is OperationDisposition.PARKED_WITH_REASON:
            object.__setattr__(
                self,
                "parked_reason",
                _reason(self.parked_reason, "parked_reason"),
            )
            if not self.evidence_refs:
                raise StageComponentCoverageError(
                    "PARKED_WITH_REASON requires evidence_refs"
                )
            if self.relational_revalidation_refs:
                raise StageComponentCoverageError(
                    "PARKED_WITH_REASON cannot claim relational revalidation"
                )
            if not isinstance(self.blocking, bool):
                raise TypeError(
                    "PARKED_WITH_REASON requires an explicit blocking boolean"
                )
            return

        if self.parked_reason is not None or self.blocking is not None:
            raise StageComponentCoverageError(
                "non-parked dispositions cannot carry parked fields"
            )
        if self.disposition is OperationDisposition.REFINED:
            if not self.evidence_refs and not self.relational_revalidation_refs:
                raise StageComponentCoverageError(
                    "REFINED requires evidence or revalidation refs"
                )
        elif not self.relational_revalidation_refs:
            raise StageComponentCoverageError(
                "VERIFIED_UNCHANGED requires relational revalidation refs"
            )

    @property
    def identity(self) -> tuple[str, str]:
        return self.predecessor_ref.identity

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor": self.predecessor_ref.to_dict(),
            "disposition": self.disposition.value,
            "evidence_refs": list(self.evidence_refs),
            "relational_revalidation_refs": list(
                self.relational_revalidation_refs
            ),
            "parked_reason": self.parked_reason,
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class OperationCoverage:
    """Resolved proof record for one denominator operation."""

    predecessor_operation: StageOperation
    disposition: OperationDisposition
    successor_operations: tuple[StageOperation, ...]
    lineage_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    relational_revalidation_refs: tuple[str, ...]
    parked_reason: str | None
    blocking: bool

    SCHEMA = "OperationCoverage@1"

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor_operation, StageOperation):
            raise TypeError("predecessor_operation must be a StageOperation")
        if not isinstance(self.disposition, OperationDisposition):
            raise TypeError("disposition must be an OperationDisposition")
        successors = _typed_tuple(
            self.successor_operations,
            StageOperation,
            "successor_operations",
            allow_empty=True,
        )
        if len({item.identity for item in successors}) != len(successors):
            raise StageComponentCoverageError(
                "successor_operations contains duplicates"
            )
        expected_successors = tuple(
            sorted(successors, key=lambda item: item.identity)
        )
        if successors != expected_successors:
            raise StageComponentCoverageError(
                "successor_operations must be deterministically ordered"
            )
        lineage_refs = _sorted_logical_refs(
            self.lineage_refs,
            "lineage_refs",
            allow_empty=True,
        )
        evidence_refs = _sorted_logical_refs(
            self.evidence_refs,
            "evidence_refs",
            allow_empty=True,
        )
        revalidation_refs = _sorted_logical_refs(
            self.relational_revalidation_refs,
            "relational_revalidation_refs",
            allow_empty=True,
        )
        if lineage_refs != self.lineage_refs:
            raise StageComponentCoverageError(
                "lineage_refs must be deterministically ordered"
            )
        if evidence_refs != self.evidence_refs:
            raise StageComponentCoverageError(
                "evidence_refs must be deterministically ordered"
            )
        if revalidation_refs != self.relational_revalidation_refs:
            raise StageComponentCoverageError(
                "relational_revalidation_refs must be deterministically ordered"
            )
        if not isinstance(self.blocking, bool):
            raise TypeError("blocking must be boolean")

        if self.disposition is OperationDisposition.REFINED:
            if not successors or not lineage_refs:
                raise StageComponentCoverageError(
                    "REFINED requires successor operations and lineage"
                )
            if not evidence_refs and not revalidation_refs:
                raise StageComponentCoverageError(
                    "REFINED requires evidence or revalidation refs"
                )
            if self.parked_reason is not None or self.blocking:
                raise StageComponentCoverageError(
                    "REFINED cannot carry parked state"
                )
        elif self.disposition is OperationDisposition.VERIFIED_UNCHANGED:
            if len(successors) != 1 or not lineage_refs:
                raise StageComponentCoverageError(
                    "VERIFIED_UNCHANGED requires one lineage-resolved successor"
                )
            if (
                successors[0].fingerprint
                != self.predecessor_operation.fingerprint
            ):
                raise StageComponentCoverageError(
                    "VERIFIED_UNCHANGED requires an unchanged fingerprint"
                )
            if not revalidation_refs:
                raise StageComponentCoverageError(
                    "VERIFIED_UNCHANGED requires relational revalidation refs"
                )
            if self.parked_reason is not None or self.blocking:
                raise StageComponentCoverageError(
                    "VERIFIED_UNCHANGED cannot carry parked state"
                )
        else:
            if successors or lineage_refs or revalidation_refs:
                raise StageComponentCoverageError(
                    "PARKED_WITH_REASON cannot resolve successor lineage"
                )
            if self.parked_reason is None or not evidence_refs:
                raise StageComponentCoverageError(
                    "PARKED_WITH_REASON requires reason and evidence"
                )

    @property
    def identity(self) -> tuple[str, str]:
        return self.predecessor_operation.identity

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_operation": self.predecessor_operation.to_dict(),
            "disposition": self.disposition.value,
            "successor_operations": [
                item.to_dict() for item in self.successor_operations
            ],
            "lineage_refs": list(self.lineage_refs),
            "evidence_refs": list(self.evidence_refs),
            "relational_revalidation_refs": list(
                self.relational_revalidation_refs
            ),
            "parked_reason": self.parked_reason,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, value: object) -> "OperationCoverage":
        payload = exact_mapping(
            value,
            {
                "schema",
                "predecessor_operation",
                "disposition",
                "successor_operations",
                "lineage_refs",
                "evidence_refs",
                "relational_revalidation_refs",
                "parked_reason",
                "blocking",
            },
            "operation coverage",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageComponentCoverageError(
                "unsupported operation coverage schema"
            )
        for field in (
            "successor_operations",
            "lineage_refs",
            "evidence_refs",
            "relational_revalidation_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            predecessor_operation=StageOperation.from_dict(
                payload["predecessor_operation"]
            ),
            disposition=OperationDisposition(payload["disposition"]),
            successor_operations=tuple(
                StageOperation.from_dict(item)
                for item in payload["successor_operations"]
            ),
            lineage_refs=tuple(payload["lineage_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            relational_revalidation_refs=tuple(
                payload["relational_revalidation_refs"]
            ),
            parked_reason=payload["parked_reason"],
            blocking=payload["blocking"],
        )
        if result.to_dict() != payload:
            raise StageComponentCoverageError(
                "operation coverage is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class ComponentCoverageSummary:
    """A complete, disjoint partition of one predecessor component."""

    component_id: str
    predecessor_operation_ids: tuple[str, ...]
    refined_operation_ids: tuple[str, ...]
    verified_unchanged_operation_ids: tuple[str, ...]
    parked_operation_ids: tuple[str, ...]
    blocking_parked_operation_ids: tuple[str, ...]
    status: StageComponentCoverageStatus

    SCHEMA = "ComponentCoverageSummary@1"

    def __post_init__(self) -> None:
        require_identifier(self.component_id, "component_id")
        denominator = _sorted_operation_ids(
            self.predecessor_operation_ids,
            "predecessor_operation_ids",
            allow_empty=False,
        )
        refined = _sorted_operation_ids(
            self.refined_operation_ids,
            "refined_operation_ids",
            allow_empty=True,
        )
        unchanged = _sorted_operation_ids(
            self.verified_unchanged_operation_ids,
            "verified_unchanged_operation_ids",
            allow_empty=True,
        )
        parked = _sorted_operation_ids(
            self.parked_operation_ids,
            "parked_operation_ids",
            allow_empty=True,
        )
        blocking = _sorted_operation_ids(
            self.blocking_parked_operation_ids,
            "blocking_parked_operation_ids",
            allow_empty=True,
        )
        for supplied, normalized, field in (
            (self.predecessor_operation_ids, denominator, "predecessor_operation_ids"),
            (self.refined_operation_ids, refined, "refined_operation_ids"),
            (
                self.verified_unchanged_operation_ids,
                unchanged,
                "verified_unchanged_operation_ids",
            ),
            (self.parked_operation_ids, parked, "parked_operation_ids"),
            (
                self.blocking_parked_operation_ids,
                blocking,
                "blocking_parked_operation_ids",
            ),
        ):
            if supplied != normalized:
                raise StageComponentCoverageError(
                    f"{field} must be deterministically ordered"
                )
        partitions = (set(refined), set(unchanged), set(parked))
        if any(
            left & right
            for index, left in enumerate(partitions)
            for right in partitions[index + 1 :]
        ):
            raise StageComponentCoverageError(
                "component disposition partitions overlap"
            )
        covered = set().union(*partitions)
        if covered != set(denominator):
            raise StageComponentCoverageError(
                "component summary omits or introduces predecessor operations"
            )
        if not set(blocking).issubset(parked):
            raise StageComponentCoverageError(
                "blocking parked operations must be parked operations"
            )
        if not isinstance(self.status, StageComponentCoverageStatus):
            raise TypeError("status must be a StageComponentCoverageStatus")
        expected_status = (
            StageComponentCoverageStatus.FAIL
            if blocking
            else StageComponentCoverageStatus.PASS
        )
        if self.status is not expected_status:
            raise StageComponentCoverageError(
                "component status disagrees with blocking parked operations"
            )

    @property
    def operation_count(self) -> int:
        return len(self.predecessor_operation_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_id": self.component_id,
            "predecessor_operation_ids": list(self.predecessor_operation_ids),
            "refined_operation_ids": list(self.refined_operation_ids),
            "verified_unchanged_operation_ids": list(
                self.verified_unchanged_operation_ids
            ),
            "parked_operation_ids": list(self.parked_operation_ids),
            "blocking_parked_operation_ids": list(
                self.blocking_parked_operation_ids
            ),
            "operation_count": self.operation_count,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentCoverageSummary":
        payload = exact_mapping(
            value,
            {
                "schema",
                "component_id",
                "predecessor_operation_ids",
                "refined_operation_ids",
                "verified_unchanged_operation_ids",
                "parked_operation_ids",
                "blocking_parked_operation_ids",
                "operation_count",
                "status",
            },
            "component coverage summary",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageComponentCoverageError(
                "unsupported component coverage summary schema"
            )
        for field in (
            "predecessor_operation_ids",
            "refined_operation_ids",
            "verified_unchanged_operation_ids",
            "parked_operation_ids",
            "blocking_parked_operation_ids",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            component_id=payload["component_id"],
            predecessor_operation_ids=tuple(
                payload["predecessor_operation_ids"]
            ),
            refined_operation_ids=tuple(payload["refined_operation_ids"]),
            verified_unchanged_operation_ids=tuple(
                payload["verified_unchanged_operation_ids"]
            ),
            parked_operation_ids=tuple(payload["parked_operation_ids"]),
            blocking_parked_operation_ids=tuple(
                payload["blocking_parked_operation_ids"]
            ),
            status=StageComponentCoverageStatus(payload["status"]),
        )
        if result.to_dict() != payload:
            raise StageComponentCoverageError(
                "component coverage summary is not canonical"
            )
        return result


def _component_summaries(
    operations: tuple[OperationCoverage, ...],
) -> tuple[ComponentCoverageSummary, ...]:
    by_component: dict[str, list[OperationCoverage]] = {}
    for operation in operations:
        by_component.setdefault(operation.identity[0], []).append(operation)

    summaries = []
    for component_id in sorted(by_component):
        component_operations = by_component[component_id]
        denominator = tuple(sorted(item.identity[1] for item in component_operations))
        refined = tuple(
            sorted(
                item.identity[1]
                for item in component_operations
                if item.disposition is OperationDisposition.REFINED
            )
        )
        unchanged = tuple(
            sorted(
                item.identity[1]
                for item in component_operations
                if item.disposition is OperationDisposition.VERIFIED_UNCHANGED
            )
        )
        parked = tuple(
            sorted(
                item.identity[1]
                for item in component_operations
                if item.disposition is OperationDisposition.PARKED_WITH_REASON
            )
        )
        blocking = tuple(
            sorted(
                item.identity[1]
                for item in component_operations
                if item.disposition is OperationDisposition.PARKED_WITH_REASON
                and item.blocking
            )
        )
        summaries.append(
            ComponentCoverageSummary(
                component_id=component_id,
                predecessor_operation_ids=denominator,
                refined_operation_ids=refined,
                verified_unchanged_operation_ids=unchanged,
                parked_operation_ids=parked,
                blocking_parked_operation_ids=blocking,
                status=(
                    StageComponentCoverageStatus.FAIL
                    if blocking
                    else StageComponentCoverageStatus.PASS
                ),
            )
        )
    return tuple(summaries)


@dataclass(frozen=True, slots=True)
class StageComponentCoverageReceipt:
    predecessor_stage_id: str
    successor_stage_id: str
    predecessor_operations: tuple[StageOperation, ...]
    successor_operations: tuple[StageOperation, ...]
    lineage_resolutions: tuple[OperationLineageResolution, ...]
    operation_coverage: tuple[OperationCoverage, ...]
    component_summaries: tuple[ComponentCoverageSummary, ...]
    status: StageComponentCoverageStatus

    SCHEMA = "StageComponentCoverageReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.predecessor_stage_id, "predecessor_stage_id")
        require_identifier(self.successor_stage_id, "successor_stage_id")
        predecessors = _typed_tuple(
            self.predecessor_operations,
            StageOperation,
            "predecessor_operations",
            allow_empty=False,
        )
        successors = _typed_tuple(
            self.successor_operations,
            StageOperation,
            "successor_operations",
            allow_empty=True,
        )
        lineages = _typed_tuple(
            self.lineage_resolutions,
            OperationLineageResolution,
            "lineage_resolutions",
            allow_empty=True,
        )
        coverage = _typed_tuple(
            self.operation_coverage,
            OperationCoverage,
            "operation_coverage",
            allow_empty=False,
        )
        summaries = _typed_tuple(
            self.component_summaries,
            ComponentCoverageSummary,
            "component_summaries",
            allow_empty=False,
        )
        ordered_sets = (
            (predecessors, tuple(sorted(predecessors, key=lambda item: item.identity)), "predecessor_operations"),
            (successors, tuple(sorted(successors, key=lambda item: item.identity)), "successor_operations"),
            (lineages, tuple(sorted(lineages, key=lambda item: item.identity)), "lineage_resolutions"),
            (coverage, tuple(sorted(coverage, key=lambda item: item.identity)), "operation_coverage"),
            (summaries, tuple(sorted(summaries, key=lambda item: item.component_id)), "component_summaries"),
        )
        for supplied, expected, field in ordered_sets:
            if supplied != expected:
                raise StageComponentCoverageError(
                    f"{field} must be deterministically ordered"
                )
        for values, field, identity in (
            (predecessors, "predecessor_operations", lambda item: item.identity),
            (successors, "successor_operations", lambda item: item.identity),
            (lineages, "lineage_resolutions", lambda item: item.identity),
            (coverage, "operation_coverage", lambda item: item.identity),
            (summaries, "component_summaries", lambda item: item.component_id),
        ):
            identities = [identity(item) for item in values]
            if len(identities) != len(set(identities)):
                raise StageComponentCoverageError(f"{field} contains duplicates")

        predecessor_by_id = {item.identity: item for item in predecessors}
        successor_by_id = {item.identity: item for item in successors}
        lineage_by_id = {item.identity: item for item in lineages}
        coverage_by_id = {item.identity: item for item in coverage}
        denominator = set(predecessor_by_id)
        if set(coverage_by_id) != denominator:
            raise StageComponentCoverageError(
                "operation coverage differs from the predecessor denominator"
            )
        if not set(lineage_by_id).issubset(denominator):
            raise StageComponentCoverageError(
                "lineage resolutions contain unknown predecessor operations"
            )
        referenced_successors = {
            ref.identity
            for lineage in lineages
            for ref in lineage.successor_refs
        }
        unbound_successors = set(successor_by_id) - referenced_successors
        if unbound_successors:
            raise StageComponentCoverageError(
                "successor operations contain unbound operations: "
                + _identity_preview(unbound_successors)
            )
        for identity, item in coverage_by_id.items():
            if item.predecessor_operation != predecessor_by_id[identity]:
                raise StageComponentCoverageError(
                    "operation coverage changed a predecessor fingerprint"
                )
            lineage = lineage_by_id.get(identity)
            if item.disposition is OperationDisposition.PARKED_WITH_REASON:
                if lineage is not None:
                    raise StageComponentCoverageError(
                        "parked operation cannot carry lineage resolution"
                    )
                continue
            if lineage is None:
                raise StageComponentCoverageError(
                    "non-parked operation requires lineage resolution"
                )
            try:
                expected_successors = tuple(
                    successor_by_id[ref.identity]
                    for ref in lineage.successor_refs
                )
            except KeyError as exc:
                raise StageComponentCoverageError(
                    "lineage references an unknown successor operation"
                ) from exc
            if (
                item.successor_operations != expected_successors
                or item.lineage_refs != lineage.lineage_refs
            ):
                raise StageComponentCoverageError(
                    "operation coverage disagrees with lineage resolution"
                )

        expected_summaries = _component_summaries(coverage)
        if summaries != expected_summaries:
            raise StageComponentCoverageError(
                "component summaries disagree with exact operation coverage"
            )
        if not isinstance(self.status, StageComponentCoverageStatus):
            raise TypeError("status must be a StageComponentCoverageStatus")
        expected_status = (
            StageComponentCoverageStatus.FAIL
            if any(item.blocking for item in coverage)
            else StageComponentCoverageStatus.PASS
        )
        if self.status is not expected_status:
            raise StageComponentCoverageError(
                "overall status disagrees with blocking parked operations"
            )

    @property
    def component_count(self) -> int:
        return len(self.component_summaries)

    @property
    def operation_count(self) -> int:
        return len(self.predecessor_operations)

    @property
    def predecessor_denominator_digest(self) -> str:
        return canonical_digest(
            [item.to_dict() for item in self.predecessor_operations]
        )

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_stage_id": self.predecessor_stage_id,
            "successor_stage_id": self.successor_stage_id,
            "predecessor_operations": [
                item.to_dict() for item in self.predecessor_operations
            ],
            "successor_operations": [
                item.to_dict() for item in self.successor_operations
            ],
            "predecessor_denominator_digest": (
                self.predecessor_denominator_digest
            ),
            "lineage_resolutions": [
                item.to_dict() for item in self.lineage_resolutions
            ],
            "operation_coverage": [
                item.to_dict() for item in self.operation_coverage
            ],
            "component_summaries": [
                item.to_dict() for item in self.component_summaries
            ],
            "component_count": self.component_count,
            "operation_count": self.operation_count,
            "status": self.status.value,
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageComponentCoverageReceipt":
        payload = exact_mapping(
            value,
            {
                "schema",
                "predecessor_stage_id",
                "successor_stage_id",
                "predecessor_operations",
                "successor_operations",
                "predecessor_denominator_digest",
                "lineage_resolutions",
                "operation_coverage",
                "component_summaries",
                "component_count",
                "operation_count",
                "status",
                "stage_acceptance_authority",
                "canonical_write_authority",
            },
            "stage component coverage receipt",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageComponentCoverageError(
                "unsupported stage component coverage receipt schema"
            )
        for field in (
            "predecessor_operations",
            "successor_operations",
            "lineage_resolutions",
            "operation_coverage",
            "component_summaries",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            predecessor_stage_id=payload["predecessor_stage_id"],
            successor_stage_id=payload["successor_stage_id"],
            predecessor_operations=tuple(
                StageOperation.from_dict(item)
                for item in payload["predecessor_operations"]
            ),
            successor_operations=tuple(
                StageOperation.from_dict(item)
                for item in payload["successor_operations"]
            ),
            lineage_resolutions=tuple(
                OperationLineageResolution.from_dict(item)
                for item in payload["lineage_resolutions"]
            ),
            operation_coverage=tuple(
                OperationCoverage.from_dict(item)
                for item in payload["operation_coverage"]
            ),
            component_summaries=tuple(
                ComponentCoverageSummary.from_dict(item)
                for item in payload["component_summaries"]
            ),
            status=StageComponentCoverageStatus(payload["status"]),
        )
        if result.to_dict() != payload:
            raise StageComponentCoverageError(
                "stage component coverage receipt is not canonical"
            )
        return result


def compile_stage_component_coverage(
    *,
    predecessor_stage_id: str,
    successor_stage_id: str,
    predecessor_operations: Iterable[StageOperation],
    successor_operations: Iterable[StageOperation],
    lineage_resolutions: Iterable[OperationLineageResolution],
    dispositions: Iterable[PredecessorOperationDisposition],
) -> StageComponentCoverageReceipt:
    """Compile fail-closed coverage from the complete predecessor operation set."""

    require_identifier(predecessor_stage_id, "predecessor_stage_id")
    require_identifier(successor_stage_id, "successor_stage_id")
    predecessors = tuple(predecessor_operations)
    successors = tuple(successor_operations)
    lineages = tuple(lineage_resolutions)
    submitted_dispositions = tuple(dispositions)
    for values, expected_type, field, allow_empty in (
        (predecessors, StageOperation, "predecessor_operations", False),
        (successors, StageOperation, "successor_operations", True),
        (
            lineages,
            OperationLineageResolution,
            "lineage_resolutions",
            True,
        ),
        (
            submitted_dispositions,
            PredecessorOperationDisposition,
            "dispositions",
            False,
        ),
    ):
        _typed_tuple(
            values,
            expected_type,
            field,
            allow_empty=allow_empty,
        )
    predecessors = tuple(sorted(predecessors, key=lambda item: item.identity))
    successors = tuple(sorted(successors, key=lambda item: item.identity))
    lineages = tuple(sorted(lineages, key=lambda item: item.identity))
    submitted_dispositions = tuple(
        sorted(submitted_dispositions, key=lambda item: item.identity)
    )

    for values, field in (
        (predecessors, "predecessor_operations"),
        (successors, "successor_operations"),
        (lineages, "lineage_resolutions"),
        (submitted_dispositions, "dispositions"),
    ):
        identities = [item.identity for item in values]
        if len(identities) != len(set(identities)):
            raise StageComponentCoverageError(f"{field} contains duplicates")

    predecessor_by_id = {item.identity: item for item in predecessors}
    successor_by_id = {item.identity: item for item in successors}
    lineage_by_id = {item.identity: item for item in lineages}
    disposition_by_id = {item.identity: item for item in submitted_dispositions}
    denominator = set(predecessor_by_id)
    disposition_identities = set(disposition_by_id)
    missing = denominator - disposition_identities
    unknown = disposition_identities - denominator
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing={_identity_preview(missing)}")
        if unknown:
            details.append(f"unknown={_identity_preview(unknown)}")
        raise StageComponentCoverageError(
            "dispositions must cover the exact predecessor denominator; "
            + "; ".join(details)
        )

    unknown_lineage_predecessors = set(lineage_by_id) - denominator
    if unknown_lineage_predecessors:
        raise StageComponentCoverageError(
            "lineage resolutions contain unknown predecessors: "
            + _identity_preview(unknown_lineage_predecessors)
        )
    for lineage in lineages:
        unknown_successors = {
            ref.identity
            for ref in lineage.successor_refs
            if ref.identity not in successor_by_id
        }
        if unknown_successors:
            raise StageComponentCoverageError(
                "lineage references unknown successors: "
                + _identity_preview(unknown_successors)
            )
    referenced_successors = {
        ref.identity
        for lineage in lineages
        for ref in lineage.successor_refs
    }
    unbound_successors = set(successor_by_id) - referenced_successors
    if unbound_successors:
        raise StageComponentCoverageError(
            "successor operations contain unbound operations: "
            + _identity_preview(unbound_successors)
        )

    coverage = []
    for identity, predecessor in predecessor_by_id.items():
        submitted = disposition_by_id[identity]
        lineage = lineage_by_id.get(identity)
        if submitted.disposition is OperationDisposition.PARKED_WITH_REASON:
            if lineage is not None:
                raise StageComponentCoverageError(
                    "PARKED_WITH_REASON cannot carry lineage resolution"
                )
            resolved_successors: tuple[StageOperation, ...] = ()
            lineage_refs: tuple[str, ...] = ()
        else:
            if lineage is None:
                raise StageComponentCoverageError(
                    f"{submitted.disposition.value} requires lineage resolution"
                )
            resolved_successors = tuple(
                successor_by_id[ref.identity] for ref in lineage.successor_refs
            )
            lineage_refs = lineage.lineage_refs
        coverage.append(
            OperationCoverage(
                predecessor_operation=predecessor,
                disposition=submitted.disposition,
                successor_operations=resolved_successors,
                lineage_refs=lineage_refs,
                evidence_refs=submitted.evidence_refs,
                relational_revalidation_refs=(
                    submitted.relational_revalidation_refs
                ),
                parked_reason=submitted.parked_reason,
                blocking=(
                    submitted.blocking
                    if submitted.disposition
                    is OperationDisposition.PARKED_WITH_REASON
                    else False
                ),
            )
        )

    operation_coverage = tuple(sorted(coverage, key=lambda item: item.identity))
    component_summaries = _component_summaries(operation_coverage)
    status = (
        StageComponentCoverageStatus.FAIL
        if any(item.blocking for item in operation_coverage)
        else StageComponentCoverageStatus.PASS
    )
    return StageComponentCoverageReceipt(
        predecessor_stage_id=predecessor_stage_id,
        successor_stage_id=successor_stage_id,
        predecessor_operations=predecessors,
        successor_operations=successors,
        lineage_resolutions=lineages,
        operation_coverage=operation_coverage,
        component_summaries=component_summaries,
        status=status,
    )

