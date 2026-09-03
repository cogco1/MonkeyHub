from __future__ import annotations

import unittest
from dataclasses import replace

from archive.archflow.control.requirements import RequirementBasisMode, RequirementTargetKind, StageCheckRequirement, StageRequirementError, StageRequirementProfile
from archflow.state.stage_workflow import CompositeStageClosureReceipt, StageClosureFindingCode, StageClosureStatus
from archive.archflow.control.stage_closure import compile_composite_stage_closure
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def branch(epoch: int = 4) -> BranchRef:
    base = ProjectVersionRef("generic-project", 0, SHA_A)
    return BranchRef(
        run=RunRef("generic-project", "design-001", base),
        branch_id="candidate",
        epoch=epoch,
    )


def universal_requirement() -> StageCheckRequirement:
    return StageCheckRequirement(
        requirement_id="support-path",
        checker_id="archflow.support-path",
        target_kind=RequirementTargetKind.RELATION,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=("component:beam", "component:column"),
    )


def claim_requirement() -> StageCheckRequirement:
    return StageCheckRequirement(
        requirement_id="opening-clearance",
        checker_id="archflow.opening-clearance",
        target_kind=RequirementTargetKind.ASSEMBLY,
        basis_mode=RequirementBasisMode.CLAIM_BOUND,
        denominator_refs=("assembly:primary-opening",),
        required_claim_refs=("claim:primary-opening-clearance",),
        required_applicability_refs=("applicability:primary-opening",),
        required_adoption_refs=("adoption:primary-opening",),
        required_source_refs=("source:primary-opening",),
        required_authority_refs=("authority:historian",),
    )


def profile(*requirements: StageCheckRequirement) -> StageRequirementProfile:
    return StageRequirementProfile(
        profile_id="generic-stage-profile",
        typology_id="caller-owned-typology",
        stage_id="detail-coordination",
        branch=branch(),
        predecessor_state_digest=SHA_B,
        scope_digest=SHA_C,
        stage_subject_ref="deliverable:detail-package",
        requirements=tuple(requirements),
    )


def receipt(
    requirement: StageCheckRequirement,
    *,
    checked_refs: tuple[str, ...] | None = None,
    status: CheckStatus = CheckStatus.PASS,
    receipt_branch: BranchRef | None = None,
    claim_refs: tuple[str, ...] | None = None,
    applicability_refs: tuple[str, ...] | None = None,
) -> CheckReceiptEnvelope:
    refs = requirement.denominator_refs if checked_refs is None else checked_refs
    return CheckReceiptEnvelope(
        check_id=requirement.requirement_id,
        checker_id=requirement.checker_id,
        checker_version="1.0.0",
        branch=receipt_branch or branch(),
        scope_digest=SHA_C,
        subject_refs=refs,
        subject_digest=SHA_B,
        status=status,
        claim_refs=(
            requirement.required_claim_refs
            if claim_refs is None
            else claim_refs
        ),
        applicability_refs=(
            requirement.required_applicability_refs
            if applicability_refs is None
            else applicability_refs
        ),
        adoption_refs=requirement.required_adoption_refs,
        source_refs=requirement.required_source_refs,
        authority_refs=requirement.required_authority_refs,
        coverage_denominator=refs,
        covered_refs=refs if status is CheckStatus.PASS else (),
    )


class StageRequirementProfileTests(unittest.TestCase):
    def test_profile_is_order_invariant_and_round_trips(self) -> None:
        first = profile(claim_requirement(), universal_requirement())
        second = profile(universal_requirement(), claim_requirement())

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.profile_digest, second.profile_digest)
        self.assertEqual(
            first,
            StageRequirementProfile.from_dict(first.to_dict()),
        )

    def test_claim_bound_requirement_needs_exact_applicability(self) -> None:
        with self.assertRaises(StageRequirementError):
            StageCheckRequirement(
                requirement_id="unsupported-parameter",
                checker_id="archflow.parameter",
                target_kind=RequirementTargetKind.PARAMETER,
                basis_mode=RequirementBasisMode.CLAIM_BOUND,
                denominator_refs=("parameter:width",),
                required_claim_refs=("claim:width",),
            )

    def test_universal_requirement_cannot_hide_human_authority(self) -> None:
        with self.assertRaises(StageRequirementError):
            StageCheckRequirement(
                requirement_id="support",
                checker_id="archflow.support",
                target_kind=RequirementTargetKind.RELATION,
                basis_mode=RequirementBasisMode.UNIVERSAL,
                denominator_refs=("relation:support",),
                required_authority_refs=("authority:designer",),
            )

    def test_serialized_authority_and_schema_drift_fail_closed(self) -> None:
        requirement_payload = universal_requirement().to_dict()
        requirement_payload["unexpected"] = "value"
        with self.assertRaises(StageRequirementError):
            StageCheckRequirement.from_dict(requirement_payload)


