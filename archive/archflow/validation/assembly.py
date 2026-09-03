"""Deterministic, project-independent assembly relationship checks.

The checker consumes explicit subject geometry and explicit relationship
requirements.  It never infers a relationship from a component name, accepts
no caller-provided pass flag, performs no I/O, and has no mutation or
persistence authority.
"""

from __future__ import annotations

import math
from collections import deque
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
from archflow.project.refs import BranchRef
from archive.archflow.validation.contracts import CheckFinding, CheckMeasurement, CheckReceiptEnvelope, CheckStatus, FindingSeverity
from archive.archflow.validation.spatial import AABB


class AssemblyValidationError(ValueError):
    """An assembly profile or relationship contract is structurally invalid."""


class RelationshipKind(StrEnum):
    """Geometry and topology relationships understood by the checker."""

    TOUCH = "touch"
    SUPPORT = "support"
    FORBIDDEN_OVERLAP = "forbidden_overlap"
    BOUNDED_EMBEDDED_OVERLAP = "bounded_embedded_overlap"
    HOST_CONTAINMENT = "host_containment"
    OPENING_CLEAR = "opening_clear"
    VERTICAL_SUPPORT_CHAIN = "vertical_support_chain"
    LOAD_PATH_TO_FOUNDATION = "load_path_to_foundation"


class GeometryBoundsBasis(StrEnum):
    """What an AABB is allowed to prove about its underlying geometry."""

    EXACT_AXIS_ALIGNED_SOLID = "exact_axis_aligned_solid"
    CONSERVATIVE_ENVELOPE = "conservative_envelope"


class AssemblyObligationDisposition(StrEnum):
    """Whether one explicit subject role requires a relationship."""

    REQUIRED = "required"
    NOT_APPLICABLE = "not_applicable"


class RelationCandidateDisposition(StrEnum):
    """How one mechanically discovered relation candidate is accounted."""

    REQUIREMENT = "requirement"
    NOT_APPLICABLE = "not_applicable"
    UNRESOLVED = "unresolved"


_UNDIRECTED_KINDS = frozenset(
    {
        RelationshipKind.TOUCH,
        RelationshipKind.FORBIDDEN_OVERLAP,
    }
)
_BINARY_KINDS = frozenset(
    {
        RelationshipKind.TOUCH,
        RelationshipKind.SUPPORT,
        RelationshipKind.FORBIDDEN_OVERLAP,
        RelationshipKind.BOUNDED_EMBEDDED_OVERLAP,
        RelationshipKind.HOST_CONTAINMENT,
        RelationshipKind.OPENING_CLEAR,
        RelationshipKind.LOAD_PATH_TO_FOUNDATION,
    }
)
_GEOMETRY_KINDS = frozenset(
    {
        RelationshipKind.TOUCH,
        RelationshipKind.SUPPORT,
        RelationshipKind.FORBIDDEN_OVERLAP,
        RelationshipKind.BOUNDED_EMBEDDED_OVERLAP,
        RelationshipKind.HOST_CONTAINMENT,
        RelationshipKind.OPENING_CLEAR,
    }
)
_AXIS_KINDS = frozenset(
    {
        RelationshipKind.SUPPORT,
        RelationshipKind.VERTICAL_SUPPORT_CHAIN,
        RelationshipKind.LOAD_PATH_TO_FOUNDATION,
    }
)
_WILDCARD_TOKENS = frozenset("*?[]")
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
_LINEAR_UNIT_REFS = {
    "m": "unit:meter",
    "meter": "unit:meter",
    "metre": "unit:meter",
    "mm": "unit:millimeter",
    "millimeter": "unit:millimeter",
    "millimetre": "unit:millimeter",
    "cm": "unit:centimeter",
    "centimeter": "unit:centimeter",
    "centimetre": "unit:centimeter",
    "ft": "unit:foot",
    "foot": "unit:foot",
    "in": "unit:inch",
    "inch": "unit:inch",
}
_AREA_UNIT_REFS = {
    "m": "unit:square-meter",
    "meter": "unit:square-meter",
    "metre": "unit:square-meter",
    "mm": "unit:square-millimeter",
    "millimeter": "unit:square-millimeter",
    "millimetre": "unit:square-millimeter",
    "cm": "unit:square-centimeter",
    "centimeter": "unit:square-centimeter",
    "centimetre": "unit:square-centimeter",
    "ft": "unit:square-foot",
    "foot": "unit:square-foot",
    "in": "unit:square-inch",
    "inch": "unit:square-inch",
}
_VOLUME_UNIT_REFS = {
    "m": "unit:cubic-meter",
    "meter": "unit:cubic-meter",
    "metre": "unit:cubic-meter",
    "mm": "unit:cubic-millimeter",
    "millimeter": "unit:cubic-millimeter",
    "millimetre": "unit:cubic-millimeter",
    "cm": "unit:cubic-centimeter",
    "centimeter": "unit:cubic-centimeter",
    "centimetre": "unit:cubic-centimeter",
    "ft": "unit:cubic-foot",
    "foot": "unit:cubic-foot",
    "in": "unit:cubic-inch",
    "inch": "unit:cubic-inch",
}


def _exact_ref(value: object, field: str) -> str:
    result = logical_ref(value, field)
    if any(token in result for token in _WILDCARD_TOKENS):
        raise AssemblyValidationError(f"{field} must name one exact endpoint")
    return result


