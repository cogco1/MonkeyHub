#!/usr/bin/env python3
"""P085 project runner for bounded Parthenon Stage 4 visual evidence.

The runner opens or resumes only ``research-005``.  Original image bytes are
kept both in that run's speculative ``visual-rag/source-images`` workspace and
in the P036 content-addressed object store.  All JSON evidence is persisted by
the repository; this runner never opens ``reconstruction-005``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from PIL import Image, UnidentifiedImageError

try:  # Package import in tests; direct import when executed as a script.
    from archive.tools._probe_paths import resolve_probe_root
except ModuleNotFoundError:
    from _probe_paths import resolve_probe_root

from archive.archflow.capabilities.visual_evidence import (
    VisualClaimKind,
    VisualEvidenceManifestPolicy,
    VisualRegionCandidate,
    VisualReviewState,
    VisualSource,
    VisualSourceModality,
    compile_visual_evidence_manifest,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef, RunRef
from archflow.project.refs import require_identifier
from archflow.contracts.canonical import canonical_digest, canonical_json


PROJECT_ID = "parthenon-reconstruction"
RESEARCH_RUN_ID = "research-005"
FORBIDDEN_RECONSTRUCTION_RUN_ID = "reconstruction-005"
BRANCH_ID = "idealized-periclean-original"
SCOPE_REF = "branch-scope:idealized-periclean-original"

MAX_SOURCE_COUNT = 8
MAX_IMAGE_BYTES = 8_000_000
MAX_TOTAL_DOWNLOAD_BYTES = 32_000_000
MAX_PIXEL_COUNT = 40_000_000
MAX_PIXEL_AXIS = 12_000
DEFAULT_TIMEOUT_SECONDS = 30.0

_MEDIA_FORMATS = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}
_ALLOWED_MODALITIES = {
    "measured_drawing",
    "reconstruction_drawing",
    "current_photo",
    "museum_object",
}
_ALLOWED_REVIEW_STATES = {"selected", "parked"}


class ParthenonVisualRagError(ValueError):
    """The project-specific visual evidence request is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class VisualImageSourceSpec:
    source_id: str
    view_id: str
    url: str
    source_family: str
    modality: str
    usage_note: str
    expected_media_type: str

    def __post_init__(self) -> None:
        require_identifier(self.source_id, "source_id")
        require_identifier(self.view_id, "view_id")
        require_identifier(self.source_family, "source_family")
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ParthenonVisualRagError("visual source URL must use HTTPS")
        if self.modality not in _ALLOWED_MODALITIES:
            raise ParthenonVisualRagError("visual source modality is unsupported")
        if not isinstance(self.usage_note, str) or not self.usage_note.strip():
            raise ParthenonVisualRagError("usage_note must be non-empty text")
        if self.expected_media_type not in {
            value[0] for value in _MEDIA_FORMATS.values()
        }:
            raise ParthenonVisualRagError("expected_media_type is unsupported")

    @property
    def source_key(self) -> str:
        return f"{self.source_id}__{self.view_id}"