class CompositeStageClosureTests(unittest.TestCase):
    def test_exact_typed_receipts_satisfy_stage_and_round_trip(self) -> None:
        support = universal_requirement()
        opening = claim_requirement()
        requirements = profile(support, opening)

        closure = compile_composite_stage_closure(
            requirements,
            subject_digest=SHA_B,
            check_receipts=(receipt(opening), receipt(support)),
        )

        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        self.assertEqual(closure.findings, ())
        self.assertFalse(closure.to_dict()["stage_acceptance_authority"])
        self.assertFalse(closure.to_dict()["canonical_write_authority"])
        self.assertEqual(
            closure,
            CompositeStageClosureReceipt.from_dict(closure.to_dict()),
        )

    def test_missing_check_cannot_be_replaced_by_caller_boolean(self) -> None:
        requirements = profile(universal_requirement(), claim_requirement())
        closure = compile_composite_stage_closure(
            requirements,
            subject_digest=SHA_B,
            check_receipts=(receipt(universal_requirement()),),
        )
        self.assertIs(closure.status, StageClosureStatus.OPEN)
        self.assertIn(
            StageClosureFindingCode.MISSING_CHECK,
            {item.code for item in closure.findings},
        )
        with self.assertRaises(TypeError):
            compile_composite_stage_closure(
                requirements,
                subject_digest=SHA_B,
                check_receipts=(True,),  # type: ignore[arg-type]
            )

    def test_passed_subset_does_not_satisfy_profile_denominator(self) -> None:
        support = universal_requirement()
        requirements = profile(support)
        subset = receipt(support, checked_refs=("component:column",))

        closure = compile_composite_stage_closure(
            requirements,
            subject_digest=SHA_B,
            check_receipts=(subset,),
        )

        self.assertIs(closure.status, StageClosureStatus.OPEN)
        self.assertIn(
            StageClosureFindingCode.DENOMINATOR_MISMATCH,
            {item.code for item in closure.findings},
        )

    def test_cross_epoch_and_unbound_claim_fail_closed(self) -> None:
        opening = claim_requirement()
        requirements = profile(opening)
        invalid = receipt(
            opening,
            receipt_branch=branch(3),
            claim_refs=(),
            applicability_refs=(),
        )

        closure = compile_composite_stage_closure(
            requirements,
            subject_digest=SHA_B,
            check_receipts=(invalid,),
        )

        codes = {item.code for item in closure.findings}
        self.assertIs(closure.status, StageClosureStatus.OPEN)
        self.assertIn(StageClosureFindingCode.BRANCH_MISMATCH, codes)
        self.assertIn(StageClosureFindingCode.CLAIM_BINDING_MISSING, codes)
        self.assertIn(
            StageClosureFindingCode.APPLICABILITY_BINDING_MISSING,
            codes,
        )

    def test_authorized_not_applicable_closes_without_claiming_coverage(
        self,
    ) -> None:
        opening = replace(
            claim_requirement(),
            allow_not_applicable=True,
        )
        not_applicable = receipt(
            opening,
            status=CheckStatus.NOT_APPLICABLE,
        )

        closure = compile_composite_stage_closure(
            profile(opening),
            subject_digest=SHA_B,
            check_receipts=(not_applicable,),
        )

        self.assertEqual((), not_applicable.covered_refs)
        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        self.assertEqual((), closure.findings)

    def test_not_applicable_without_requirement_permission_stays_open(
        self,
    ) -> None:
        opening = claim_requirement()
        closure = compile_composite_stage_closure(
            profile(opening),
            subject_digest=SHA_B,
            check_receipts=(
                receipt(opening, status=CheckStatus.NOT_APPLICABLE),
            ),
        )

        self.assertIs(closure.status, StageClosureStatus.OPEN)
        self.assertIn(
            StageClosureFindingCode.NOT_APPLICABLE_FORBIDDEN,
            {item.code for item in closure.findings},
        )


if __name__ == "__main__":
    unittest.main()