def _ordered_exact_refs(values: object, field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if not values or len(values) > 4_096:
        raise AssemblyValidationError(f"{field} must not be empty")
    result = tuple(_exact_ref(item, field) for item in values)
    if len(result) != len(set(result)):
        raise AssemblyValidationError(f"{field} must contain unique endpoints")
    return result


def _non_negative(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise AssemblyValidationError(f"{field} must be a finite number")
    result = float(value)
    if result < 0.0:
        raise AssemblyValidationError(f"{field} must be non-negative")
    return 0.0 if result == 0.0 else result


def _optional_positive(value: object, field: str) -> float | None:
    if value is None:
        return None
    result = _non_negative(value, field)
    if result <= 0.0:
        raise AssemblyValidationError(f"{field} must be positive")
    return result


def _bounds_from_dict(value: object) -> AABB:
    payload = exact_mapping(
        value,
        {"schema", "minimum", "maximum"},
        "assembly subject bounds",
    )
    if payload["schema"] != AABB.SCHEMA:
        raise AssemblyValidationError("assembly subject bounds schema drifted")
    if not isinstance(payload["minimum"], list) or not isinstance(
        payload["maximum"], list
    ):
        raise TypeError("assembly subject bounds vectors must be lists")
    return AABB(
        minimum=tuple(payload["minimum"]),
        maximum=tuple(payload["maximum"]),
    )


@dataclass(frozen=True, slots=True)
class AssemblySubject:
    """One exact assembly endpoint and its optional checked geometry."""

    subject_ref: str
    bounds: AABB | None
    bounds_basis: GeometryBoundsBasis | None
    geometry_ref: str | None = None

    SCHEMA = "AssemblySubject@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_ref",
            _exact_ref(self.subject_ref, "assembly subject_ref"),
        )
        if self.bounds is not None and not isinstance(self.bounds, AABB):
            raise TypeError("bounds must be an AABB or None")
        if self.bounds is None:
            if self.bounds_basis is not None:
                raise AssemblyValidationError(
                    "bounds_basis must be None when geometry bounds are absent"
                )
        elif not isinstance(self.bounds_basis, GeometryBoundsBasis):
            raise AssemblyValidationError(
                "available bounds require an explicit GeometryBoundsBasis"
            )
        if self.geometry_ref is not None:
            object.__setattr__(
                self,
                "geometry_ref",
                _exact_ref(self.geometry_ref, "assembly geometry_ref"),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "subject_ref": self.subject_ref,
            "bounds": None if self.bounds is None else self.bounds.to_dict(),
            "bounds_basis": (
                None if self.bounds_basis is None else self.bounds_basis.value
            ),
            "geometry_ref": self.geometry_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssemblySubject":
        payload = exact_mapping(
            value,
            {
                "schema",
                "subject_ref",
                "bounds",
                "bounds_basis",
                "geometry_ref",
            },
            "assembly subject",
        )
        if payload["schema"] != cls.SCHEMA:
            raise AssemblyValidationError("assembly subject schema drifted")
        bounds_value = payload["bounds"]
        geometry_ref = payload["geometry_ref"]
        if geometry_ref is not None and not isinstance(geometry_ref, str):
            raise TypeError("geometry_ref must be text or None")
        return cls(
            subject_ref=payload["subject_ref"],
            bounds=(
                None
                if bounds_value is None
                else _bounds_from_dict(bounds_value)
            ),
            bounds_basis=(
                None
                if payload["bounds_basis"] is None
                else GeometryBoundsBasis(payload["bounds_basis"])
            ),
            geometry_ref=geometry_ref,
        )


@dataclass(frozen=True, slots=True)
class RelationshipRequirement:
    """One authority-backed requirement over exact ordered endpoints.

    Directed binary endpoint order is ``(subject, target)``.  In particular,
    support means ``(supported, supporter)``, embedded overlap means
    ``(embedded, host)``, containment means ``(contained, host)``, and opening
    clear means ``(opening-clear region, possible obstruction)``.  A vertical
    chain lists every endpoint from top to terminal support.  A load-path
    requirement lists exactly ``(start, foundation)``.
    """

    requirement_id: str
    kind: RelationshipKind
    subject_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    maximum_overlap_volume: float | None = None

    SCHEMA = "RelationshipRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.requirement_id, "relationship requirement_id")
        if not isinstance(self.kind, RelationshipKind):
            raise TypeError("kind must be a RelationshipKind")
        endpoints = _ordered_exact_refs(
            self.subject_refs,
            "relationship subject_refs",
        )
        if self.kind in _BINARY_KINDS and len(endpoints) != 2:
            raise AssemblyValidationError(
                f"{self.kind.value} requires exactly two endpoints"
            )
        if (
            self.kind is RelationshipKind.VERTICAL_SUPPORT_CHAIN
            and len(endpoints) < 2
        ):
            raise AssemblyValidationError(
                "vertical_support_chain requires at least two endpoints"
            )
        if self.kind in _UNDIRECTED_KINDS:
            endpoints = tuple(sorted(endpoints))
        object.__setattr__(self, "subject_refs", endpoints)

        deterministic_refs(self.evidence_refs, "relationship evidence_refs")
        deterministic_refs(self.authority_refs, "relationship authority_refs")
        if any(
            any(token in ref for token in _WILDCARD_TOKENS)
            for ref in (*self.evidence_refs, *self.authority_refs)
        ):
            raise AssemblyValidationError(
                "relationship basis refs must be exact"
            )

        maximum = _optional_positive(
            self.maximum_overlap_volume,
            "maximum_overlap_volume",
        )
        if self.kind is RelationshipKind.BOUNDED_EMBEDDED_OVERLAP:
            if maximum is None:
                raise AssemblyValidationError(
                    "bounded embedded overlap requires a positive maximum"
                )
        elif maximum is not None:
            raise AssemblyValidationError(
                "maximum_overlap_volume only applies to bounded embedded overlap"
            )
        object.__setattr__(self, "maximum_overlap_volume", maximum)

    @property
    def ref(self) -> str:
        endpoint_digest = canonical_digest(
            {
                "kind": self.kind.value,
                "subject_refs": list(self.subject_refs),
            }
        )[:16]
        return (
            "assembly-requirement:"
            f"{self.kind.value}:{self.requirement_id}:{endpoint_digest}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "kind": self.kind.value,
            "subject_refs": list(self.subject_refs),
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "maximum_overlap_volume": self.maximum_overlap_volume,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationshipRequirement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "requirement_id",
                "kind",
                "subject_refs",
                "evidence_refs",
                "authority_refs",
                "maximum_overlap_volume",
            },
            "relationship requirement",
        )
        if payload["schema"] != cls.SCHEMA:
            raise AssemblyValidationError(
                "relationship requirement schema drifted"
            )
        for field in ("subject_refs", "evidence_refs", "authority_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"relationship {field} must be a list")
        return cls(
            requirement_id=payload["requirement_id"],
            kind=RelationshipKind(payload["kind"]),
            subject_refs=tuple(payload["subject_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
            maximum_overlap_volume=payload["maximum_overlap_volume"],
        )


@dataclass(frozen=True, slots=True)
class AssemblySubjectObligation:
    """Authority-backed applicability for one exact subject role."""

    obligation_id: str
    role_id: str
    subject_ref: str
    disposition: AssemblyObligationDisposition
    relationship_kind: RelationshipKind | None
    endpoint_index: int | None
    requirement_id: str | None
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA = "AssemblySubjectObligation@1"

    def __post_init__(self) -> None:
        identifier(self.obligation_id, "assembly obligation_id")
        identifier(self.role_id, "assembly obligation role_id")
        object.__setattr__(
            self,
            "subject_ref",
            _exact_ref(self.subject_ref, "assembly obligation subject_ref"),
        )
        if not isinstance(self.disposition, AssemblyObligationDisposition):
            raise TypeError(
                "disposition must be an AssemblyObligationDisposition"
            )
        deterministic_refs(self.evidence_refs, "assembly obligation evidence_refs")
        deterministic_refs(self.authority_refs, "assembly obligation authority_refs")
        if self.disposition is AssemblyObligationDisposition.REQUIRED:
            if not isinstance(self.relationship_kind, RelationshipKind):
                raise AssemblyValidationError(
                    "required obligation needs a RelationshipKind"
                )
            if (
                not isinstance(self.endpoint_index, int)
                or isinstance(self.endpoint_index, bool)
                or self.endpoint_index < 0
                or self.endpoint_index > 4_095
            ):
                raise AssemblyValidationError(
                    "required obligation needs a bounded endpoint_index"
                )
            identifier(self.requirement_id, "assembly obligation requirement_id")
        elif any(
            item is not None
            for item in (
                self.relationship_kind,
                self.endpoint_index,
                self.requirement_id,
            )
        ):
            raise AssemblyValidationError(
                "not-applicable obligation cannot name a relationship"
            )

    @property
    def ref(self) -> str:
        digest = canonical_digest(
            {
                "role_id": self.role_id,
                "subject_ref": self.subject_ref,
                "disposition": self.disposition.value,
                "relationship_kind": (
                    None
                    if self.relationship_kind is None
                    else self.relationship_kind.value
                ),
                "endpoint_index": self.endpoint_index,
                "requirement_id": self.requirement_id,
            }
        )[:16]
        return f"assembly-obligation:{self.obligation_id}:{digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "obligation_id": self.obligation_id,
            "role_id": self.role_id,
            "subject_ref": self.subject_ref,
            "disposition": self.disposition.value,
            "relationship_kind": (
                None
                if self.relationship_kind is None
                else self.relationship_kind.value
            ),
            "endpoint_index": self.endpoint_index,
            "requirement_id": self.requirement_id,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssemblySubjectObligation":
        payload = exact_mapping(
            value,
            {
                "schema",
                "obligation_id",
                "role_id",
                "subject_ref",
                "disposition",
                "relationship_kind",
                "endpoint_index",
                "requirement_id",
                "evidence_refs",
                "authority_refs",
            },
            "assembly subject obligation",
        )
        if payload["schema"] != cls.SCHEMA:
            raise AssemblyValidationError(
                "assembly subject obligation schema drifted"
            )
        for field in ("evidence_refs", "authority_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"assembly obligation {field} must be a list")
        kind = payload["relationship_kind"]
        return cls(
            obligation_id=payload["obligation_id"],
            role_id=payload["role_id"],
            subject_ref=payload["subject_ref"],
            disposition=AssemblyObligationDisposition(payload["disposition"]),
            relationship_kind=(
                None if kind is None else RelationshipKind(kind)
            ),
            endpoint_index=payload["endpoint_index"],
            requirement_id=payload["requirement_id"],
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )


@dataclass(frozen=True, slots=True)
class AssemblyRelationCandidate:
    """One discovered subject pair and its explicit coverage disposition."""

    candidate_id: str
    subject_refs: tuple[str, str]
    disposition: RelationCandidateDisposition
    requirement_id: str | None
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...] = ()

    SCHEMA = "AssemblyRelationCandidate@1"

    def __post_init__(self) -> None:
        identifier(self.candidate_id, "assembly relation candidate_id")
        refs = _ordered_exact_refs(
            self.subject_refs,
            "assembly relation candidate subject_refs",
        )
        if len(refs) != 2:
            raise AssemblyValidationError(
                "assembly relation candidate requires exactly two subjects"
            )
        object.__setattr__(self, "subject_refs", tuple(sorted(refs)))
        if not isinstance(self.disposition, RelationCandidateDisposition):
            raise TypeError(
                "disposition must be a RelationCandidateDisposition"
            )
        deterministic_refs(self.evidence_refs, "relation candidate evidence_refs")
        deterministic_refs(
            self.authority_refs,
            "relation candidate authority_refs",
            allow_empty=True,
        )
        if self.disposition is RelationCandidateDisposition.REQUIREMENT:
            identifier(self.requirement_id, "relation candidate requirement_id")
        elif self.requirement_id is not None:
            raise AssemblyValidationError(
                "non-requirement candidate cannot name a requirement"
            )
        if (
            self.disposition is RelationCandidateDisposition.NOT_APPLICABLE
            and not self.authority_refs
        ):
            raise AssemblyValidationError(
                "not-applicable relation candidate requires authority refs"
            )

    @property
    def ref(self) -> str:
        digest = canonical_digest(
            {
                "subject_refs": list(self.subject_refs),
                "disposition": self.disposition.value,
                "requirement_id": self.requirement_id,
            }
        )[:16]
        return f"assembly-candidate:{self.candidate_id}:{digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_id": self.candidate_id,
            "subject_refs": list(self.subject_refs),
            "disposition": self.disposition.value,
            "requirement_id": self.requirement_id,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssemblyRelationCandidate":
        payload = exact_mapping(
            value,
            {
                "schema",
                "candidate_id",
                "subject_refs",
                "disposition",
                "requirement_id",
                "evidence_refs",
                "authority_refs",
            },
            "assembly relation candidate",
        )
        if payload["schema"] != cls.SCHEMA:
            raise AssemblyValidationError(
                "assembly relation candidate schema drifted"
            )
        for field in ("subject_refs", "evidence_refs", "authority_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"assembly relation candidate {field} must be a list")
        return cls(
            candidate_id=payload["candidate_id"],
            subject_refs=tuple(payload["subject_refs"]),
            disposition=RelationCandidateDisposition(payload["disposition"]),
            requirement_id=payload["requirement_id"],
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )


@dataclass(frozen=True, slots=True)
class AssemblyCoverageManifest:
    """Complete stage-subject universe and per-subject applicability."""

    manifest_id: str
    stage_subject_refs: tuple[str, ...]
    stage_subject_source_digest: str
    obligations: tuple[AssemblySubjectObligation, ...]
    relation_candidates: tuple[AssemblyRelationCandidate, ...] = ()

    SCHEMA = "AssemblyCoverageManifest@1"

    def __post_init__(self) -> None:
        identifier(self.manifest_id, "assembly coverage manifest_id")
        object.__setattr__(
            self,
            "stage_subject_refs",
            tuple(
                sorted(
                    _ordered_exact_refs(
                        self.stage_subject_refs,
                        "assembly coverage stage_subject_refs",
                    )
                )
            ),
        )
        object.__setattr__(
            self,
            "stage_subject_source_digest",
            require_sha256(
                self.stage_subject_source_digest,
                "stage_subject_source_digest",
            ),
        )
        if not isinstance(self.obligations, tuple) or not self.obligations:
            raise AssemblyValidationError(
                "assembly coverage obligations must be a non-empty tuple"
            )
        if any(
            not isinstance(item, AssemblySubjectObligation)
            for item in self.obligations
        ):
            raise TypeError(
                "assembly coverage obligations contain an invalid value"
            )
        obligations = tuple(sorted(self.obligations, key=lambda item: item.ref))
        obligation_ids = tuple(item.obligation_id for item in obligations)
        role_keys = tuple(
            (item.subject_ref, item.role_id) for item in obligations
        )
        if len(obligation_ids) != len(set(obligation_ids)):
            raise AssemblyValidationError(
                "assembly coverage obligations contain duplicate IDs"
            )
        if len(role_keys) != len(set(role_keys)):
            raise AssemblyValidationError(
                "assembly coverage obligations duplicate a subject role"
            )
        if {item.subject_ref for item in obligations} != set(
            self.stage_subject_refs
        ):
            raise AssemblyValidationError(
                "assembly coverage obligations must account for every subject"
            )
        object.__setattr__(self, "obligations", obligations)

        if not isinstance(self.relation_candidates, tuple) or any(
            not isinstance(item, AssemblyRelationCandidate)
            for item in self.relation_candidates
        ):
            raise TypeError(
                "relation_candidates must contain AssemblyRelationCandidate"
            )
        candidates = tuple(
            sorted(self.relation_candidates, key=lambda item: item.ref)
        )
        candidate_ids = tuple(item.candidate_id for item in candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise AssemblyValidationError(
                "relation candidates contain duplicate IDs"
            )
        universe = set(self.stage_subject_refs)
        if any(not set(item.subject_refs) <= universe for item in candidates):
            raise AssemblyValidationError(
                "relation candidate escapes the stage subject universe"
            )
        object.__setattr__(self, "relation_candidates", candidates)

    @property
    def manifest_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"assembly-coverage-manifest:{self.manifest_digest}"

    @property
    def denominator_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    self.ref,
                    *(item.ref for item in self.obligations),
                    *(item.ref for item in self.relation_candidates),
                }
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "manifest_id": self.manifest_id,
            "stage_subject_refs": list(self.stage_subject_refs),
            "stage_subject_source_digest": self.stage_subject_source_digest,
            "obligations": [item.to_dict() for item in self.obligations],
            "relation_candidates": [
                item.to_dict() for item in self.relation_candidates
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssemblyCoverageManifest":
        payload = exact_mapping(
            value,
            {
                "schema",
                "manifest_id",
                "stage_subject_refs",
                "stage_subject_source_digest",
                "obligations",
                "relation_candidates",
            },
            "assembly coverage manifest",
        )
        if payload["schema"] != cls.SCHEMA:
            raise AssemblyValidationError(
                "assembly coverage manifest schema drifted"
            )
        for field in (
            "stage_subject_refs",
            "obligations",
            "relation_candidates",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"assembly coverage manifest {field} must be a list")
        return cls(
            manifest_id=payload["manifest_id"],
            stage_subject_refs=tuple(payload["stage_subject_refs"]),
            stage_subject_source_digest=payload[
                "stage_subject_source_digest"
            ],
            obligations=tuple(
                AssemblySubjectObligation.from_dict(item)
                for item in payload["obligations"]
            ),
            relation_candidates=tuple(
                AssemblyRelationCandidate.from_dict(item)
                for item in payload["relation_candidates"]
            ),
        )


@dataclass(frozen=True, slots=True)
class AssemblyProfile:
    """Immutable project-independent denominator for one assembly check."""

    profile_id: str
    subjects: tuple[AssemblySubject, ...]
    requirements: tuple[RelationshipRequirement, ...]
    coverage_manifest: AssemblyCoverageManifest
    length_unit: str = "m"
    vertical_axis: str = "z"
    linear_tolerance: float = 1.0e-6
    volume_tolerance: float = 1.0e-9

    SCHEMA = "AssemblyProfile@2"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "assembly profile_id")
        text(self.length_unit, "assembly length_unit", maximum=40)
        text(self.vertical_axis, "assembly vertical_axis", maximum=20)
        object.__setattr__(
            self,
            "linear_tolerance",
            _non_negative(self.linear_tolerance, "linear_tolerance"),
        )
        object.__setattr__(
            self,
            "volume_tolerance",
            _non_negative(self.volume_tolerance, "volume_tolerance"),
        )
        if not isinstance(self.subjects, tuple) or not self.subjects:
            raise AssemblyValidationError("subjects must be a non-empty tuple")
        if any(not isinstance(item, AssemblySubject) for item in self.subjects):
            raise TypeError("subjects must contain AssemblySubject values")
        subjects = tuple(sorted(self.subjects, key=lambda item: item.subject_ref))
        subject_refs = tuple(item.subject_ref for item in subjects)
        if len(subject_refs) != len(set(subject_refs)):
            raise AssemblyValidationError("subjects contain duplicate refs")
        object.__setattr__(self, "subjects", subjects)

        if not isinstance(self.requirements, tuple) or not self.requirements:
            raise AssemblyValidationError(
                "requirements must be a non-empty tuple"
            )
        if any(
            not isinstance(item, RelationshipRequirement)
            for item in self.requirements
        ):
            raise TypeError(
                "requirements must contain RelationshipRequirement values"
            )
        requirements = tuple(
            sorted(self.requirements, key=lambda item: item.requirement_id)
        )
        requirement_ids = tuple(item.requirement_id for item in requirements)
        if len(requirement_ids) != len(set(requirement_ids)):
            raise AssemblyValidationError(
                "requirements contain duplicate IDs"
            )
        endpoint_keys = tuple(
            (item.kind.value, item.subject_refs) for item in requirements
        )
        if len(endpoint_keys) != len(set(endpoint_keys)):
            raise AssemblyValidationError(
                "requirements duplicate a kind and exact endpoint denominator"
            )
        object.__setattr__(self, "requirements", requirements)
        if not isinstance(self.coverage_manifest, AssemblyCoverageManifest):
            raise TypeError(
                "coverage_manifest must be an AssemblyCoverageManifest"
            )

    @property
    def subject_refs(self) -> tuple[str, ...]:
        """All supplied and required exact endpoints in deterministic order."""

        return tuple(
            sorted(
                {
                    item.subject_ref for item in self.subjects
                }
                | {
                    ref
                    for requirement in self.requirements
                    for ref in requirement.subject_refs
                }
            )
        )

    @property
    def endpoint_denominator(self) -> tuple[str, ...]:
        """Exact relationship denominator represented in the receipt."""

        return tuple(item.ref for item in self.requirements)

    @property
    def ref(self) -> str:
        return f"assembly-profile:{self.profile_digest}"

    @property
    def check_denominator(self) -> tuple[str, ...]:
        """Every geometry subject and relationship interpreted by the check."""

        return tuple(
            sorted(
                {
                    self.ref,
                    *self.subject_refs,
                    *self.endpoint_denominator,
                    *self.coverage_manifest.denominator_refs,
                }
            )
        )

    @property
    def subject_digest(self) -> str:
        return canonical_digest(
            {
                "schema": "AssemblySubjectSet@1",
                "subjects": [item.to_dict() for item in self.subjects],
            }
        )

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "subjects": [item.to_dict() for item in self.subjects],
            "requirements": [item.to_dict() for item in self.requirements],
            "coverage_manifest": self.coverage_manifest.to_dict(),
            "length_unit": self.length_unit,
            "vertical_axis": self.vertical_axis,
            "linear_tolerance": self.linear_tolerance,
            "volume_tolerance": self.volume_tolerance,
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssemblyProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "subjects",
                "requirements",
                "coverage_manifest",
                "length_unit",
                "vertical_axis",
                "linear_tolerance",
                "volume_tolerance",
            },
            "assembly profile",
        )
        if payload["schema"] != cls.SCHEMA:
            raise AssemblyValidationError("assembly profile schema drifted")
        if not isinstance(payload["subjects"], list) or not isinstance(
            payload["requirements"], list
        ):
            raise TypeError("assembly subjects and requirements must be lists")
        return cls(
            profile_id=payload["profile_id"],
            subjects=tuple(
                AssemblySubject.from_dict(item) for item in payload["subjects"]
            ),
            requirements=tuple(
                RelationshipRequirement.from_dict(item)
                for item in payload["requirements"]
            ),
            coverage_manifest=AssemblyCoverageManifest.from_dict(
                payload["coverage_manifest"]
            ),
            length_unit=payload["length_unit"],
            vertical_axis=payload["vertical_axis"],
            linear_tolerance=payload["linear_tolerance"],
            volume_tolerance=payload["volume_tolerance"],
        )


@dataclass(frozen=True, slots=True)
class _RequirementOutcome:
    status: CheckStatus
    covered: bool
    findings: tuple[CheckFinding, ...] = ()
    measurements: tuple[CheckMeasurement, ...] = ()


def _finding(
    requirement: RelationshipRequirement,
    *,
    code: str,
    severity: FindingSeverity,
    message: str,
) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=severity,
        message=f"{requirement.requirement_id}: {message}",
        subject_refs=tuple(sorted(requirement.subject_refs)),
        evidence_refs=requirement.evidence_refs,
    )


def _measurement(
    requirement: RelationshipRequirement,
    *,
    name: str,
    value: int | float,
    unit_ref: str | None,
) -> CheckMeasurement:
    measurement_id = (
        "assembly-"
        f"{canonical_digest({'requirement': requirement.ref, 'name': name})[:24]}"
    )
    return CheckMeasurement(
        measurement_id=measurement_id,
        subject_ref=requirement.subject_refs[0],
        name=name,
        value=value,
        unit_ref=unit_ref,
        evidence_refs=requirement.evidence_refs,
    )


def _unknown(
    requirement: RelationshipRequirement,
    *,
    code: str,
    message: str,
    measurements: tuple[CheckMeasurement, ...] = (),
) -> _RequirementOutcome:
    return _RequirementOutcome(
        status=CheckStatus.UNKNOWN,
        covered=False,
        findings=(
            _finding(
                requirement,
                code=code,
                severity=FindingSeverity.UNKNOWN,
                message=message,
            ),
        ),
        measurements=measurements,
    )


def _failed(
    requirement: RelationshipRequirement,
    *,
    code: str,
    message: str,
    measurements: tuple[CheckMeasurement, ...] = (),
) -> _RequirementOutcome:
    return _RequirementOutcome(
        status=CheckStatus.FAIL,
        covered=True,
        findings=(
            _finding(
                requirement,
                code=code,
                severity=FindingSeverity.ERROR,
                message=message,
            ),
        ),
        measurements=measurements,
    )


def _passed(
    *,
    measurements: tuple[CheckMeasurement, ...] = (),
) -> _RequirementOutcome:
    return _RequirementOutcome(
        status=CheckStatus.PASS,
        covered=True,
        measurements=measurements,
    )


def _subjects_for(
    requirement: RelationshipRequirement,
    subjects_by_ref: dict[str, AssemblySubject],
) -> tuple[AssemblySubject, ...] | _RequirementOutcome:
    missing = tuple(
        ref for ref in requirement.subject_refs if ref not in subjects_by_ref
    )
    if missing:
        return _unknown(
            requirement,
            code="assembly-subject-missing",
            message="required assembly endpoint(s) are absent: " + ", ".join(missing),
        )
    return tuple(subjects_by_ref[ref] for ref in requirement.subject_refs)


def _horizontal_overlap_area(
    first: AABB,
    second: AABB,
    vertical_axis: int,
) -> float:
    axes = tuple(index for index in range(3) if index != vertical_axis)
    extents = tuple(
        max(
            0.0,
            min(first.maximum[index], second.maximum[index])
            - max(first.minimum[index], second.minimum[index]),
        )
        for index in axes
    )
    result = extents[0] * extents[1]
    return 0.0 if result == 0.0 else result


def _evaluate_geometry_requirement(
    requirement: RelationshipRequirement,
    *,
    subjects_by_ref: dict[str, AssemblySubject],
    profile: AssemblyProfile,
) -> _RequirementOutcome:
    unit_key = profile.length_unit.casefold()
    if unit_key not in _LINEAR_UNIT_REFS:
        return _unknown(
            requirement,
            code="assembly-length-unit-unknown",
            message=f"length unit {profile.length_unit!r} is not recognized",
        )
    axis_key = profile.vertical_axis.casefold()
    if requirement.kind in _AXIS_KINDS and axis_key not in _AXIS_INDEX:
        return _unknown(
            requirement,
            code="assembly-vertical-axis-unknown",
            message=f"vertical axis {profile.vertical_axis!r} is not recognized",
        )
    subject_values = _subjects_for(requirement, subjects_by_ref)
    if isinstance(subject_values, _RequirementOutcome):
        return subject_values
    missing_geometry = tuple(
        item.subject_ref for item in subject_values if item.bounds is None
    )
    if missing_geometry:
        return _unknown(
            requirement,
            code="assembly-geometry-missing",
            message=(
                "checked geometry is unavailable for: "
                + ", ".join(missing_geometry)
            ),
        )
    first = subject_values[0].bounds
    second = subject_values[1].bounds
    assert first is not None and second is not None
    exact_solids = all(
        item.bounds_basis is GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID
        for item in subject_values
    )
    linear_unit = _LINEAR_UNIT_REFS[unit_key]
    area_unit = _AREA_UNIT_REFS[unit_key]
    volume_unit = _VOLUME_UNIT_REFS[unit_key]

    if requirement.kind is RelationshipKind.TOUCH:
        gap = first.distance_to(second)
        overlap = first.intersection_volume(second)
        measurements = (
            _measurement(
                requirement,
                name="solid-gap",
                value=gap,
                unit_ref=linear_unit,
            ),
            _measurement(
                requirement,
                name="overlap-volume",
                value=overlap,
                unit_ref=volume_unit,
            ),
        )
        if not exact_solids:
            if gap > profile.linear_tolerance:
                return _failed(
                    requirement,
                    code="touch-requirement-unsatisfied",
                    message="conservative envelopes are spatially separated",
                    measurements=measurements,
                )
            return _unknown(
                requirement,
                code="touch-narrow-phase-required",
                message=(
                    "conservative envelopes cannot prove exact surface contact"
                ),
                measurements=measurements,
            )
        if gap <= profile.linear_tolerance and overlap <= profile.volume_tolerance:
            return _passed(measurements=measurements)
        return _failed(
            requirement,
            code="touch-requirement-unsatisfied",
            message=(
                "endpoints neither touch within tolerance nor remain free of "
                "positive-volume penetration"
            ),
            measurements=measurements,
        )

    if requirement.kind is RelationshipKind.SUPPORT:
        axis = _AXIS_INDEX[axis_key]
        vertical_gap = first.minimum[axis] - second.maximum[axis]
        bearing_area = _horizontal_overlap_area(first, second, axis)
        measurements = (
            _measurement(
                requirement,
                name="bearing-area",
                value=bearing_area,
                unit_ref=area_unit,
            ),
            _measurement(
                requirement,
                name="vertical-gap",
                value=vertical_gap,
                unit_ref=linear_unit,
            ),
        )
        if not exact_solids:
            if (
                vertical_gap > profile.linear_tolerance
                or bearing_area <= 0.0
            ):
                return _failed(
                    requirement,
                    code="support-requirement-unsatisfied",
                    message=(
                        "conservative envelopes prove separation or zero "
                        "possible bearing area"
                    ),
                    measurements=measurements,
                )
            return _unknown(
                requirement,
                code="support-narrow-phase-required",
                message=(
                    "conservative envelopes cannot prove direct bearing contact"
                ),
                measurements=measurements,
            )
        if (
            abs(vertical_gap) <= profile.linear_tolerance
            and bearing_area > 0.0
        ):
            return _passed(measurements=measurements)
        return _failed(
            requirement,
            code="support-requirement-unsatisfied",
            message=(
                "the declared supporter is not directly below the supported "
                "endpoint with positive bearing area"
            ),
            measurements=measurements,
        )

    overlap = first.intersection_volume(second)
    overlap_measurement = (
        _measurement(
            requirement,
            name="overlap-volume",
            value=overlap,
            unit_ref=volume_unit,
        ),
    )
    if requirement.kind is RelationshipKind.FORBIDDEN_OVERLAP:
        if overlap <= profile.volume_tolerance:
            return _passed(measurements=overlap_measurement)
        if not exact_solids:
            return _unknown(
                requirement,
                code="overlap-narrow-phase-required",
                message=(
                    "overlapping conservative envelopes cannot prove solid "
                    "penetration"
                ),
                measurements=overlap_measurement,
            )
        return _failed(
            requirement,
            code="forbidden-overlap-detected",
            message="declared non-overlapping endpoints have positive-volume overlap",
            measurements=overlap_measurement,
        )

    if requirement.kind is RelationshipKind.BOUNDED_EMBEDDED_OVERLAP:
        if not exact_solids:
            return _unknown(
                requirement,
                code="embedded-overlap-narrow-phase-required",
                message=(
                    "conservative envelopes cannot prove bounded solid overlap"
                ),
                measurements=overlap_measurement,
            )
        maximum = requirement.maximum_overlap_volume
        assert maximum is not None
        if (
            overlap > profile.volume_tolerance
            and overlap <= maximum + profile.volume_tolerance
        ):
            return _passed(measurements=overlap_measurement)
        return _failed(
            requirement,
            code="embedded-overlap-out-of-bounds",
            message=(
                "embedded overlap is absent or exceeds its explicit maximum"
            ),
            measurements=overlap_measurement,
        )

    if requirement.kind is RelationshipKind.HOST_CONTAINMENT:
        outside = second.outside_distance(first)
        measurements = (
            _measurement(
                requirement,
                name="host-exceedance",
                value=outside,
                unit_ref=linear_unit,
            ),
        )
        if not exact_solids:
            return _unknown(
                requirement,
                code="containment-narrow-phase-required",
                message=(
                    "conservative envelopes cannot prove exact host containment"
                ),
                measurements=measurements,
            )
        if outside <= profile.linear_tolerance:
            return _passed(measurements=measurements)
        return _failed(
            requirement,
            code="host-containment-unsatisfied",
            message="the contained endpoint exceeds its explicit host bounds",
            measurements=measurements,
        )

    if requirement.kind is RelationshipKind.OPENING_CLEAR:
        if overlap <= profile.volume_tolerance:
            return _passed(measurements=overlap_measurement)
        if not exact_solids:
            return _unknown(
                requirement,
                code="opening-narrow-phase-required",
                message=(
                    "overlapping conservative envelopes cannot prove an actual "
                    "opening obstruction"
                ),
                measurements=overlap_measurement,
            )
        return _failed(
            requirement,
            code="opening-obstructed",
            message="the opening-clear region intersects its explicit obstruction",
            measurements=overlap_measurement,
        )

    raise AssertionError(f"unhandled geometry requirement {requirement.kind}")


def _evaluate_vertical_chain(
    requirement: RelationshipRequirement,
    *,
    subjects_by_ref: dict[str, AssemblySubject],
    support_by_endpoints: dict[tuple[str, str], _RequirementOutcome],
    profile: AssemblyProfile,
) -> _RequirementOutcome:
    subjects = _subjects_for(requirement, subjects_by_ref)
    if isinstance(subjects, _RequirementOutcome):
        return subjects
    unit_key = profile.length_unit.casefold()
    if unit_key not in _LINEAR_UNIT_REFS:
        return _unknown(
            requirement,
            code="assembly-length-unit-unknown",
            message=f"length unit {profile.length_unit!r} is not recognized",
        )
    if profile.vertical_axis.casefold() not in _AXIS_INDEX:
        return _unknown(
            requirement,
            code="assembly-vertical-axis-unknown",
            message=f"vertical axis {profile.vertical_axis!r} is not recognized",
        )
    pairs = tuple(zip(requirement.subject_refs, requirement.subject_refs[1:]))
    missing_pairs = tuple(pair for pair in pairs if pair not in support_by_endpoints)
    if missing_pairs:
        return _failed(
            requirement,
            code="vertical-support-chain-broken",
            message=(
                "explicit support edge(s) are missing: "
                + ", ".join(f"{first}->{second}" for first, second in missing_pairs)
            ),
        )
    edge_outcomes = tuple(support_by_endpoints[pair] for pair in pairs)
    if any(item.status is CheckStatus.FAIL for item in edge_outcomes):
        return _failed(
            requirement,
            code="vertical-support-chain-invalid",
            message="one or more explicit support edges fail geometric support",
        )
    if any(item.status is CheckStatus.UNKNOWN for item in edge_outcomes):
        return _unknown(
            requirement,
            code="vertical-support-chain-unknown",
            message="one or more explicit support edges lack checkable geometry",
        )
    return _passed(
        measurements=(
            _measurement(
                requirement,
                name="verified-support-edge-count",
                value=len(pairs),
                unit_ref=None,
            ),
        )
    )


def _shortest_support_path(
    start: str,
    target: str,
    adjacency: dict[str, tuple[tuple[str, CheckStatus], ...]],
    *,
    allowed_statuses: frozenset[CheckStatus],
) -> int | None:
    queue: deque[tuple[str, int]] = deque(((start, 0),))
    visited = {start}
    while queue:
        current, depth = queue.popleft()
        if current == target:
            return depth
        for next_ref, status in adjacency.get(current, ()):
            if status not in allowed_statuses or next_ref in visited:
                continue
            visited.add(next_ref)
            queue.append((next_ref, depth + 1))
    return None


def _evaluate_load_path(
    requirement: RelationshipRequirement,
    *,
    subjects_by_ref: dict[str, AssemblySubject],
    support_by_endpoints: dict[tuple[str, str], _RequirementOutcome],
    profile: AssemblyProfile,
) -> _RequirementOutcome:
    subjects = _subjects_for(requirement, subjects_by_ref)
    if isinstance(subjects, _RequirementOutcome):
        return subjects
    unit_key = profile.length_unit.casefold()
    if unit_key not in _LINEAR_UNIT_REFS:
        return _unknown(
            requirement,
            code="assembly-length-unit-unknown",
            message=f"length unit {profile.length_unit!r} is not recognized",
        )
    if profile.vertical_axis.casefold() not in _AXIS_INDEX:
        return _unknown(
            requirement,
            code="assembly-vertical-axis-unknown",
            message=f"vertical axis {profile.vertical_axis!r} is not recognized",
        )
    if any(item.bounds is None for item in subjects):
        return _unknown(
            requirement,
            code="assembly-geometry-missing",
            message="load-path endpoint geometry is unavailable",
        )

    adjacency_lists: dict[str, list[tuple[str, CheckStatus]]] = {}
    for endpoints, outcome in sorted(support_by_endpoints.items()):
        supported, supporter = endpoints
        adjacency_lists.setdefault(supported, []).append(
            (supporter, outcome.status)
        )
    adjacency = {
        ref: tuple(sorted(edges)) for ref, edges in adjacency_lists.items()
    }
    start, foundation = requirement.subject_refs
    structural_hops = _shortest_support_path(
        start,
        foundation,
        adjacency,
        allowed_statuses=frozenset(
            {CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN}
        ),
    )
    if structural_hops is None:
        return _failed(
            requirement,
            code="load-path-disconnected",
            message="no explicit support path reaches the declared foundation",
        )
    verified_hops = _shortest_support_path(
        start,
        foundation,
        adjacency,
        allowed_statuses=frozenset({CheckStatus.PASS}),
    )
    if verified_hops is not None:
        return _passed(
            measurements=(
                _measurement(
                    requirement,
                    name="verified-support-hop-count",
                    value=verified_hops,
                    unit_ref=None,
                ),
            )
        )
    possible_hops = _shortest_support_path(
        start,
        foundation,
        adjacency,
        allowed_statuses=frozenset({CheckStatus.PASS, CheckStatus.UNKNOWN}),
    )
    if possible_hops is not None:
        return _unknown(
            requirement,
            code="load-path-geometry-unknown",
            message="the explicit load path contains an uncheckable support edge",
        )
    return _failed(
        requirement,
        code="load-path-invalid",
        message="all explicit paths to the foundation contain failed support edges",
    )


@dataclass(frozen=True, slots=True)
class _CoverageManifestOutcome:
    covered_refs: tuple[str, ...]
    findings: tuple[CheckFinding, ...]


def _manifest_finding(
    *,
    code: str,
    message: str,
    subject_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...] = (),
) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=FindingSeverity.ERROR,
        message=message,
        subject_refs=tuple(sorted(set(subject_refs))),
        evidence_refs=evidence_refs,
    )


