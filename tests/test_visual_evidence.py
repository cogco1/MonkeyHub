from __future__ import annotations

import unittest

from archflow.capabilities import visual_evidence as legacy_visual_evidence
from archflow.evidence import visual as canonical_visual_evidence
from archflow.evidence.visual import (
    VisualClaimKind,
    VisualEvidenceError,
    VisualEvidenceManifestPolicy,
    VisualMeasurementBasis,
    VisualMeasurementBasisKind,
    VisualRegionCandidate,
    VisualReviewState,
    VisualSource,
    VisualSourceModality,
    compile_visual_evidence_manifest,
)


BRANCH = "historical-detail"
SCOPE = "project://example/runs/research/branches/historical/scope"


def source(
    source_id: str,
    *,
    modality: VisualSourceModality = VisualSourceModality.CURRENT_PHOTO,
    width: int = 1_000,
    height: int = 500,
    usage_note: str = "Visible condition only; retain source modality limits.",
) -> VisualSource:
    return VisualSource(
        source_id=source_id,
        view_id="view-001",
        url=f"https://example.invalid/{source_id}.jpg",
        retrieved_at="2026-08-29T12:00:00Z",
        media_type="image/jpeg",
        pixel_width=width,
        pixel_height=height,
        content_sha256=("a" if source_id == "source-a" else "b") * 64,
        artifact_ref=f"project://example/objects/sha256/{source_id}",
        source_family="example-source-family",
        modality=modality,
        usage_note=usage_note,
        branch_id=BRANCH,
        scope_ref=SCOPE,
    )


def candidate(
    candidate_id: str,
    source_id: str,
    *,
    pixel_bbox: tuple[int, int, int, int] = (100, 50, 400, 250),
    normalized_bbox: tuple[float, float, float, float] = (0.1, 0.1, 0.4, 0.5),
    supports: tuple[VisualClaimKind, ...] = (
        VisualClaimKind.ELEMENT_EXISTENCE,
        VisualClaimKind.VISIBLE_MORPHOLOGY,
    ),
    cannot_support: tuple[VisualClaimKind, ...] = (
        VisualClaimKind.EXACT_DIMENSION,
    ),
    review_state: VisualReviewState = VisualReviewState.PARKED,
    measurement_basis: VisualMeasurementBasis | None = None,
) -> VisualRegionCandidate:
    return VisualRegionCandidate(
        candidate_id=candidate_id,
        source_id=source_id,
        pixel_bbox=pixel_bbox,
        normalized_bbox=normalized_bbox,
        element_tag="architectural-element",
        host_component="host-component",
        spatial_location="At the visible edge of the host component.",
        confidence=0.82,
        supports=supports,
        cannot_support=cannot_support,
        review_state=review_state,
        measurement_basis=measurement_basis,
    )


def policy() -> VisualEvidenceManifestPolicy:
    return VisualEvidenceManifestPolicy(
        policy_id="visual-stage-policy",
        branch_id=BRANCH,
        scope_ref=SCOPE,
        minimum_selected_candidates=1,
    )


