"""Pure, branch-bound visual-evidence candidate contracts.

Callers own every source, architectural tag, claim, and review decision.  This
module only validates typed source/region boundaries and compiles a stable
manifest.  It performs no download, image processing, persistence, geometry
mutation, or building-type routing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from archflow.contracts.canonical import canonical_digest


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX = frozenset("0123456789abcdef")


class VisualEvidenceError(ValueError):
    """A visual source, region, or manifest boundary failed closed."""


class VisualSourceModality(StrEnum):
    MEASURED_DRAWING = "measured_drawing"
    RECONSTRUCTION_DRAWING = "reconstruction_drawing"
    CURRENT_PHOTO = "current_photo"
    MUSEUM_OBJECT = "museum_object"


class VisualClaimKind(StrEnum):
    ELEMENT_EXISTENCE = "element_existence"
    TOPOLOGY = "topology"
    RELATIVE_POSITION = "relative_position"
    VISIBLE_MORPHOLOGY = "visible_morphology"
    MATERIAL_CONDITION = "material_condition"
    EXACT_DIMENSION = "exact_dimension"


class VisualMeasurementBasisKind(StrEnum):
    RETAINED_SCALE = "retained_scale"
    INDEPENDENT_MEASUREMENT = "independent_measurement"
    CALIBRATED_IMAGE = "calibrated_image"


class VisualReviewState(StrEnum):
    PENDING = "pending"
    SELECTED = "selected"
    PARKED = "parked"
    REJECTED = "rejected"


_ALL_MODALITIES = tuple(VisualSourceModality)


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    if _IDENTIFIER.fullmatch(value) is None:
        raise VisualEvidenceError(
            f"{field} must be a portable 1-128 character identifier"
        )
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    candidate = value.strip()
    if not candidate or len(candidate) > 4_000:
        raise VisualEvidenceError(f"{field} must be bounded non-empty text")
    return candidate


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    candidate = value.lower()
    if len(candidate) != 64 or any(char not in _HEX for char in candidate):
        raise VisualEvidenceError(f"{field} must be a SHA-256 digest")
    return candidate


def _positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VisualEvidenceError(f"{field} must be a positive integer")
    return value


def _pixel_bbox(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, tuple) or len(value) != 4:
        raise TypeError("pixel_bbox must be a four-integer tuple")
    if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
        raise TypeError("pixel_bbox must be a four-integer tuple")
    left, top, right, bottom = value
    if left >= right or top >= bottom:
        raise VisualEvidenceError("pixel_bbox must have positive width and height")
    return (left, top, right, bottom)


def _normalized_bbox(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, tuple) or len(value) != 4:
        raise TypeError("normalized_bbox must be a four-number tuple")
    result = []
    for item in value:
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
        ):
            raise VisualEvidenceError(
                "normalized_bbox must contain finite numeric values"
            )
        result.append(float(item))
    left, top, right, bottom = result
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        raise VisualEvidenceError(
            "normalized_bbox must lie inside the unit source rectangle"
        )
    return (left, top, right, bottom)


def _text_tuple(
    value: object,
    field: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    normalized = tuple(_text(item, field) for item in value)
    if required and not normalized:
        raise VisualEvidenceError(f"{field} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise VisualEvidenceError(f"{field} must be unique")
    return tuple(sorted(normalized))


def _claim_tuple(
    value: object,
    field: str,
    *,
    required: bool,
) -> tuple[VisualClaimKind, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if any(not isinstance(item, VisualClaimKind) for item in value):
        raise TypeError(f"{field} must contain VisualClaimKind values")
    if required and not value:
        raise VisualEvidenceError(f"{field} must not be empty")
    if len(value) != len(set(value)):
        raise VisualEvidenceError(f"{field} must be unique")
    return tuple(sorted(value, key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class VisualMeasurementBasis:
    basis_id: str
    kind: VisualMeasurementBasisKind
    evidence_refs: tuple[str, ...]
    statement: str

    SCHEMA = "VisualMeasurementBasis@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "basis_id", _identifier(self.basis_id, "basis_id"))
        if not isinstance(self.kind, VisualMeasurementBasisKind):
            raise TypeError("kind must be VisualMeasurementBasisKind")
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs", required=True),
        )
        object.__setattr__(self, "statement", _text(self.statement, "statement"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "basis_id": self.basis_id,
            "kind": self.kind.value,
            "evidence_refs": list(self.evidence_refs),
            "statement": self.statement,
            "measurement_authority": False,
        }


@dataclass(frozen=True, slots=True)
class VisualSource:
    source_id: str
    view_id: str
    url: str
    retrieved_at: str
    media_type: str
    pixel_width: int
    pixel_height: int
    content_sha256: str
    artifact_ref: str
    source_family: str
    modality: VisualSourceModality
    usage_note: str
    branch_id: str
    scope_ref: str

    SCHEMA = "VisualSource@1"

    def __post_init__(self) -> None:
        for field in ("source_id", "view_id", "branch_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        for field in (
            "url",
            "retrieved_at",
            "artifact_ref",
            "source_family",
            "usage_note",
            "scope_ref",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        media_type = _text(self.media_type, "media_type")
        if not media_type.startswith("image/"):
            raise VisualEvidenceError("media_type must describe an image")
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(
            self,
            "pixel_width",
            _positive_integer(self.pixel_width, "pixel_width"),
        )
        object.__setattr__(
            self,
            "pixel_height",
            _positive_integer(self.pixel_height, "pixel_height"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            _sha256(self.content_sha256, "content_sha256"),
        )
        if not isinstance(self.modality, VisualSourceModality):
            raise TypeError("modality must be VisualSourceModality")

    @property
    def pixel_size(self) -> tuple[int, int]:
        return (self.pixel_width, self.pixel_height)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_id": self.source_id,
            "view_id": self.view_id,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "media_type": self.media_type,
            "pixel_width": self.pixel_width,
            "pixel_height": self.pixel_height,
            "content_sha256": self.content_sha256,
            "artifact_ref": self.artifact_ref,
            "source_family": self.source_family,
            "modality": self.modality.value,
            "usage_note": self.usage_note,
            "branch_id": self.branch_id,
            "scope_ref": self.scope_ref,
            "evidence_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class VisualRegionCandidate:
    candidate_id: str
    source_id: str
    pixel_bbox: tuple[int, int, int, int]
    normalized_bbox: tuple[float, float, float, float]
    element_tag: str
    host_component: str
    spatial_location: str
    confidence: float
    supports: tuple[VisualClaimKind, ...]
    cannot_support: tuple[VisualClaimKind, ...]
    review_state: VisualReviewState
    measurement_basis: VisualMeasurementBasis | None = None

    SCHEMA = "VisualRegionCandidate@1"

    def __post_init__(self) -> None:
        for field in ("candidate_id", "source_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "pixel_bbox", _pixel_bbox(self.pixel_bbox))
        object.__setattr__(
            self,
            "normalized_bbox",
            _normalized_bbox(self.normalized_bbox),
        )
        for field in ("element_tag", "host_component", "spatial_location"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(float(self.confidence))
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise VisualEvidenceError("confidence must be finite inside [0, 1]")
        object.__setattr__(self, "confidence", float(self.confidence))
        supports = _claim_tuple(self.supports, "supports", required=True)
        cannot_support = _claim_tuple(
            self.cannot_support,
            "cannot_support",
            required=True,
        )
        if set(supports) & set(cannot_support):
            raise VisualEvidenceError(
                "supports and cannot_support must not contradict each other"
            )
        object.__setattr__(self, "supports", supports)
        object.__setattr__(self, "cannot_support", cannot_support)
        if not isinstance(self.review_state, VisualReviewState):
            raise TypeError("review_state must be VisualReviewState")
        if self.measurement_basis is not None and not isinstance(
            self.measurement_basis,
            VisualMeasurementBasis,
        ):
            raise TypeError(
                "measurement_basis must be VisualMeasurementBasis or None"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_id": self.candidate_id,
            "source_id": self.source_id,
            "pixel_bbox": list(self.pixel_bbox),
            "normalized_bbox": list(self.normalized_bbox),
            "element_tag": self.element_tag,
            "host_component": self.host_component,
            "spatial_location": self.spatial_location,
            "confidence": self.confidence,
            "supports": [item.value for item in self.supports],
            "cannot_support": [item.value for item in self.cannot_support],
            "review_state": self.review_state.value,
            "measurement_basis": (
                None
                if self.measurement_basis is None
                else self.measurement_basis.to_dict()
            ),
            "selection_authority": False,
            "geometry_mutation_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class VisualEvidenceManifestPolicy:
    policy_id: str
    branch_id: str
    scope_ref: str
    allowed_modalities: tuple[VisualSourceModality, ...] = _ALL_MODALITIES
    normalized_bbox_tolerance: float = 1e-6
    minimum_selected_candidates: int = 0

    SCHEMA = "VisualEvidenceManifestPolicy@1"

    def __post_init__(self) -> None:
        for field in ("policy_id", "branch_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "scope_ref", _text(self.scope_ref, "scope_ref"))
        if not isinstance(self.allowed_modalities, tuple):
            raise TypeError("allowed_modalities must be a tuple")
        if not self.allowed_modalities or any(
            not isinstance(item, VisualSourceModality)
            for item in self.allowed_modalities
        ):
            raise VisualEvidenceError(
                "allowed_modalities must contain VisualSourceModality values"
            )
        if len(self.allowed_modalities) != len(set(self.allowed_modalities)):
            raise VisualEvidenceError("allowed_modalities must be unique")
        object.__setattr__(
            self,
            "allowed_modalities",
            tuple(sorted(self.allowed_modalities, key=lambda item: item.value)),
        )
        tolerance = self.normalized_bbox_tolerance
        if (
            not isinstance(tolerance, (int, float))
            or isinstance(tolerance, bool)
            or not math.isfinite(float(tolerance))
            or not 0.0 <= float(tolerance) <= 0.01
        ):
            raise VisualEvidenceError(
                "normalized_bbox_tolerance must be finite inside [0, 0.01]"
            )
        object.__setattr__(self, "normalized_bbox_tolerance", float(tolerance))
        if (
            not isinstance(self.minimum_selected_candidates, int)
            or isinstance(self.minimum_selected_candidates, bool)
            or self.minimum_selected_candidates < 0
        ):
            raise VisualEvidenceError(
                "minimum_selected_candidates must be a non-negative integer"
            )

    @property
    def policy_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "branch_id": self.branch_id,
            "scope_ref": self.scope_ref,
            "allowed_modalities": [
                item.value for item in self.allowed_modalities
            ],
            "normalized_bbox_tolerance": self.normalized_bbox_tolerance,
            "minimum_selected_candidates": self.minimum_selected_candidates,
            "selection_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class VisualRegionManifestBinding:
    candidate: VisualRegionCandidate
    source_modality: VisualSourceModality
    source_usage_note: str
    source_artifact_ref: str
    source_content_sha256: str
    source_branch_id: str
    source_scope_ref: str

    SCHEMA = "VisualRegionManifestBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, VisualRegionCandidate):
            raise TypeError("candidate must be VisualRegionCandidate")
        if not isinstance(self.source_modality, VisualSourceModality):
            raise TypeError("source_modality must be VisualSourceModality")
        for field in (
            "source_usage_note",
            "source_artifact_ref",
            "source_scope_ref",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self,
            "source_content_sha256",
            _sha256(self.source_content_sha256, "source_content_sha256"),
        )
        object.__setattr__(
            self,
            "source_branch_id",
            _identifier(self.source_branch_id, "source_branch_id"),
        )

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate": self.candidate.to_dict(),
            "source_binding": {
                "source_id": self.candidate.source_id,
                "modality": self.source_modality.value,
                "usage_note": self.source_usage_note,
                "artifact_ref": self.source_artifact_ref,
                "content_sha256": self.source_content_sha256,
                "branch_id": self.source_branch_id,
                "scope_ref": self.source_scope_ref,
            },
        }


@dataclass(frozen=True, slots=True)
class VisualEvidenceManifest:
    policy: VisualEvidenceManifestPolicy
    sources: tuple[VisualSource, ...]
    candidate_bindings: tuple[VisualRegionManifestBinding, ...]
    selected_candidate_ids: tuple[str, ...]
    parked_candidate_ids: tuple[str, ...]
    pending_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]

    SCHEMA = "VisualEvidenceManifest@1"

    def __post_init__(self) -> None:
        if not isinstance(self.policy, VisualEvidenceManifestPolicy):
            raise TypeError("policy must be VisualEvidenceManifestPolicy")
        if not isinstance(self.sources, tuple) or any(
            not isinstance(item, VisualSource) for item in self.sources
        ):
            raise TypeError("sources must contain VisualSource values")
        if not isinstance(self.candidate_bindings, tuple) or any(
            not isinstance(item, VisualRegionManifestBinding)
            for item in self.candidate_bindings
        ):
            raise TypeError(
                "candidate_bindings must contain VisualRegionManifestBinding"
            )
        expected_sources = tuple(sorted(self.sources, key=lambda item: item.source_id))
        expected_bindings = tuple(
            sorted(self.candidate_bindings, key=lambda item: item.candidate_id)
        )
        if self.sources != expected_sources:
            raise VisualEvidenceError("sources must be deterministically ordered")
        if self.candidate_bindings != expected_bindings:
            raise VisualEvidenceError(
                "candidate_bindings must be deterministically ordered"
            )
        if len({item.source_id for item in self.sources}) != len(self.sources):
            raise VisualEvidenceError("source_id values must be unique")
        if len({item.candidate_id for item in self.candidate_bindings}) != len(
            self.candidate_bindings
        ):
            raise VisualEvidenceError("candidate_id values must be unique")
        state_ids = {
            VisualReviewState.SELECTED: self.selected_candidate_ids,
            VisualReviewState.PARKED: self.parked_candidate_ids,
            VisualReviewState.PENDING: self.pending_candidate_ids,
            VisualReviewState.REJECTED: self.rejected_candidate_ids,
        }
        for state, values in state_ids.items():
            normalized = _text_tuple(
                values,
                f"{state.value}_candidate_ids",
                required=False,
            )
            object.__setattr__(self, f"{state.value}_candidate_ids", normalized)
            expected = tuple(
                item.candidate_id
                for item in self.candidate_bindings
                if item.candidate.review_state is state
            )
            if normalized != expected:
                raise VisualEvidenceError(
                    f"{state.value} candidate ids disagree with review states"
                )

    @property
    def manifest_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy": self.policy.to_dict(),
            "policy_digest": self.policy.policy_digest,
            "branch_id": self.policy.branch_id,
            "scope_ref": self.policy.scope_ref,
            "sources": [item.to_dict() for item in self.sources],
            "candidate_bindings": [
                item.to_dict() for item in self.candidate_bindings
            ],
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "parked_candidate_ids": list(self.parked_candidate_ids),
            "pending_candidate_ids": list(self.pending_candidate_ids),
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
            "source_modality_limits_preserved": True,
            "selection_authority": False,
            "measurement_authority": False,
            "geometry_mutation_authority": False,
            "canonical_write_authority": False,
        }


def _require_bbox_inside_source(
    candidate: VisualRegionCandidate,
    source: VisualSource,
    *,
    tolerance: float,
) -> None:
    left, top, right, bottom = candidate.pixel_bbox
    if not (
        0 <= left < right <= source.pixel_width
        and 0 <= top < bottom <= source.pixel_height
    ):
        raise VisualEvidenceError(
            f"candidate {candidate.candidate_id} pixel bbox is outside source "
            f"{source.source_id}"
        )
    expected = (
        left / source.pixel_width,
        top / source.pixel_height,
        right / source.pixel_width,
        bottom / source.pixel_height,
    )
    if any(
        abs(actual - derived) > tolerance
        for actual, derived in zip(candidate.normalized_bbox, expected, strict=True)
    ):
        raise VisualEvidenceError(
            f"candidate {candidate.candidate_id} normalized bbox disagrees with "
            "its source pixel bbox"
        )


def compile_visual_evidence_manifest(
    policy: VisualEvidenceManifestPolicy,
    *,
    sources: Iterable[VisualSource],
    candidates: Iterable[VisualRegionCandidate],
) -> VisualEvidenceManifest:
    """Validate and deterministically bind visual candidates to their sources."""

    if not isinstance(policy, VisualEvidenceManifestPolicy):
        raise TypeError("policy must be VisualEvidenceManifestPolicy")
    source_values = tuple(sources)
    candidate_values = tuple(candidates)
    if not source_values:
        raise VisualEvidenceError("manifest requires at least one visual source")
    if not candidate_values:
        raise VisualEvidenceError("manifest requires at least one region candidate")
    if any(not isinstance(item, VisualSource) for item in source_values):
        raise TypeError("sources must contain VisualSource values")
    if any(not isinstance(item, VisualRegionCandidate) for item in candidate_values):
        raise TypeError("candidates must contain VisualRegionCandidate values")
    if len({item.source_id for item in source_values}) != len(source_values):
        raise VisualEvidenceError("duplicate source_id in visual manifest")
    if len({item.candidate_id for item in candidate_values}) != len(
        candidate_values
    ):
        raise VisualEvidenceError("duplicate candidate_id in visual manifest")

    ordered_sources = tuple(sorted(source_values, key=lambda item: item.source_id))
    ordered_candidates = tuple(
        sorted(candidate_values, key=lambda item: item.candidate_id)
    )
    by_source = {item.source_id: item for item in ordered_sources}
    allowed = set(policy.allowed_modalities)
    for source in ordered_sources:
        if source.branch_id != policy.branch_id or source.scope_ref != policy.scope_ref:
            raise VisualEvidenceError(
                f"source {source.source_id} is outside the policy branch/scope"
            )
        if source.modality not in allowed:
            raise VisualEvidenceError(
                f"source {source.source_id} modality is outside policy"
            )

    bindings = []
    for candidate in ordered_candidates:
        source = by_source.get(candidate.source_id)
        if source is None:
            raise VisualEvidenceError(
                f"candidate {candidate.candidate_id} references a missing source"
            )
        _require_bbox_inside_source(
            candidate,
            source,
            tolerance=policy.normalized_bbox_tolerance,
        )
        if (
            VisualClaimKind.EXACT_DIMENSION in candidate.supports
            and candidate.measurement_basis is None
        ):
            raise VisualEvidenceError(
                f"candidate {candidate.candidate_id} exact-dimension support "
                "requires a retained measurement basis"
            )
        bindings.append(
            VisualRegionManifestBinding(
                candidate=candidate,
                source_modality=source.modality,
                source_usage_note=source.usage_note,
                source_artifact_ref=source.artifact_ref,
                source_content_sha256=source.content_sha256,
                source_branch_id=source.branch_id,
                source_scope_ref=source.scope_ref,
            )
        )
    binding_values = tuple(bindings)
    selected = tuple(
        item.candidate_id
        for item in binding_values
        if item.candidate.review_state is VisualReviewState.SELECTED
    )
    if len(selected) < policy.minimum_selected_candidates:
        raise VisualEvidenceError(
            "selected candidate count is below the manifest policy minimum"
        )

    def ids(state: VisualReviewState) -> tuple[str, ...]:
        return tuple(
            item.candidate_id
            for item in binding_values
            if item.candidate.review_state is state
        )

    return VisualEvidenceManifest(
        policy=policy,
        sources=ordered_sources,
        candidate_bindings=binding_values,
        selected_candidate_ids=selected,
        parked_candidate_ids=ids(VisualReviewState.PARKED),
        pending_candidate_ids=ids(VisualReviewState.PENDING),
        rejected_candidate_ids=ids(VisualReviewState.REJECTED),
    )
