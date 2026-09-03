#!/usr/bin/env python3
"""P086 offline localization of retained Parthenon Stage 4 image regions.

The runner reads one exact P085 manifest and its already-retained source bytes.
It performs no network access, appends only immutable evidence to ``research-005``,
and never opens ``reconstruction-005`` or changes canonical project state.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

try:  # Package import in tests; direct import when executed as a script.
    from tools._probe_paths import resolve_probe_root
except ModuleNotFoundError:
    from _probe_paths import resolve_probe_root

from archflow.capabilities.visual_evidence import (
    VisualClaimKind,
    VisualEvidenceManifestPolicy,
    VisualRegionCandidate,
    VisualReviewState,
    VisualSource,
    VisualSourceModality,
    compile_visual_evidence_manifest,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectArtifactRef,
    ProjectRecordRef,
    RunRef,
)
from archflow.project.refs import (
    require_identifier,
    require_project_relative_path,
)
from archflow.contracts.canonical import canonical_digest, canonical_json


PROJECT_ID = "parthenon-reconstruction"
RESEARCH_RUN_ID = "research-005"
FORBIDDEN_RECONSTRUCTION_RUN_ID = "reconstruction-005"
BRANCH_ID = "idealized-periclean-original"
SCOPE_REF = "branch-scope:idealized-periclean-original"

PREDECESSOR_MANIFEST_SHA256 = (
    "76b73f91e9ab2af8552a7fcf3abef9cd596702c0f96f78daffc6c1ebd00e0fb0"
)
PREDECESSOR_MANIFEST_RELATIVE_PATH = (
    "runs/research-005/branches/idealized-periclean-original/records/"
    "visual-candidate-manifest-"
    f"{PREDECESSOR_MANIFEST_SHA256}.json"
)
DEFAULT_PREDECESSOR_MANIFEST_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=PREDECESSOR_MANIFEST_RELATIVE_PATH,
    sha256=PREDECESSOR_MANIFEST_SHA256,
)
LEGACY_P085_MANIFEST_URI = (
    "project://parthenon-reconstruction/runs/research-005/branches/"
    "idealized-periclean-original/records/visual-candidate-manifest-"
    "aef6871d841823483c6852a4b5b2945e47075600f2ebde572f7b375cc0fe208d.json"
)
DEFAULT_SUPERSEDES_MANIFEST_REFS = (
    LEGACY_P085_MANIFEST_URI,
    DEFAULT_PREDECESSOR_MANIFEST_REF.uri,
)

EXPECTED_SOURCE_COUNT = 8
EXPECTED_REGION_COUNT = 32
EXPECTED_REGIONS_PER_SOURCE = 4
PNG_MEDIA_TYPE = "image/png"

_SOURCE_MEDIA_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_REVIEW_COLORS = {
    VisualReviewState.SELECTED: (25, 138, 83),
    VisualReviewState.PARKED: (226, 142, 36),
    VisualReviewState.REJECTED: (190, 48, 48),
}
_TEMPORARY_EQUIPMENT_TAGS = frozenset(
    {"scaffold", "lifting_tackle", "boom"}
)


class ParthenonVisualRegionError(ValueError):
    """A P086 predecessor, source, ROI, or persistence boundary failed."""


def _validate_normalized_bbox(value: object) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) != 4
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in value
        )
    ):
        raise ParthenonVisualRegionError(
            "normalized_bbox must be a four-number tuple"
        )
    left, top, right, bottom = (float(item) for item in value)
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        raise ParthenonVisualRegionError(
            "normalized_bbox must lie inside the unit source rectangle"
        )


@dataclass(frozen=True, slots=True)
class VisualRegionSpec:
    source_id: str
    region_id: str
    normalized_bbox: tuple[float, float, float, float]
    element_tag: str
    host_component: str
    spatial_location: str
    confidence: float
    supports: tuple[VisualClaimKind, ...]
    cannot_support: tuple[VisualClaimKind, ...]
    review_state: VisualReviewState

    def __post_init__(self) -> None:
        for value, field in (
            (self.source_id, "source_id"),
            (self.region_id, "region_id"),
            (self.element_tag, "element_tag"),
            (self.host_component, "host_component"),
            (self.spatial_location, "spatial_location"),
        ):
            require_identifier(value, field)
        _validate_normalized_bbox(self.normalized_bbox)
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(float(self.confidence))
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ParthenonVisualRegionError(
                "confidence must be finite inside [0, 1]"
            )
        object.__setattr__(self, "confidence", float(self.confidence))
        for values, field in (
            (self.supports, "supports"),
            (self.cannot_support, "cannot_support"),
        ):
            if (
                not isinstance(values, tuple)
                or not values
                or any(not isinstance(item, VisualClaimKind) for item in values)
                or len(values) != len(set(values))
            ):
                raise ParthenonVisualRegionError(
                    f"{field} must be a non-empty unique VisualClaimKind tuple"
                )
        if set(self.supports) & set(self.cannot_support):
            raise ParthenonVisualRegionError(
                "supports and cannot_support must not overlap"
            )
        if VisualClaimKind.EXACT_DIMENSION in self.supports:
            raise ParthenonVisualRegionError(
                "uncalibrated P086 regions cannot support exact dimensions"
            )
        if VisualClaimKind.EXACT_DIMENSION not in self.cannot_support:
            raise ParthenonVisualRegionError(
                "every P086 region must explicitly reject exact dimensions"
            )
        if not isinstance(self.review_state, VisualReviewState):
            raise TypeError("review_state must be VisualReviewState")
        if (
            self.element_tag in _TEMPORARY_EQUIPMENT_TAGS
            and self.review_state is not VisualReviewState.REJECTED
        ):
            raise ParthenonVisualRegionError(
                "temporary restoration equipment must remain rejected"
            )

    @property
    def candidate_id(self) -> str:
        return f"roi_{self.source_id}_{self.region_id}"

    def to_input_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "region_id": self.region_id,
            "candidate_id": self.candidate_id,
            "normalized_bbox": list(self.normalized_bbox),
            "element_tag": self.element_tag,
            "host_component": self.host_component,
            "spatial_location": self.spatial_location,
            "confidence": self.confidence,
            "supports": [item.value for item in self.supports],
            "cannot_support": [item.value for item in self.cannot_support],
            "review_state": self.review_state.value,
        }


@dataclass(frozen=True, slots=True)
class _LoadedSource:
    source: VisualSource
    source_record_ref: ProjectRecordRef
    artifact_ref: ProjectArtifactRef
    workspace_path: Path
    source_bytes: bytes


@dataclass(frozen=True, slots=True)
class _RegionOutput:
    spec: VisualRegionSpec
    candidate: VisualRegionCandidate
    crop_path: Path
    crop_sha256: str
    crop_artifact_ref: ProjectArtifactRef


@dataclass(frozen=True, slots=True)
class _OverlayOutput:
    source_id: str
    candidate_ids: tuple[str, ...]
    overlay_path: Path
    overlay_sha256: str
    overlay_artifact_ref: ProjectArtifactRef


_PLAN_SUPPORTS = (
    VisualClaimKind.ELEMENT_EXISTENCE,
    VisualClaimKind.TOPOLOGY,
    VisualClaimKind.RELATIVE_POSITION,
)
_PLAN_CANNOT = (
    VisualClaimKind.VISIBLE_MORPHOLOGY,
    VisualClaimKind.MATERIAL_CONDITION,
    VisualClaimKind.EXACT_DIMENSION,
)
_RECONSTRUCTION_SUPPORTS = (
    VisualClaimKind.ELEMENT_EXISTENCE,
    VisualClaimKind.TOPOLOGY,
    VisualClaimKind.RELATIVE_POSITION,
    VisualClaimKind.VISIBLE_MORPHOLOGY,
)
_RECONSTRUCTION_CANNOT = (
    VisualClaimKind.MATERIAL_CONDITION,
    VisualClaimKind.EXACT_DIMENSION,
)
_PHOTO_SUPPORTS = (
    VisualClaimKind.ELEMENT_EXISTENCE,
    VisualClaimKind.RELATIVE_POSITION,
    VisualClaimKind.VISIBLE_MORPHOLOGY,
    VisualClaimKind.MATERIAL_CONDITION,
)
_PHOTO_CANNOT = (
    VisualClaimKind.TOPOLOGY,
    VisualClaimKind.EXACT_DIMENSION,
)


def _region(
    source_id: str,
    region_id: str,
    normalized_bbox: tuple[float, float, float, float],
    *,
    host_component: str,
    spatial_location: str,
    confidence: float,
    supports: tuple[VisualClaimKind, ...],
    cannot_support: tuple[VisualClaimKind, ...],
    review_state: VisualReviewState,
) -> VisualRegionSpec:
    return VisualRegionSpec(
        source_id=source_id,
        region_id=region_id,
        normalized_bbox=normalized_bbox,
        element_tag=region_id,
        host_component=host_component,
        spatial_location=spatial_location,
        confidence=confidence,
        supports=supports,
        cannot_support=cannot_support,
        review_state=review_state,
    )


DEFAULT_REGION_SPECS: tuple[VisualRegionSpec, ...] = (
    _region(
        "ysma_11_1",
        "outer_peristyle",
        (0.103, 0.136, 0.964, 0.866),
        host_component="parthenon",
        spatial_location="overall",
        confidence=0.98,
        supports=_PLAN_SUPPORTS,
        cannot_support=_PLAN_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_11_1",
        "cella_wall",
        (0.216, 0.248, 0.859, 0.755),
        host_component="cella",
        spatial_location="overall",
        confidence=0.97,
        supports=_PLAN_SUPPORTS,
        cannot_support=_PLAN_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_11_1",
        "inner_colonnade",
        (0.282, 0.358, 0.595, 0.645),
        host_component="cella",
        spatial_location="east_chamber",
        confidence=0.95,
        supports=_PLAN_SUPPORTS,
        cannot_support=_PLAN_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_11_1",
        "four_column",
        (0.682, 0.392, 0.772, 0.605),
        host_component="west_chamber",
        spatial_location="center",
        confidence=0.95,
        supports=_PLAN_SUPPORTS,
        cannot_support=_PLAN_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_11_2",
        "pediment",
        (0.097, 0.143, 0.898, 0.304),
        host_component="east_front",
        spatial_location="upper",
        confidence=0.88,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_2",
        "frieze",
        (0.117, 0.304, 0.868, 0.383),
        host_component="east_entablature",
        spatial_location="upper",
        confidence=0.86,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_2",
        "east_colonnade",
        (0.111, 0.402, 0.870, 0.804),
        host_component="east_front",
        spatial_location="center",
        confidence=0.90,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_2",
        "stepped_base",
        (0.075, 0.795, 0.920, 0.879),
        host_component="east_front",
        spatial_location="base",
        confidence=0.88,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_3",
        "pediment",
        (0.093, 0.137, 0.899, 0.302),
        host_component="west_front",
        spatial_location="upper",
        confidence=0.88,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_3",
        "frieze",
        (0.115, 0.295, 0.868, 0.377),
        host_component="west_entablature",
        spatial_location="upper",
        confidence=0.86,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_3",
        "west_colonnade",
        (0.112, 0.394, 0.869, 0.796),
        host_component="west_front",
        spatial_location="center",
        confidence=0.90,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_3",
        "stepped_base",
        (0.077, 0.787, 0.918, 0.868),
        host_component="west_front",
        spatial_location="base",
        confidence=0.88,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_4",
        "roof",
        (0.527, 0.110, 0.930, 0.323),
        host_component="parthenon",
        spatial_location="upper_northeast",
        confidence=0.79,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_4",
        "two_tier_colonnade",
        (0.273, 0.254, 0.615, 0.668),
        host_component="cella",
        spatial_location="interior",
        confidence=0.77,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_4",
        "outer_long_colonnade",
        (0.531, 0.261, 0.925, 0.711),
        host_component="peristyle",
        spatial_location="north_long_side",
        confidence=0.84,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_4",
        "sectioned_masonry",
        (0.453, 0.392, 0.615, 0.695),
        host_component="cella_wall",
        spatial_location="northeast_section",
        confidence=0.80,
        supports=_RECONSTRUCTION_SUPPORTS,
        cannot_support=_RECONSTRUCTION_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_11_6",
        "central_doorway",
        (0.442, 0.333, 0.554, 0.676),
        host_component="cella_wall",
        spatial_location="center",
        confidence=0.92,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_11_6",
        "far_wall",
        (0.266, 0.250, 0.718, 0.672),
        host_component="cella",
        spatial_location="far_interior",
        confidence=0.87,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_11_6",
        "left_column_row",
        (0.048, 0.176, 0.283, 0.643),
        host_component="interior_colonnade",
        spatial_location="image_left",
        confidence=0.93,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_11_6",
        "right_column_row",
        (0.744, 0.0, 1.0, 0.671),
        host_component="interior_colonnade",
        spatial_location="image_right",
        confidence=0.90,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_12_11",
        "left_fluting",
        (0.104, 0.381, 0.352, 0.666),
        host_component="left_pronaos_column",
        spatial_location="image_left",
        confidence=0.96,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_12_11",
        "center_fluting",
        (0.650, 0.363, 0.904, 0.796),
        host_component="center_pronaos_column",
        spatial_location="image_center_right",
        confidence=0.96,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_12_11",
        "entablature",
        (0.076, 0.059, 1.0, 0.278),
        host_component="pronaos",
        spatial_location="upper",
        confidence=0.93,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_12_11",
        "scaffold",
        (0.0, 0.0, 0.145, 0.713),
        host_component="restoration_site",
        spatial_location="image_left",
        confidence=0.98,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.REJECTED,
    ),
    _region(
        "ysma_12_7",
        "suspended_relief",
        (0.362, 0.174, 0.493, 0.319),
        host_component="north_entablature",
        spatial_location="west_corner",
        confidence=0.93,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.PARKED,
    ),
    _region(
        "ysma_12_7",
        "lifting_tackle",
        (0.381, 0.0, 0.460, 0.306),
        host_component="restoration_site",
        spatial_location="upper_center",
        confidence=0.98,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.REJECTED,
    ),
    _region(
        "ysma_12_7",
        "scaffold",
        (0.020, 0.087, 0.426, 1.0),
        host_component="restoration_site",
        spatial_location="image_left",
        confidence=0.99,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.REJECTED,
    ),
    _region(
        "ysma_12_7",
        "frieze_entablature",
        (0.337, 0.275, 0.960, 0.512),
        host_component="north_entablature",
        spatial_location="west_corner",
        confidence=0.94,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_12_9",
        "building",
        (0.115, 0.248, 0.834, 0.833),
        host_component="parthenon",
        spatial_location="northwest",
        confidence=0.97,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_12_9",
        "north_colonnade",
        (0.120, 0.489, 0.439, 0.806),
        host_component="peristyle",
        spatial_location="north_side",
        confidence=0.94,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.SELECTED,
    ),
    _region(
        "ysma_12_9",
        "scaffold",
        (0.432, 0.269, 0.833, 0.792),
        host_component="restoration_site",
        spatial_location="west_end",
        confidence=0.99,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.REJECTED,
    ),
    _region(
        "ysma_12_9",
        "boom",
        (0.362, 0.250, 0.435, 0.543),
        host_component="restoration_site",
        spatial_location="image_center_left",
        confidence=0.98,
        supports=_PHOTO_SUPPORTS,
        cannot_support=_PHOTO_CANNOT,
        review_state=VisualReviewState.REJECTED,
    ),
)


def _region_ordinal_map() -> dict[tuple[str, str], int]:
    counts: dict[str, int] = {}
    result: dict[tuple[str, str], int] = {}
    for spec in DEFAULT_REGION_SPECS:
        ordinal = counts.get(spec.source_id, 0) + 1
        counts[spec.source_id] = ordinal
        result[(spec.source_id, spec.region_id)] = ordinal
    return result


_REGION_ORDINAL_BY_KEY = _region_ordinal_map()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_captured_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ParthenonVisualRegionError(
            "captured_at must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ParthenonVisualRegionError(
            "captured_at must include a timezone"
        )
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_ref_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
        "uri": ref.uri,
    }


def _artifact_ref_dict(ref: ProjectArtifactRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "artifact_id": ref.artifact_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
        "uri": ref.uri,
    }


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ParthenonVisualRegionError(f"{field} must be a mapping")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ParthenonVisualRegionError(f"{field} must be non-empty text")
    return value


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ParthenonVisualRegionError(f"{field} must be an integer")
    return value


def _record_ref_from_dict(value: object, field: str) -> ProjectRecordRef:
    row = _mapping(value, field)
    ref = ProjectRecordRef(
        project_id=_text(row.get("project_id"), f"{field}.project_id"),
        relative_path=_text(
            row.get("relative_path"),
            f"{field}.relative_path",
        ),
        sha256=_text(row.get("sha256"), f"{field}.sha256"),
        media_type=_text(row.get("media_type"), f"{field}.media_type"),
    )
    if row.get("uri") != ref.uri:
        raise ParthenonVisualRegionError(f"{field}.uri disagrees with its fields")
    return ref


def _artifact_ref_from_dict(value: object, field: str) -> ProjectArtifactRef:
    row = _mapping(value, field)
    ref = ProjectArtifactRef(
        project_id=_text(row.get("project_id"), f"{field}.project_id"),
        artifact_id=_text(row.get("artifact_id"), f"{field}.artifact_id"),
        relative_path=_text(
            row.get("relative_path"),
            f"{field}.relative_path",
        ),
        sha256=_text(row.get("sha256"), f"{field}.sha256"),
        media_type=_text(row.get("media_type"), f"{field}.media_type"),
    )
    if row.get("uri") != ref.uri:
        raise ParthenonVisualRegionError(f"{field}.uri disagrees with its fields")
    return ref


def _source_from_dict(value: object) -> VisualSource:
    row = _mapping(value, "source")
    try:
        source = VisualSource(
            source_id=_text(row.get("source_id"), "source.source_id"),
            view_id=_text(row.get("view_id"), "source.view_id"),
            url=_text(row.get("url"), "source.url"),
            retrieved_at=_text(
                row.get("retrieved_at"),
                "source.retrieved_at",
            ),
            media_type=_text(row.get("media_type"), "source.media_type"),
            pixel_width=_integer(
                row.get("pixel_width"),
                "source.pixel_width",
            ),
            pixel_height=_integer(
                row.get("pixel_height"),
                "source.pixel_height",
            ),
            content_sha256=_text(
                row.get("content_sha256"),
                "source.content_sha256",
            ),
            artifact_ref=_text(
                row.get("artifact_ref"),
                "source.artifact_ref",
            ),
            source_family=_text(
                row.get("source_family"),
                "source.source_family",
            ),
            modality=VisualSourceModality(
                _text(row.get("modality"), "source.modality")
            ),
            usage_note=_text(row.get("usage_note"), "source.usage_note"),
            branch_id=_text(row.get("branch_id"), "source.branch_id"),
            scope_ref=_text(row.get("scope_ref"), "source.scope_ref"),
        )
    except (TypeError, ValueError) as exc:
        raise ParthenonVisualRegionError("source contract is invalid") from exc
    if dict(row) != source.to_dict():
        raise ParthenonVisualRegionError(
            f"source {source.source_id} record schema drifted"
        )
    return source


def _validate_predecessor_ref(ref: ProjectRecordRef) -> None:
    if not isinstance(ref, ProjectRecordRef):
        raise TypeError("predecessor_manifest_ref must be ProjectRecordRef")
    prefix = (
        "runs/research-005/branches/idealized-periclean-original/records/"
        "visual-candidate-manifest-"
    )
    if (
        ref.project_id != PROJECT_ID
        or not ref.relative_path.startswith(prefix)
        or ref.relative_path != f"{prefix}{ref.sha256}.json"
        or ref.media_type != "application/json"
    ):
        raise ParthenonVisualRegionError(
            "predecessor manifest is outside the exact P085 record boundary"
        )


def _normalize_supersedes(
    predecessor: ProjectRecordRef,
    values: tuple[str, ...] | None,
) -> tuple[str, str]:
    resolved = (
        (LEGACY_P085_MANIFEST_URI, predecessor.uri)
        if values is None
        else values
    )
    if (
        not isinstance(resolved, tuple)
        or len(resolved) != 2
        or any(not isinstance(item, str) or not item for item in resolved)
        or resolved[0] != LEGACY_P085_MANIFEST_URI
        or resolved[1] != predecessor.uri
        or len(set(resolved)) != 2
    ):
        raise ParthenonVisualRegionError(
            "supersedes manifests must be [legacy P085, exact predecessor]"
        )
    return resolved


def _ensure_reconstruction_unopened(
    repository: FilesystemProjectRepository,
) -> None:
    forbidden = repository.layout.run(FORBIDDEN_RECONSTRUCTION_RUN_ID).root
    if forbidden.exists():
        raise FileExistsError(
            f"{FORBIDDEN_RECONSTRUCTION_RUN_ID} must remain unopened during P086"
        )


def _validate_source_image(data: bytes, source: VisualSource) -> None:
    if _sha256(data) != source.content_sha256:
        raise ParthenonVisualRegionError(
            f"source {source.source_id} content digest mismatch"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                image_format = image.format
                image_size = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as exc:
        raise ParthenonVisualRegionError(
            f"source {source.source_id} is not a valid retained image"
        ) from exc
    if (
        _SOURCE_MEDIA_FORMATS.get(str(image_format)) != source.media_type
        or image_size != source.pixel_size
    ):
        raise ParthenonVisualRegionError(
            f"source {source.source_id} image metadata disagrees with P085"
        )


def _load_predecessor_sources(
    repository: FilesystemProjectRepository,
    predecessor_ref: ProjectRecordRef,
) -> tuple[dict[str, object], tuple[_LoadedSource, ...]]:
    predecessor = repository.load_json(predecessor_ref)
    if (
        predecessor.get("schema") != "ParthenonVisualCandidateSelection@1"
        or predecessor.get("project_id") != PROJECT_ID
        or predecessor.get("run_id") != RESEARCH_RUN_ID
        or predecessor.get("branch_id") != BRANCH_ID
        or predecessor.get("scope_ref") != SCOPE_REF
        or predecessor.get("reconstruction_005_created") is not False
    ):
        raise ParthenonVisualRegionError(
            "predecessor manifest identity or authority boundary drifted"
        )
    source_ref_rows = predecessor.get("source_record_refs")
    if (
        not isinstance(source_ref_rows, list)
        or len(source_ref_rows) != EXPECTED_SOURCE_COUNT
    ):
        raise ParthenonVisualRegionError(
            f"predecessor must retain exactly {EXPECTED_SOURCE_COUNT} source records"
        )
    source_refs = tuple(
        _record_ref_from_dict(value, f"source_record_refs[{index}]")
        for index, value in enumerate(source_ref_rows)
    )
    if len({ref.uri for ref in source_refs}) != len(source_refs):
        raise ParthenonVisualRegionError("predecessor source records are duplicated")

    loaded: list[_LoadedSource] = []
    expected_source_root = (
        f"runs/{RESEARCH_RUN_ID}/workspaces/visual-rag/source-images"
    )
    expected_record_root = (
        f"runs/{RESEARCH_RUN_ID}/branches/{BRANCH_ID}/records/visual-source-"
    )
    for ref in source_refs:
        if (
            ref.project_id != PROJECT_ID
            or ref.media_type != "application/json"
            or not ref.relative_path.startswith(expected_record_root)
            or not ref.relative_path.endswith(f"-{ref.sha256}.json")
        ):
            raise ParthenonVisualRegionError(
                "predecessor source record escaped the P085 branch boundary"
            )
        payload = repository.load_json(ref)
        if (
            payload.get("schema") != "ParthenonVisualSourceRecord@1"
            or payload.get("project_id") != PROJECT_ID
            or payload.get("run_id") != RESEARCH_RUN_ID
            or payload.get("branch_id") != BRANCH_ID
            or payload.get("scope_ref") != SCOPE_REF
        ):
            raise ParthenonVisualRegionError(
                f"source record {ref.uri} identity drifted"
            )
        source = _source_from_dict(payload.get("source"))
        artifact = _artifact_ref_from_dict(
            payload.get("artifact_ref"),
            f"source {source.source_id} artifact_ref",
        )
        if (
            artifact.project_id != PROJECT_ID
            or artifact.sha256 != source.content_sha256
            or artifact.media_type != source.media_type
            or artifact.uri != source.artifact_ref
            or artifact.relative_path
            != (
                f"objects/sha256/{artifact.sha256[:2]}/"
                f"{artifact.sha256}"
            )
        ):
            raise ParthenonVisualRegionError(
                f"source {source.source_id} object binding drifted"
            )
        try:
            object_bytes = repository.layout.resolve_record(artifact).read_bytes()
        except OSError as exc:
            raise ParthenonVisualRegionError(
                f"source {source.source_id} object is unavailable"
            ) from exc
        if _sha256(object_bytes) != artifact.sha256:
            raise ParthenonVisualRegionError(
                f"source {source.source_id} object digest mismatch"
            )

        filename = _text(payload.get("filename"), "source filename")
        expected_prefix = f"{source.source_id}__{source.view_id}."
        if (
            not filename.startswith(expected_prefix)
            or Path(filename).suffix.lower() not in {".jpg", ".png", ".webp"}
            or Path(filename).name != filename
        ):
            raise ParthenonVisualRegionError(
                f"source {source.source_id} filename drifted"
            )
        workspace_relative = require_project_relative_path(
            _text(
                payload.get("workspace_relative_path"),
                "workspace_relative_path",
            )
        )
        if workspace_relative != f"{expected_source_root}/{filename}":
            raise ParthenonVisualRegionError(
                f"source {source.source_id} workspace path drifted"
            )
        workspace_path = repository.layout.resolve_relative(workspace_relative)
        try:
            workspace_bytes = workspace_path.read_bytes()
        except OSError as exc:
            raise ParthenonVisualRegionError(
                f"source {source.source_id} workspace file is unavailable"
            ) from exc
        if workspace_bytes != object_bytes:
            raise ParthenonVisualRegionError(
                f"source {source.source_id} workspace/object digest mismatch"
            )
        _validate_source_image(workspace_bytes, source)
        loaded.append(
            _LoadedSource(
                source=source,
                source_record_ref=ref,
                artifact_ref=artifact,
                workspace_path=workspace_path,
                source_bytes=workspace_bytes,
            )
        )

    ordered = tuple(sorted(loaded, key=lambda item: item.source.source_id))
    if len({item.source.source_id for item in ordered}) != EXPECTED_SOURCE_COUNT:
        raise ParthenonVisualRegionError("predecessor source ids are duplicated")
    manifest = _mapping(predecessor.get("manifest"), "predecessor manifest")
    manifest_sources = manifest.get("sources")
    if not isinstance(manifest_sources, list) or len(manifest_sources) != len(ordered):
        raise ParthenonVisualRegionError("predecessor manifest sources drifted")
    retained_by_id = {
        item.source.source_id: item.source.to_dict() for item in ordered
    }
    manifest_by_id: dict[str, object] = {}
    for value in manifest_sources:
        row = _mapping(value, "predecessor manifest source")
        source_id = _text(row.get("source_id"), "manifest source_id")
        if source_id in manifest_by_id:
            raise ParthenonVisualRegionError(
                "predecessor manifest source ids are duplicated"
            )
        manifest_by_id[source_id] = dict(row)
    if manifest_by_id != retained_by_id:
        raise ParthenonVisualRegionError(
            "predecessor manifest and exact source records disagree"
        )
    return predecessor, ordered


def _ordered_region_specs(
    values: tuple[VisualRegionSpec, ...],
    *,
    sources: Mapping[str, _LoadedSource],
) -> tuple[VisualRegionSpec, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) != EXPECTED_REGION_COUNT
        or any(not isinstance(item, VisualRegionSpec) for item in values)
    ):
        raise ParthenonVisualRegionError(
            f"region_specs must contain exactly {EXPECTED_REGION_COUNT} typed ROIs"
        )
    identities = {(item.source_id, item.region_id) for item in values}
    if identities != set(_REGION_ORDINAL_BY_KEY):
        raise ParthenonVisualRegionError(
            "region_specs must retain the exact reviewed source/region identities"
        )
    ordered = tuple(
        sorted(
            values,
            key=lambda item: (
                item.source_id,
                _REGION_ORDINAL_BY_KEY[(item.source_id, item.region_id)],
            ),
        )
    )
    if len({item.candidate_id for item in ordered}) != len(ordered):
        raise ParthenonVisualRegionError("region candidate ids are duplicated")
    by_source = {source_id: 0 for source_id in sources}
    for spec in ordered:
        _validate_normalized_bbox(spec.normalized_bbox)
        loaded = sources.get(spec.source_id)
        if loaded is None:
            raise ParthenonVisualRegionError(
                f"region {spec.candidate_id} references an absent source"
            )
        by_source[spec.source_id] += 1
        modality = loaded.source.modality
        if (
            modality is VisualSourceModality.RECONSTRUCTION_DRAWING
            and spec.review_state is not VisualReviewState.PARKED
        ):
            raise ParthenonVisualRegionError(
                "reconstruction-drawing regions must remain parked"
            )
        if (
            modality is VisualSourceModality.MEASURED_DRAWING
            and not set(spec.supports).issubset(_PLAN_SUPPORTS)
        ):
            raise ParthenonVisualRegionError(
                "measured drawing ROI claims exceed the localization boundary"
            )
        if (
            modality is VisualSourceModality.RECONSTRUCTION_DRAWING
            and VisualClaimKind.MATERIAL_CONDITION in spec.supports
        ):
            raise ParthenonVisualRegionError(
                "reconstruction drawings cannot establish material condition"
            )
        if (
            modality is VisualSourceModality.CURRENT_PHOTO
            and VisualClaimKind.TOPOLOGY in spec.supports
        ):
            raise ParthenonVisualRegionError(
                "uncalibrated photographs cannot establish topology"
            )
        if VisualClaimKind.EXACT_DIMENSION not in spec.cannot_support:
            raise ParthenonVisualRegionError(
                "region omitted the exact-dimension non-claim"
            )
    if set(by_source.values()) != {EXPECTED_REGIONS_PER_SOURCE}:
        raise ParthenonVisualRegionError(
            f"each retained source must have {EXPECTED_REGIONS_PER_SOURCE} ROIs"
        )
    return ordered


def _pixel_bbox(
    normalized: tuple[float, float, float, float],
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    left_n, top_n, right_n, bottom_n = normalized
    left = min(width - 1, max(0, math.floor(left_n * width)))
    top = min(height - 1, max(0, math.floor(top_n * height)))
    right = min(width, max(left + 1, math.ceil(right_n * width)))
    bottom = min(height, max(top + 1, math.ceil(bottom_n * height)))
    return left, top, right, bottom


def _candidate_from_spec(
    spec: VisualRegionSpec,
    source: VisualSource,
) -> VisualRegionCandidate:
    pixel_bbox = _pixel_bbox(
        spec.normalized_bbox,
        width=source.pixel_width,
        height=source.pixel_height,
    )
    left, top, right, bottom = pixel_bbox
    return VisualRegionCandidate(
        candidate_id=spec.candidate_id,
        source_id=source.source_id,
        pixel_bbox=pixel_bbox,
        normalized_bbox=(
            left / source.pixel_width,
            top / source.pixel_height,
            right / source.pixel_width,
            bottom / source.pixel_height,
        ),
        element_tag=spec.element_tag,
        host_component=spec.host_component,
        spatial_location=spec.spatial_location,
        confidence=spec.confidence,
        supports=spec.supports,
        cannot_support=spec.cannot_support,
        review_state=spec.review_state,
        measurement_basis=None,
    )


def _open_rgb(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.convert("RGB")


def _png_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(
        stream,
        format="PNG",
        optimize=False,
        compress_level=9,
    )
    return stream.getvalue()


def _derived_path(
    repository: FilesystemProjectRepository,
    run: RunRef,
    *,
    category: str,
    filename: str,
) -> Path:
    require_identifier(category, "derived category")
    root = (
        repository.layout.run(run.run_id).workspaces
        / "visual-rag"
        / "derived"
        / category
    ).resolve(strict=False)
    target = (root / filename).resolve(strict=False)
    if target.parent != root or target.name != filename:
        raise ParthenonVisualRegionError(
            "derived image escaped its speculative workspace"
        )
    return target


def _write_immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise FileExistsError(
                f"derived image already exists with different bytes: {path.name}"
            )


def _overlay_png(
    source_image: Image.Image,
    candidates: tuple[VisualRegionCandidate, ...],
) -> bytes:
    overlay = source_image.copy()
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    line_width = max(2, min(source_image.size) // 400)
    for candidate in candidates:
        color = _REVIEW_COLORS[candidate.review_state]
        left, top, right, bottom = candidate.pixel_bbox
        draw.rectangle(
            (left, top, right - 1, bottom - 1),
            outline=color,
            width=line_width,
        )
        label = (
            f"{candidate.element_tag} [{candidate.review_state.value}]"
        )
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        label_left = min(max(0, left), max(0, overlay.width - text_width - 4))
        label_top = max(0, top - text_height - 4)
        draw.rectangle(
            (
                label_left,
                label_top,
                label_left + text_width + 4,
                label_top + text_height + 4,
            ),
            fill=color,
        )
        draw.text(
            (label_left + 2, label_top + 2),
            label,
            fill=(255, 255, 255),
            font=font,
        )
    return _png_bytes(overlay)


def refine_project(
    root: Path,
    *,
    captured_at: str,
    predecessor_manifest_ref: ProjectRecordRef = DEFAULT_PREDECESSOR_MANIFEST_REF,
    supersedes_manifest_refs: tuple[str, ...] | None = None,
    region_specs: tuple[VisualRegionSpec, ...] = DEFAULT_REGION_SPECS,
) -> dict[str, object]:
    """Append deterministic P086 ROI evidence to an existing research run."""

    root = Path(root).resolve(strict=False)
    captured_at = _normalize_captured_at(captured_at)
    _validate_predecessor_ref(predecessor_manifest_ref)
    supersedes = _normalize_supersedes(
        predecessor_manifest_ref,
        supersedes_manifest_refs,
    )
    repository = FilesystemProjectRepository.open(root)
    if repository.load_manifest().project_id != PROJECT_ID:
        raise ParthenonVisualRegionError(
            "this project-local runner cannot author another project"
        )
    _ensure_reconstruction_unopened(repository)
    run = repository.load_run(RESEARCH_RUN_ID)
    head_before = repository.read_head()
    predecessor, loaded_sources = _load_predecessor_sources(
        repository,
        predecessor_manifest_ref,
    )
    sources_by_id = {
        item.source.source_id: item for item in loaded_sources
    }
    ordered_specs = _ordered_region_specs(
        region_specs,
        sources=sources_by_id,
    )
    region_spec_digest = canonical_digest(
        [item.to_input_dict() for item in ordered_specs]
    )
    derived_root = (
        repository.layout.run(run.run_id).workspaces
        / "visual-rag"
        / "derived"
    )
    resume_mode = "resumed" if derived_root.exists() else "created"
    branch_destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=run.run_id,
        branch_id=BRANCH_ID,
    )
    record_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )

    region_outputs: list[_RegionOutput] = []
    overlay_outputs: list[_OverlayOutput] = []
    candidates: list[VisualRegionCandidate] = []
    for loaded in loaded_sources:
        source_specs = tuple(
            item for item in ordered_specs if item.source_id == loaded.source.source_id
        )
        source_image = _open_rgb(loaded.source_bytes)
        source_candidates: list[VisualRegionCandidate] = []
        for spec in source_specs:
            candidate = _candidate_from_spec(spec, loaded.source)
            candidates.append(candidate)
            source_candidates.append(candidate)
            crop = source_image.crop(candidate.pixel_bbox)
            crop_bytes = _png_bytes(crop)
            region_ordinal = _REGION_ORDINAL_BY_KEY[
                (spec.source_id, spec.region_id)
            ]
            crop_filename = (
                f"{loaded.source.source_id}__r{region_ordinal:03d}__"
                f"{spec.region_id}.png"
            )
            crop_path = _derived_path(
                repository,
                run,
                category="regions",
                filename=crop_filename,
            )
            _write_immutable(crop_path, crop_bytes)
            crop_artifact = repository.ingest(
                run=run,
                destination=PersistenceDestination(PersistenceArea.OBJECT),
                artifact_id=f"visual-region-{candidate.candidate_id}",
                media_type=PNG_MEDIA_TYPE,
                source=io.BytesIO(crop_bytes),
            )
            if crop_artifact.sha256 != _sha256(crop_bytes):
                raise ParthenonVisualRegionError(
                    f"crop object digest mismatch for {candidate.candidate_id}"
                )
            region_outputs.append(
                _RegionOutput(
                    spec=spec,
                    candidate=candidate,
                    crop_path=crop_path,
                    crop_sha256=crop_artifact.sha256,
                    crop_artifact_ref=crop_artifact,
                )
            )

        source_candidate_tuple = tuple(
            sorted(source_candidates, key=lambda item: item.candidate_id)
        )
        overlay_bytes = _overlay_png(source_image, source_candidate_tuple)
        overlay_filename = f"{loaded.source.source_id}__labels.png"
        overlay_path = _derived_path(
            repository,
            run,
            category="overlays",
            filename=overlay_filename,
        )
        _write_immutable(overlay_path, overlay_bytes)
        overlay_artifact = repository.ingest(
            run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id=f"visual-region-overlay-{loaded.source.source_id}",
            media_type=PNG_MEDIA_TYPE,
            source=io.BytesIO(overlay_bytes),
        )
        if overlay_artifact.sha256 != _sha256(overlay_bytes):
            raise ParthenonVisualRegionError(
                f"overlay object digest mismatch for {loaded.source.source_id}"
            )
        overlay_outputs.append(
            _OverlayOutput(
                source_id=loaded.source.source_id,
                candidate_ids=tuple(
                    item.candidate_id for item in source_candidate_tuple
                ),
                overlay_path=overlay_path,
                overlay_sha256=overlay_artifact.sha256,
                overlay_artifact_ref=overlay_artifact,
            )
        )

    candidate_tuple = tuple(
        sorted(candidates, key=lambda item: item.candidate_id)
    )
    policy = VisualEvidenceManifestPolicy(
        policy_id="parthenon-stage4-visual-regions",
        branch_id=BRANCH_ID,
        scope_ref=SCOPE_REF,
        allowed_modalities=tuple(VisualSourceModality),
        normalized_bbox_tolerance=1e-6,
        minimum_selected_candidates=1,
    )
    manifest = compile_visual_evidence_manifest(
        policy,
        sources=tuple(item.source for item in loaded_sources),
        candidates=candidate_tuple,
    )
    manifest_contract = manifest.to_dict()

    region_record_refs: list[ProjectRecordRef] = []
    loaded_by_id = {
        item.source.source_id: item for item in loaded_sources
    }
    for output in sorted(
        region_outputs,
        key=lambda item: item.candidate.candidate_id,
    ):
        loaded = loaded_by_id[output.candidate.source_id]
        region_record_refs.append(
            repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"visual-region-{output.candidate.candidate_id}",
                payload={
                    "schema": "ParthenonVisualRegionArtifact@1",
                    "project_id": PROJECT_ID,
                    "run_id": RESEARCH_RUN_ID,
                    "branch_id": BRANCH_ID,
                    "scope_ref": SCOPE_REF,
                    "predecessor_manifest_ref": predecessor_manifest_ref.uri,
                    "source_record_ref": _record_ref_dict(
                        loaded.source_record_ref
                    ),
                    "source_id": loaded.source.source_id,
                    "view_id": loaded.source.view_id,
                    "source_content_sha256": loaded.source.content_sha256,
                    "requested_normalized_bbox": list(
                        output.spec.normalized_bbox
                    ),
                    "region_ordinal": _REGION_ORDINAL_BY_KEY[
                        (output.spec.source_id, output.spec.region_id)
                    ],
                    "candidate": output.candidate.to_dict(),
                    "crop": {
                        "relative_path": output.crop_path.relative_to(
                            root
                        ).as_posix(),
                        "sha256": output.crop_sha256,
                        "media_type": PNG_MEDIA_TYPE,
                        "pixel_width": (
                            output.candidate.pixel_bbox[2]
                            - output.candidate.pixel_bbox[0]
                        ),
                        "pixel_height": (
                            output.candidate.pixel_bbox[3]
                            - output.candidate.pixel_bbox[1]
                        ),
                        "artifact_ref": _artifact_ref_dict(
                            output.crop_artifact_ref
                        ),
                    },
                    "measurement_authority": False,
                    "geometry_mutation_authority": False,
                    "canonical_write_authority": False,
                },
            )
        )

    overlay_record_refs: list[ProjectRecordRef] = []
    for output in sorted(overlay_outputs, key=lambda item: item.source_id):
        loaded = loaded_by_id[output.source_id]
        overlay_record_refs.append(
            repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"visual-region-overlay-{output.source_id}",
                payload={
                    "schema": "ParthenonVisualRegionOverlay@1",
                    "project_id": PROJECT_ID,
                    "run_id": RESEARCH_RUN_ID,
                    "branch_id": BRANCH_ID,
                    "scope_ref": SCOPE_REF,
                    "predecessor_manifest_ref": predecessor_manifest_ref.uri,
                    "source_record_ref": _record_ref_dict(
                        loaded.source_record_ref
                    ),
                    "source_id": loaded.source.source_id,
                    "view_id": loaded.source.view_id,
                    "source_content_sha256": loaded.source.content_sha256,
                    "candidate_ids": list(output.candidate_ids),
                    "overlay": {
                        "relative_path": output.overlay_path.relative_to(
                            root
                        ).as_posix(),
                        "sha256": output.overlay_sha256,
                        "media_type": PNG_MEDIA_TYPE,
                        "pixel_width": loaded.source.pixel_width,
                        "pixel_height": loaded.source.pixel_height,
                        "artifact_ref": _artifact_ref_dict(
                            output.overlay_artifact_ref
                        ),
                    },
                    "geometry_mutation_authority": False,
                    "canonical_write_authority": False,
                },
            )
        )

    refined_manifest_ref = repository.put_json(
        run=run,
        destination=branch_destination,
        record_kind="visual-region-manifest",
        payload={
            "schema": "ParthenonVisualRegionSelection@1",
            "project_id": PROJECT_ID,
            "run_id": RESEARCH_RUN_ID,
            "branch_id": BRANCH_ID,
            "scope_ref": SCOPE_REF,
            "predecessor_manifest_ref": predecessor_manifest_ref.uri,
            "predecessor_manifest_record": _record_ref_dict(
                predecessor_manifest_ref
            ),
            "supersedes_manifest_refs": list(supersedes),
            "predecessor_schema": predecessor["schema"],
            "region_spec_digest": region_spec_digest,
            "manifest": manifest_contract,
            "source_record_refs": [
                _record_ref_dict(item.source_record_ref)
                for item in loaded_sources
            ],
            "region_record_refs": [
                _record_ref_dict(item) for item in region_record_refs
            ],
            "overlay_record_refs": [
                _record_ref_dict(item) for item in overlay_record_refs
            ],
            "selected_candidate_ids": list(manifest.selected_candidate_ids),
            "parked_candidate_ids": list(manifest.parked_candidate_ids),
            "rejected_candidate_ids": list(manifest.rejected_candidate_ids),
            "pending_candidate_ids": list(manifest.pending_candidate_ids),
            "reconstruction_005_created": False,
            "measurement_authority": False,
            "geometry_mutation_authority": False,
            "canonical_write_authority": False,
        },
    )

    head_after = repository.read_head()
    if head_after != head_before:
        raise ParthenonVisualRegionError(
            "canonical project state changed during P086"
        )
    progress_ref = repository.put_json(
        run=run,
        destination=record_destination,
        record_kind="stage4-visual-region-progress",
        payload={
            "schema": "ParthenonStage4VisualRegionProgress@1",
            "project_id": PROJECT_ID,
            "run_id": RESEARCH_RUN_ID,
            "branch_id": BRANCH_ID,
            "scope_ref": SCOPE_REF,
            "captured_at": captured_at,
            "resume_mode": resume_mode,
            "predecessor_manifest_ref": predecessor_manifest_ref.uri,
            "supersedes_manifest_refs": list(supersedes),
            "region_spec_digest": region_spec_digest,
            "source_count": len(loaded_sources),
            "region_count": len(region_record_refs),
            "overlay_count": len(overlay_record_refs),
            "selected_candidate_count": len(manifest.selected_candidate_ids),
            "parked_candidate_count": len(manifest.parked_candidate_ids),
            "rejected_candidate_count": len(manifest.rejected_candidate_ids),
            "pending_candidate_count": len(manifest.pending_candidate_ids),
            "network_calls": 0,
            "derived_workspace_relative_path": (
                f"runs/{RESEARCH_RUN_ID}/workspaces/visual-rag/derived"
            ),
            "refined_manifest_ref": _record_ref_dict(refined_manifest_ref),
            "canonical_head_before": {
                "project_id": head_before.project_id,
                "version": head_before.version,
                "state_sha256": head_before.require_digest(),
            },
            "canonical_head_after": {
                "project_id": head_after.project_id,
                "version": head_after.version,
                "state_sha256": head_after.require_digest(),
            },
            "disposition": "HOLD",
            "reconstruction_005_created": False,
            "measurement_authority": False,
            "geometry_mutation_authority": False,
            "canonical_write_authority": False,
        },
    )
    _ensure_reconstruction_unopened(repository)
    if repository.read_head() != head_before:
        raise ParthenonVisualRegionError(
            "canonical project state changed after P086 progress write"
        )
    repository.verify()
    return {
        "root": root,
        "run": run,
        "resume_mode": resume_mode,
        "predecessor_manifest_ref": predecessor_manifest_ref,
        "supersedes_manifest_refs": supersedes,
        "region_spec_digest": region_spec_digest,
        "region_record_refs": tuple(region_record_refs),
        "overlay_record_refs": tuple(overlay_record_refs),
        "refined_manifest_ref": refined_manifest_ref,
        "progress_ref": progress_ref,
        "selected_candidate_ids": manifest.selected_candidate_ids,
        "parked_candidate_ids": manifest.parked_candidate_ids,
        "rejected_candidate_ids": manifest.rejected_candidate_ids,
        "pending_candidate_ids": manifest.pending_candidate_ids,
        "crop_paths": tuple(
            item.crop_path
            for item in sorted(
                region_outputs,
                key=lambda output: output.candidate.candidate_id,
            )
        ),
        "crop_hashes": tuple(
            item.crop_sha256
            for item in sorted(
                region_outputs,
                key=lambda output: output.candidate.candidate_id,
            )
        ),
        "overlay_paths": tuple(
            item.overlay_path
            for item in sorted(overlay_outputs, key=lambda output: output.source_id)
        ),
        "overlay_hashes": tuple(
            item.overlay_sha256
            for item in sorted(overlay_outputs, key=lambda output: output.source_id)
        ),
        "canonical_head": head_before,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captured-at")
    args = parser.parse_args(argv)
    captured_at = args.captured_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    root = resolve_probe_root(PROJECT_ID)
    result = refine_project(root, captured_at=captured_at)
    print("project:", root)
    print("research run:", result["run"].run_id)
    print("predecessor:", result["predecessor_manifest_ref"].uri)
    print("regions:", len(result["region_record_refs"]))
    print("overlays:", len(result["overlay_record_refs"]))
    print("selected:", len(result["selected_candidate_ids"]))
    print("parked:", len(result["parked_candidate_ids"]))
    print("rejected:", len(result["rejected_candidate_ids"]))
    print("progress:", result["progress_ref"].uri)
    print("reconstruction-005: not created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
