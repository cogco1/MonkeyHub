from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
import unittest

from archflow.capabilities.visual_inventory import (
    AcceptedComponentIdentityRef,
    ComponentHypothesis,
    CoverageOutcome,
    EquivalenceResolution,
    ImageDuplicateAssessment,
    ImageDuplicateKind,
    PerceptualHash,
    PhysicalComponentEquivalence,
    PhysicalComponentIdentity,
    PixelRegion,
    ProposedMachineLabel,
    ROICoverageEntry,
    ROISelection,
    SourceDerivationKind,
    SourceImageEvidence,
    UnknownComponentQuestion,
    VisualComponentObservation,
    VisualEvidenceAspect,
    VisualEvidenceInventoryReceipt,
    VisualInventoryError,
    VisualInventoryStatus,
    VisualSourceDisposition,
    VisualSourceDispositionKind,
    compile_visual_evidence_inventory,
)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def disposition(
    kind: VisualSourceDispositionKind = VisualSourceDispositionKind.VISUAL_SOURCES,
) -> VisualSourceDisposition:
    return VisualSourceDisposition(
        kind=kind,
        source_refs=("evidence:intake",),
        authority_refs=("authority:architect",),
    )


def image(image_id: str, value: str, phash: str) -> SourceImageEvidence:
    return SourceImageEvidence(
        image_id=image_id,
        exact_sha256=digest(value),
        width_px=1200,
        height_px=800,
        derivation_kind=SourceDerivationKind.ORIGINAL,
        derivation_refs=(),
        perceptual_hash=PerceptualHash(
            algorithm="phash-v1",
            bit_length=64,
            hex_value=phash,
        ),
    )


def roi(roi_id: str, image_id: str) -> PixelRegion:
    return PixelRegion(
        roi_id=roi_id,
        image_id=image_id,
        source_width_px=1200,
        source_height_px=800,
        x_px=100,
        y_px=100,
        width_px=200,
        height_px=300,
        selection=ROISelection.SELECTED,
    )


def observation(observation_id: str, roi_id: str) -> VisualComponentObservation:
    return VisualComponentObservation(
        observation_id=observation_id,
        roi_id=roi_id,
        proposed_component_kind="facade-window",
        evidence_aspects=(
            VisualEvidenceAspect.EXISTENCE,
            VisualEvidenceAspect.RELATIVE_POSITION,
        ),
        statement="A vertically proportioned opening is visible in the bay.",
        source_refs=(f"roi:{roi_id}",),
        machine_labels=(ProposedMachineLabel("window", 0.92),),
    )


def full_receipt() -> VisualEvidenceInventoryReceipt:
    images = (
        image("image-east", "east", "0123456789abcdef"),
        image("image-oblique", "oblique", "0123456789abcdee"),
    )
    rois = (roi("roi-east", "image-east"), roi("roi-oblique", "image-oblique"))
    observations = (
        observation("observation-east", "roi-east"),
        observation("observation-oblique", "roi-oblique"),
    )
    identity = PhysicalComponentIdentity(
        facade="east",
        level="piano-nobile",
        bay="bay-03",
        zone="main-block",
    )
    equivalence = PhysicalComponentEquivalence(
        equivalence_id="equivalence-window-03",
        observation_ids=("observation-east", "observation-oblique"),
        resolution=EquivalenceResolution.CONFIRMED_IDENTITY,
        evidence_refs=("evidence:axis-registration",),
        identity=identity,
    )
    hypothesis = ComponentHypothesis(
        hypothesis_id="hypothesis-window-03",
        observation_ids=("observation-east", "observation-oblique"),
        proposed_component_kind="facade-window",
        source_refs=("evidence:cross-view-window-03",),
        identity=identity,
        equivalence_ref="equivalence-window-03",
    )
    accepted = AcceptedComponentIdentityRef(
        hypothesis_id="hypothesis-window-03",
        proposal_component_id="component-window-03",
        component_identity_ref="component-identity:window-03",
        acceptance_ref="decision:accept-window-03",
        source_refs=("evidence:architect-review",),
    )
    return compile_visual_evidence_inventory(
        source_disposition=disposition(),
        source_images=images,
        duplicate_assessments=(
            ImageDuplicateAssessment(
                assessment_id="duplicate-east-oblique",
                image_ids=("image-east", "image-oblique"),
                kind=ImageDuplicateKind.NEAR_DUPLICATE,
                evidence_refs=("evidence:phash-run",),
                hamming_distance=1,
                near_duplicate_threshold=8,
            ),
        ),
        rois=rois,
        observations=observations,
        equivalences=(equivalence,),
        component_hypotheses=(hypothesis,),
        accepted_component_identity_refs=(accepted,),
        visual_origin_proposal_component_ids=("component-window-03",),
        coverage_entries=(
            ROICoverageEntry(
                roi_id="roi-east",
                outcome=CoverageOutcome.COMPONENT_HYPOTHESIS,
                target_ref="hypothesis-window-03",
            ),
            ROICoverageEntry(
                roi_id="roi-oblique",
                outcome=CoverageOutcome.COMPONENT_HYPOTHESIS,
                target_ref="hypothesis-window-03",
            ),
        ),
    )


