"""Contract tests for generic exact material-binding verification."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archive.archflow.control.requirements import RequirementBasisMode, RequirementTargetKind, StageCheckRequirement, StageRequirementProfile
from archive.archflow.control.check_requirements import (
    material_binding_stage_requirement,
)
from archflow.state.stage_workflow import StageClosureStatus
from archive.archflow.control.stage_closure import compile_composite_stage_closure
from archflow.contracts.canonical import canonical_digest
from archive.archflow.materials.binding import (
    MaterialBindingObservation,
    MaterialBindingProfile,
    MaterialBindingRequirement,
    MaterialBindingResolution,
    MaterialBindingSnapshot,
    validate_material_bindings,
)
from archive.archflow.materials.ledger import MaterialIntent, MaterialLedger
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.validation.contracts import CheckStatus


SHA_A = "a" * 64
SHA_B = "b" * 64
STAGE_SUBJECT_DIGEST = "e" * 64


def branch(branch_id: str = "selected") -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="material-fixture",
            run_id="design-001",
            base=ProjectVersionRef("material-fixture", 3, SHA_A),
        ),
        branch_id=branch_id,
        epoch=2,
    )


def ledger(
    assignments: tuple[tuple[str, str], ...] | None = None,
) -> MaterialLedger:
    return MaterialLedger(
        intents=(
            MaterialIntent(
                material_id="metal",
                label="Metal",
                source_refs=("evidence:metal-specification",),
            ),
            MaterialIntent(
                material_id="stone",
                label="Stone",
                source_refs=("evidence:stone-specification",),
            ),
        ),
        assignments=(
            assignments
            if assignments is not None
            else (
                ("component-a", "stone"),
                ("component-b", "metal"),
            )
        ),
    )


def requirements() -> tuple[MaterialBindingRequirement, ...]:
    return (
        MaterialBindingRequirement(
            requirement_id="subject-b-material",
            semantic_subject_ref="semantic:subject-b",
            ledger_component_id="component-b",
            material_id="metal",
            material_intent_ref="material-intent:metal",
            geometry_object_refs=("geometry:object-b",),
        ),
        MaterialBindingRequirement(
            requirement_id="subject-a-material",
            semantic_subject_ref="semantic:subject-a",
            ledger_component_id="component-a",
            material_id="stone",
            material_intent_ref="material-intent:stone",
            geometry_object_refs=("geometry:object-a-2", "geometry:object-a-1"),
        ),
    )


def profile(
    material_ledger: MaterialLedger | None = None,
) -> MaterialBindingProfile:
    selected = material_ledger or ledger()
    return MaterialBindingProfile(
        profile_id="schematic-materials",
        branch=branch(),
        scope_digest=SHA_B,
        ledger_ref="material-ledger:schematic",
        ledger_digest=canonical_digest(selected.to_dict()),
        requirements=requirements(),
    )


def observations() -> tuple[MaterialBindingObservation, ...]:
    return (
        MaterialBindingObservation(
            semantic_subject_ref="semantic:subject-b",
            resolution=MaterialBindingResolution.RESOLVED,
            ledger_component_id="component-b",
            material_intent_ref="material-intent:metal",
            geometry_object_refs=("geometry:object-b",),
        ),
        MaterialBindingObservation(
            semantic_subject_ref="semantic:subject-a",
            resolution=MaterialBindingResolution.RESOLVED,
            ledger_component_id="component-a",
            material_intent_ref="material-intent:stone",
            geometry_object_refs=("geometry:object-a-2", "geometry:object-a-1"),
        ),
    )


def snapshot(
    selected_profile: MaterialBindingProfile | None = None,
    *,
    selected_observations: tuple[MaterialBindingObservation, ...] | None = None,
) -> MaterialBindingSnapshot:
    active = selected_profile or profile()
    return MaterialBindingSnapshot(
        branch=active.branch,
        scope_digest=active.scope_digest,
        profile_digest=active.profile_digest,
        ledger_ref=active.ledger_ref,
        ledger_digest=active.ledger_digest,
        observations=(
            observations()
            if selected_observations is None
            else selected_observations
        ),
        reported_summary_passed=True,
    )


def _validate(
    selected_profile: MaterialBindingProfile,
    material_ledger: MaterialLedger,
    selected_snapshot: MaterialBindingSnapshot,
):
    return validate_material_bindings(
        selected_profile,
        material_ledger,
        selected_snapshot,
        stage_subject_digest=STAGE_SUBJECT_DIGEST,
    )


class MaterialBindingContractTests(unittest.TestCase):
    def test_stage_requirement_is_derived_before_readback(self) -> None:
        selected_profile = profile()

        stage_requirement = material_binding_stage_requirement(
            selected_profile
        )

        self.assertEqual(
            selected_profile.check_denominator,
            stage_requirement.denominator_refs,
        )
        self.assertIs(
            stage_requirement.basis_mode,
            RequirementBasisMode.UNIVERSAL,
        )
        self.assertIs(
            stage_requirement.target_kind,
            RequirementTargetKind.MATERIAL,
        )

    def test_exact_binding_passes_with_explicit_denominator(self) -> None:
        material_ledger = ledger()
        selected_profile = profile(material_ledger)

        receipt = _validate(
            selected_profile,
            material_ledger,
            snapshot(selected_profile),
        )

        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertEqual(
            ("semantic:subject-a", "semantic:subject-b"),
            selected_profile.denominator_refs,
        )
        self.assertEqual(
            selected_profile.check_denominator,
            receipt.subject_refs,
        )
        self.assertEqual(receipt.subject_refs, receipt.coverage_denominator)
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertEqual(STAGE_SUBJECT_DIGEST, receipt.subject_digest)
        self.assertFalse(receipt.findings)
        measurements = {
            item.measurement_id: item.value for item in receipt.measurements
        }
        self.assertEqual(
            selected_profile.profile_digest,
            measurements["profile-digest"],
        )
        self.assertEqual(
            snapshot(selected_profile).snapshot_digest,
            measurements["input-digest"],
        )
        self.assertIs(receipt.to_dict()["canonical_write_authority"], False)

    def test_pass_receipt_satisfies_composite_stage_closure(self) -> None:
        material_ledger = ledger()
        selected_profile = profile(material_ledger)
        receipt = _validate(
            selected_profile,
            material_ledger,
            snapshot(selected_profile),
        )
        requirement = StageCheckRequirement(
            requirement_id=receipt.check_id,
            checker_id=receipt.checker_id,
            target_kind=RequirementTargetKind.MATERIAL,
            basis_mode=RequirementBasisMode.UNIVERSAL,
            denominator_refs=selected_profile.check_denominator,
        )
        stage_profile = StageRequirementProfile(
            profile_id="material-stage-closure",
            typology_id="generic-material-system",
            stage_id="material-stage",
            branch=selected_profile.branch,
            predecessor_state_digest=SHA_A,
            scope_digest=selected_profile.scope_digest,
            stage_subject_ref="deliverable:material-stage",
            requirements=(requirement,),
        )

        closure = compile_composite_stage_closure(
            stage_profile,
            subject_digest=STAGE_SUBJECT_DIGEST,
            check_receipts=(receipt,),
        )

        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        self.assertFalse(closure.findings)
        self.assertEqual(
            (
                receipt.claim_refs,
                receipt.applicability_refs,
                receipt.adoption_refs,
                receipt.source_refs,
                receipt.authority_refs,
            ),
            ((), (), (), (), ()),
        )

    def test_schema_roundtrip_and_input_order_are_deterministic(self) -> None:
        first_profile = profile()
        second_profile = replace(
            first_profile,
            requirements=tuple(reversed(first_profile.requirements)),
        )
        first_snapshot = snapshot(first_profile)
        second_snapshot = replace(
            first_snapshot,
            observations=tuple(reversed(first_snapshot.observations)),
        )

        self.assertEqual(first_profile, second_profile)
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(
            first_profile,
            MaterialBindingProfile.from_dict(first_profile.to_dict()),
        )
        self.assertEqual(
            first_snapshot,
            MaterialBindingSnapshot.from_dict(first_snapshot.to_dict()),
        )
        self.assertEqual("MaterialBindingProfile@1", first_profile.SCHEMA)
        self.assertEqual("MaterialBindingSnapshot@1", first_snapshot.SCHEMA)

    def test_reference_fixture_freezes_profile_and_receipt_digests(self) -> None:
        material_ledger = ledger()
        selected_profile = profile(material_ledger)
        selected_snapshot = snapshot(selected_profile)
        receipt = _validate(
            selected_profile,
            material_ledger,
            selected_snapshot,
        )

        self.assertEqual(
            "95c4cd95cddf24b91ae429e0de58d69d0214578e491de1515699fed142291ecb",
            selected_profile.profile_digest,
        )
        self.assertEqual(
            "1ba987548ba02a60fc7eb7dab1945d0169121cbd1283039a153c25e09d30bae6",
            selected_snapshot.snapshot_digest,
        )
        self.assertEqual(
            "8d4afdbc4ecab6d6548e8d6984f513e33c4377efb17bd5e194de689f49aee365",
            receipt.receipt_digest,
        )

    def test_unassigned_and_orphan_ledger_components_fail_closed(self) -> None:
        changed = ledger(
            assignments=(
                ("component-b", "metal"),
                ("component-orphan", "stone"),
            )
        )
        selected_profile = profile(changed)

        receipt = _validate(
            selected_profile,
            changed,
            snapshot(selected_profile),
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("unassigned-ledger-component", codes)
        self.assertIn("orphan-ledger-assignment", codes)

    def test_wrong_branch_and_digests_fail_even_with_pass_summary(self) -> None:
        selected_profile = profile()
        forged = replace(
            snapshot(selected_profile),
            branch=branch("other"),
            profile_digest="c" * 64,
            ledger_digest="d" * 64,
            reported_summary_passed=True,
        )

        receipt = _validate(
            selected_profile,
            ledger(),
            forged,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("branch-mismatch", codes)
        self.assertIn("profile-digest-mismatch", codes)
        self.assertIn("snapshot-ledger-digest-mismatch", codes)

    def test_unknown_binding_never_becomes_pass(self) -> None:
        selected_profile = profile()
        unknown = replace(
            observations()[0],
            resolution=MaterialBindingResolution.UNKNOWN,
            ledger_component_id=None,
            material_intent_ref=None,
            geometry_object_refs=(),
        )
        selected = (unknown, observations()[1])

        receipt = _validate(
            selected_profile,
            ledger(),
            snapshot(
                selected_profile,
                selected_observations=selected,
            ),
        )

        self.assertIs(receipt.status, CheckStatus.UNKNOWN)
        self.assertNotEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertIn(
            "unknown-material-binding",
            {item.code for item in receipt.findings},
        )

    def test_stage_subject_digest_is_a_required_keyword(self) -> None:
        selected_profile = profile()
        with self.assertRaises(TypeError):
            validate_material_bindings(  # type: ignore[call-arg]
                selected_profile,
                ledger(),
                snapshot(selected_profile),
            )

    def test_wrong_intent_or_geometry_refs_fail_exact_binding(self) -> None:
        selected_profile = profile()
        wrong = replace(
            observations()[0],
            material_intent_ref="material-intent:stone",
            geometry_object_refs=("geometry:other-object",),
        )

        receipt = _validate(
            selected_profile,
            ledger(),
            snapshot(
                selected_profile,
                selected_observations=(wrong, observations()[1]),
            ),
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("material-intent-ref-mismatch", codes)
        self.assertIn("geometry-object-refs-mismatch", codes)

    def test_duplicate_and_orphan_observations_fail_closed(self) -> None:
        selected_profile = profile()
        duplicate = observations()[0]
        orphan = replace(
            duplicate,
            semantic_subject_ref="semantic:orphan-subject",
        )

        receipt = _validate(
            selected_profile,
            ledger(),
            snapshot(
                selected_profile,
                selected_observations=(
                    *observations(),
                    duplicate,
                    orphan,
                ),
            ),
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("duplicate-binding-observation", codes)
        self.assertIn("orphan-binding-observation", codes)
        self.assertEqual(selected_profile.check_denominator, receipt.subject_refs)
        self.assertEqual(receipt.subject_refs, receipt.coverage_denominator)


if __name__ == "__main__":
    unittest.main()
