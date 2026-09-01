"""Tests for the fail-closed vertical-circulation maturity contract."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from archflow.validation.vertical_circulation import (
    CirculationAabbNegativePrecheck,
    LandingOwnership,
    LandingRole,
    LandingWitness,
    VerticalCirculationAssemblyWitness,
    VerticalCirculationContract,
    VerticalCirculationCriteria,
    VerticalCirculationError,
    VerticalCirculationInterface,
    VerticalCirculationInterfaceBasis,
    VerticalCirculationInterfaceRole,
    VerticalCirculationMaturity,
    VerticalCirculationObjectBinding,
    VerticalCirculationUpAxis,
    WalkingSurfaceGeometryWitness,
    WalkingSurfaceAdjacency,
    WalkingSurfaceSample,
    WalkingSurfaceSampleRole,
    WalkingSurfaceWitnessKind,
    VERTICAL_CIRCULATION_HOST_CUT_CHECKER_ID,
    VERTICAL_CIRCULATION_LOAD_PATH_CHECKER_ID,
    VERTICAL_CIRCULATION_SUPPORT_CHECKER_ID,
    VERTICAL_CIRCULATION_UNDERPASS_CHECKER_ID,
    check_vertical_circulation,
    check_vertical_circulation_maturity,
)
from archflow.validation.vaulted_underpass import (
    UnderpassConstructionForm,
    VaultedUnderpassAssemblyContract,
    check_vaulted_underpass_assembly,
)
from tests.test_vaulted_underpass import passing_contract as passing_underpass_contract


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="vertical-circulation-fixture",
            run_id="run-004",
            base=ProjectVersionRef("vertical-circulation-fixture", 3, SHA_A),
        ),
        branch_id="candidate-a",
        epoch=4,
    )


def bindings() -> tuple[VerticalCirculationObjectBinding, ...]:
    return (
        VerticalCirculationObjectBinding(
            component_ref="component:stair-01",
            object_ref="cad-object:stair-flight",
            operation_ref="geometry-op:stair-flight",
        ),
        VerticalCirculationObjectBinding(
            component_ref="component:stair-01",
            object_ref="cad-object:stair-landings",
            operation_ref="geometry-op:stair-landings",
        ),
    )


def interface_host_bindings() -> tuple[VerticalCirculationObjectBinding, ...]:
    return (
        VerticalCirculationObjectBinding(
            component_ref="component:site-landing",
            object_ref="cad-object:site-landing",
            operation_ref="geometry-op:site-landing",
        ),
        VerticalCirculationObjectBinding(
            component_ref="component:floor-landing",
            object_ref="cad-object:floor-landing",
            operation_ref="geometry-op:floor-landing",
        ),
    )


def interface(role: VerticalCirculationInterfaceRole) -> VerticalCirculationInterface:
    if role is VerticalCirculationInterfaceRole.LOWER:
        suffix = "lower"
        level_ref = "level:site"
        datum = 0.0
        point = (0.0, 0.0, 0.0)
    else:
        suffix = "upper"
        level_ref = "level:floor-01"
        datum = 3.0
        point = (4.0, 0.0, 3.0)
    return VerticalCirculationInterface(
        role=role,
        relation_ref=f"architectural-relation:access-{suffix}",
        interface_ref=f"interface:stair-{suffix}",
        level_ref=level_ref,
        datum_fact_ref=f"state-fact:{suffix}-datum",
        datum=datum,
        point=point,
    )


def criteria() -> VerticalCirculationCriteria:
    return VerticalCirculationCriteria(
        endpoint_tolerance=0.05,
        min_clear_width=1.10,
        min_headroom=2.00,
        min_landing_depth=1.20,
        min_underpass_clearance=2.00,
        max_horizontal_step=1.10,
        max_vertical_step=0.80,
        max_surface_gap=0.01,
        evidence_refs=("evidence:adopted-stair-range",),
        adoption_refs=("adoption:stair-policy",),
    )


def sample(
    ordinal: int,
    point: tuple[float, float, float],
    *,
    role: WalkingSurfaceSampleRole,
    binding: VerticalCirculationObjectBinding | None = None,
    surface_ref: str | None = None,
    solver_tread_ref: str | None = None,
    interface_ref: str | None = None,
    clear_width: float | None = 1.20,
    headroom: float | None = 2.20,
) -> WalkingSurfaceSample:
    selected_binding = binding or bindings()[0]
    return WalkingSurfaceSample(
        ordinal=ordinal,
        role=role,
        sample_ref=f"surface-sample:path-{ordinal}",
        surface_ref=surface_ref or f"brep-face:stair-{ordinal}",
        component_ref=selected_binding.component_ref,
        object_ref=selected_binding.object_ref,
        operation_ref=selected_binding.operation_ref,
        point=point,
        clear_width=clear_width,
        headroom=headroom,
        clear_width_witness_ref=f"width-witness:path-{ordinal}",
        headroom_witness_ref=f"headroom-witness:path-{ordinal}",
        solver_tread_ref=solver_tread_ref,
        interface_ref=interface_ref,
    )


def landing(
    role: LandingRole,
    *,
    ownership: LandingOwnership = LandingOwnership.STAIR_ASSEMBLY,
    binding: VerticalCirculationObjectBinding | None = None,
) -> LandingWitness:
    if role is LandingRole.LOWER:
        suffix = "lower"
        point = (0.0, 0.0, 0.0)
    elif role is LandingRole.UPPER:
        suffix = "upper"
        point = (4.0, 0.0, 3.0)
    else:
        suffix = "middle"
        point = (2.0, 0.0, 1.5)
    selected_binding = binding or bindings()[1]
    return LandingWitness(
        role=role,
        ownership=ownership,
        landing_ref=f"landing:{suffix}",
        surface_ref=f"brep-face:landing-{suffix}",
        component_ref=selected_binding.component_ref,
        object_ref=selected_binding.object_ref,
        operation_ref=selected_binding.operation_ref,
        point=point,
        clear_depth=1.30,
        clear_width=1.20,
        clear_depth_witness_ref=f"depth-witness:landing-{suffix}",
        clear_width_witness_ref=f"width-witness:landing-{suffix}",
        interface_ref=(
            f"interface:stair-{suffix}"
            if role in {LandingRole.LOWER, LandingRole.UPPER}
            else None
        ),
    )


def adjacencies(
    samples: tuple[WalkingSurfaceSample, ...],
) -> tuple[WalkingSurfaceAdjacency, ...]:
    return tuple(
        WalkingSurfaceAdjacency(
            ordinal=ordinal,
            adjacency_ref=f"surface-adjacency:path-{ordinal}",
            from_sample_ref=previous.sample_ref,
            to_sample_ref=current.sample_ref,
            from_surface_ref=previous.surface_ref,
            to_surface_ref=current.surface_ref,
            surface_gap=0.0,
            gap_witness_ref=f"gap-witness:path-{ordinal}",
        )
        for ordinal, (previous, current) in enumerate(zip(samples, samples[1:]))
    )


def geometry_witness() -> WalkingSurfaceGeometryWitness:
    lower_landing = landing(LandingRole.LOWER)
    upper_landing = landing(LandingRole.UPPER)
    stair_samples = (
        sample(
            0,
            (0.0, 0.0, 0.0),
            role=WalkingSurfaceSampleRole.LOWER_INTERFACE,
            binding=bindings()[1],
            surface_ref=lower_landing.surface_ref,
            interface_ref="interface:stair-lower",
        ),
        sample(
            1,
            (1.0, 0.0, 0.75),
            role=WalkingSurfaceSampleRole.TREAD,
            solver_tread_ref="solver-tread:000",
        ),
        sample(
            2,
            (2.0, 0.0, 1.50),
            role=WalkingSurfaceSampleRole.TREAD,
            solver_tread_ref="solver-tread:001",
        ),
        sample(
            3,
            (3.0, 0.0, 2.25),
            role=WalkingSurfaceSampleRole.TREAD,
            solver_tread_ref="solver-tread:002",
        ),
        sample(
            4,
            (4.0, 0.0, 3.0),
            role=WalkingSurfaceSampleRole.UPPER_INTERFACE,
            binding=bindings()[1],
            surface_ref=upper_landing.surface_ref,
            interface_ref="interface:stair-upper",
        ),
    )
    return WalkingSurfaceGeometryWitness(
        witness_id="readback-stair-surfaces",
        branch=branch(),
        scope_digest=SHA_A,
        stage_id="stage-4",
        stage_subject_ref="stage-subject:stair-01",
        stage_subject_digest=SHA_E,
        design_state_digest=SHA_F,
        witness_kind=WalkingSurfaceWitnessKind.CAD_BREP_FACE,
        extraction_ref="readback-extraction:stair-surfaces",
        extraction_digest=SHA_F,
        artifact_sha256=SHA_B,
        program_digest=SHA_C,
        readback_digest=SHA_D,
        length_unit_ref="unit:metre",
        coordinate_frame_ref="frame:world-xyz",
        up_axis=VerticalCirculationUpAxis.Z,
        solver_result_digest=SHA_A,
        solver_tread_refs=(
            "solver-tread:000",
            "solver-tread:001",
            "solver-tread:002",
        ),
        object_bindings=bindings(),
        path_samples=stair_samples,
        surface_adjacencies=adjacencies(stair_samples),
        landings=(lower_landing, upper_landing),
    )


def aabb() -> CirculationAabbNegativePrecheck:
    return CirculationAabbNegativePrecheck(
        source_ref="readback-envelope:stair-01",
        component_ref="component:stair-01",
        minimum=(-0.5, -1.0, 0.0),
        maximum=(4.5, 1.0, 3.0),
    )


def assembly_check_receipt(
    label: str,
    checker_id: str,
    *,
    status: CheckStatus = CheckStatus.PASS,
    underpass_clearance: float | None = None,
    bind_surface: bool = True,
) -> CheckReceiptEnvelope:
    surface_denominator = {
        "walking-surface-witness:readback-stair-surfaces",
        "solver-tread:000",
        "solver-tread:001",
        "solver-tread:002",
        "landing:lower",
        "landing:upper",
        "surface-adjacency:path-0",
        "surface-adjacency:path-1",
        "surface-adjacency:path-2",
        "surface-adjacency:path-3",
        "brep-face:landing-lower",
        "brep-face:stair-1",
        "brep-face:stair-2",
        "brep-face:stair-3",
        "brep-face:landing-upper",
    }
    denominator = tuple(
        sorted(
            {
                f"{label}-path:stair-01",
                *(surface_denominator if bind_surface else ()),
            }
        )
    )
    subject_refs = {
        *denominator,
        "stage-subject:stair-01",
        "component:stair-01",
    }
    if bind_surface:
        subject_refs.update(surface_denominator)
    findings: tuple[CheckFinding, ...] = ()
    if status is CheckStatus.FAIL:
        findings = (
            CheckFinding(
                code=f"{label}-failed",
                severity=FindingSeverity.ERROR,
                message=f"{label} contradiction",
                subject_refs=denominator,
            ),
        )
    elif status is CheckStatus.UNKNOWN:
        findings = (
            CheckFinding(
                code=f"{label}-unknown",
                severity=FindingSeverity.UNKNOWN,
                message=f"{label} unresolved",
                subject_refs=denominator,
            ),
        )
    measurements: tuple[CheckMeasurement, ...] = ()
    if underpass_clearance is not None:
        measurements = (
            CheckMeasurement(
                measurement_id="underpass-clearance",
                subject_ref=denominator[0],
                name="underpass_clearance",
                value=underpass_clearance,
                unit_ref="unit:metre",
                evidence_refs=("evidence:underpass-readback",),
            ),
        )
    return CheckReceiptEnvelope(
        check_id=f"{label}-stair-01",
        checker_id=checker_id,
        checker_version="1.0.0",
        branch=branch(),
        scope_digest=SHA_A,
        subject_refs=tuple(sorted(subject_refs)),
        subject_digest=SHA_E,
        status=status,
        source_refs=(f"source:{label}-readback",),
        findings=findings,
        measurements=measurements,
        coverage_denominator=denominator,
        covered_refs=(denominator if status is CheckStatus.PASS else ()),
    )


def assembly_witness(
    *,
    support_status: CheckStatus | None = CheckStatus.PASS,
    underpass_applicable: bool | None = False,
    underpass_clearance: float | None = None,
    underpass_form: UnderpassConstructionForm = UnderpassConstructionForm.UNKNOWN,
    underpass_assembly_contract: VaultedUnderpassAssemblyContract | None = None,
    underpass_assembly_receipt: CheckReceiptEnvelope | None = None,
    bind_surface: bool = True,
) -> VerticalCirculationAssemblyWitness:
    return VerticalCirculationAssemblyWitness(
        witness_id="coordinated-stair-assembly",
        branch=branch(),
        scope_digest=SHA_A,
        stage_id="stage-4",
        stage_subject_ref="stage-subject:stair-01",
        stage_subject_digest=SHA_E,
        design_state_digest=SHA_F,
        artifact_sha256=SHA_B,
        program_digest=SHA_C,
        readback_digest=SHA_D,
        solver_result_digest=SHA_A,
        support_receipt=(
            None
            if support_status is None
            else assembly_check_receipt(
                "support",
                VERTICAL_CIRCULATION_SUPPORT_CHECKER_ID,
                status=support_status,
                bind_surface=bind_surface,
            )
        ),
        load_path_receipt=assembly_check_receipt(
            "load-path",
            VERTICAL_CIRCULATION_LOAD_PATH_CHECKER_ID,
            bind_surface=bind_surface,
        ),
        host_cut_receipt=assembly_check_receipt(
            "host-cut",
            VERTICAL_CIRCULATION_HOST_CUT_CHECKER_ID,
            bind_surface=bind_surface,
        ),
        underpass_applicable=underpass_applicable,
        underpass_applicability_ref="applicability:stair-underpass",
        underpass_receipt=(
            assembly_check_receipt(
                "underpass",
                VERTICAL_CIRCULATION_UNDERPASS_CHECKER_ID,
                underpass_clearance=underpass_clearance,
                bind_surface=bind_surface,
            )
            if underpass_applicable and underpass_clearance is not None
            else None
        ),
        underpass_form=underpass_form,
        underpass_form_evidence_refs=(
            underpass_assembly_contract.form_evidence_refs
            if underpass_assembly_contract is not None
            else (
                ("evidence:vaulted-underpass-form",)
                if underpass_form is UnderpassConstructionForm.VAULTED
                else ()
            )
        ),
        underpass_form_adoption_refs=(
            underpass_assembly_contract.form_adoption_refs
            if underpass_assembly_contract is not None
            else (
                ("adoption:vaulted-underpass-form",)
                if underpass_form is UnderpassConstructionForm.VAULTED
                else ()
            )
        ),
        underpass_assembly_contract=underpass_assembly_contract,
        underpass_assembly_receipt=underpass_assembly_receipt,
    )


_DEFAULT_AABB = object()


def contract(
    maturity: VerticalCirculationMaturity,
    *,
    geometry: WalkingSurfaceGeometryWitness | None = None,
    reservation_aabb: CirculationAabbNegativePrecheck | None | object = _DEFAULT_AABB,
    assembly: VerticalCirculationAssemblyWitness | None = None,
    adopted_criteria: VerticalCirculationCriteria | None = None,
    lower: VerticalCirculationInterface | None = None,
    upper: VerticalCirculationInterface | None = None,
    circulation_bindings: tuple[VerticalCirculationObjectBinding, ...] | None = None,
    interface_hosts: tuple[VerticalCirculationObjectBinding, ...] = (),
    lower_ownership: LandingOwnership = LandingOwnership.STAIR_ASSEMBLY,
    upper_ownership: LandingOwnership = LandingOwnership.STAIR_ASSEMBLY,
) -> VerticalCirculationContract:
    realized = maturity is not VerticalCirculationMaturity.RESERVATION
    return VerticalCirculationContract(
        contract_id="stair-01-stage-contract",
        branch=branch(),
        scope_digest=SHA_A,
        stage_id="stage-4",
        stage_subject_ref="stage-subject:stair-01",
        stage_subject_digest=SHA_E,
        design_state_digest=SHA_F,
        maturity=maturity,
        artifact_sha256=SHA_B if realized else None,
        program_digest=SHA_C if realized else None,
        readback_digest=SHA_D if realized else None,
        solver_result_digest=SHA_A,
        solver_tread_refs=(
            (
                "solver-tread:000",
                "solver-tread:001",
                "solver-tread:002",
            )
            if realized
            else ()
        ),
        component_refs=("component:stair-01",),
        object_bindings=(circulation_bindings or bindings()) if realized else (),
        interface_host_bindings=interface_hosts if realized else (),
        length_unit_ref="unit:metre",
        coordinate_frame_ref="frame:world-xyz",
        up_axis=VerticalCirculationUpAxis.Z,
        lower_interface=lower or interface(VerticalCirculationInterfaceRole.LOWER),
        upper_interface=upper or interface(VerticalCirculationInterfaceRole.UPPER),
        lower_landing_ownership=lower_ownership,
        upper_landing_ownership=upper_ownership,
        criteria=adopted_criteria or criteria(),
        geometry_witness=geometry,
        aabb_precheck=(
            aabb()
            if reservation_aabb is _DEFAULT_AABB
            else reservation_aabb
        ),
        assembly_witness=assembly,
        evidence_refs=("evidence:stage-subject-binding",),
    )


class VerticalCirculationTests(unittest.TestCase):
    def test_reservation_aabb_requires_exact_component_and_three_positive_extents(self) -> None:
        with self.assertRaisesRegex(VerticalCirculationError, "positive extent"):
            replace(aabb(), maximum=(4.5, -1.0, 3.0))
        with self.assertRaisesRegex(VerticalCirculationError, "AABB component_ref"):
            contract(
                VerticalCirculationMaturity.RESERVATION,
                reservation_aabb=replace(
                    aabb(),
                    component_ref="component:foreign-stair",
                ),
            )

    def test_contract_cannot_share_one_solver_denominator_across_stairs(self) -> None:
        with self.assertRaisesRegex(VerticalCirculationError, "exactly one component"):
            replace(
                contract(VerticalCirculationMaturity.RESERVATION),
                component_refs=(
                    "component:stair-01",
                    "component:stair-02",
                ),
            )

    def test_schema_roundtrip_and_no_authority(self) -> None:
        value = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=geometry_witness(),
            reservation_aabb=aabb(),
            assembly=assembly_witness(),
        )

        rebuilt = VerticalCirculationContract.from_dict(value.to_dict())
        receipt = check_vertical_circulation(rebuilt)

        self.assertEqual(rebuilt, value)
        self.assertEqual(receipt.status, CheckStatus.PASS)
        self.assertEqual(CheckReceiptEnvelope.from_dict(receipt.to_dict()), receipt)
        self.assertEqual(
            WalkingSurfaceGeometryWitness.from_dict(value.geometry_witness.to_dict()),
            value.geometry_witness,
        )
        self.assertEqual(
            VerticalCirculationAssemblyWitness.from_dict(
                value.assembly_witness.to_dict()
            ),
            value.assembly_witness,
        )

        def assert_no_authority(node: object) -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    if key.endswith("authority"):
                        self.assertIs(child, False)
                    assert_no_authority(child)
            elif isinstance(node, list):
                for child in node:
                    assert_no_authority(child)

        payload = value.to_dict()
        assert_no_authority(payload)
        payload["design_authority"] = True
        with self.assertRaises(VerticalCirculationError):
            VerticalCirculationContract.from_dict(payload)

        criterion_payload = criteria().to_dict()
        criterion_payload["verification_authority"] = True
        with self.assertRaises(VerticalCirculationError):
            VerticalCirculationCriteria.from_dict(criterion_payload)

    def test_endpoint_basis_rejects_rag_coordinates(self) -> None:
        payload = interface(VerticalCirculationInterfaceRole.LOWER).to_dict()
        payload["basis"] = "rag"

        with self.assertRaises(ValueError):
            VerticalCirculationInterface.from_dict(payload)

        self.assertIs(
            interface(VerticalCirculationInterfaceRole.LOWER).basis,
            VerticalCirculationInterfaceBasis.DESIGN_STATE_RELATION,
        )

    def test_reservation_aabb_touching_endpoints_never_passes_walkability(self) -> None:
        value = contract(
            VerticalCirculationMaturity.RESERVATION,
            reservation_aabb=aabb(),
        )

        receipt = check_vertical_circulation(value)

        self.assertEqual(receipt.status, CheckStatus.UNKNOWN)
        self.assertEqual(receipt.covered_refs, ())
        self.assertIn(
            "vertical-circulation-reservation-not-walkable",
            {item.code for item in receipt.findings},
        )

        maturity_receipt = check_vertical_circulation_maturity(
            value,
            required_maturity=VerticalCirculationMaturity.RESERVATION,
        )
        self.assertEqual(maturity_receipt.status, CheckStatus.PASS)
        self.assertEqual(maturity_receipt.subject_refs, (value.ref,))
        self.assertIn("not-walkable", maturity_receipt.findings[0].code)

    def test_aabb_alone_cannot_satisfy_resolved_path(self) -> None:
        value = contract(
            VerticalCirculationMaturity.RESOLVED_PATH,
            reservation_aabb=aabb(),
        )

        receipt = check_vertical_circulation(value)

        self.assertEqual(receipt.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "vertical-circulation-walking-surface-witness-unknown",
            {item.code for item in receipt.findings},
        )

    def test_geometry_derived_resolved_path_passes(self) -> None:
        value = contract(
            VerticalCirculationMaturity.RESOLVED_PATH,
            geometry=geometry_witness(),
            reservation_aabb=aabb(),
        )

        receipt = check_vertical_circulation(value)

        self.assertEqual(receipt.status, CheckStatus.PASS)
        self.assertEqual(receipt.coverage_denominator, value.surface_denominator_refs)
        self.assertEqual(receipt.covered_refs, value.surface_denominator_refs)
        self.assertEqual(receipt.branch, value.branch)
        self.assertEqual(receipt.scope_digest, value.scope_digest)
        self.assertEqual(receipt.subject_digest, value.stage_subject_digest)
        self.assertNotIn(
            "bbox",
            " ".join(item.name for item in receipt.measurements).lower(),
        )

    def test_adjoining_interface_landings_bind_to_hosts_without_becoming_stairs(self) -> None:
        stair_binding = (bindings()[0],)
        lower_host, upper_host = interface_host_bindings()
        base = geometry_witness()
        hosted_landings = tuple(
            replace(
                item,
                ownership=LandingOwnership.ADJOINING_INTERFACE,
                component_ref=(
                    lower_host.component_ref
                    if item.role is LandingRole.LOWER
                    else upper_host.component_ref
                ),
                object_ref=(
                    lower_host.object_ref
                    if item.role is LandingRole.LOWER
                    else upper_host.object_ref
                ),
                operation_ref=(
                    lower_host.operation_ref
                    if item.role is LandingRole.LOWER
                    else upper_host.operation_ref
                ),
            )
            for item in base.landings
        )
        hosted_by_role = {item.role: item for item in hosted_landings}
        hosted_samples = tuple(
            replace(
                item,
                component_ref=(
                    hosted_by_role[LandingRole.LOWER].component_ref
                    if item.role is WalkingSurfaceSampleRole.LOWER_INTERFACE
                    else hosted_by_role[LandingRole.UPPER].component_ref
                ),
                object_ref=(
                    hosted_by_role[LandingRole.LOWER].object_ref
                    if item.role is WalkingSurfaceSampleRole.LOWER_INTERFACE
                    else hosted_by_role[LandingRole.UPPER].object_ref
                ),
                operation_ref=(
                    hosted_by_role[LandingRole.LOWER].operation_ref
                    if item.role is WalkingSurfaceSampleRole.LOWER_INTERFACE
                    else hosted_by_role[LandingRole.UPPER].operation_ref
                ),
            )
            if item.role
            in {
                WalkingSurfaceSampleRole.LOWER_INTERFACE,
                WalkingSurfaceSampleRole.UPPER_INTERFACE,
            }
            else item
            for item in base.path_samples
        )
        hosted_witness = replace(
            base,
            object_bindings=(*stair_binding, lower_host, upper_host),
            path_samples=hosted_samples,
            surface_adjacencies=adjacencies(hosted_samples),
            landings=hosted_landings,
        )
        value = contract(
            VerticalCirculationMaturity.RESOLVED_PATH,
            geometry=hosted_witness,
            circulation_bindings=stair_binding,
            interface_hosts=(lower_host, upper_host),
            lower_ownership=LandingOwnership.ADJOINING_INTERFACE,
            upper_ownership=LandingOwnership.ADJOINING_INTERFACE,
        )

        receipt = check_vertical_circulation(value)

        self.assertEqual(receipt.status, CheckStatus.PASS)
        self.assertEqual(value.component_refs, ("component:stair-01",))
        self.assertNotIn(lower_host.component_ref, value.component_refs)
        self.assertEqual(
            VerticalCirculationContract.from_dict(value.to_dict()),
            value,
        )

    def test_unknown_criterion_or_surface_observation_is_unknown(self) -> None:
        base_geometry = geometry_witness()
        cases = {
            "criterion": contract(
                VerticalCirculationMaturity.RESOLVED_PATH,
                geometry=base_geometry,
                adopted_criteria=replace(criteria(), min_headroom=None),
            ),
            "surface-value": contract(
                VerticalCirculationMaturity.RESOLVED_PATH,
                geometry=replace(
                    base_geometry,
                    path_samples=tuple(
                        replace(item, headroom=None) if item.ordinal == 1 else item
                        for item in base_geometry.path_samples
                    ),
                ),
            ),
            "landing": contract(
                VerticalCirculationMaturity.RESOLVED_PATH,
                geometry=replace(
                    base_geometry,
                    landings=tuple(
                        item for item in base_geometry.landings if item.role is not LandingRole.UPPER
                    ),
                ),
            ),
        }

        for label, value in cases.items():
            with self.subTest(label=label):
                receipt = check_vertical_circulation(value)
                self.assertEqual(receipt.status, CheckStatus.UNKNOWN)
                self.assertEqual(receipt.covered_refs, ())

    def test_exact_artifact_program_readback_unit_frame_axis_and_binding_mismatch_fail(self) -> None:
        base_geometry = geometry_witness()
        foreign_binding = VerticalCirculationObjectBinding(
            component_ref="component:stair-01",
            object_ref="cad-object:foreign-stair",
            operation_ref="geometry-op:foreign-stair",
        )
        cases = {
            "branch": replace(
                base_geometry,
                branch=replace(base_geometry.branch, epoch=5),
            ),
            "scope": replace(base_geometry, scope_digest=SHA_F),
            "stage": replace(base_geometry, stage_id="stage-3"),
            "stage-subject-ref": replace(
                base_geometry, stage_subject_ref="stage-subject:foreign-stair"
            ),
            "stage-subject-digest": replace(
                base_geometry, stage_subject_digest=SHA_F
            ),
            "design-state": replace(base_geometry, design_state_digest=SHA_A),
            "artifact": replace(base_geometry, artifact_sha256=SHA_F),
            "program": replace(base_geometry, program_digest=SHA_F),
            "readback": replace(base_geometry, readback_digest=SHA_F),
            "unit": replace(base_geometry, length_unit_ref="unit:millimetre"),
            "coordinate-frame": replace(
                base_geometry, coordinate_frame_ref="frame:foreign-local"
            ),
            "up-axis": replace(base_geometry, up_axis=VerticalCirculationUpAxis.Y),
            "object-operation": replace(
                base_geometry, object_bindings=(foreign_binding,)
            ),
        }

        for label, geometry in cases.items():
            with self.subTest(label=label):
                receipt = check_vertical_circulation(
                    contract(VerticalCirculationMaturity.RESOLVED_PATH, geometry=geometry)
                )
                self.assertEqual(receipt.status, CheckStatus.FAIL)
                self.assertIn(
                    f"vertical-circulation-{label}-binding-contradiction",
                    {item.code for item in receipt.findings},
                )

    def test_interface_datum_and_negative_aabb_contradictions_fail(self) -> None:
        upper = replace(
            interface(VerticalCirculationInterfaceRole.UPPER),
            point=(4.0, 0.0, 4.0),
        )
        too_short_aabb = replace(aabb(), maximum=(4.5, 1.0, 2.5))
        cases = {
            "interface": contract(
                VerticalCirculationMaturity.RESERVATION,
                upper=upper,
            ),
            "aabb": contract(
                VerticalCirculationMaturity.RESERVATION,
                reservation_aabb=too_short_aabb,
            ),
        }

        for label, value in cases.items():
            with self.subTest(label=label):
                self.assertEqual(check_vertical_circulation(value).status, CheckStatus.FAIL)

    def test_width_headroom_landing_and_endpoint_contradictions_fail(self) -> None:
        base = geometry_witness()
        narrow_samples = tuple(
            replace(item, clear_width=0.8) if item.ordinal == 1 else item
            for item in base.path_samples
        )
        low_headroom_samples = tuple(
            replace(item, headroom=1.5) if item.ordinal == 1 else item
            for item in base.path_samples
        )
        shallow_landings = tuple(
            replace(item, clear_depth=0.5) if item.role is LandingRole.LOWER else item
            for item in base.landings
        )
        missed_endpoint_samples = tuple(
            replace(item, point=(10.0, 0.0, 3.0)) if item.ordinal == 2 else item
            for item in base.path_samples
        )
        cases = (
            replace(base, path_samples=narrow_samples),
            replace(base, path_samples=low_headroom_samples),
            replace(base, landings=shallow_landings),
            replace(base, path_samples=missed_endpoint_samples),
        )

        for geometry in cases:
            with self.subTest(geometry=geometry.witness_digest if hasattr(geometry, "witness_digest") else geometry.extraction_digest):
                receipt = check_vertical_circulation(
                    contract(VerticalCirculationMaturity.RESOLVED_PATH, geometry=geometry)
                )
                self.assertEqual(receipt.status, CheckStatus.FAIL)

    def test_assembly_requires_support_load_path_host_cut_and_underpass_resolution(self) -> None:
        missing = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=geometry_witness(),
        )
        partial = assembly_witness(support_status=None)
        failed = assembly_witness(support_status=CheckStatus.FAIL)

        self.assertEqual(check_vertical_circulation(missing).status, CheckStatus.UNKNOWN)
        self.assertEqual(
            check_vertical_circulation(
                contract(
                    VerticalCirculationMaturity.ASSEMBLY,
                    geometry=geometry_witness(),
                    assembly=partial,
                )
            ).status,
            CheckStatus.UNKNOWN,
        )
        self.assertEqual(
            check_vertical_circulation(
                contract(
                    VerticalCirculationMaturity.ASSEMBLY,
                    geometry=geometry_witness(),
                    assembly=failed,
                )
            ).status,
            CheckStatus.FAIL,
        )

    def test_complete_assembly_and_applicable_underpass(self) -> None:
        resolved_geometry = geometry_witness()
        complete = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(),
        )
        underpass_unknown = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(
                underpass_applicable=True,
                underpass_clearance=None,
            ),
        )
        underpass_low = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(
                underpass_applicable=True,
                underpass_clearance=1.5,
            ),
        )
        underpass_clear = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(
                underpass_applicable=True,
                underpass_clearance=2.1,
            ),
        )
        context_source = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(
                underpass_applicable=True,
                underpass_clearance=2.1,
                underpass_form=UnderpassConstructionForm.VAULTED,
            ),
        )
        context_refs = tuple(
            sorted(
                {
                    "applicability:stair-underpass",
                    *(
                        set(context_source.surface_denominator_refs)
                        - {context_source.ref}
                    ),
                }
            )
        )
        underpass_contract = passing_underpass_contract(
            selected_branch=branch(),
            scope_digest=SHA_A,
            stage_subject_digest=SHA_E,
            stage_id="stage-4",
            program_digest=SHA_C,
            context_refs=context_refs,
        )
        underpass_receipt = check_vaulted_underpass_assembly(
            underpass_contract
        )
        vaulted_underpass_clear = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(
                underpass_applicable=True,
                underpass_clearance=2.1,
                underpass_form=UnderpassConstructionForm.VAULTED,
                underpass_assembly_contract=underpass_contract,
                underpass_assembly_receipt=underpass_receipt,
            ),
        )
        irrelevant_context_contract = replace(
            underpass_contract,
            context_refs=tuple(
                sorted((*underpass_contract.context_refs, "context:irrelevant"))
            ),
        )
        irrelevant_context_value = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(
                underpass_applicable=True,
                underpass_clearance=2.1,
                underpass_form=UnderpassConstructionForm.VAULTED,
                underpass_assembly_contract=irrelevant_context_contract,
                underpass_assembly_receipt=check_vaulted_underpass_assembly(
                    irrelevant_context_contract
                ),
            ),
        )
        forged_receipt_value = contract(
            VerticalCirculationMaturity.ASSEMBLY,
            geometry=resolved_geometry,
            assembly=assembly_witness(
                underpass_applicable=True,
                underpass_clearance=2.1,
                underpass_form=UnderpassConstructionForm.VAULTED,
                underpass_assembly_contract=underpass_contract,
                underpass_assembly_receipt=replace(
                    underpass_receipt,
                    source_refs=tuple(
                        sorted((*underpass_receipt.source_refs, "evidence:forged"))
                    ),
                ),
            ),
        )

        self.assertEqual(check_vertical_circulation(complete).status, CheckStatus.PASS)
        self.assertEqual(
            check_vertical_circulation(underpass_unknown).status, CheckStatus.UNKNOWN
        )
        self.assertEqual(check_vertical_circulation(underpass_low).status, CheckStatus.FAIL)
        self.assertEqual(
            check_vertical_circulation(underpass_clear).status, CheckStatus.UNKNOWN
        )
        self.assertEqual(
            check_vertical_circulation(vaulted_underpass_clear).status,
            CheckStatus.PASS,
        )
        self.assertEqual(
            check_vertical_circulation(irrelevant_context_value).status,
            CheckStatus.FAIL,
        )
        self.assertEqual(
            check_vertical_circulation(forged_receipt_value).status,
            CheckStatus.FAIL,
        )

    def test_contract_rejects_non_one_to_one_object_operation_bindings(self) -> None:
        duplicate_object = replace(
            bindings()[1],
            object_ref=bindings()[0].object_ref,
        )

        with self.assertRaises(VerticalCirculationError):
            replace(
                contract(
                    VerticalCirculationMaturity.RESOLVED_PATH,
                    geometry=geometry_witness(),
                ),
                object_bindings=(bindings()[0], duplicate_object),
            )

        with self.assertRaisesRegex(
            VerticalCirculationError,
            "outside the circulation component denominator",
        ):
            replace(
                contract(
                    VerticalCirculationMaturity.RESOLVED_PATH,
                    geometry=geometry_witness(),
                ),
                interface_host_bindings=(bindings()[0],),
            )

    def test_adopted_criteria_require_evidence_and_adoption(self) -> None:
        with self.assertRaises(ValueError):
            replace(criteria(), evidence_refs=())
        with self.assertRaises(ValueError):
            replace(criteria(), adoption_refs=())

    def test_reservation_without_typed_envelope_stays_open(self) -> None:
        value = contract(
            VerticalCirculationMaturity.RESERVATION,
            reservation_aabb=None,
        )

        detailed = check_vertical_circulation(value)
        maturity = check_vertical_circulation_maturity(
            value,
            required_maturity=VerticalCirculationMaturity.RESERVATION,
        )

        self.assertEqual(detailed.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "vertical-circulation-reservation-envelope-witness-unknown",
            {item.code for item in detailed.findings},
        )
        self.assertEqual(maturity.status, CheckStatus.UNKNOWN)

    def test_endpoint_only_or_shrunken_solver_denominator_cannot_pass(self) -> None:
        base = geometry_witness()
        endpoint_samples = (base.path_samples[0], base.path_samples[-1])
        endpoint_only = replace(
            base,
            path_samples=tuple(
                replace(item, ordinal=ordinal)
                for ordinal, item in enumerate(endpoint_samples)
            ),
        )
        endpoint_only = replace(
            endpoint_only,
            surface_adjacencies=adjacencies(endpoint_only.path_samples),
        )
        shrunken_samples = tuple(
            replace(item, ordinal=ordinal)
            for ordinal, item in enumerate(
                item
                for item in base.path_samples
                if item.solver_tread_ref != "solver-tread:002"
            )
        )
        shrunken = replace(
            base,
            solver_tread_refs=("solver-tread:000", "solver-tread:001"),
            path_samples=shrunken_samples,
            surface_adjacencies=adjacencies(shrunken_samples),
        )

        endpoint_receipt = check_vertical_circulation(
            contract(
                VerticalCirculationMaturity.RESOLVED_PATH,
                geometry=endpoint_only,
            )
        )
        shrunken_receipt = check_vertical_circulation(
            contract(
                VerticalCirculationMaturity.RESOLVED_PATH,
                geometry=shrunken,
            )
        )

        self.assertNotEqual(endpoint_receipt.status, CheckStatus.PASS)
        self.assertIn(
            "vertical-circulation-solver-tread-coverage-unknown",
            {item.code for item in endpoint_receipt.findings},
        )
        self.assertEqual(shrunken_receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vertical-circulation-solver-tread-denominator-binding-contradiction",
            {item.code for item in shrunken_receipt.findings},
        )

    def test_missing_or_forged_surface_adjacency_cannot_pass(self) -> None:
        base = geometry_witness()
        missing = replace(base, surface_adjacencies=base.surface_adjacencies[:-1])
        forged = replace(
            base,
            surface_adjacencies=(
                replace(
                    base.surface_adjacencies[0],
                    to_sample_ref=base.path_samples[2].sample_ref,
                    to_surface_ref=base.path_samples[2].surface_ref,
                ),
                *base.surface_adjacencies[1:],
            ),
        )
        gapped = replace(
            base,
            surface_adjacencies=(
                replace(base.surface_adjacencies[0], surface_gap=0.25),
                *base.surface_adjacencies[1:],
            ),
        )

        missing_receipt = check_vertical_circulation(
            contract(VerticalCirculationMaturity.RESOLVED_PATH, geometry=missing)
        )
        forged_receipt = check_vertical_circulation(
            contract(VerticalCirculationMaturity.RESOLVED_PATH, geometry=forged)
        )
        gapped_receipt = check_vertical_circulation(
            contract(VerticalCirculationMaturity.RESOLVED_PATH, geometry=gapped)
        )

        self.assertEqual(missing_receipt.status, CheckStatus.UNKNOWN)
        self.assertEqual(forged_receipt.status, CheckStatus.FAIL)
        self.assertEqual(gapped_receipt.status, CheckStatus.FAIL)

    def test_self_reported_width_without_geometry_observation_stays_open(self) -> None:
        base = geometry_witness()
        samples = tuple(
            replace(item, clear_width_witness_ref=None)
            if item.ordinal == 2
            else item
            for item in base.path_samples
        )
        receipt = check_vertical_circulation(
            contract(
                VerticalCirculationMaturity.RESOLVED_PATH,
                geometry=replace(base, path_samples=samples),
            )
        )

        self.assertEqual(receipt.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "vertical-circulation-clear_width-derivation-witness-unknown",
            {item.code for item in receipt.findings},
        )

    def test_terminal_landing_ownership_mismatch_or_duplicate_fails(self) -> None:
        base = geometry_witness()
        lower = next(
            item for item in base.landings if item.role is LandingRole.LOWER
        )
        ownership_mismatch = replace(
            base,
            landings=tuple(
                replace(item, ownership=LandingOwnership.ADJOINING_INTERFACE)
                if item.role is LandingRole.LOWER
                else item
                for item in base.landings
            ),
        )
        duplicate = replace(
            base,
            landings=(
                *base.landings,
                replace(
                    lower,
                    landing_ref="landing:lower-duplicate",
                    surface_ref="brep-face:landing-lower-duplicate",
                ),
            ),
        )

        for witness in (ownership_mismatch, duplicate):
            with self.subTest(witness=witness.extraction_digest):
                self.assertEqual(
                    check_vertical_circulation(
                        contract(
                            VerticalCirculationMaturity.RESOLVED_PATH,
                            geometry=witness,
                        )
                    ).status,
                    CheckStatus.FAIL,
                )

    def test_assembly_replays_typed_receipts_and_rejects_wrong_scope(self) -> None:
        valid = assembly_witness()
        assert valid.support_receipt is not None
        wrong_checker = replace(
            valid,
            support_receipt=replace(
                valid.support_receipt,
                checker_id="unrelated-checker",
            ),
        )
        foreign_branch = replace(
            valid,
            support_receipt=replace(
                valid.support_receipt,
                branch=replace(branch(), epoch=5),
            ),
        )
        unbound = assembly_witness(bind_surface=False)
        unknown = assembly_witness(support_status=CheckStatus.UNKNOWN)

        for witness in (wrong_checker, foreign_branch, unbound):
            with self.subTest(witness=witness.ref):
                self.assertEqual(
                    check_vertical_circulation(
                        contract(
                            VerticalCirculationMaturity.ASSEMBLY,
                            geometry=geometry_witness(),
                            assembly=witness,
                        )
                    ).status,
                    CheckStatus.FAIL,
                )
        self.assertEqual(
            check_vertical_circulation(
                contract(
                    VerticalCirculationMaturity.ASSEMBLY,
                    geometry=geometry_witness(),
                    assembly=unknown,
                )
            ).status,
            CheckStatus.UNKNOWN,
        )

        payload = valid.to_dict()
        payload["support_passed"] = True
        with self.assertRaises(ValueError):
            VerticalCirculationAssemblyWitness.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