def _evaluate_coverage_manifest(
    profile: AssemblyProfile,
    *,
    stage_subject_digest: str,
) -> _CoverageManifestOutcome:
    manifest = profile.coverage_manifest
    requirements = {
        item.requirement_id: item for item in profile.requirements
    }
    supplied_subject_refs = tuple(
        item.subject_ref for item in profile.subjects
    )
    findings: list[CheckFinding] = []
    covered_refs: list[str] = []

    if (
        manifest.stage_subject_refs != supplied_subject_refs
        or profile.subject_refs != supplied_subject_refs
    ):
        findings.append(
            _manifest_finding(
                code="assembly-subject-universe-mismatch",
                message=(
                    "assembly subjects and relationship endpoints do not "
                    "equal the complete manifest universe"
                ),
                subject_refs=(manifest.ref,),
            )
        )
    if manifest.stage_subject_source_digest != stage_subject_digest:
        findings.append(
            _manifest_finding(
                code="assembly-subject-source-mismatch",
                message=(
                    "assembly manifest is not bound to the exact stage "
                    "subject source digest"
                ),
                subject_refs=(manifest.ref,),
            )
        )

    for obligation in manifest.obligations:
        if (
            obligation.disposition
            is AssemblyObligationDisposition.NOT_APPLICABLE
        ):
            covered_refs.append(obligation.ref)
            continue
        requirement = requirements.get(obligation.requirement_id)
        exact = (
            requirement is not None
            and requirement.kind is obligation.relationship_kind
            and obligation.endpoint_index is not None
            and obligation.endpoint_index < len(requirement.subject_refs)
            and requirement.subject_refs[obligation.endpoint_index]
            == obligation.subject_ref
        )
        if exact:
            covered_refs.append(obligation.ref)
        else:
            findings.append(
                _manifest_finding(
                    code="assembly-obligation-uncovered",
                    message=(
                        f"subject role {obligation.role_id} does not map to "
                        "the declared relationship kind and endpoint"
                    ),
                    subject_refs=(obligation.subject_ref,),
                    evidence_refs=obligation.evidence_refs,
                )
            )

    for candidate in manifest.relation_candidates:
        if candidate.disposition is RelationCandidateDisposition.UNRESOLVED:
            findings.append(
                _manifest_finding(
                    code="assembly-relation-candidate-unresolved",
                    message=(
                        f"relation candidate {candidate.candidate_id} is "
                        "neither mapped nor authority-excluded"
                    ),
                    subject_refs=candidate.subject_refs,
                    evidence_refs=candidate.evidence_refs,
                )
            )
            continue
        if (
            candidate.disposition
            is RelationCandidateDisposition.NOT_APPLICABLE
        ):
            covered_refs.append(candidate.ref)
            continue
        requirement = requirements.get(candidate.requirement_id)
        exact = (
            requirement is not None
            and len(requirement.subject_refs) == 2
            and set(requirement.subject_refs) == set(candidate.subject_refs)
        )
        if exact:
            covered_refs.append(candidate.ref)
        else:
            findings.append(
                _manifest_finding(
                    code="assembly-relation-candidate-mismatch",
                    message=(
                        f"relation candidate {candidate.candidate_id} does "
                        "not map to an exact two-endpoint requirement"
                    ),
                    subject_refs=candidate.subject_refs,
                    evidence_refs=candidate.evidence_refs,
                )
            )

    if not findings:
        covered_refs.append(manifest.ref)
    return _CoverageManifestOutcome(
        covered_refs=tuple(sorted(covered_refs)),
        findings=tuple(
            sorted(
                findings,
                key=lambda item: (item.code, item.subject_refs, item.message),
            )
        ),
    )


