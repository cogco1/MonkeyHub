"""Tests for claim-bound, project-neutral architectural invariant checks."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.stage_closure import (
    StageClosureFindingCode,
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.validation.architectural_invariants import (
    ArchitecturalInvariantError,
    ClaimBoundValidationBasis,
    ComponentCardinalityPartitionProfile,
    ComponentCardinalityPartitionRequirement,
    ComponentPartitionObservation,
    LevelDatumObservation,
    LevelStratificationProfile,
    LevelStratificationRequirement,
    NfoldRotationalSymmetryProfile,
    NfoldRotationalSymmetryRequirement,
    OrientedFrameAngleProfile,
    OrientedFrameAngleRequirement,
    PartitionExpectation,
    PlanarDirectionObservation,
    PlanarPoseObservation,
    check_component_cardinality_partition,
    check_level_stratification,
    check_nfold_rotational_symmetry,
    check_oriented_frame_angle,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="architectural-invariant-fixture",
            run_id="run-001",
            base=ProjectVersionRef(
                "architectural-invariant-fixture",
                2,
                SHA_A,
            ),
        ),
        branch_id="candidate-a",
        epoch=3,
    )


def basis() -> ClaimBoundValidationBasis:
    return ClaimBoundValidationBasis(
        claim_refs=("claim:generic-invariant-values",),
        applicability_refs=("applicability:generic-invariant-values",),
        adoption_refs=("adoption:generic-invariant-values",),
        source_refs=("source:generic-invariant-values",),
        authority_refs=("authority:generic-invariant-values",),
    )


def check_kwargs() -> dict[str, object]:
    return {
        "branch": branch(),
        "scope_digest": SHA_C,
        "stage_subject_digest": SHA_B,
    }


def cardinality_profile() -> ComponentCardinalityPartitionProfile:
    member_refs = tuple(f"component:device-{index}" for index in range(1, 7))
    partition_refs = ("partition:alpha", "partition:beta", "partition:gamma")
    observations = tuple(
        ComponentPartitionObservation(
            component_ref=member_ref,
            partition_refs=(partition_refs[(index - 1) // 2],),
            evidence_refs=(f"evidence:membership-{index}",),
        )
        for index, member_ref in enumerate(member_refs, start=1)
    )
    return ComponentCardinalityPartitionProfile(
        profile_id="generic-device-partitions",
        check_id="generic-device-partitions",
        basis=basis(),
        requirements=(
            ComponentCardinalityPartitionRequirement(
                requirement_id="device-groups",
                member_refs=member_refs,
                expected_total=6,
                partitions=tuple(
                    PartitionExpectation(
                        partition_ref=partition_ref,
                        expected_count=2,
                    )
                    for partition_ref in partition_refs
                ),
            ),
        ),
        observations=observations,
    )


def oriented_frame_profile() -> OrientedFrameAngleProfile:
    angle = math.radians(30.0)
    return OrientedFrameAngleProfile(
        profile_id="generic-frame-angle",
        check_id="generic-frame-angle",
        basis=basis(),
        requirements=(
            OrientedFrameAngleRequirement(
                requirement_id="assembly-relative-to-survey",
                frame_ref="frame:assembly-axis",
                reference_frame_ref="frame:survey-datum",
                expected_angle_degrees=30.0,
                periodicity_degrees=180.0,
                tolerance_degrees=0.01,
            ),
        ),
        observations=(
            PlanarDirectionObservation(
                frame_ref="frame:assembly-axis",
                direction=(math.cos(angle), math.sin(angle)),
                evidence_refs=("evidence:assembly-axis",),
            ),
            PlanarDirectionObservation(
                frame_ref="frame:survey-datum",
                direction=(1.0, 0.0),
                evidence_refs=("evidence:survey-axis",),
            ),
        ),
        angle_unit_ref="unit:degree",
    )


def symmetry_profile() -> NfoldRotationalSymmetryProfile:
    half_root_three = math.sqrt(3.0) / 2.0
    poses = {
        "component:orbit-a": (1.0, 0.0),
        "component:orbit-b": (-0.5, half_root_three),
        "component:orbit-c": (-0.5, -half_root_three),
    }
    return NfoldRotationalSymmetryProfile(
        profile_id="generic-three-fold-orbit",
        check_id="generic-three-fold-orbit",
        basis=basis(),
        requirements=(
            NfoldRotationalSymmetryRequirement(
                requirement_id="three-member-orbit",
                center_ref="datum:rotation-center",
                member_refs=tuple(sorted(poses)),
                fold_count=3,
                position_tolerance=1.0e-6,
                compare_member_directions=True,
                direction_tolerance_degrees=1.0e-6,
            ),
        ),
        observations=(
            PlanarPoseObservation(
                subject_ref="datum:rotation-center",
                position=(0.0, 0.0),
                direction=None,
                evidence_refs=("evidence:rotation-center",),
            ),
            *tuple(
                PlanarPoseObservation(
                    subject_ref=subject_ref,
                    position=position,
                    direction=position,
                    evidence_refs=(
                        f"evidence:pose-{subject_ref.rsplit('-', maxsplit=1)[-1]}",
                    ),
                )
                for subject_ref, position in sorted(poses.items())
            ),
        ),
        length_unit_ref="unit:metre",
        angle_unit_ref="unit:degree",
    )


def level_profile() -> LevelStratificationProfile:
    return LevelStratificationProfile(
        profile_id="generic-level-order",
        check_id="generic-level-order",
        basis=basis(),
        requirements=(
            LevelStratificationRequirement(
                requirement_id="lower-before-upper",
                lower_level_ref="level:lower",
                upper_level_ref="level:upper",
                datum_frame_ref="frame:vertical-datum",
                minimum_separation=3.0,
                maximum_separation=4.0,
            ),
        ),
        observations=(
            LevelDatumObservation(
                level_ref="level:lower",
                datum_frame_ref="frame:vertical-datum",
                datum=1.0,
                evidence_refs=("evidence:lower-level",),
            ),
            LevelDatumObservation(
                level_ref="level:upper",
                datum_frame_ref="frame:vertical-datum",
                datum=4.25,
                evidence_refs=("evidence:upper-level",),
            ),
        ),
        length_unit_ref="unit:metre",
    )


def run_cardinality(
    profile: ComponentCardinalityPartitionProfile,
) -> CheckReceiptEnvelope:
    return check_component_cardinality_partition(profile, **check_kwargs())


def run_orientation(profile: OrientedFrameAngleProfile) -> CheckReceiptEnvelope:
    return check_oriented_frame_angle(profile, **check_kwargs())


def run_symmetry(profile: NfoldRotationalSymmetryProfile) -> CheckReceiptEnvelope:
    return check_nfold_rotational_symmetry(profile, **check_kwargs())


def run_levels(profile: LevelStratificationProfile) -> CheckReceiptEnvelope:
    return check_level_stratification(profile, **check_kwargs())


class ArchitecturalInvariantContractTests(unittest.TestCase):
    def test_claim_bound_basis_round_trips_and_rejects_authority_drift(self) -> None:
        original = basis()
        self.assertEqual(original, ClaimBoundValidationBasis.from_dict(original.to_dict()))

    def test_all_passing_profiles_and_receipts_round_trip(self) -> None:
        cases = (
            (cardinality_profile(), run_cardinality),
            (oriented_frame_profile(), run_orientation),
            (symmetry_profile(), run_symmetry),
            (level_profile(), run_levels),
        )

        for profile, checker in cases:
            with self.subTest(profile=profile.profile_id):
                receipt = checker(profile)
                self.assertEqual(CheckStatus.PASS, receipt.status)
                self.assertEqual(profile.checker_requirement_refs, receipt.subject_refs)
                self.assertEqual(
                    profile.checker_requirement_refs,
                    receipt.coverage_denominator,
                )
                self.assertEqual(
                    profile.checker_requirement_refs,
                    receipt.covered_refs,
                )
                self.assertEqual(basis().claim_refs, receipt.claim_refs)
                self.assertEqual(
                    receipt,
                    CheckReceiptEnvelope.from_dict(receipt.to_dict()),
                )
                self.assertEqual(
                    profile,
                    type(profile).from_dict(profile.to_dict()),
                )

    def test_project_value_change_changes_exact_requirement_ref(self) -> None:
        original = oriented_frame_profile()
        changed_requirement = replace(
            original.requirements[0],
            expected_angle_degrees=35.0,
        )
        changed = replace(original, requirements=(changed_requirement,))

        self.assertNotEqual(
            original.checker_requirement_refs,
            changed.checker_requirement_refs,
        )
        self.assertNotEqual(original.profile_digest, changed.profile_digest)


class ComponentCardinalityPartitionTests(unittest.TestCase):
    def test_exact_total_and_exclusive_partition_pass(self) -> None:
        receipt = run_cardinality(cardinality_profile())

        self.assertEqual(CheckStatus.PASS, receipt.status)
        self.assertIn("inventory_total", {item.name for item in receipt.measurements})
        self.assertEqual((), receipt.findings)

    def test_inventory_cardinality_mismatch_fails(self) -> None:
        original = cardinality_profile()
        changed_requirement = replace(
            original.requirements[0],
            member_refs=original.requirements[0].member_refs[:-1],
        )
        changed = replace(
            original,
            requirements=(changed_requirement,),
            observations=original.observations[:-1],
        )

        receipt = run_cardinality(changed)

        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "component-cardinality-mismatch",
            {item.code for item in receipt.findings},
        )

    def test_multiple_partition_memberships_fail_exclusive_rule(self) -> None:
        original = cardinality_profile()
        changed = replace(
            original,
            observations=(
                replace(
                    original.observations[0],
                    partition_refs=("partition:alpha", "partition:beta"),
                ),
                *original.observations[1:],
            ),
        )

        receipt = run_cardinality(changed)

        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "component-exclusive-partition-violated",
            {item.code for item in receipt.findings},
        )

    def test_missing_or_unresolved_membership_is_unknown(self) -> None:
        original = cardinality_profile()
        cases = (
            replace(original, observations=original.observations[:-1]),
            replace(
                original,
                observations=(
                    replace(original.observations[0], partition_refs=None),
                    *original.observations[1:],
                ),
            ),
        )

        for profile in cases:
            with self.subTest(observation_count=len(profile.observations)):
                receipt = run_cardinality(profile)
                self.assertEqual(CheckStatus.UNKNOWN, receipt.status)

    def test_partition_expectations_must_sum_to_expected_total(self) -> None:
        original = cardinality_profile().requirements[0]
        with self.assertRaises(ArchitecturalInvariantError):
            replace(
                original,
                partitions=(
                    PartitionExpectation("partition:alpha", 1),
                    *original.partitions[1:],
                ),
            )


class OrientedFrameAngleTests(unittest.TestCase):
    def test_periodic_axis_reversal_still_passes(self) -> None:
        original = oriented_frame_profile()
        assembly = original.observations[0]
        changed = replace(
            original,
            observations=(
                replace(
                    assembly,
                    direction=(-assembly.direction[0], -assembly.direction[1]),
                ),
                original.observations[1],
            ),
        )

        receipt = run_orientation(changed)

        self.assertEqual(CheckStatus.PASS, receipt.status)

    def test_angle_outside_project_tolerance_fails(self) -> None:
        original = oriented_frame_profile()
        angle = math.radians(42.0)
        changed = replace(
            original,
            observations=(
                replace(
                    original.observations[0],
                    direction=(math.cos(angle), math.sin(angle)),
                ),
                original.observations[1],
            ),
        )

        receipt = run_orientation(changed)

        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "oriented-frame-angle-out-of-tolerance",
            {item.code for item in receipt.findings},
        )

    def test_missing_direction_is_unknown(self) -> None:
        original = oriented_frame_profile()
        changed = replace(
            original,
            observations=(
                replace(original.observations[0], direction=None),
                original.observations[1],
            ),
        )

        receipt = run_orientation(changed)

        self.assertEqual(CheckStatus.UNKNOWN, receipt.status)
        self.assertIn(
            "oriented-frame-observation-unknown",
            {item.code for item in receipt.findings},
        )


class NfoldRotationalSymmetryTests(unittest.TestCase):
    def test_complete_position_and_direction_orbit_passes(self) -> None:
        receipt = run_symmetry(symmetry_profile())

        self.assertEqual(CheckStatus.PASS, receipt.status)
        self.assertEqual(
            {"maximum_direction_error", "maximum_position_error"},
            {item.name for item in receipt.measurements},
        )

    def test_displaced_member_fails(self) -> None:
        original = symmetry_profile()
        changed = replace(
            original,
            observations=tuple(
                replace(item, position=(item.position[0] + 0.2, item.position[1]))
                if item.subject_ref == "component:orbit-c"
                else item
                for item in original.observations
            ),
        )

        receipt = run_symmetry(changed)

        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "rotational-symmetry-target-missing",
            {item.code for item in receipt.findings},
        )

    def test_missing_position_or_required_direction_is_unknown(self) -> None:
        original = symmetry_profile()
        cases = (
            replace(
                original,
                observations=tuple(
                    replace(item, position=None)
                    if item.subject_ref == "component:orbit-c"
                    else item
                    for item in original.observations
                ),
            ),
            replace(
                original,
                observations=tuple(
                    replace(item, direction=None)
                    if item.subject_ref == "component:orbit-c"
                    else item
                    for item in original.observations
                ),
            ),
        )

        for profile in cases:
            with self.subTest(profile=profile.profile_digest):
                receipt = run_symmetry(profile)
                self.assertEqual(CheckStatus.UNKNOWN, receipt.status)

    def test_direction_mismatch_fails_when_profile_requires_pose_comparison(self) -> None:
        original = symmetry_profile()
        changed = replace(
            original,
            observations=tuple(
                replace(item, direction=(1.0, 0.0))
                if item.subject_ref == "component:orbit-c"
                else item
                for item in original.observations
            ),
        )

        receipt = run_symmetry(changed)

        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "rotational-symmetry-direction-out-of-tolerance",
            {item.code for item in receipt.findings},
        )


class LevelStratificationTests(unittest.TestCase):
    def test_ordered_levels_in_one_datum_frame_pass(self) -> None:
        receipt = run_levels(level_profile())

        self.assertEqual(CheckStatus.PASS, receipt.status)
        self.assertEqual(
            {"upper_minus_lower_separation"},
            {item.name for item in receipt.measurements},
        )

    def test_insufficient_or_inverted_separation_fails(self) -> None:
        original = level_profile()
        cases = (2.0, 0.5)
        for upper_datum in cases:
            changed = replace(
                original,
                observations=(
                    original.observations[0],
                    replace(original.observations[1], datum=upper_datum),
                ),
            )
            with self.subTest(upper_datum=upper_datum):
                receipt = run_levels(changed)
                self.assertEqual(CheckStatus.FAIL, receipt.status)
                self.assertIn(
                    "level-minimum-separation-unsatisfied",
                    {item.code for item in receipt.findings},
                )

    def test_maximum_separation_is_project_supplied_and_enforced(self) -> None:
        original = level_profile()
        changed = replace(
            original,
            observations=(
                original.observations[0],
                replace(original.observations[1], datum=5.5),
            ),
        )

        receipt = run_levels(changed)

        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "level-maximum-separation-exceeded",
            {item.code for item in receipt.findings},
        )

    def test_missing_datum_is_unknown(self) -> None:
        original = level_profile()
        changed = replace(
            original,
            observations=(
                original.observations[0],
                replace(original.observations[1], datum=None),
            ),
        )

        receipt = run_levels(changed)

        self.assertEqual(CheckStatus.UNKNOWN, receipt.status)
        self.assertIn("level-datum-unknown", {item.code for item in receipt.findings})

    def test_mixed_datum_frames_fail(self) -> None:
        original = level_profile()
        changed = replace(
            original,
            observations=(
                original.observations[0],
                replace(
                    original.observations[1],
                    datum_frame_ref="frame:other-datum",
                ),
            ),
        )

        receipt = run_levels(changed)

        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "level-datum-frame-mismatch",
            {item.code for item in receipt.findings},
        )


class StageClosureMappingTests(unittest.TestCase):
    def test_four_receipts_satisfy_exact_claim_bound_stage_requirements(self) -> None:
        profiles_and_targets = (
            (cardinality_profile(), run_cardinality, RequirementTargetKind.COMPONENT),
            (oriented_frame_profile(), run_orientation, RequirementTargetKind.PARAMETER),
            (symmetry_profile(), run_symmetry, RequirementTargetKind.RELATION),
            (level_profile(), run_levels, RequirementTargetKind.RELATION),
        )
        requirements = tuple(
            StageCheckRequirement(
                requirement_id=profile.check_id,
                checker_id=profile.CHECKER_ID,
                target_kind=target_kind,
                basis_mode=RequirementBasisMode.CLAIM_BOUND,
                denominator_refs=profile.checker_requirement_refs,
                required_claim_refs=profile.basis.claim_refs,
                required_applicability_refs=profile.basis.applicability_refs,
                required_adoption_refs=profile.basis.adoption_refs,
                required_source_refs=profile.basis.source_refs,
                required_authority_refs=profile.basis.authority_refs,
            )
            for profile, _, target_kind in profiles_and_targets
        )
        stage_profile = StageRequirementProfile(
            profile_id="generic-architectural-invariant-stage",
            typology_id="caller-owned-typology",
            stage_id="spatial-invariants",
            branch=branch(),
            predecessor_state_digest=SHA_B,
            scope_digest=SHA_C,
            stage_subject_ref="deliverable:architectural-invariants",
            requirements=requirements,
        )
        receipts = tuple(
            checker(profile) for profile, checker, _ in profiles_and_targets
        )

        closure = compile_composite_stage_closure(
            stage_profile,
            subject_digest=SHA_B,
            check_receipts=receipts,
        )

        self.assertEqual(StageClosureStatus.SATISFIED, closure.status)
        self.assertEqual((), closure.findings)

    def test_unknown_receipt_keeps_stage_open_without_denominator_drift(self) -> None:
        profile = oriented_frame_profile()
        profile = replace(
            profile,
            observations=(
                replace(profile.observations[0], direction=None),
                profile.observations[1],
            ),
        )
        requirement = StageCheckRequirement(
            requirement_id=profile.check_id,
            checker_id=profile.CHECKER_ID,
            target_kind=RequirementTargetKind.PARAMETER,
            basis_mode=RequirementBasisMode.CLAIM_BOUND,
            denominator_refs=profile.checker_requirement_refs,
            required_claim_refs=profile.basis.claim_refs,
            required_applicability_refs=profile.basis.applicability_refs,
            required_adoption_refs=profile.basis.adoption_refs,
            required_source_refs=profile.basis.source_refs,
            required_authority_refs=profile.basis.authority_refs,
        )
        stage_profile = StageRequirementProfile(
            profile_id="generic-unknown-invariant-stage",
            typology_id="caller-owned-typology",
            stage_id="spatial-invariants",
            branch=branch(),
            predecessor_state_digest=SHA_B,
            scope_digest=SHA_C,
            stage_subject_ref="deliverable:architectural-invariants",
            requirements=(requirement,),
        )

        closure = compile_composite_stage_closure(
            stage_profile,
            subject_digest=SHA_B,
            check_receipts=(run_orientation(profile),),
        )

        self.assertEqual(StageClosureStatus.OPEN, closure.status)
        codes = {item.code for item in closure.findings}
        self.assertIn(StageClosureFindingCode.CHECK_UNKNOWN, codes)
        self.assertNotIn(StageClosureFindingCode.DENOMINATOR_MISMATCH, codes)


if __name__ == "__main__":
    unittest.main()
