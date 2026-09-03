"""Contract tests for generic CAD readback and preview verification."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archive.archflow.control.check_requirements import cad_readback_stage_requirement
from archflow.control.stage_closure import (
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.validation.cad_readback import (
    CadBoundingBox,
    CadObjectReadback,
    CadObjectRequirement,
    CadPreviewProjection,
    CadReadbackProfile,
    CadReadbackSnapshot,
    CadUpAxis,
    validate_cad_readback,
)
from archflow.validation.contracts import CheckStatus


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
STAGE_SUBJECT_DIGEST = "e" * 64


def branch(branch_id: str = "selected") -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="cad-fixture",
            run_id="detail-001",
            base=ProjectVersionRef("cad-fixture", 5, SHA_A),
        ),
        branch_id=branch_id,
        epoch=4,
    )


def box(
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> CadBoundingBox:
    return CadBoundingBox(minimum=minimum, maximum=maximum)


def requirements() -> tuple[CadObjectRequirement, ...]:
    return (
        CadObjectRequirement(
            requirement_id="hosted-detail",
            object_ref="cad-object:detail-b",
            operation_ref="cad-operation:operation-b",
            required_layer_ref="cad-layer:details",
            required_attributes=(
                ("operation-id", "operation-b"),
                ("semantic-ref", "semantic:subject-b"),
            ),
            host_ref="cad-object:host-b",
            host_local_envelope=box((-2, -2, -2), (2, 2, 2)),
        ),
        CadObjectRequirement(
            requirement_id="primary-object",
            object_ref="cad-object:object-a",
            operation_ref="cad-operation:operation-a",
            required_layer_ref="cad-layer:primary",
            required_attributes=(
                ("operation-id", "operation-a"),
                ("semantic-ref", "semantic:subject-a"),
            ),
            predecessor_envelope=box((0, 0, 0), (10, 10, 10)),
        ),
    )


def profile() -> CadReadbackProfile:
    return CadReadbackProfile(
        profile_id="detail-readback",
        branch=branch(),
        stage_id="detail-stage",
        scope_digest=SHA_B,
        program_digest=SHA_C,
        length_unit="meter",
        up_axis=CadUpAxis.Z,
        required_layer_refs=("cad-layer:primary", "cad-layer:details"),
        object_requirements=requirements(),
    )


def objects() -> tuple[CadObjectReadback, ...]:
    return (
        CadObjectReadback(
            object_ref="cad-object:detail-b",
            operation_ref="cad-operation:operation-b",
            layer_ref="cad-layer:details",
            attributes=(
                ("operation-id", "operation-b"),
                ("semantic-ref", "semantic:subject-b"),
            ),
            host_ref="cad-object:host-b",
            host_local_bbox=box((-1, -1, -1), (1, 1, 1)),
        ),
        CadObjectReadback(
            object_ref="cad-object:object-a",
            operation_ref="cad-operation:operation-a",
            layer_ref="cad-layer:primary",
            attributes=(
                ("operation-id", "operation-a"),
                ("semantic-ref", "semantic:subject-a"),
            ),
            world_bbox=box((1, 1, 1), (9, 9, 9)),
        ),
    )


def snapshot(
    selected_profile: CadReadbackProfile | None = None,
    *,
    selected_objects: tuple[CadObjectReadback, ...] | None = None,
) -> CadReadbackSnapshot:
    active = selected_profile or profile()
    return CadReadbackSnapshot(
        project_id=active.branch.run.project_id,
        branch=active.branch,
        stage_id=active.stage_id,
        profile_digest=active.profile_digest,
        program_digest=active.program_digest,
        length_unit=active.length_unit,
        up_axis=active.up_axis,
        declared_layer_refs=("cad-layer:details", "cad-layer:primary"),
        operation_refs=(
            "cad-operation:operation-b",
            "cad-operation:operation-a",
        ),
        objects=objects() if selected_objects is None else selected_objects,
        reported_summary_passed=True,
    )


def _validate(
    selected_profile: CadReadbackProfile,
    selected_snapshot: CadReadbackSnapshot,
):
    return validate_cad_readback(
        selected_profile,
        selected_snapshot,
        stage_subject_digest=STAGE_SUBJECT_DIGEST,
    )


class CadReadbackContractTests(unittest.TestCase):
    def test_stage_requirement_is_derived_before_readback(self) -> None:
        selected_profile = profile()

        stage_requirement = cad_readback_stage_requirement(selected_profile)

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
            RequirementTargetKind.ARTIFACT,
        )

    def test_exact_readback_passes_all_contracts(self) -> None:
        selected_profile = profile()

        receipt = _validate(
            selected_profile,
            snapshot(selected_profile),
        )

        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertEqual(
            ("cad-binding:hosted-detail", "cad-binding:primary-object"),
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
        selected_snapshot = snapshot(selected_profile)
        measurements = {
            item.measurement_id: item.value for item in receipt.measurements
        }
        self.assertEqual(
            selected_profile.profile_digest,
            measurements["profile-digest"],
        )
        self.assertEqual(
            selected_snapshot.snapshot_digest,
            measurements["input-digest"],
        )
        self.assertIs(receipt.to_dict()["canonical_write_authority"], False)

    def test_pass_receipt_satisfies_composite_stage_closure(self) -> None:
        selected_profile = profile()
        receipt = _validate(
            selected_profile,
            snapshot(selected_profile),
        )
        requirement = StageCheckRequirement(
            requirement_id=receipt.check_id,
            checker_id=receipt.checker_id,
            target_kind=RequirementTargetKind.ARTIFACT,
            basis_mode=RequirementBasisMode.UNIVERSAL,
            denominator_refs=selected_profile.check_denominator,
        )
        stage_profile = StageRequirementProfile(
            profile_id="cad-stage-closure",
            typology_id="generic-cad-system",
            stage_id=selected_profile.stage_id,
            branch=selected_profile.branch,
            predecessor_state_digest=SHA_A,
            scope_digest=selected_profile.scope_digest,
            stage_subject_ref="deliverable:cad-stage",
            requirements=(requirement,),
        )

        closure = compile_composite_stage_closure(
            stage_profile,
            subject_digest=STAGE_SUBJECT_DIGEST,
            check_receipts=(receipt,),
        )

        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        self.assertFalse(closure.findings)

    def test_schema_roundtrip_and_input_order_are_deterministic(self) -> None:
        first_profile = profile()
        second_profile = replace(
            first_profile,
            required_layer_refs=tuple(
                reversed(first_profile.required_layer_refs)
            ),
            object_requirements=tuple(
                reversed(first_profile.object_requirements)
            ),
        )
        first_snapshot = snapshot(first_profile)
        second_snapshot = replace(
            first_snapshot,
            declared_layer_refs=tuple(
                reversed(first_snapshot.declared_layer_refs)
            ),
            operation_refs=tuple(reversed(first_snapshot.operation_refs)),
            objects=tuple(reversed(first_snapshot.objects)),
        )

        self.assertEqual(first_profile, second_profile)
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(
            first_profile,
            CadReadbackProfile.from_dict(first_profile.to_dict()),
        )
        self.assertEqual(
            first_snapshot,
            CadReadbackSnapshot.from_dict(first_snapshot.to_dict()),
        )

    def test_reference_fixture_freezes_profile_snapshot_and_receipt_digests(
        self,
    ) -> None:
        selected_profile = profile()
        selected_snapshot = snapshot(selected_profile)
        receipt = _validate(selected_profile, selected_snapshot)

        self.assertEqual(
            "29dc364fd94b67b96aac48240747145fb9699699b758921579ec0a6b954abe2a",
            selected_profile.profile_digest,
        )
        self.assertEqual(
            "a052192b0768b70681aa26fb17010f09bf19944cb873b5bd695aea16b196c3ed",
            selected_snapshot.snapshot_digest,
        )
        self.assertEqual(
            "e128650036a3b5aa3c5cf4e0705bf43747e9ad736690837a365fbf8bcf20dd59",
            receipt.receipt_digest,
        )

    def test_forged_summary_pass_cannot_hide_a_missing_object(self) -> None:
        selected_profile = profile()
        missing = tuple(
            item
            for item in objects()
            if item.object_ref != "cad-object:object-a"
        )

        receipt = _validate(
            selected_profile,
            snapshot(selected_profile, selected_objects=missing),
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn("missing-object", {item.code for item in receipt.findings})

    def test_missing_units_and_axis_are_unknown_not_pass(self) -> None:
        selected_profile = profile()
        incomplete = replace(
            snapshot(selected_profile),
            length_unit=None,
            up_axis=None,
            reported_summary_passed=True,
        )

        receipt = _validate(selected_profile, incomplete)

        self.assertIs(receipt.status, CheckStatus.UNKNOWN)
        codes = {item.code for item in receipt.findings}
        self.assertIn("missing-length-unit", codes)
        self.assertIn("missing-up-axis", codes)
        self.assertNotEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertNotIn(selected_profile.ref, receipt.covered_refs)

    def test_duplicate_object_and_operation_break_exact_bijection(self) -> None:
        selected_profile = profile()
        duplicated = replace(
            snapshot(
                selected_profile,
                selected_objects=(*objects(), objects()[0]),
            ),
            operation_refs=(
                "cad-operation:operation-a",
                "cad-operation:operation-b",
                "cad-operation:operation-b",
            ),
        )

        receipt = _validate(selected_profile, duplicated)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("duplicate-object", codes)
        self.assertIn("duplicate-operation-ref", codes)

    def test_swapped_object_operation_mapping_fails_bijection(self) -> None:
        selected_profile = profile()
        swapped = (
            replace(
                objects()[0],
                operation_ref="cad-operation:operation-a",
            ),
            replace(
                objects()[1],
                operation_ref="cad-operation:operation-b",
            ),
        )

        receipt = _validate(
            selected_profile,
            snapshot(selected_profile, selected_objects=swapped),
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "object-operation-mismatch",
            {item.code for item in receipt.findings},
        )

    def test_orphan_object_does_not_expand_closure_denominator(self) -> None:
        selected_profile = profile()
        orphan = CadObjectReadback(
            object_ref="cad-object:orphan",
            operation_ref="cad-operation:orphan",
            layer_ref="cad-layer:orphan",
            attributes=(),
            world_bbox=box((0, 0, 0), (1, 1, 1)),
        )
        selected_snapshot = replace(
            snapshot(
                selected_profile,
                selected_objects=(*objects(), orphan),
            ),
            operation_refs=(
                "cad-operation:operation-a",
                "cad-operation:operation-b",
                "cad-operation:orphan",
            ),
            declared_layer_refs=(
                "cad-layer:details",
                "cad-layer:orphan",
                "cad-layer:primary",
            ),
        )

        receipt = _validate(selected_profile, selected_snapshot)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn("orphan-object", {item.code for item in receipt.findings})
        self.assertEqual(selected_profile.check_denominator, receipt.subject_refs)
        self.assertEqual(receipt.subject_refs, receipt.coverage_denominator)

    def test_flying_world_and_host_local_bounds_fail_closed(self) -> None:
        selected_profile = profile()
        flying = (
            replace(
                objects()[0],
                host_local_bbox=box((50, 50, 50), (51, 51, 51)),
            ),
            replace(
                objects()[1],
                world_bbox=box((100, 100, 100), (101, 101, 101)),
            ),
        )

        receipt = _validate(
            selected_profile,
            snapshot(selected_profile, selected_objects=flying),
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("predecessor-envelope-exceeded", codes)
        self.assertIn("host-local-envelope-exceeded", codes)

    def test_required_layer_and_attributes_are_derived_from_objects(self) -> None:
        selected_profile = profile()
        wrong = replace(
            objects()[1],
            layer_ref="cad-layer:details",
            attributes=(("operation-id", "wrong-operation"),),
        )
        changed = replace(
            snapshot(
                selected_profile,
                selected_objects=(objects()[0], wrong),
            ),
            declared_layer_refs=("cad-layer:details",),
        )

        receipt = _validate(selected_profile, changed)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("missing-required-layer", codes)
        self.assertIn("object-layer-mismatch", codes)
        self.assertIn("missing-required-attribute", codes)
        self.assertIn("attribute-value-mismatch", codes)

    def test_branch_and_program_leakage_fail_closed(self) -> None:
        selected_profile = profile()
        leaked = replace(
            snapshot(selected_profile),
            project_id="another-project",
            branch=branch("other"),
            stage_id="another-stage",
            program_digest="d" * 64,
            reported_summary_passed=True,
        )

        receipt = _validate(selected_profile, leaked)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        codes = {item.code for item in receipt.findings}
        self.assertIn("project-id-mismatch", codes)
        self.assertIn("branch-mismatch", codes)
        self.assertIn("stage-id-mismatch", codes)
        self.assertIn("program-digest-mismatch", codes)

    def test_preview_is_read_only_and_cannot_substitute_for_readback(self) -> None:
        selected_profile = profile()
        preview = CadPreviewProjection(
            preview_id="detail-preview",
            projection_ref="preview:detail-stage",
            branch=selected_profile.branch,
            stage_id=selected_profile.stage_id,
            profile_digest=selected_profile.profile_digest,
            program_digest=selected_profile.program_digest,
            projected_object_refs=(
                "cad-object:object-a",
                "cad-object:detail-b",
            ),
        )

        payload = preview.to_dict()
        self.assertIs(payload["read_only_projection"], True)
        self.assertIs(payload["readback_authority"], False)
        self.assertIs(payload["closure_authority"], False)
        self.assertIs(payload["canonical_write_authority"], False)
        self.assertEqual(preview, CadPreviewProjection.from_dict(payload))
        with self.assertRaisesRegex(TypeError, "cannot substitute"):
            _validate(selected_profile, preview)  # type: ignore[arg-type]

    def test_stage_subject_digest_is_a_required_keyword(self) -> None:
        selected_profile = profile()
        with self.assertRaises(TypeError):
            validate_cad_readback(  # type: ignore[call-arg]
                selected_profile,
                snapshot(selected_profile),
            )


if __name__ == "__main__":
    unittest.main()