class VisualInventoryContractTests(unittest.TestCase):
    def test_full_cross_view_inventory_passes_and_roundtrips(self) -> None:
        receipt = full_receipt()

        self.assertIs(receipt.status, VisualInventoryStatus.PASS)
        self.assertEqual(64, len(receipt.inventory_digest))
        payload = receipt.to_dict()
        restored = VisualEvidenceInventoryReceipt.from_dict(payload)
        self.assertEqual(receipt, restored)
        self.assertEqual(receipt.inventory_digest, restored.inventory_digest)
        self.assertFalse(payload["machine_design_authority"])
        self.assertFalse(payload["automatic_physical_merge_authority"])

    def test_contracts_are_immutable(self) -> None:
        source = image("image-a", "a", "0123456789abcdef")
        with self.assertRaises(FrozenInstanceError):
            source.width_px = 1  # type: ignore[misc]

    def test_perceptual_hash_is_bounded_and_lowercase(self) -> None:
        with self.assertRaisesRegex(VisualInventoryError, r"inside \[4, 256\]"):
            PerceptualHash("phash", 512, "0" * 128)
        with self.assertRaisesRegex(VisualInventoryError, "lowercase hexadecimal"):
            PerceptualHash("phash", 8, "AA")

    def test_derived_source_requires_lineage_and_exact_sha(self) -> None:
        with self.assertRaisesRegex(VisualInventoryError, "derivation_refs"):
            SourceImageEvidence(
                image_id="crop",
                exact_sha256=digest("crop"),
                width_px=10,
                height_px=10,
                derivation_kind=SourceDerivationKind.DERIVED,
                derivation_refs=(),
            )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            SourceImageEvidence(
                image_id="bad",
                exact_sha256="not-a-sha",
                width_px=10,
                height_px=10,
                derivation_kind=SourceDerivationKind.ORIGINAL,
                derivation_refs=(),
            )

    def test_roi_is_pixel_bounded_and_must_match_source_dimensions(self) -> None:
        with self.assertRaisesRegex(VisualInventoryError, "remain inside"):
            PixelRegion(
                roi_id="outside",
                image_id="image-a",
                source_width_px=100,
                source_height_px=100,
                x_px=90,
                y_px=0,
                width_px=20,
                height_px=10,
                selection=ROISelection.SELECTED,
            )
        source = image("image-a", "a", "0123456789abcdef")
        mismatched = PixelRegion(
            roi_id="roi-a",
            image_id="image-a",
            source_width_px=600,
            source_height_px=400,
            x_px=0,
            y_px=0,
            width_px=10,
            height_px=10,
            selection=ROISelection.SELECTED,
        )
        with self.assertRaisesRegex(VisualInventoryError, "dimensions disagree"):
            compile_visual_evidence_inventory(
                source_disposition=disposition(),
                source_images=(source,),
                rois=(mismatched,),
            )

    def test_observation_rejects_exact_dimension_schema_injection(self) -> None:
        payload = observation("observation-a", "roi-a").to_dict()
        payload["exact_dimension"] = {"width": 1.2, "unit": "m"}
        with self.assertRaisesRegex(VisualInventoryError, "fields drifted"):
            VisualComponentObservation.from_dict(payload)
        self.assertEqual(
            {
                "existence",
                "morphology",
                "topology",
                "relative_position",
            },
            {item.value for item in VisualEvidenceAspect},
        )

    def test_exact_and_near_image_duplicates_obey_distinct_rules(self) -> None:
        left = image("image-a", "same", "0123456789abcdef")
        right = image("image-b", "different", "0123456789abcdee")
        exact = ImageDuplicateAssessment(
            assessment_id="assessment-exact",
            image_ids=("image-a", "image-b"),
            kind=ImageDuplicateKind.EXACT_DUPLICATE,
            evidence_refs=("evidence:sha",),
        )
        with self.assertRaisesRegex(VisualInventoryError, "equal SHA-256"):
            compile_visual_evidence_inventory(
                source_disposition=disposition(),
                source_images=(left, right),
                duplicate_assessments=(exact,),
                rois=(roi("roi-a", "image-a"),),
            )

        same_left = image("image-a", "same", "0123456789abcdef")
        same_right = image("image-b", "same", "0123456789abcdef")
        with self.assertRaisesRegex(VisualInventoryError, "exhaustively assessed"):
            compile_visual_evidence_inventory(
                source_disposition=disposition(),
                source_images=(same_left, same_right),
                rois=(roi("roi-a", "image-a"),),
            )

        wrong_distance = ImageDuplicateAssessment(
            assessment_id="assessment-near",
            image_ids=("image-a", "image-b"),
            kind=ImageDuplicateKind.NEAR_DUPLICATE,
            evidence_refs=("evidence:phash",),
            hamming_distance=2,
            near_duplicate_threshold=8,
        )
        with self.assertRaisesRegex(VisualInventoryError, "distance disagrees"):
            compile_visual_evidence_inventory(
                source_disposition=disposition(),
                source_images=(left, right),
                duplicate_assessments=(wrong_distance,),
                rois=(roi("roi-a", "image-a"),),
            )

    def test_cross_view_similarity_cannot_merge_without_confirmed_identity(self) -> None:
        images = (
            image("image-a", "a", "0123456789abcdef"),
            image("image-b", "b", "0123456789abcdee"),
        )
        rois = (roi("roi-a", "image-a"), roi("roi-b", "image-b"))
        observations = (
            observation("observation-a", "roi-a"),
            observation("observation-b", "roi-b"),
        )
        hypothesis = ComponentHypothesis(
            hypothesis_id="hypothesis-a",
            observation_ids=("observation-a", "observation-b"),
            proposed_component_kind="facade-window",
            source_refs=("evidence:visual-similarity",),
        )
        with self.assertRaisesRegex(VisualInventoryError, "requires equivalence_ref"):
            compile_visual_evidence_inventory(
                source_disposition=disposition(),
                source_images=images,
                rois=rois,
                observations=observations,
                component_hypotheses=(hypothesis,),
            )

        unresolved = PhysicalComponentEquivalence(
            equivalence_id="equivalence-a",
            observation_ids=("observation-a", "observation-b"),
            resolution=EquivalenceResolution.UNRESOLVED,
            evidence_refs=("evidence:visual-similarity",),
            unresolved_question="Are these views of the same physical window?",
        )
        hypothesis = ComponentHypothesis(
            hypothesis_id="hypothesis-a",
            observation_ids=("observation-a", "observation-b"),
            proposed_component_kind="facade-window",
            source_refs=("evidence:visual-similarity",),
            equivalence_ref="equivalence-a",
        )
        with self.assertRaisesRegex(VisualInventoryError, "cannot merge"):
            compile_visual_evidence_inventory(
                source_disposition=disposition(),
                source_images=images,
                rois=rois,
                observations=observations,
                equivalences=(unresolved,),
                component_hypotheses=(hypothesis,),
            )

    def test_selected_roi_can_resolve_to_evidence_bound_unknown_question(self) -> None:
        question = UnknownComponentQuestion(
            question_id="question-a",
            roi_id="roi-a",
            question="Is the dark region an opening or surface staining?",
            evidence_refs=("roi:roi-a",),
        )
        receipt = compile_visual_evidence_inventory(
            source_disposition=disposition(),
            source_images=(image("image-a", "a", "0123456789abcdef"),),
            rois=(roi("roi-a", "image-a"),),
            unknown_questions=(question,),
            coverage_entries=(
                ROICoverageEntry(
                    roi_id="roi-a",
                    outcome=CoverageOutcome.UNKNOWN_QUESTION,
                    target_ref="question-a",
                ),
            ),
        )
        self.assertIs(receipt.status, VisualInventoryStatus.PASS)

    def test_parked_and_rejected_roi_require_and_preserve_reasons(self) -> None:
        source = image("image-a", "a", "0123456789abcdef")
        parked = PixelRegion(
            roi_id="roi-parked",
            image_id="image-a",
            source_width_px=1200,
            source_height_px=800,
            x_px=0,
            y_px=0,
            width_px=10,
            height_px=10,
            selection=ROISelection.PARKED,
            reason="Occluded by vegetation.",
        )
        receipt = compile_visual_evidence_inventory(
            source_disposition=disposition(),
            source_images=(source,),
            rois=(parked,),
            coverage_entries=(
                ROICoverageEntry(
                    roi_id="roi-parked",
                    outcome=CoverageOutcome.PARKED,
                    reason="Occluded by vegetation.",
                ),
            ),
        )
        self.assertIs(receipt.status, VisualInventoryStatus.PASS)
        with self.assertRaisesRegex(VisualInventoryError, "preserve parked reason"):
            compile_visual_evidence_inventory(
                source_disposition=disposition(),
                source_images=(source,),
                rois=(parked,),
                coverage_entries=(
                    ROICoverageEntry(
                        roi_id="roi-parked",
                        outcome=CoverageOutcome.PARKED,
                        reason="Different reason.",
                    ),
                ),
            )

    def test_missing_roi_coverage_returns_typed_blocking_receipt(self) -> None:
        receipt = compile_visual_evidence_inventory(
            source_disposition=disposition(),
            source_images=(image("image-a", "a", "0123456789abcdef"),),
            rois=(roi("roi-a", "image-a"),),
        )
        self.assertIs(receipt.status, VisualInventoryStatus.BLOCKED)
        self.assertEqual(("roi-a",), receipt.missing_roi_ids)

    def test_empty_intake_requires_explicit_evidence_bound_disposition(self) -> None:
        with self.assertRaisesRegex(VisualInventoryError, "explicitly declared"):
            compile_visual_evidence_inventory(
                source_disposition=None,
                source_images=(),
                rois=(),
            )
        receipt = compile_visual_evidence_inventory(
            source_disposition=disposition(VisualSourceDispositionKind.TEXT_ONLY),
            source_images=(),
            rois=(),
        )
        self.assertIs(receipt.status, VisualInventoryStatus.PASS)
        self.assertEqual("text_only", receipt.to_dict()["source_disposition"]["kind"])

    def test_accepted_hypothesis_join_is_bidirectional_and_exact(self) -> None:
        receipt = full_receipt()
        accepted = receipt.accepted_component_identity_refs
        with self.assertRaisesRegex(VisualInventoryError, "exactly reverse-map"):
            compile_visual_evidence_inventory(
                source_disposition=receipt.source_disposition,
                source_images=receipt.source_images,
                duplicate_assessments=receipt.duplicate_assessments,
                rois=receipt.rois,
                observations=receipt.observations,
                equivalences=receipt.equivalences,
                component_hypotheses=receipt.component_hypotheses,
                accepted_component_identity_refs=accepted,
                visual_origin_proposal_component_ids=("component-window-99",),
                coverage_entries=receipt.coverage_entries,
            )

        orphan = AcceptedComponentIdentityRef(
            hypothesis_id="hypothesis-missing",
            proposal_component_id="component-window-99",
            component_identity_ref="component-identity:window-99",
            acceptance_ref="decision:accept-window-99",
            source_refs=("evidence:architect-review",),
        )
        with self.assertRaisesRegex(VisualInventoryError, "no component hypothesis"):
            compile_visual_evidence_inventory(
                source_disposition=receipt.source_disposition,
                source_images=receipt.source_images,
                rois=receipt.rois,
                observations=receipt.observations,
                equivalences=receipt.equivalences,
                component_hypotheses=receipt.component_hypotheses,
                accepted_component_identity_refs=(orphan,),
                visual_origin_proposal_component_ids=("component-window-99",),
                coverage_entries=receipt.coverage_entries,
            )

    def test_digest_and_fixed_authority_tampering_are_rejected(self) -> None:
        payload = full_receipt().to_dict()
        payload["inventory_digest"] = digest("tampered")
        with self.assertRaisesRegex(VisualInventoryError, "does not match"):
            VisualEvidenceInventoryReceipt.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