def check_assembly(
    profile: AssemblyProfile,
    *,
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Check one exact assembly denominator and return a no-authority receipt."""

    if not isinstance(profile, AssemblyProfile):
        raise TypeError("profile must be an AssemblyProfile")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )
    subjects_by_ref = {item.subject_ref: item for item in profile.subjects}
    manifest_outcome = _evaluate_coverage_manifest(
        profile,
        stage_subject_digest=stage_subject_digest,
    )
    outcomes: dict[str, _RequirementOutcome] = {}

    for requirement in profile.requirements:
        if requirement.kind not in _GEOMETRY_KINDS:
            continue
        outcomes[requirement.ref] = _evaluate_geometry_requirement(
            requirement,
            subjects_by_ref=subjects_by_ref,
            profile=profile,
        )

    support_by_endpoints = {
        requirement.subject_refs: outcomes[requirement.ref]
        for requirement in profile.requirements
        if requirement.kind is RelationshipKind.SUPPORT
    }
    for requirement in profile.requirements:
        if requirement.kind is RelationshipKind.VERTICAL_SUPPORT_CHAIN:
            outcomes[requirement.ref] = _evaluate_vertical_chain(
                requirement,
                subjects_by_ref=subjects_by_ref,
                support_by_endpoints=support_by_endpoints,
                profile=profile,
            )
        elif requirement.kind is RelationshipKind.LOAD_PATH_TO_FOUNDATION:
            outcomes[requirement.ref] = _evaluate_load_path(
                requirement,
                subjects_by_ref=subjects_by_ref,
                support_by_endpoints=support_by_endpoints,
                profile=profile,
            )

    if set(outcomes) != set(profile.endpoint_denominator):
        raise AssertionError("assembly checker omitted a requirement kind")

    ordered_outcomes = tuple(
        outcomes[requirement.ref] for requirement in profile.requirements
    )
    if manifest_outcome.findings or any(
        item.status is CheckStatus.FAIL for item in ordered_outcomes
    ):
        status = CheckStatus.FAIL
    elif any(item.status is CheckStatus.UNKNOWN for item in ordered_outcomes):
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    findings = tuple(
        sorted(
            tuple(
                finding
                for outcome in ordered_outcomes
                for finding in outcome.findings
            )
            + manifest_outcome.findings,
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )
    measurements = tuple(
        sorted(
            (
                CheckMeasurement(
                    measurement_id="assembly-profile-digest",
                    subject_ref=profile.ref,
                    name="assembly_profile_digest",
                    value=profile.profile_digest,
                    unit_ref=None,
                ),
                CheckMeasurement(
                    measurement_id="assembly-coverage-manifest-digest",
                    subject_ref=profile.coverage_manifest.ref,
                    name="assembly_coverage_manifest_digest",
                    value=profile.coverage_manifest.manifest_digest,
                    unit_ref=None,
                ),
            )
            + tuple(
                measurement
                for outcome in ordered_outcomes
                for measurement in outcome.measurements
            ),
            key=lambda item: item.measurement_id,
        )
    )
    covered_relationship_refs = tuple(
        requirement.ref
        for requirement in profile.requirements
        if outcomes[requirement.ref].covered
    )
    coordinate_basis_known = (
        profile.length_unit.casefold() in _LINEAR_UNIT_REFS
        and profile.vertical_axis.casefold() in _AXIS_INDEX
    )
    covered_subject_refs = (
        tuple(
            item.subject_ref
            for item in profile.subjects
            if item.bounds is not None
        )
        if coordinate_basis_known
        else ()
    )
    covered_refs = tuple(
        sorted(
            {
                profile.ref,
                *covered_subject_refs,
                *covered_relationship_refs,
                *manifest_outcome.covered_refs,
            }
        )
    )
    evidence_refs = tuple(
        sorted(
            {
                ref
                for requirement in profile.requirements
                for ref in requirement.evidence_refs
            }
            | {
                ref
                for obligation in profile.coverage_manifest.obligations
                for ref in obligation.evidence_refs
            }
            | {
                ref
                for candidate in profile.coverage_manifest.relation_candidates
                for ref in candidate.evidence_refs
            }
        )
    )
    authority_refs = tuple(
        sorted(
            {
                ref
                for requirement in profile.requirements
                for ref in requirement.authority_refs
            }
            | {
                ref
                for obligation in profile.coverage_manifest.obligations
                for ref in obligation.authority_refs
            }
            | {
                ref
                for candidate in profile.coverage_manifest.relation_candidates
                for ref in candidate.authority_refs
            }
        )
    )
    return CheckReceiptEnvelope(
        check_id=f"assembly-{profile.profile_digest[:24]}",
        checker_id="assembly-relationship-checker",
        checker_version="1.0.0",
        branch=branch,
        scope_digest=scope_digest,
        subject_refs=profile.check_denominator,
        subject_digest=stage_subject_digest,
        status=status,
        source_refs=evidence_refs,
        authority_refs=authority_refs,
        findings=findings,
        measurements=measurements,
        coverage_denominator=profile.check_denominator,
        covered_refs=covered_refs,
    )


validate_assembly = check_assembly


__all__ = [
    "AssemblyCoverageManifest",
    "AssemblyObligationDisposition",
    "AssemblyProfile",
    "AssemblyRelationCandidate",
    "AssemblySubject",
    "AssemblySubjectObligation",
    "AssemblyValidationError",
    "GeometryBoundsBasis",
    "RelationCandidateDisposition",
    "RelationshipKind",
    "RelationshipRequirement",
    "check_assembly",
    "validate_assembly",
]