class VisualEvidenceManifestTests(unittest.TestCase):
    def test_legacy_import_is_thin_canonical_facade(self) -> None:
        for name in legacy_visual_evidence.__all__:
            self.assertIs(
                getattr(legacy_visual_evidence, name),
                getattr(canonical_visual_evidence, name),
                name,
            )

    def test_deterministic_manifest_preserves_modality_limits_after_selection(
        self,
    ) -> None:
        reconstruction_usage = (
            "Interpretive reconstruction; no exact dimensions without an "
            "independent measurement basis."
        )
        measured = source(
            "source-a",
            modality=VisualSourceModality.MEASURED_DRAWING,
        )
        reconstruction = source(
            "source-b",
            modality=VisualSourceModality.RECONSTRUCTION_DRAWING,
            usage_note=reconstruction_usage,
        )
        selected = candidate(
            "candidate-selected",
            "source-b",
            review_state=VisualReviewState.SELECTED,
            supports=(
                VisualClaimKind.ELEMENT_EXISTENCE,
                VisualClaimKind.TOPOLOGY,
            ),
        )
        measured_basis = VisualMeasurementBasis(
            basis_id="retained-scale-001",
            kind=VisualMeasurementBasisKind.RETAINED_SCALE,
            evidence_refs=("project:evidence/drawing-scale",),
            statement="The retained drawing carries an explicit scale.",
        )
        parked = candidate(
            "candidate-parked",
            "source-a",
            review_state=VisualReviewState.PARKED,
            supports=(VisualClaimKind.EXACT_DIMENSION,),
            cannot_support=(VisualClaimKind.MATERIAL_CONDITION,),
            measurement_basis=measured_basis,
        )

        first = compile_visual_evidence_manifest(
            policy(),
            sources=(reconstruction, measured),
            candidates=(selected, parked),
        )
        second = compile_visual_evidence_manifest(
            policy(),
            sources=(measured, reconstruction),
            candidates=(parked, selected),
        )

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.manifest_digest, second.manifest_digest)
        self.assertEqual(("candidate-selected",), first.selected_candidate_ids)
        self.assertEqual(("candidate-parked",), first.parked_candidate_ids)
        selected_binding = next(
            item
            for item in first.candidate_bindings
            if item.candidate_id == "candidate-selected"
        )
        self.assertIs(
            selected_binding.source_modality,
            VisualSourceModality.RECONSTRUCTION_DRAWING,
        )
        self.assertEqual(reconstruction_usage, selected_binding.source_usage_note)
        self.assertEqual(
            ["exact_dimension"],
            selected_binding.to_dict()["candidate"]["cannot_support"],
        )
        self.assertTrue(first.to_dict()["source_modality_limits_preserved"])
        self.assertFalse(first.to_dict()["geometry_mutation_authority"])
        self.assertEqual(
            "43ee0e2b53f4ca3469a59379f23d77795317eff7b53249d888ca5e27e5e6f979",
            first.policy.policy_digest,
        )
        self.assertEqual(
            "ef18b0942478b6a88d4fc04acaa3dfb95cdb942eefeee6c180445767f9463e5e",
            first.manifest_digest,
        )

    def test_bbox_outside_source_fails_closed(self) -> None:
        small_source = source("source-a", width=100, height=100)
        outside = candidate(
            "outside-region",
            "source-a",
            pixel_bbox=(0, 0, 101, 20),
            normalized_bbox=(0.0, 0.0, 1.0, 0.2),
            review_state=VisualReviewState.SELECTED,
        )

        with self.assertRaisesRegex(VisualEvidenceError, "outside source"):
            compile_visual_evidence_manifest(
                policy(),
                sources=(small_source,),
                candidates=(outside,),
            )

    def test_missing_source_and_duplicate_id_fail_closed(self) -> None:
        retained = source("source-a")
        missing = candidate(
            "missing-source-region",
            "source-b",
            review_state=VisualReviewState.SELECTED,
        )
        with self.assertRaisesRegex(VisualEvidenceError, "missing source"):
            compile_visual_evidence_manifest(
                policy(),
                sources=(retained,),
                candidates=(missing,),
            )

        selected = candidate(
            "duplicate-region",
            "source-a",
            review_state=VisualReviewState.SELECTED,
        )
        parked = candidate(
            "duplicate-region",
            "source-a",
            review_state=VisualReviewState.PARKED,
        )
        with self.assertRaisesRegex(VisualEvidenceError, "duplicate candidate_id"):
            compile_visual_evidence_manifest(
                policy(),
                sources=(retained,),
                candidates=(selected, parked),
            )
        with self.assertRaisesRegex(VisualEvidenceError, "duplicate source_id"):
            compile_visual_evidence_manifest(
                policy(),
                sources=(retained, retained),
                candidates=(selected,),
            )

    def test_empty_cannot_support_fails_closed(self) -> None:
        with self.assertRaisesRegex(VisualEvidenceError, "cannot_support"):
            candidate(
                "no-nonclaims",
                "source-a",
                cannot_support=(),
                review_state=VisualReviewState.SELECTED,
            )

    def test_exact_dimension_without_measurement_basis_fails_closed(self) -> None:
        retained = source(
            "source-a",
            modality=VisualSourceModality.RECONSTRUCTION_DRAWING,
        )
        unsupported_exact = candidate(
            "unsupported-exact",
            "source-a",
            supports=(VisualClaimKind.EXACT_DIMENSION,),
            cannot_support=(VisualClaimKind.MATERIAL_CONDITION,),
            review_state=VisualReviewState.SELECTED,
            measurement_basis=None,
        )

        with self.assertRaisesRegex(VisualEvidenceError, "measurement basis"):
            compile_visual_evidence_manifest(
                policy(),
                sources=(retained,),
                candidates=(unsupported_exact,),
            )

    def test_normalized_bbox_must_match_source_pixels(self) -> None:
        retained = source("source-a")
        inconsistent = candidate(
            "inconsistent-bbox",
            "source-a",
            normalized_bbox=(0.2, 0.1, 0.4, 0.5),
            review_state=VisualReviewState.SELECTED,
        )

        with self.assertRaisesRegex(VisualEvidenceError, "normalized bbox"):
            compile_visual_evidence_manifest(
                policy(),
                sources=(retained,),
                candidates=(inconsistent,),
            )


if __name__ == "__main__":
    unittest.main()