@dataclass(frozen=True, slots=True)
class VisualCandidateSpec:
    candidate_id: str
    source_id: str
    view_id: str
    normalized_bbox: tuple[float, float, float, float]
    element_tag: str
    host_component: str
    spatial_location: str
    confidence: float
    supports: tuple[str, ...]
    cannot_support: tuple[str, ...]
    review_state: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.candidate_id, "candidate_id"),
            (self.source_id, "source_id"),
            (self.view_id, "view_id"),
            (self.element_tag, "element_tag"),
            (self.host_component, "host_component"),
        ):
            require_identifier(value, field_name)
        if (
            not isinstance(self.normalized_bbox, tuple)
            or len(self.normalized_bbox) != 4
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in self.normalized_bbox
            )
        ):
            raise ParthenonVisualRagError(
                "normalized_bbox must contain four finite numbers"
            )
        x0, y0, x1, y1 = (float(value) for value in self.normalized_bbox)
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            raise ParthenonVisualRagError("normalized_bbox is outside [0, 1]")
        object.__setattr__(self, "normalized_bbox", (x0, y0, x1, y1))
        if (
            not isinstance(self.spatial_location, str)
            or not self.spatial_location.strip()
        ):
            raise ParthenonVisualRagError(
                "spatial_location must be non-empty text"
            )
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(float(self.confidence))
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ParthenonVisualRagError("confidence must be within [0, 1]")
        object.__setattr__(self, "confidence", float(self.confidence))
        for values, field_name in (
            (self.supports, "supports"),
            (self.cannot_support, "cannot_support"),
        ):
            if (
                not isinstance(values, tuple)
                or not values
                or len(values) != len(set(values))
                or any(not isinstance(item, str) or not item for item in values)
            ):
                raise ParthenonVisualRagError(
                    f"{field_name} must be a non-empty unique string tuple"
                )
            try:
                tuple(VisualClaimKind(item) for item in values)
            except ValueError as exc:
                raise ParthenonVisualRagError(
                    f"{field_name} contains an unsupported visual claim"
                ) from exc
        if set(self.supports) & set(self.cannot_support):
            raise ParthenonVisualRagError(
                "supports and cannot_support must not overlap"
            )
        if VisualClaimKind.EXACT_DIMENSION.value in self.supports:
            raise ParthenonVisualRagError(
                "this runner has no retained measurement basis for exact dimensions"
            )
        if self.review_state not in _ALLOWED_REVIEW_STATES:
            raise ParthenonVisualRagError("review_state must be selected or parked")

    @property
    def source_key(self) -> str:
        return f"{self.source_id}__{self.view_id}"


# Live source/candidate inventories are deliberately explicit and bounded.
# They come from the P085 review of YSMA's Parthenon pages; no crawler expands
# this set.  YSMA's site terms are not represented as a Creative Commons grant,
# so each usage note retains that limitation.  Tests use generated images and
# never call these URLs.
_YSMA_RIGHTS_NOTE = (
    "Rights boundary: YSMA all-rights-reserved terms; not treated as CC/open "
    "access. Retained here as bounded research evidence with attribution."
)

