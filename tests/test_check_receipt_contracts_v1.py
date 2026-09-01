"""Targeted tests for the generic deterministic-check receipt envelope."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.evidence import (
    AllowedClaimUse,
    ApplicabilityDisposition,
    ApplicabilityTargetKind,
    ClaimApplicability,
    EvidenceClaimBinding,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def branch(branch_id: str = "selected") -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="check-fixture",
            run_id="reconstruction-001",
            base=ProjectVersionRef("check-fixture", 4, SHA_A),
        ),
        branch_id=branch_id,
        epoch=3,
    )


def chain() -> tuple[EvidenceClaimBinding, ClaimApplicability]:
    binding = EvidenceClaimBinding(
        binding_id="column-support",
        branch=branch(),
        scope_digest=SHA_B,
        obligation_id="column-support-basis",
        target_ref="decision:column-support",
        fact_ref="fact:column-lands-on-stylobate",
        source_ref="project-record:column-source",
        source_family_ref="source-family:measured-drawing",
        claim_key="claim:column-support",
        position_key="position:stylobate",
        authority_ref="authority:adoption-reviewer",
    )
    use = ClaimApplicability.from_claim(
        binding,
        applicability_id="column-support-check-use",
        target_kind=ApplicabilityTargetKind.CHECK,
        target_ref="check:column-support",
        disposition=ApplicabilityDisposition.APPLICABLE,
        allowed_uses=(AllowedClaimUse.TOPOLOGY, AllowedClaimUse.VALIDATION),
        authority_refs=("authority:adoption-reviewer",),
        source_refs=("project-record:column-source",),
        rationale="The adopted claim governs this deterministic check.",
        invalidates_on=("change:column-or-stylobate-geometry",),
    )
    return binding, use


def passing_receipt() -> tuple[CheckReceiptEnvelope, ClaimApplicability]:
    binding, use = chain()
    subject = "component:column-a"
    receipt = CheckReceiptEnvelope(
        check_id="column-support-requirement",
        checker_id="structural-support",
        checker_version="1.0.0",
        branch=branch(),
        scope_digest=SHA_B,
        subject_refs=(subject,),
        subject_digest=SHA_C,
        status=CheckStatus.PASS,
        claim_refs=(binding.ref,),
        applicability_refs=(use.ref,),
        adoption_refs=("adoption:column-support",),
        source_refs=("project-record:column-source",),
        authority_refs=("authority:adoption-reviewer",),
        findings=(),
        measurements=(
            CheckMeasurement(
                measurement_id="support-gap",
                subject_ref=subject,
                name="vertical-gap",
                value=0.0,
                unit_ref="unit:meter",
                evidence_refs=("observation:column-a-support",),
            ),
        ),
        coverage_denominator=(subject,),
        covered_refs=(subject,),
        revalidation_refs=("check:roof-load-path",),
    )
    return receipt, use


class CheckReceiptTests(unittest.TestCase):
    def test_pass_roundtrips_with_stable_receipt_identity(self):
        original, use = passing_receipt()
        restored = CheckReceiptEnvelope.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.receipt_digest, original.receipt_digest)
        self.assertTrue(restored.receipt_id.startswith(original.check_id))
        self.assertEqual(restored.to_dict()["receipt_id"], restored.receipt_id)
        restored.require_applicabilities((use,))
        payload = restored.to_dict()
        self.assertIs(payload["design_authority"], False)
        self.assertIs(payload["geometry_mutation_authority"], False)
        self.assertIs(payload["promotion_authority"], False)
        self.assertIs(payload["canonical_write_authority"], False)

    def test_pass_cannot_hide_incomplete_denominator(self):
        receipt, _ = passing_receipt()
        with self.assertRaisesRegex(ValueError, "exact denominator"):
            replace(
                receipt,
                coverage_denominator=(
                    "component:column-a",
                    "component:column-b",
                ),
            )

    def test_duplicate_and_empty_subjects_fail_closed(self):
        receipt, _ = passing_receipt()
        with self.assertRaises(ValueError):
            replace(receipt, subject_refs=())
        with self.assertRaises(ValueError):
            replace(
                receipt,
                subject_refs=("component:column-a", "component:column-a"),
            )

    def test_claim_chain_cannot_skip_applicability_or_authority(self):
        receipt, _ = passing_receipt()
        with self.assertRaises(ValueError):
            replace(receipt, applicability_refs=())
        with self.assertRaises(ValueError):
            replace(receipt, authority_refs=())

    def test_fail_unknown_and_not_applicable_have_typed_semantics(self):
        receipt, _ = passing_receipt()
        error = CheckFinding(
            code="support-gap",
            severity=FindingSeverity.ERROR,
            message="The column does not land on its support.",
            subject_refs=("component:column-a",),
            evidence_refs=("observation:column-a-support",),
        )
        failed = replace(
            receipt,
            status=CheckStatus.FAIL,
            findings=(error,),
            covered_refs=(),
        )
        self.assertEqual(failed.status, CheckStatus.FAIL)
        unknown = replace(
            receipt,
            status=CheckStatus.UNKNOWN,
            findings=(
                replace(error, severity=FindingSeverity.UNKNOWN),
            ),
            covered_refs=(),
        )
        self.assertEqual(unknown.status, CheckStatus.UNKNOWN)
        not_applicable = replace(
            receipt,
            status=CheckStatus.NOT_APPLICABLE,
            findings=(),
            measurements=(),
            covered_refs=(),
        )
        self.assertEqual(not_applicable.status, CheckStatus.NOT_APPLICABLE)

    def test_wrong_branch_applicability_fails_closed(self):
        receipt, use = passing_receipt()
        wrong = replace(use, branch=branch("other-branch"))
        with self.assertRaisesRegex(ValueError, "crossed its exact branch"):
            receipt.require_applicabilities((wrong,))

    def test_authority_flags_cannot_be_tampered(self):
        receipt, _ = passing_receipt()
        payload = receipt.to_dict()
        payload["canonical_write_authority"] = True
        with self.assertRaises(ValueError):
            CheckReceiptEnvelope.from_dict(payload)

    def test_receipt_identity_cannot_be_tampered(self):
        receipt, _ = passing_receipt()
        payload = receipt.to_dict()
        payload["receipt_id"] = "another-check-00000000000000000000"
        with self.assertRaisesRegex(ValueError, "identity drifted"):
            CheckReceiptEnvelope.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
