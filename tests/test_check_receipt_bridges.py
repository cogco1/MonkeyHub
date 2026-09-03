"""Integration tests for requirement-first legacy validator bridges."""

from __future__ import annotations

import unittest
from dataclasses import replace
from hashlib import sha256

from archive.archflow.control.check_requirements import (
    component_lineage_stage_requirement,
    spatial_layout_stage_requirement,
)
from archflow.control.requirements import StageRequirementProfile
from archflow.control.stage_closure import (
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.validation.check_bridges import (
    ComponentLineageCheckProfile,
    SpatialLayoutCheckProfile,
    bridge_component_lineage_receipt,
    bridge_spatial_validation_receipt,
)
from archive.archflow.validation.component_lineage import (
    OperationDisposition,
    OperationLineageResolution,
    PredecessorOperationDisposition,
    StageOperation,
    StageOperationRef,
    compile_stage_component_coverage,
)
from archflow.validation.contracts import CheckStatus
from archive.archflow.validation.spatial import (
    AABB,
    HostRegion,
    SpatialElement,
    SpatialElementKind,
    SpatialValidationStatus,
    spatial_validation_input_digest,
    validate_spatial_layout,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SUBJECT_DIGEST = "c" * 64


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            "bridge-fixture",
            "validation-001",
            ProjectVersionRef("bridge-fixture", 2, SHA_A),
        ),
        branch_id="candidate",
        epoch=1,
    )


def operation(value: str = "same") -> StageOperation:
    return StageOperation(
        ref=StageOperationRef("shell", "surface-resolution"),
        fingerprint=sha256(value.encode("utf-8")).hexdigest(),
    )


def lineage_receipt(*, blocking: bool = False):
    predecessor = operation()
    if blocking:
        return compile_stage_component_coverage(
            predecessor_stage_id="stage-1",
            successor_stage_id="stage-2",
            predecessor_operations=(predecessor,),
            successor_operations=(),
            lineage_resolutions=(),
            dispositions=(
                PredecessorOperationDisposition(
                    predecessor_ref=predecessor.ref,
                    disposition=OperationDisposition.PARKED_WITH_REASON,
                    evidence_refs=("evidence:deferred-surface",),
                    parked_reason="The required surface remains unresolved.",
                    blocking=True,
                ),
            ),
        )
    return compile_stage_component_coverage(
        predecessor_stage_id="stage-1",
        successor_stage_id="stage-2",
        predecessor_operations=(predecessor,),
        successor_operations=(predecessor,),
        lineage_resolutions=(
            OperationLineageResolution(
                predecessor_ref=predecessor.ref,
                successor_refs=(predecessor.ref,),
                lineage_refs=("lineage:surface-resolution",),
            ),
        ),
        dispositions=(
            PredecessorOperationDisposition(
                predecessor_ref=predecessor.ref,
                disposition=OperationDisposition.VERIFIED_UNCHANGED,
                relational_revalidation_refs=(
                    "revalidation:surface-resolution",
                ),
            ),
        ),
    )


def lineage_profile() -> ComponentLineageCheckProfile:
    return ComponentLineageCheckProfile(
        profile_id="lineage-stage-2",
        branch=branch(),
        scope_digest=SHA_B,
        predecessor_stage_id="stage-1",
        successor_stage_id="stage-2",
        predecessor_operations=(operation(),),
        denominator_refs=("component:all",),
    )


HOSTS = (HostRegion("host", AABB((0, 0, 0), (10, 10, 10))),)
ELEMENTS = (
    SpatialElement(
        "shell",
        "shell-component",
        SpatialElementKind.OTHER,
        AABB((1, 1, 1), (9, 9, 9)),
        "host",
    ),
)


def spatial_kwargs(required: tuple[str, ...] = ("shell-component",)):
    return {
        "elements": ELEMENTS,
        "host_regions": HOSTS,
        "required_component_ids": required,
        "length_unit": "meter",
    }


def spatial_profile(required: tuple[str, ...] = ("shell-component",)):
    return SpatialLayoutCheckProfile(
        profile_id="spatial-stage-2",
        branch=branch(),
        scope_digest=SHA_B,
        stage_id="stage-2",
        input_digest=spatial_validation_input_digest(
            **spatial_kwargs(required)
        ),
        denominator_refs=("spatial-envelope:all",),
    )


