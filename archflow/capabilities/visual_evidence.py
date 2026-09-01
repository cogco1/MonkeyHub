"""Compatibility facade for visual-evidence contracts.

The canonical implementation lives in ``archflow.evidence.visual``.  This
module intentionally owns no implementation so existing imports preserve
class and function identity.
"""

from archflow.evidence.visual import (
    VisualClaimKind,
    VisualEvidenceError,
    VisualEvidenceManifest,
    VisualEvidenceManifestPolicy,
    VisualMeasurementBasis,
    VisualMeasurementBasisKind,
    VisualRegionCandidate,
    VisualRegionManifestBinding,
    VisualReviewState,
    VisualSource,
    VisualSourceModality,
    compile_visual_evidence_manifest,
)

__all__ = [
    "VisualClaimKind",
    "VisualEvidenceError",
    "VisualEvidenceManifest",
    "VisualEvidenceManifestPolicy",
    "VisualMeasurementBasis",
    "VisualMeasurementBasisKind",
    "VisualRegionCandidate",
    "VisualRegionManifestBinding",
    "VisualReviewState",
    "VisualSource",
    "VisualSourceModality",
    "compile_visual_evidence_manifest",
]