DEFAULT_SOURCE_SPECS: tuple[VisualImageSourceSpec, ...] = (
    VisualImageSourceSpec(
        source_id="ysma_11_1",
        view_id="korres_plan",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/11_1_PA_KatopsiKorres.jpg",
        source_family="official_heritage_authority_ysma",
        modality="measured_drawing",
        usage_note=(
            "YSMA caption: Plan of the monument by M. Korres. Use only for "
            "overall topology and relative placement of peristyle, cella, "
            "cross-wall, east-chamber pi colonnade, west-chamber four supports, "
            "and axial openings. Do not infer dimensions from pixels. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
    VisualImageSourceSpec(
        source_id="ysma_11_2",
        view_id="orlandos_east_elevation",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/11_2.jpg",
        source_family="official_heritage_authority_ysma",
        modality="reconstruction_drawing",
        usage_note=(
            "YSMA caption: A. Orlandos east view restoration drawing. Use as a "
            "reconstruction hypothesis for component hierarchy and relative "
            "position, not extant condition or sculpture completeness. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
    VisualImageSourceSpec(
        source_id="ysma_11_3",
        view_id="orlandos_west_elevation",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/11_3.jpg",
        source_family="official_heritage_authority_ysma",
        modality="reconstruction_drawing",
        usage_note=(
            "YSMA caption: A. Orlandos west view restoration drawing. Use as a "
            "reconstruction hypothesis for component hierarchy and relative "
            "position, not extant condition or sculpture completeness. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
    VisualImageSourceSpec(
        source_id="ysma_11_4",
        view_id="orlandos_interior_ne_cutaway_1948",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/11_4.jpg",
        source_family="official_heritage_authority_ysma",
        modality="reconstruction_drawing",
        usage_note=(
            "YSMA caption: A. Orlandos 1948 graphic representation viewed from "
            "the northeast. Use for candidate spatial/construction topology; it "
            "does not override the Korres measured plan. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
    VisualImageSourceSpec(
        source_id="ysma_11_6",
        view_id="pre_restoration_interior",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/11_6.jpg",
        source_family="official_heritage_authority_ysma",
        modality="current_photo",
        usage_note=(
            "YSMA identifies this only as before restoration works, without an "
            "exact date or orientation. Use for visible pre-restoration material "
            "condition; do not assign east/west identity or original-phase purity. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
    VisualImageSourceSpec(
        source_id="ysma_12_9",
        view_id="nw_view_after_potain_removal_2018",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/12_9.jpg",
        source_family="official_heritage_authority_ysma",
        modality="current_photo",
        usage_note=(
            "YSMA caption: view from northwest after removal of the Potain crane "
            "(2018). Use for that dated scaffold/restoration state, north peristyle, "
            "and fragmentary west end; not the current 2026 condition. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
    VisualImageSourceSpec(
        source_id="ysma_12_7",
        view_id="nw_metope_lowering_2007",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/12_7.jpg",
        source_family="official_heritage_authority_ysma",
        modality="current_photo",
        usage_note=(
            "YSMA caption: lowering an ancient metope at the west corner of the "
            "north side (2007). Use for visible Doric capital, architrave, "
            "triglyph-metope zone, and intervention state. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
    VisualImageSourceSpec(
        source_id="ysma_12_11",
        view_id="pronaos_north_columns_fluting_2016",
        url="https://www.ysma.gr/wp-content/uploads/2018/05/12_11.jpg",
        source_family="official_heritage_authority_ysma",
        modality="current_photo",
        usage_note=(
            "YSMA caption: two northern pronaos columns after cutting of flutes, "
            "view from west (2016). Use for visible new/ancient marble contrast, "
            "fluting, capitals, and architrave relation. "
            + _YSMA_RIGHTS_NOTE
        ),
        expected_media_type="image/jpeg",
    ),
)

_NO_DIMENSION = (VisualClaimKind.EXACT_DIMENSION.value,)
DEFAULT_CANDIDATE_SPECS: tuple[VisualCandidateSpec, ...] = (
    VisualCandidateSpec(
        candidate_id="korres_plan_overall",
        source_id="ysma_11_1",
        view_id="korres_plan",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="temple_plan",
        host_component="parthenon",
        spatial_location="overall",
        confidence=0.99,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.TOPOLOGY.value,
            VisualClaimKind.RELATIVE_POSITION.value,
        ),
        cannot_support=(
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
            VisualClaimKind.MATERIAL_CONDITION.value,
            *_NO_DIMENSION,
        ),
        review_state="selected",
    ),
    VisualCandidateSpec(
        candidate_id="orlandos_east_front_overall",
        source_id="ysma_11_2",
        view_id="orlandos_east_elevation",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="east_front",
        host_component="parthenon",
        spatial_location="east",
        confidence=0.86,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.TOPOLOGY.value,
            VisualClaimKind.RELATIVE_POSITION.value,
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
        ),
        cannot_support=(
            VisualClaimKind.MATERIAL_CONDITION.value,
            *_NO_DIMENSION,
        ),
        review_state="parked",
    ),
    VisualCandidateSpec(
        candidate_id="orlandos_west_front_overall",
        source_id="ysma_11_3",
        view_id="orlandos_west_elevation",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="west_front",
        host_component="parthenon",
        spatial_location="west",
        confidence=0.86,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.TOPOLOGY.value,
            VisualClaimKind.RELATIVE_POSITION.value,
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
        ),
        cannot_support=(
            VisualClaimKind.MATERIAL_CONDITION.value,
            *_NO_DIMENSION,
        ),
        review_state="parked",
    ),
    VisualCandidateSpec(
        candidate_id="orlandos_interior_cutaway_overall",
        source_id="ysma_11_4",
        view_id="orlandos_interior_ne_cutaway_1948",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="interior_cutaway",
        host_component="parthenon",
        spatial_location="view_from_northeast",
        confidence=0.78,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.TOPOLOGY.value,
            VisualClaimKind.RELATIVE_POSITION.value,
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
        ),
        cannot_support=(
            VisualClaimKind.MATERIAL_CONDITION.value,
            *_NO_DIMENSION,
        ),
        review_state="parked",
    ),
    VisualCandidateSpec(
        candidate_id="pre_restoration_interior_overall",
        source_id="ysma_11_6",
        view_id="pre_restoration_interior",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="interior_ruin",
        host_component="parthenon",
        spatial_location="orientation_unknown",
        confidence=0.82,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
            VisualClaimKind.MATERIAL_CONDITION.value,
        ),
        cannot_support=(
            VisualClaimKind.TOPOLOGY.value,
            VisualClaimKind.RELATIVE_POSITION.value,
            *_NO_DIMENSION,
        ),
        review_state="selected",
    ),
    VisualCandidateSpec(
        candidate_id="northwest_restoration_state_2018",
        source_id="ysma_12_9",
        view_id="nw_view_after_potain_removal_2018",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="west_and_north_exterior",
        host_component="parthenon",
        spatial_location="northwest",
        confidence=0.94,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.RELATIVE_POSITION.value,
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
            VisualClaimKind.MATERIAL_CONDITION.value,
        ),
        cannot_support=(VisualClaimKind.TOPOLOGY.value, *_NO_DIMENSION),
        review_state="selected",
    ),
    VisualCandidateSpec(
        candidate_id="northwest_metope_intervention_2007",
        source_id="ysma_12_7",
        view_id="nw_metope_lowering_2007",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="capital_entablature_and_metope",
        host_component="north_colonnade",
        spatial_location="west_corner",
        confidence=0.96,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.RELATIVE_POSITION.value,
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
            VisualClaimKind.MATERIAL_CONDITION.value,
        ),
        cannot_support=(VisualClaimKind.TOPOLOGY.value, *_NO_DIMENSION),
        review_state="selected",
    ),
    VisualCandidateSpec(
        candidate_id="pronaos_north_columns_2016",
        source_id="ysma_12_11",
        view_id="pronaos_north_columns_fluting_2016",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        element_tag="two_pronaos_columns",
        host_component="pronaos",
        spatial_location="northern_pair_view_from_west",
        confidence=0.96,
        supports=(
            VisualClaimKind.ELEMENT_EXISTENCE.value,
            VisualClaimKind.RELATIVE_POSITION.value,
            VisualClaimKind.VISIBLE_MORPHOLOGY.value,
            VisualClaimKind.MATERIAL_CONDITION.value,
        ),
        cannot_support=(VisualClaimKind.TOPOLOGY.value, *_NO_DIMENSION),
        review_state="selected",
    ),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_captured_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ParthenonVisualRagError(
            "captured_at must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ParthenonVisualRagError("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_ref(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
        "uri": ref.uri,
    }


def _artifact_ref(ref: ProjectArtifactRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "artifact_id": ref.artifact_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
        "uri": ref.uri,
    }


def _ordered_source_specs(
    values: tuple[VisualImageSourceSpec, ...],
) -> tuple[VisualImageSourceSpec, ...]:
    if (
        not isinstance(values, tuple)
        or not 1 <= len(values) <= MAX_SOURCE_COUNT
        or any(not isinstance(item, VisualImageSourceSpec) for item in values)
    ):
        raise ParthenonVisualRagError(
            f"source_specs must contain 1-{MAX_SOURCE_COUNT} typed sources"
        )
    ordered = tuple(sorted(values, key=lambda item: item.source_key))
    keys = tuple(item.source_key for item in ordered)
    if len(keys) != len(set(keys)):
        raise ParthenonVisualRagError("source_specs contains duplicate identities")
    source_ids = tuple(item.source_id for item in ordered)
    if len(source_ids) != len(set(source_ids)):
        raise ParthenonVisualRagError(
            "source_id values must be unique for visual manifest binding"
        )
    return ordered


def _ordered_candidate_specs(
    values: tuple[VisualCandidateSpec, ...],
    *,
    source_keys: set[str],
) -> tuple[VisualCandidateSpec, ...]:
    if (
        not isinstance(values, tuple)
        or not values
        or any(not isinstance(item, VisualCandidateSpec) for item in values)
    ):
        raise ParthenonVisualRagError(
            "candidate_specs must be a non-empty typed tuple"
        )
    ordered = tuple(sorted(values, key=lambda item: item.candidate_id))
    identities = tuple(item.candidate_id for item in ordered)
    if len(identities) != len(set(identities)):
        raise ParthenonVisualRagError(
            "candidate_specs contains duplicate identities"
        )
    absent = tuple(
        item.candidate_id for item in ordered if item.source_key not in source_keys
    )
    if absent:
        raise ParthenonVisualRagError(
            "candidate sources are absent: " + ", ".join(absent)
        )
    return ordered


def _inspect_image(data: bytes) -> tuple[str, str, int, int]:
    if not isinstance(data, bytes) or not data:
        raise ParthenonVisualRagError("image payload must be non-empty bytes")
    if len(data) > MAX_IMAGE_BYTES:
        raise ParthenonVisualRagError(
            f"image exceeds the {MAX_IMAGE_BYTES}-byte source bound"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as exc:
        raise ParthenonVisualRagError("image payload is invalid") from exc
    if image_format not in _MEDIA_FORMATS:
        raise ParthenonVisualRagError("image format is unsupported")
    if (
        width <= 0
        or height <= 0
        or width > MAX_PIXEL_AXIS
        or height > MAX_PIXEL_AXIS
        or width * height > MAX_PIXEL_COUNT
    ):
        raise ParthenonVisualRagError("image dimensions exceed the pixel bound")
    media_type, extension = _MEDIA_FORMATS[image_format]
    return media_type, extension, width, height


def _download_source(
    spec: VisualImageSourceSpec,
    *,
    timeout_seconds: float,
) -> bytes:
    request = urllib.request.Request(
        spec.url,
        headers={"User-Agent": "ArchFlow-V4-visual-evidence/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response_url = response.geturl()
        final_url = urllib.parse.urlparse(response_url)
        if final_url.scheme != "https" or not final_url.netloc:
            raise ParthenonVisualRagError(
                "visual source redirected outside HTTPS"
            )
        if response_url != spec.url:
            raise ParthenonVisualRagError(
                "visual source redirected away from its reviewed URL"
            )
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError as exc:
                raise ParthenonVisualRagError(
                    "source Content-Length is malformed"
                ) from exc
            if declared > MAX_IMAGE_BYTES:
                raise ParthenonVisualRagError("declared image size exceeds bound")
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ParthenonVisualRagError("downloaded image size exceeds bound")
    return data


def _workspace_source_path(
    repository: FilesystemProjectRepository,
    run: RunRef,
    *,
    source_id: str,
    view_id: str,
    extension: str,
) -> Path:
    source_root = (
        repository.layout.run(run.run_id).workspaces
        / "visual-rag"
        / "source-images"
    ).resolve(strict=False)
    target = (source_root / f"{source_id}__{view_id}.{extension}").resolve(
        strict=False
    )
    if target.parent != source_root:
        raise ParthenonVisualRagError("source image escaped visual-rag workspace")
    return target


def _write_workspace_original(path: Path, data: bytes) -> None:
    """Write once inside the already-derived speculative run workspace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        existing = path.read_bytes()
        if existing != data:
            raise FileExistsError(
                f"workspace source already exists with different bytes: {path.name}"
            )


def _pixel_bbox(
    normalized: tuple[float, float, float, float],
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = normalized
    left = min(width - 1, max(0, math.floor(x0 * width)))
    top = min(height - 1, max(0, math.floor(y0 * height)))
    right = min(width, max(left + 1, math.ceil(x1 * width)))
    bottom = min(height, max(top + 1, math.ceil(y1 * height)))
    return left, top, right, bottom


def _make_visual_source_adapter(
    spec: VisualImageSourceSpec,
    *,
    captured_at: str,
    media_type: str,
    width: int,
    height: int,
    content_sha256: str,
    artifact_ref: ProjectArtifactRef,
    scope_ref: str,
) -> VisualSource:
    """Isolate the reusable visual-evidence constructor from project I/O."""

    return VisualSource(
        source_id=spec.source_id,
        view_id=spec.view_id,
        url=spec.url,
        retrieved_at=captured_at,
        media_type=media_type,
        pixel_width=width,
        pixel_height=height,
        content_sha256=content_sha256,
        artifact_ref=artifact_ref.uri,
        source_family=spec.source_family,
        modality=VisualSourceModality(spec.modality),
        usage_note=spec.usage_note,
        branch_id=BRANCH_ID,
        scope_ref=scope_ref,
    )


def _make_visual_candidate_adapter(
    spec: VisualCandidateSpec,
    *,
    source: VisualSource,
) -> VisualRegionCandidate:
    """Isolate candidate-field adaptation to the generic contract."""

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
        supports=tuple(VisualClaimKind(value) for value in spec.supports),
        cannot_support=tuple(
            VisualClaimKind(value) for value in spec.cannot_support
        ),
        review_state=VisualReviewState(spec.review_state),
        measurement_basis=None,
    )


def _compile_visual_manifest_adapter(
    *,
    sources: tuple[VisualSource, ...],
    candidates: tuple[VisualRegionCandidate, ...],
):
    """Keep the generic manifest API change surface in one helper."""

    policy = VisualEvidenceManifestPolicy(
        policy_id="parthenon-stage4-visual-rag",
        branch_id=BRANCH_ID,
        scope_ref=sources[0].scope_ref,
        allowed_modalities=tuple(VisualSourceModality),
        normalized_bbox_tolerance=1e-6,
        minimum_selected_candidates=1,
    )
    return compile_visual_evidence_manifest(
        policy,
        sources=sources,
        candidates=candidates,
    )


def _manifest_partitions(
    payload: Mapping[str, object],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    selected = payload.get("selected_candidate_ids")
    parked = payload.get("parked_candidate_ids")
    if (
        not isinstance(selected, list)
        or not isinstance(parked, list)
        or any(not isinstance(item, str) for item in (*selected, *parked))
    ):
        raise RuntimeError("visual manifest candidate partitions drifted")
    return tuple(selected), tuple(parked)


def _ensure_reconstruction_unopened(
    repository: FilesystemProjectRepository,
) -> None:
    forbidden = repository.layout.run(FORBIDDEN_RECONSTRUCTION_RUN_ID).root
    if forbidden.exists():
        raise FileExistsError(
            f"{FORBIDDEN_RECONSTRUCTION_RUN_ID} must remain unopened during P085"
        )


def _open_or_create_research_run(
    repository: FilesystemProjectRepository,
) -> tuple[RunRef, str]:
    manifest = repository.layout.run(RESEARCH_RUN_ID).manifest
    if manifest.exists():
        return repository.load_run(RESEARCH_RUN_ID), "resumed"
    return repository.create_run(RESEARCH_RUN_ID), "created"


def run_project(
    root: Path,
    *,
    captured_at: str,
    source_specs: tuple[VisualImageSourceSpec, ...] = DEFAULT_SOURCE_SPECS,
    candidate_specs: tuple[VisualCandidateSpec, ...] = DEFAULT_CANDIDATE_SPECS,
    offline_images: Mapping[str, bytes] | None = None,
    scope_ref: str = SCOPE_REF,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Compile one bounded Stage 4 visual manifest in ``research-005`` only."""

    root = Path(root).resolve(strict=False)
    captured_at = _normalize_captured_at(captured_at)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0.0
    ):
        raise ParthenonVisualRagError("timeout_seconds must be positive")
    if not isinstance(scope_ref, str) or not scope_ref.strip():
        raise ParthenonVisualRagError("scope_ref must be non-empty text")
    sources_ordered = _ordered_source_specs(source_specs)
    candidates_ordered = _ordered_candidate_specs(
        candidate_specs,
        source_keys={item.source_key for item in sources_ordered},
    )
    if offline_images is not None:
        if not isinstance(offline_images, Mapping):
            raise TypeError("offline_images must be a mapping or None")
        expected = {item.source_key for item in sources_ordered}
        if set(offline_images) != expected or any(
            not isinstance(value, bytes) for value in offline_images.values()
        ):
            raise ParthenonVisualRagError(
                "offline_images must exactly cover source identities with bytes"
            )

    repository = FilesystemProjectRepository.open(root)
    if repository.load_manifest().project_id != PROJECT_ID:
        raise ParthenonVisualRagError(
            "this project-local runner cannot author another project"
        )
    _ensure_reconstruction_unopened(repository)
    run, resume_mode = _open_or_create_research_run(repository)
    branch_destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=run.run_id,
        branch_id=BRANCH_ID,
    )
    record_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )

    visual_sources: list[VisualSource] = []
    source_rows: list[dict[str, object]] = []
    total_bytes = 0
    for spec in sources_ordered:
        data = (
            _download_source(spec, timeout_seconds=float(timeout_seconds))
            if offline_images is None
            else offline_images[spec.source_key]
        )
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_DOWNLOAD_BYTES:
            raise ParthenonVisualRagError("visual source set exceeds total byte bound")
        media_type, extension, width, height = _inspect_image(data)
        if media_type != spec.expected_media_type:
            raise ParthenonVisualRagError(
                f"{spec.source_key}: detected media type differs from declaration"
            )
        source_path = _workspace_source_path(
            repository,
            run,
            source_id=spec.source_id,
            view_id=spec.view_id,
            extension=extension,
        )
        _write_workspace_original(source_path, data)
        artifact = repository.ingest(
            run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id=f"visual-source-{_sha256(spec.source_key.encode())[:20]}",
            media_type=media_type,
            source=io.BytesIO(data),
        )
        source = _make_visual_source_adapter(
            spec,
            captured_at=captured_at,
            media_type=media_type,
            width=width,
            height=height,
            content_sha256=_sha256(data),
            artifact_ref=artifact,
            scope_ref=scope_ref,
        )
        visual_sources.append(source)
        source_rows.append(
            {
                "source": source,
                "artifact": artifact,
                "workspace_relative_path": source_path.relative_to(root).as_posix(),
                "filename": source_path.name,
            }
        )

    sources_by_key = {
        f"{item.source_id}__{item.view_id}": item for item in visual_sources
    }
    visual_candidates = tuple(
        _make_visual_candidate_adapter(
            spec,
            source=sources_by_key[spec.source_key],
        )
        for spec in candidates_ordered
    )
    manifest = _compile_visual_manifest_adapter(
        sources=tuple(visual_sources),
        candidates=visual_candidates,
    )
    manifest_contract = manifest.to_dict()
    selected_ids, parked_ids = _manifest_partitions(manifest_contract)

    source_refs: list[ProjectRecordRef] = []
    for row in source_rows:
        source = row["source"]
        artifact = row["artifact"]
        assert isinstance(source, VisualSource)
        assert isinstance(artifact, ProjectArtifactRef)
        source_refs.append(
            repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"visual-source-{canonical_digest(source.to_dict())[:20]}",
                payload={
                    "schema": "ParthenonVisualSourceRecord@1",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "scope_ref": scope_ref,
                    "source": source.to_dict(),
                    "workspace_relative_path": row["workspace_relative_path"],
                    "filename": row["filename"],
                    "artifact_ref": _artifact_ref(artifact),
                    "retrieval_mode": (
                        "network" if offline_images is None else "offline-fixture"
                    ),
                    "canonical_write_authority": False,
                },
            )
        )

    candidate_refs = tuple(
        repository.put_json(
            run=run,
            destination=branch_destination,
            record_kind=f"visual-candidate-{canonical_digest(item.to_dict())[:20]}",
            payload={
                "schema": "ParthenonVisualCandidateRecord@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "branch_id": BRANCH_ID,
                "scope_ref": scope_ref,
                "candidate": item.to_dict(),
                "canonical_write_authority": False,
            },
        )
        for item in visual_candidates
    )
    manifest_ref = repository.put_json(
        run=run,
        destination=branch_destination,
        record_kind="visual-candidate-manifest",
        payload={
            "schema": "ParthenonVisualCandidateSelection@1",
            "project_id": PROJECT_ID,
            "run_id": run.run_id,
            "branch_id": BRANCH_ID,
            "scope_ref": scope_ref,
            "manifest": manifest_contract,
            "source_record_refs": [_record_ref(item) for item in source_refs],
            "candidate_record_refs": [
                _record_ref(item) for item in candidate_refs
            ],
            "selected_candidate_ids": list(selected_ids),
            "parked_candidate_ids": list(parked_ids),
            "reconstruction_005_created": False,
            "canonical_write_authority": False,
        },
    )
    progress_ref = repository.put_json(
        run=run,
        destination=record_destination,
        record_kind="stage4-visual-rag-progress",
        payload={
            "schema": "ParthenonStage4VisualRagProgress@1",
            "project_id": PROJECT_ID,
            "run_id": run.run_id,
            "branch_id": BRANCH_ID,
            "scope_ref": scope_ref,
            "captured_at": captured_at,
            "resume_mode": resume_mode,
            "workspace_relative_path": (
                f"runs/{RESEARCH_RUN_ID}/workspaces/visual-rag"
            ),
            "source_count": len(source_refs),
            "candidate_count": len(candidate_refs),
            "selected_candidate_count": len(selected_ids),
            "parked_candidate_count": len(parked_ids),
            "downloaded_bytes": total_bytes,
            "network_calls": 0 if offline_images is not None else len(source_refs),
            "source_record_refs": [_record_ref(item) for item in source_refs],
            "candidate_record_refs": [
                _record_ref(item) for item in candidate_refs
            ],
            "manifest_ref": _record_ref(manifest_ref),
            "disposition": "HOLD",
            "reconstruction_005_created": False,
            "canonical_write_authority": False,
        },
    )
    _ensure_reconstruction_unopened(repository)
    repository.verify()
    return {
        "root": root,
        "run": run,
        "resume_mode": resume_mode,
        "source_refs": tuple(source_refs),
        "candidate_refs": candidate_refs,
        "manifest_ref": manifest_ref,
        "progress_ref": progress_ref,
        "selected_candidate_ids": selected_ids,
        "parked_candidate_ids": parked_ids,
        "workspace_source_paths": tuple(
            root / str(row["workspace_relative_path"]) for row in source_rows
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captured-at")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    args = parser.parse_args(argv)
    captured_at = args.captured_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    root = resolve_probe_root(PROJECT_ID)
    result = run_project(
        root,
        captured_at=captured_at,
        timeout_seconds=args.timeout_seconds,
    )
    print("project:", root)
    print("research run:", result["run"].run_id)
    print("sources:", len(result["source_refs"]))
    print("selected:", list(result["selected_candidate_ids"]))
    print("parked:", list(result["parked_candidate_ids"]))
    print("progress:", result["progress_ref"].uri)
    print("reconstruction-005: not created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
