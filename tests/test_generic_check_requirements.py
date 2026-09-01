from __future__ import annotations

import unittest

from archflow.control.check_requirements import assembly_stage_requirement
from archflow.control.requirements import StageRequirementProfile
from archflow.control.stage_closure import (
    StageClosureFindingCode,
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.validation.assembly import check_assembly
from tests.test_assembly_validation import SHA_A, SHA_B, SHA_C, branch, passing_profile


class GenericCheckRequirementIntegrationTests(unittest.TestCase):
    def test_assembly_requirement_closes_only_exact_stage_subject(self) -> None:
        assembly = passing_profile(reverse=True)
        requirement = assembly_stage_requirement(assembly)
        stage_profile = StageRequirementProfile(
            profile_id="generic-building-stage",
            typology_id="synthetic-building",
            stage_id="detail",
            branch=branch(),
            predecessor_state_digest=SHA_A,
            scope_digest=SHA_B,
            stage_subject_ref="artifact://candidate/model",
            requirements=(requirement,),
        )
        receipt = check_assembly(
            assembly,
            branch=branch(),
            scope_digest=stage_profile.scope_digest,
            stage_subject_digest=SHA_C,
        )

        closure = compile_composite_stage_closure(
            stage_profile,
            subject_digest=SHA_C,
            check_receipts=(receipt,),
        )

        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        self.assertEqual(requirement.denominator_refs, receipt.subject_refs)
        self.assertEqual(
            requirement.denominator_refs,
            receipt.coverage_denominator,
        )
        self.assertEqual(
            requirement.required_source_refs,
            receipt.source_refs,
        )
        self.assertEqual(
            requirement.required_authority_refs,
            receipt.authority_refs,
        )
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)

        stale = compile_composite_stage_closure(
            stage_profile,
            subject_digest="d" * 64,
            check_receipts=(receipt,),
        )
        self.assertIs(stale.status, StageClosureStatus.OPEN)
        self.assertIn(
            StageClosureFindingCode.SUBJECT_DIGEST_MISMATCH,
            {item.code for item in stale.findings},
        )


if __name__ == "__main__":
    unittest.main()
