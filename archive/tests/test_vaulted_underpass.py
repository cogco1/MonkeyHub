"""Regression tests for evidence-bound vaulted-underpass materialization."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import rhino3dm

from archflow.adapters.three_dm_inspector import ThreeDmInspection, inspect_three_dm
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.relations.realization import RELATION_REALIZATION_CHECKER_ID
from archive.archflow.validation.assembly import (
    AssemblyCoverageManifest,
    AssemblyObligationDisposition,
    AssemblyProfile,
    AssemblySubject,
    AssemblySubjectObligation,
    GeometryBoundsBasis,
    RelationshipKind,
    RelationshipRequirement,
)
from archive.archflow.validation.cad_readback import (
    CadBoundingBox,
    CadObjectReadback,
    CadObjectRequirement,
    CadReadbackProfile,
    CadReadbackSnapshot,
    CadUpAxis,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.interface_continuity import (
    InterfaceBoundarySegment,
    InterfaceBoundarySupportSet,
    check_interface_boundary_continuity,
)
from archive.archflow.validation.spatial import AABB
from archive.archflow.validation.vaulted_underpass import (
    UNDERPASS_ASSEMBLY_CHECKER_ID,
    UnderpassConstructionForm,
    VaultedUnderpassAssemblyContract,
    VaultedUnderpassInspectionBinding,
    VaultedUnderpassInterfaceBinding,
    VaultedUnderpassRole,
    VaultedUnderpassRoleBinding,
    check_vaulted_underpass_assembly,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="vaulted-underpass-fixture",
            run_id="run-001",
            base=ProjectVersionRef("vaulted-underpass-fixture", 0, SHA_A),
        ),
        branch_id="candidate",
        epoch=4,
    )


def role_bindings() -> tuple[VaultedUnderpassRoleBinding, ...]:
    return tuple(
        VaultedUnderpassRoleBinding(
            role=role,
            component_ref=f"component:{role.value}",
            program_object_ref=f"cad-object:{role.value}",
            producer_operation_ref=f"cad-operation:{role.value}",
            readback_object_ref=f"cad-object:{role.value}",
            readback_operation_ref=f"cad-operation:{role.value}",
            relation_binding_ref=f"relation-binding:{role.value}",
        )
        for role in VaultedUnderpassRole
    )


def relationship(
    requirement_id: str,
    kind: RelationshipKind,
    subject_refs: tuple[str, ...],
) -> RelationshipRequirement:
    return RelationshipRequirement(
        requirement_id=requirement_id,
        kind=kind,
        subject_refs=subject_refs,
        evidence_refs=(f"evidence:{requirement_id}",),
        authority_refs=("adoption:vaulted-underpass",),
    )


def role_bounds() -> dict[VaultedUnderpassRole, AABB]:
    return {
        VaultedUnderpassRole.LOAD_PATH_TERMINAL: AABB(
            minimum=(0.0, 0.0, 0.0), maximum=(6.0, 4.0, 1.0)
        ),
        VaultedUnderpassRole.LEFT_SUPPORT: AABB(
            minimum=(0.0, 0.0, 1.0), maximum=(1.0, 4.0, 3.0)
        ),
        VaultedUnderpassRole.RIGHT_SUPPORT: AABB(
            minimum=(5.0, 0.0, 1.0), maximum=(6.0, 4.0, 3.0)
        ),
        VaultedUnderpassRole.OVERHEAD_VAULT: AABB(
            minimum=(0.0, 0.0, 3.0), maximum=(6.0, 4.0, 4.0)
        ),
        VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE: AABB(
            minimum=(0.0, 0.0, 4.0), maximum=(6.0, 4.0, 5.0)
        ),
        VaultedUnderpassRole.HOST_OPENING: AABB(
            minimum=(1.0, 0.5, 1.0), maximum=(5.0, 3.5, 4.0)
        ),
    }


def assembly_profile(
    bindings: tuple[VaultedUnderpassRoleBinding, ...],
    *,
    stage_subject_digest: str = SHA_C,
) -> AssemblyProfile:
    by_role = {item.role: item for item in bindings}
    bounds = role_bounds()
    subjects = tuple(
        AssemblySubject(
            subject_ref=binding.ref,
            bounds=bounds[binding.role],
            bounds_basis=GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            geometry_ref=binding.readback_object_ref,
        )
        for binding in bindings
    )
    ref = {role: item.ref for role, item in by_role.items()}
    requirements = (
        relationship(
            "stair-on-vault",
            RelationshipKind.SUPPORT,
            (
                ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                ref[VaultedUnderpassRole.OVERHEAD_VAULT],
            ),
        ),
        relationship(
            "vault-on-left",
            RelationshipKind.SUPPORT,
            (
                ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                ref[VaultedUnderpassRole.LEFT_SUPPORT],
            ),
        ),
        relationship(
            "vault-on-right",
            RelationshipKind.SUPPORT,
            (
                ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                ref[VaultedUnderpassRole.RIGHT_SUPPORT],
            ),
        ),
        relationship(
            "left-on-terminal",
            RelationshipKind.SUPPORT,
            (
                ref[VaultedUnderpassRole.LEFT_SUPPORT],
                ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
            ),
        ),
        relationship(
            "right-on-terminal",
            RelationshipKind.SUPPORT,
            (
                ref[VaultedUnderpassRole.RIGHT_SUPPORT],
                ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
            ),
        ),
        relationship(
            "left-support-chain",
            RelationshipKind.VERTICAL_SUPPORT_CHAIN,
            (
                ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                ref[VaultedUnderpassRole.LEFT_SUPPORT],
                ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
            ),
        ),
        relationship(
            "right-support-chain",
            RelationshipKind.VERTICAL_SUPPORT_CHAIN,
            (
                ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                ref[VaultedUnderpassRole.RIGHT_SUPPORT],
                ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
            ),
        ),
        relationship(
            "stair-load-path",
            RelationshipKind.LOAD_PATH_TO_FOUNDATION,
            (
                ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
            ),
        ),
        relationship(
            "opening-clear",
            RelationshipKind.OPENING_CLEAR,
            (
                ref[VaultedUnderpassRole.HOST_OPENING],
                ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
            ),
        ),
    )
    obligation_specs = (
        (
            "terminal",
            VaultedUnderpassRole.LOAD_PATH_TERMINAL,
            RelationshipKind.SUPPORT,
            1,
            "left-on-terminal",
        ),
        (
            "left",
            VaultedUnderpassRole.LEFT_SUPPORT,
            RelationshipKind.SUPPORT,
            0,
            "left-on-terminal",
        ),
        (
            "right",
            VaultedUnderpassRole.RIGHT_SUPPORT,
            RelationshipKind.SUPPORT,
            0,
            "right-on-terminal",
        ),
        (
            "vault",
            VaultedUnderpassRole.OVERHEAD_VAULT,
            RelationshipKind.SUPPORT,
            0,
            "vault-on-left",
        ),
        (
            "stair",
            VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE,
            RelationshipKind.SUPPORT,
            0,
            "stair-on-vault",
        ),
        (
            "opening",
            VaultedUnderpassRole.HOST_OPENING,
            RelationshipKind.OPENING_CLEAR,
            0,
            "opening-clear",
        ),
    )
    obligations = tuple(
        AssemblySubjectObligation(
            obligation_id=f"underpass-{obligation_id}",
            role_id=role.value,
            subject_ref=ref[role],
            disposition=AssemblyObligationDisposition.REQUIRED,
            relationship_kind=kind,
            endpoint_index=endpoint_index,
            requirement_id=requirement_id,
            evidence_refs=(f"evidence:obligation-{obligation_id}",),
            authority_refs=("adoption:vaulted-underpass",),
        )
        for obligation_id, role, kind, endpoint_index, requirement_id in obligation_specs
    )
    return AssemblyProfile(
        profile_id="vaulted-underpass",
        subjects=subjects,
        requirements=requirements,
        coverage_manifest=AssemblyCoverageManifest(
            manifest_id="vaulted-underpass-coverage",
            stage_subject_refs=tuple(item.subject_ref for item in subjects),
            stage_subject_source_digest=stage_subject_digest,
            obligations=obligations,
        ),
    )


def cad_readback(
    bindings: tuple[VaultedUnderpassRoleBinding, ...],
    *,
    selected_branch: BranchRef,
    scope_digest: str,
    stage_id: str,
    program_digest: str,
) -> tuple[CadReadbackProfile, CadReadbackSnapshot]:
    layer_ref = "cad-layer:vaulted-underpass"
    bounds = role_bounds()
    envelope = CadBoundingBox(
        minimum=(-100.0, -100.0, -100.0),
        maximum=(100.0, 100.0, 100.0),
    )
    requirements = tuple(
        CadObjectRequirement(
            requirement_id=f"underpass-{binding.role.value}",
            object_ref=binding.program_object_ref,
            operation_ref=binding.producer_operation_ref,
            required_layer_ref=layer_ref,
            required_attributes=(("semantic-role", binding.role.value),),
            predecessor_envelope=envelope,
        )
        for binding in bindings
    )
    profile = CadReadbackProfile(
        profile_id="vaulted-underpass-readback",
        branch=selected_branch,
        stage_id=stage_id,
        scope_digest=scope_digest,
        program_digest=program_digest,
        length_unit="meter",
        up_axis=CadUpAxis.Z,
        required_layer_refs=(layer_ref,),
        object_requirements=requirements,
    )
    snapshot = CadReadbackSnapshot(
        project_id=selected_branch.run.project_id,
        branch=selected_branch,
        stage_id=stage_id,
        profile_digest=profile.profile_digest,
        program_digest=program_digest,
        length_unit="meter",
        up_axis=CadUpAxis.Z,
        declared_layer_refs=(layer_ref,),
        operation_refs=tuple(
            sorted(binding.readback_operation_ref for binding in bindings)
        ),
        objects=tuple(
            CadObjectReadback(
                object_ref=binding.readback_object_ref,
                operation_ref=binding.readback_operation_ref,
                layer_ref=layer_ref,
                attributes=(("semantic-role", binding.role.value),),
                world_bbox=CadBoundingBox(
                    minimum=bounds[binding.role].minimum,
                    maximum=bounds[binding.role].maximum,
                ),
            )
            for binding in bindings
        ),
        reported_summary_passed=True,
    )
    return profile, snapshot


def _box_mesh(bounds: AABB) -> rhino3dm.Mesh:
    mesh = rhino3dm.Mesh()
    low = bounds.minimum
    high = bounds.maximum
    for x, y, z in (
        (low[0], low[1], low[2]),
        (high[0], low[1], low[2]),
        (high[0], high[1], low[2]),
        (low[0], high[1], low[2]),
        (low[0], low[1], high[2]),
        (high[0], low[1], high[2]),
        (high[0], high[1], high[2]),
        (low[0], high[1], high[2]),
    ):
        mesh.Vertices.Add(x, y, z)
    for face in (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ):
        mesh.Faces.AddFace(*face)
    return mesh


@lru_cache(maxsize=1)
def real_underpass_inspection() -> ThreeDmInspection:
    """Create and inspect an actual immutable six-role 3DM test artifact."""

    bindings = role_bindings()
    bounds = role_bounds()
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "vaulted-underpass.3dm"
        model = rhino3dm.File3dm()
        model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
        layer = rhino3dm.Layer()
        layer.Name = "cad-layer:vaulted-underpass"
        layer_index = model.Layers.Add(layer)
        for binding in bindings:
            attributes = rhino3dm.ObjectAttributes()
            attributes.Name = f"underpass-{binding.role.value}"
            attributes.LayerIndex = layer_index
            attributes.SetUserString(
                "archflow:object_ref",
                binding.readback_object_ref,
            )
            attributes.SetUserString(
                "archflow:operation_ref",
                binding.readback_operation_ref,
            )
            attributes.SetUserString("semantic-role", binding.role.value)
            model.Objects.AddMesh(_box_mesh(bounds[binding.role]), attributes)
        if not model.Write(str(source), 8):
            raise RuntimeError("failed to write vaulted-underpass test 3dm")
        return inspect_three_dm(source)


def inspection_binding() -> VaultedUnderpassInspectionBinding:
    inspection = real_underpass_inspection()
    return VaultedUnderpassInspectionBinding(
        artifact_ref="artifact:temporary-vaulted-underpass-3dm",
        model_sha256=inspection.file_sha256,
        inspection=inspection,
    )


def relation_receipt(
    bindings: tuple[VaultedUnderpassRoleBinding, ...],
    *,
    selected_branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    denominator = tuple(
        sorted(
            {
                ref
                for binding in bindings
                for ref in (
                    binding.relation_binding_ref,
                    binding.readback_object_ref,
                    binding.readback_operation_ref,
                )
            }
        )
    )
    return CheckReceiptEnvelope(
        check_id="vaulted-underpass-relations",
        checker_id=RELATION_REALIZATION_CHECKER_ID,
        checker_version="1.0.0",
        branch=selected_branch,
        scope_digest=scope_digest,
        subject_refs=denominator,
        subject_digest=stage_subject_digest,
        status=CheckStatus.PASS,
        source_refs=("evidence:relation-readback",),
        coverage_denominator=denominator,
        covered_refs=denominator,
    )


def interface_binding(
    by_role: dict[VaultedUnderpassRole, VaultedUnderpassRoleBinding],
    support_role: VaultedUnderpassRole,
    *,
    selected_branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> VaultedUnderpassInterfaceBinding:
    required_ref = f"interface:vault-{support_role.value}"
    supporting_ref = f"interface:{support_role.value}-vault"
    boundary_x = (
        0.5
        if support_role is VaultedUnderpassRole.LEFT_SUPPORT
        else 5.5
    )
    boundary_z = 3.0
    check_id = f"vault-{support_role.value}"
    required_segments = (
        InterfaceBoundarySegment(
            required_ref,
            (boundary_x, 0.0, boundary_z),
            (boundary_x, 4.0, boundary_z),
        ),
    )
    supporting_segments = (
        InterfaceBoundarySegment(
            supporting_ref,
            (boundary_x, 0.0, boundary_z),
            (boundary_x, 4.0, boundary_z),
        ),
    )
    support_sets = (
        InterfaceBoundarySupportSet(required_ref, (supporting_ref,)),
    )
    evidence_refs = tuple(
        sorted(
            (
                by_role[VaultedUnderpassRole.OVERHEAD_VAULT].ref,
                by_role[support_role].ref,
            )
        )
    )
    authority_refs = ("adoption:vaulted-underpass",)
    binding_refs = tuple(
        sorted(
            (
                by_role[VaultedUnderpassRole.OVERHEAD_VAULT].readback_object_ref,
                by_role[VaultedUnderpassRole.OVERHEAD_VAULT].readback_operation_ref,
                by_role[support_role].readback_object_ref,
                by_role[support_role].readback_operation_ref,
            )
        )
    )
    receipt = check_interface_boundary_continuity(
        check_id=check_id,
        branch=selected_branch,
        scope_digest=scope_digest,
        required_segments=required_segments,
        supporting_segments=supporting_segments,
        support_sets=support_sets,
        expected_required_refs=(required_ref,),
        tolerance=0.001,
        evidence_refs=evidence_refs,
        authority_refs=authority_refs,
        stage_subject_digest=stage_subject_digest,
        binding_refs=binding_refs,
    )
    return VaultedUnderpassInterfaceBinding(
        support_role=support_role,
        check_id=check_id,
        vault_boundary_ref=required_ref,
        support_boundary_ref=supporting_ref,
        required_segments=required_segments,
        supporting_segments=supporting_segments,
        support_sets=support_sets,
        expected_required_refs=(required_ref,),
        tolerance=0.001,
        evidence_refs=evidence_refs,
        authority_refs=authority_refs,
        receipt=receipt,
    )


def passing_contract(
    *,
    selected_branch: BranchRef | None = None,
    scope_digest: str = SHA_B,
    stage_subject_digest: str = SHA_C,
    stage_id: str = "stage-4",
    program_digest: str = SHA_A,
    context_refs: tuple[str, ...] = (),
) -> VaultedUnderpassAssemblyContract:
    active_branch = selected_branch or branch()
    bindings = role_bindings()
    by_role = {item.role: item for item in bindings}
    readback_profile, readback_snapshot = cad_readback(
        bindings,
        selected_branch=active_branch,
        scope_digest=scope_digest,
        stage_id=stage_id,
        program_digest=program_digest,
    )
    return VaultedUnderpassAssemblyContract(
        contract_id="passage-001",
        branch=active_branch,
        scope_digest=scope_digest,
        stage_subject_digest=stage_subject_digest,
        construction_form=UnderpassConstructionForm.VAULTED,
        form_evidence_refs=("evidence:vaulted-section",),
        form_adoption_refs=("adoption:vaulted-underpass",),
        context_refs=context_refs,
        role_bindings=bindings,
        cad_readback_profile=readback_profile,
        cad_readback_snapshot=readback_snapshot,
        inspection_binding=inspection_binding(),
        assembly_profile=assembly_profile(
            bindings,
            stage_subject_digest=stage_subject_digest,
        ),
        relation_realization_receipt=relation_receipt(
            bindings,
            selected_branch=active_branch,
            scope_digest=scope_digest,
            stage_subject_digest=stage_subject_digest,
        ),
        interface_bindings=tuple(
            interface_binding(
                by_role,
                role,
                selected_branch=active_branch,
                scope_digest=scope_digest,
                stage_subject_digest=stage_subject_digest,
            )
            for role in (
                VaultedUnderpassRole.LEFT_SUPPORT,
                VaultedUnderpassRole.RIGHT_SUPPORT,
            )
        ),
    )


class VaultedUnderpassValidationTests(unittest.TestCase):
    def test_empty_geometry_denominator_fails_selected_vault(self) -> None:
        contract = VaultedUnderpassAssemblyContract(
            contract_id="empty-vault",
            branch=branch(),
            scope_digest=SHA_B,
            stage_subject_digest=SHA_C,
            construction_form=UnderpassConstructionForm.VAULTED,
            form_evidence_refs=("evidence:vaulted-section",),
            form_adoption_refs=("adoption:vaulted-underpass",),
        )

        receipt = check_vaulted_underpass_assembly(contract)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertEqual(
            6,
            sum(
                item.code == "vaulted-underpass-role-missing"
                for item in receipt.findings
            ),
        )

    def test_missing_side_support_fails_even_with_other_witnesses(self) -> None:
        contract = passing_contract()
        retained = tuple(
            item
            for item in contract.role_bindings
            if item.role is not VaultedUnderpassRole.RIGHT_SUPPORT
        )

        receipt = check_vaulted_underpass_assembly(
            replace(
                contract,
                role_bindings=retained,
                assembly_profile=None,
                relation_realization_receipt=None,
                interface_bindings=(),
            )
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-role-missing",
            {item.code for item in receipt.findings},
        )

    def test_complete_vault_passes_and_round_trips(self) -> None:
        contract = passing_contract()

        receipt = check_vaulted_underpass_assembly(contract)

        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertEqual(receipt.checker_id, UNDERPASS_ASSEMBLY_CHECKER_ID)
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertEqual(
            VaultedUnderpassAssemblyContract.from_dict(contract.to_dict()),
            contract,
        )
        self.assertEqual(CheckReceiptEnvelope.from_dict(receipt.to_dict()), receipt)

    def test_handwritten_readback_without_real_3dm_inspection_cannot_pass(self) -> None:
        contract = passing_contract()

        receipt = check_vaulted_underpass_assembly(
            replace(contract, inspection_binding=None)
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-3dm-inspection-missing",
            {item.code for item in receipt.findings},
        )

    def test_wrong_artifact_sha_cannot_pass_real_3dm_inspection(self) -> None:
        contract = passing_contract()
        assert contract.inspection_binding is not None

        receipt = check_vaulted_underpass_assembly(
            replace(
                contract,
                inspection_binding=replace(
                    contract.inspection_binding,
                    model_sha256="d" * 64,
                ),
            )
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-3dm-artifact-sha-contradiction",
            {item.code for item in receipt.findings},
        )

    def test_missing_vault_support_interface_stays_unknown(self) -> None:
        contract = passing_contract()

        receipt = check_vaulted_underpass_assembly(
            replace(contract, interface_bindings=contract.interface_bindings[:1])
        )

        self.assertIs(receipt.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "vaulted-underpass-interface-witness-unknown",
            {item.code for item in receipt.findings},
        )

    def test_irrelevant_interface_binding_tokens_cannot_pass(self) -> None:
        contract = passing_contract()
        interface = contract.interface_bindings[0]
        bad_receipt = check_interface_boundary_continuity(
            check_id="irrelevant-interface-binding",
            branch=contract.branch,
            scope_digest=contract.scope_digest,
            required_segments=(
                InterfaceBoundarySegment(
                    interface.vault_boundary_ref,
                    (0.0, 0.0, 0.0),
                    (0.0, 4.0, 0.0),
                ),
            ),
            supporting_segments=(
                InterfaceBoundarySegment(
                    interface.support_boundary_ref,
                    (0.0, 0.0, 0.0),
                    (0.0, 4.0, 0.0),
                ),
            ),
            support_sets=(
                InterfaceBoundarySupportSet(
                    interface.vault_boundary_ref,
                    (interface.support_boundary_ref,),
                ),
            ),
            expected_required_refs=(interface.vault_boundary_ref,),
            tolerance=0.001,
            evidence_refs=interface.receipt.source_refs,
            authority_refs=("adoption:vaulted-underpass",),
            stage_subject_digest=contract.stage_subject_digest,
            binding_refs=("readback:irrelevant-a", "readback:irrelevant-b"),
        )
        bad_binding = replace(interface, receipt=bad_receipt)

        receipt = check_vaulted_underpass_assembly(
            replace(
                contract,
                interface_bindings=(
                    bad_binding,
                    *contract.interface_bindings[1:],
                ),
            )
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-interface-denominator-contradiction",
            {item.code for item in receipt.findings},
        )

    def test_unscoped_interface_receipt_cannot_pass(self) -> None:
        contract = passing_contract()
        interface = contract.interface_bindings[0]
        bad_receipt = check_interface_boundary_continuity(
            check_id="unscoped-interface",
            branch=contract.branch,
            scope_digest=contract.scope_digest,
            required_segments=(
                InterfaceBoundarySegment(
                    interface.vault_boundary_ref,
                    (0.0, 0.0, 0.0),
                    (0.0, 4.0, 0.0),
                ),
            ),
            supporting_segments=(
                InterfaceBoundarySegment(
                    interface.support_boundary_ref,
                    (0.0, 0.0, 0.0),
                    (0.0, 4.0, 0.0),
                ),
            ),
            support_sets=(
                InterfaceBoundarySupportSet(
                    interface.vault_boundary_ref,
                    (interface.support_boundary_ref,),
                ),
            ),
            expected_required_refs=(interface.vault_boundary_ref,),
            tolerance=0.001,
            evidence_refs=interface.receipt.source_refs,
            authority_refs=("adoption:vaulted-underpass",),
        )

        receipt = check_vaulted_underpass_assembly(
            replace(
                contract,
                interface_bindings=(
                    replace(interface, receipt=bad_receipt),
                    *contract.interface_bindings[1:],
                ),
            )
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-interface-left_support-subject-contradiction",
            {item.code for item in receipt.findings},
        )

    def test_copied_interface_receipt_cannot_replace_raw_boundary_replay(self) -> None:
        contract = passing_contract()
        left, right = contract.interface_bindings

        receipt = check_vaulted_underpass_assembly(
            replace(
                contract,
                interface_bindings=(replace(left, receipt=right.receipt), right),
            )
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-interface-receipt-contradiction",
            {item.code for item in receipt.findings},
        )

    def test_continuous_interface_outside_inspected_role_geometry_fails(
        self,
    ) -> None:
        contract = passing_contract()
        left = contract.interface_bindings[0]
        by_role = contract.role_bindings_by_role
        displaced_required = (
            InterfaceBoundarySegment(
                left.vault_boundary_ref,
                (999.0, 0.0, 3.0),
                (999.0, 4.0, 3.0),
            ),
        )
        displaced_supporting = (
            InterfaceBoundarySegment(
                left.support_boundary_ref,
                (999.0, 0.0, 3.0),
                (999.0, 4.0, 3.0),
            ),
        )
        binding_refs = tuple(
            sorted(
                (
                    by_role[
                        VaultedUnderpassRole.OVERHEAD_VAULT
                    ].readback_object_ref,
                    by_role[
                        VaultedUnderpassRole.OVERHEAD_VAULT
                    ].readback_operation_ref,
                    by_role[
                        VaultedUnderpassRole.LEFT_SUPPORT
                    ].readback_object_ref,
                    by_role[
                        VaultedUnderpassRole.LEFT_SUPPORT
                    ].readback_operation_ref,
                )
            )
        )
        displaced_receipt = check_interface_boundary_continuity(
            check_id=left.check_id,
            branch=contract.branch,
            scope_digest=contract.scope_digest,
            required_segments=displaced_required,
            supporting_segments=displaced_supporting,
            support_sets=left.support_sets,
            expected_required_refs=left.expected_required_refs,
            tolerance=left.tolerance,
            evidence_refs=left.evidence_refs,
            authority_refs=left.authority_refs,
            stage_subject_digest=contract.stage_subject_digest,
            binding_refs=binding_refs,
        )
        self.assertIs(displaced_receipt.status, CheckStatus.PASS)
        displaced_binding = replace(
            left,
            required_segments=displaced_required,
            supporting_segments=displaced_supporting,
            receipt=displaced_receipt,
        )

        receipt = check_vaulted_underpass_assembly(
            replace(
                contract,
                interface_bindings=(
                    displaced_binding,
                    contract.interface_bindings[1],
                ),
            )
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-interface-geometry-contradiction",
            {item.code for item in receipt.findings},
        )

    def test_reported_pass_cannot_hide_fake_role_readback(self) -> None:
        contract = passing_contract()
        snapshot = contract.cad_readback_snapshot
        assert snapshot is not None
        altered_objects = (
            replace(
                snapshot.objects[0],
                operation_ref="cad-operation:fake-role-readback",
            ),
            *snapshot.objects[1:],
        )
        altered_snapshot = replace(
            snapshot,
            operation_refs=tuple(
                sorted(item.operation_ref for item in altered_objects)
            ),
            objects=altered_objects,
            reported_summary_passed=True,
        )

        receipt = check_vaulted_underpass_assembly(
            replace(contract, cad_readback_snapshot=altered_snapshot)
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-underpass-cad-readback-failed",
            {item.code for item in receipt.findings},
        )


if __name__ == "__main__":
    unittest.main()