def closure_profile(requirement, *, stage_id: str) -> StageRequirementProfile:
    return StageRequirementProfile(
        profile_id=f"{stage_id}-closure",
        typology_id="generic-bridge-fixture",
        stage_id=stage_id,
        branch=branch(),
        predecessor_state_digest=SHA_A,
        scope_digest=SHA_B,
        stage_subject_ref=f"deliverable:{stage_id}",
        requirements=(requirement,),
    )


class CheckReceiptBridgeTests(unittest.TestCase):
    def test_component_lineage_bridge_closes_exact_requirement(self) -> None:
        profile = lineage_profile()
        source = lineage_receipt()
        requirement = component_lineage_stage_requirement(profile)
        receipt = bridge_component_lineage_receipt(
            profile,
            source,
            stage_subject_digest=SUBJECT_DIGEST,
        )

        closure = compile_composite_stage_closure(
            closure_profile(requirement, stage_id="stage-2"),
            subject_digest=SUBJECT_DIGEST,
            check_receipts=(receipt,),
        )

        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertEqual(requirement.requirement_id, receipt.check_id)
        self.assertEqual(requirement.denominator_refs, receipt.subject_refs)
        self.assertEqual(receipt.subject_refs, receipt.coverage_denominator)
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        measurements = {
            item.measurement_id: item.value for item in receipt.measurements
        }
        self.assertEqual(
            source.receipt_digest,
            measurements["original-receipt-digest"],
        )
        self.assertEqual(
            profile,
            ComponentLineageCheckProfile.from_dict(profile.to_dict()),
        )

    def test_blocking_operation_is_derived_from_coverage_not_summary(self) -> None:
        receipt = bridge_component_lineage_receipt(
            lineage_profile(),
            lineage_receipt(blocking=True),
            stage_subject_digest=SUBJECT_DIGEST,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "blocking-lineage-operation",
            {item.code for item in receipt.findings},
        )

    def test_spatial_bridge_closes_exact_normalized_input(self) -> None:
        profile = spatial_profile()
        source = validate_spatial_layout(**spatial_kwargs())
        requirement = spatial_layout_stage_requirement(profile)
        receipt = bridge_spatial_validation_receipt(
            profile,
            source,
            stage_subject_digest=SUBJECT_DIGEST,
        )

        closure = compile_composite_stage_closure(
            closure_profile(requirement, stage_id=profile.stage_id),
            subject_digest=SUBJECT_DIGEST,
            check_receipts=(receipt,),
        )

        self.assertEqual(profile.input_digest, source.input_digest)
        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertEqual(requirement.requirement_id, receipt.check_id)
        self.assertEqual(requirement.denominator_refs, receipt.subject_refs)
        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        measurements = {
            item.measurement_id: item.value for item in receipt.measurements
        }
        self.assertEqual(
            source.receipt_digest,
            measurements["original-receipt-digest"],
        )
        self.assertEqual(
            profile,
            SpatialLayoutCheckProfile.from_dict(profile.to_dict()),
        )

    def test_spatial_failed_checks_override_a_forged_pass_summary(self) -> None:
        required = ("missing-component",)
        profile = spatial_profile(required)
        source = validate_spatial_layout(**spatial_kwargs(required))
        object.__setattr__(source, "status", SpatialValidationStatus.PASSED)

        receipt = bridge_spatial_validation_receipt(
            profile,
            source,
            stage_subject_digest=SUBJECT_DIGEST,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "spatial-check-failed",
            {item.code for item in receipt.findings},
        )

    def test_loose_context_cannot_replace_required_bridge_profile(self) -> None:
        source = validate_spatial_layout(**spatial_kwargs())
        with self.assertRaisesRegex(TypeError, "no branch/scope"):
            bridge_spatial_validation_receipt(
                None,  # type: ignore[arg-type]
                source,
                stage_subject_digest=SUBJECT_DIGEST,
            )

    def test_profile_mismatch_fails_without_expanding_denominator(self) -> None:
        profile = spatial_profile()
        wrong_source = validate_spatial_layout(
            **spatial_kwargs(("different-component",))
        )

        receipt = bridge_spatial_validation_receipt(
            profile,
            wrong_source,
            stage_subject_digest=SUBJECT_DIGEST,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertEqual(profile.denominator_refs, receipt.subject_refs)
        self.assertEqual(receipt.subject_refs, receipt.coverage_denominator)
        self.assertFalse(receipt.covered_refs)


if __name__ == "__main__":
    unittest.main()
