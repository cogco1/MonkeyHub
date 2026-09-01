"""Targeted tests for the first generic evidence contract package."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

from archflow.contracts.canonical import (
    CanonicalValueError,
    canonical_digest,
    canonical_json,
)
from archflow.evidence import (
    AllowedClaimUse,
    ApplicabilityDisposition,
    ApplicabilityTargetKind,
    ClaimApplicability,
    EpistemicRole,
    EvidenceClaimBinding,
    EvidenceModality,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef


SHA_A = "a" * 64
SHA_B = "b" * 64


def branch(branch_id: str = "selected", epoch: int = 2) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="evidence-fixture",
            run_id="research-001",
            base=ProjectVersionRef("evidence-fixture", 3, SHA_A),
        ),
        branch_id=branch_id,
        epoch=epoch,
    )


def claim() -> EvidenceClaimBinding:
    return EvidenceClaimBinding(
        binding_id="roof-support-claim",
        branch=branch(),
        scope_digest=SHA_B,
        obligation_id="roof-support-basis",
        target_ref="decision:roof-support",
        fact_ref="fact:roof-supported-by-inner-colonnade",
        source_ref="project-record:adopted-roof-source",
        source_family_ref="source-family:measured-drawing",
        claim_key="claim:roof-support",
        position_key="position:inner-colonnade",
        modality=EvidenceModality.DRAWING_OBSERVATION,
        epistemic_role=EpistemicRole.AUTHOR_DECLARATION,
        authority_ref="authority:measured-drawing-author",
        qualifiers=("load-path", "roof"),
    )


def applicability(binding: EvidenceClaimBinding) -> ClaimApplicability:
    return ClaimApplicability.from_claim(
        binding,
        applicability_id="roof-support-check-use",
        target_kind=ApplicabilityTargetKind.CHECK,
        target_ref="check:roof-load-path",
        disposition=ApplicabilityDisposition.APPLICABLE,
        allowed_uses=(AllowedClaimUse.TOPOLOGY, AllowedClaimUse.VALIDATION),
        authority_refs=("authority:measured-drawing-author",),
        source_refs=("project-record:adopted-roof-source",),
        rationale="The adopted measured drawing declares this support path.",
        invalidates_on=(
            "change:branch-revision",
            "change:roof-or-inner-colonnade-topology",
        ),
    )


class CanonicalContractTests(unittest.TestCase):
    def test_canonical_json_and_digest_ignore_mapping_insertion_order(self):
        left = {"z": [2, 1], "a": {"text": "Δ"}}
        right = {"a": {"text": "Δ"}, "z": [2, 1]}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(canonical_digest(left), canonical_digest(right))
        self.assertEqual(len(canonical_digest(left)), 64)

    def test_canonical_json_rejects_nonfinite_and_nonstring_keys(self):
        with self.assertRaises(CanonicalValueError):
            canonical_json({"bad": math.nan})
        with self.assertRaises(CanonicalValueError):
            canonical_json({1: "not-canonical"})


class EvidenceBindingTests(unittest.TestCase):
    def test_claim_binding_roundtrips_with_stable_digest(self):
        original = claim()
        restored = EvidenceClaimBinding.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.binding_digest, original.binding_digest)
        self.assertEqual(original.to_dict()["design_authority"], False)
        self.assertEqual(original.to_dict()["canonical_write_authority"], False)

    def test_claim_binding_rejects_empty_and_duplicate_values(self):
        with self.assertRaises(ValueError):
            replace(claim(), target_ref="")
        with self.assertRaises(ValueError):
            replace(claim(), qualifiers=("roof", "roof"))
        with self.assertRaises(ValueError):
            replace(claim(), source_ref="file:///private/source.pdf")

    def test_claim_binding_rejects_undigested_branch_base(self):
        unsafe = BranchRef(
            run=RunRef(
                project_id="evidence-fixture",
                run_id="research-001",
                base=ProjectVersionRef("evidence-fixture", 3),
            ),
            branch_id="selected",
            epoch=2,
        )
        with self.assertRaises(ValueError):
            replace(claim(), branch=unsafe)


class ApplicabilityTests(unittest.TestCase):
    def test_applicability_roundtrips_and_replays_exact_claim(self):
        binding = claim()
        original = applicability(binding)
        restored = ClaimApplicability.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.applicability_digest, original.applicability_digest)
        restored.require_claim(binding)

    def test_applicable_claim_requires_sorted_unique_uses_and_refs(self):
        binding = claim()
        with self.assertRaises(ValueError):
            ClaimApplicability.from_claim(
                binding,
                applicability_id="empty-use",
                target_kind=ApplicabilityTargetKind.CHECK,
                target_ref="check:roof-load-path",
                disposition=ApplicabilityDisposition.APPLICABLE,
                allowed_uses=(),
                authority_refs=("authority:measured-drawing-author",),
                source_refs=("project-record:adopted-roof-source",),
                rationale="Missing allowed use must fail.",
                invalidates_on=("change:branch-revision",),
            )
        with self.assertRaises(ValueError):
            replace(
                applicability(binding),
                source_refs=(
                    "project-record:adopted-roof-source",
                    "project-record:adopted-roof-source",
                ),
            )

    def test_cross_branch_claim_fails_closed(self):
        original = claim()
        wrong_branch_claim = replace(original, branch=branch("other-branch"))
        with self.assertRaisesRegex(ValueError, "crossed its exact branch"):
            applicability(original).require_claim(wrong_branch_claim)

    def test_authority_flags_cannot_be_tampered(self):
        payload = applicability(claim()).to_dict()
        payload["design_authority"] = True
        with self.assertRaises(ValueError):
            ClaimApplicability.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
