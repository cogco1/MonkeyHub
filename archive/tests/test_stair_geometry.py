from __future__ import annotations

import inspect
import json
from dataclasses import replace
import unittest

from archive.archflow.capabilities.stair_geometry import (
    StairDesignBinding,
    StairGeometryError,
    StairInterfaceDesignBinding,
    StairMaterializationGate,
    StairMaterializationMaturity,
    StairObligationDisposition,
    StairObligationResolution,
    StairPlacement,
    StairPlanEnvelopeDesignBinding,
    StairRealizationProfile,
    compile_stair_geometry_proposal,
)
from archive.archflow.capabilities.stair_solver import (
    StairDimensionBand,
    StairDimensionValue,
    StairFlightConstraint,
    StairInterface,
    StairInterfaceRole,
    StairLayout,
    StairObligationKind,
    StairPlanEnvelope,
    StairPreferenceOrder,
    StairRiserTreadRule,
    StairRiserTreadRuleMode,
    StairSolveRequest,
    StairSolveStatus,
    StairTerminalLandingOwnership,
    solve_stair,
)
from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import BranchRef, RunRef
from archive.archflow.realization.sandbox import RealizationStatus, realize_geometry
from archflow.runtime.geometry_compiler import (
    GeometryCompileStatus,
    compile_geometry_program,
)
from archflow.state.geometry_program import GeometryTolerance, LengthUnit
from archflow.state.developed_design import (
    DevelopedAttribute,
    DevelopmentDiscipline,
)
from archive.tests.test_design_development import _coordinated_state


EVIDENCE = ("evidence:anonymous-stair-realization",)
AUTHORITY = ("authority:anonymous-stair-adoption",)
FRAME_REF = "coordinate-frame:world-y-up"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _request(
    *,
    with_landings: bool = True,
    lower_datum: float = 0.0,
) -> StairSolveRequest:
    ownership = (
        StairTerminalLandingOwnership.STAIR_ASSEMBLY
        if with_landings
        else StairTerminalLandingOwnership.ADJOINING_INTERFACE
    )
    return StairSolveRequest(
        request_id="anonymous-stair",
        length_unit=LengthUnit.METER,
        lower_interface=StairInterface(
            StairInterfaceRole.LOWER,
            "interface:anonymous-stair-lower",
            "relation:anonymous-stair-lower",
            "level:anonymous-lower",
            "fact:anonymous-lower-datum",
            lower_datum,
        ),
        upper_interface=StairInterface(
            StairInterfaceRole.UPPER,
            "interface:anonymous-stair-upper",
            "relation:anonymous-stair-upper",
            "level:anonymous-upper",
            "fact:anonymous-upper-datum",
            lower_datum + 3.0,
        ),
        plan_envelope=StairPlanEnvelope(
            "envelope:anonymous-stair",
            "relation:anonymous-stair-envelope",
            (0.0, 0.0),
            (8.0, 2.0),
            EVIDENCE,
            AUTHORITY,
        ),
        layout=StairLayout.STRAIGHT,
        width=StairDimensionValue(1.0, EVIDENCE, AUTHORITY),
        riser_height=StairDimensionBand(
            3.0 / 17.0,
            3.0 / 17.0,
            3.0 / 17.0,
            EVIDENCE,
            AUTHORITY,
        ),
        tread_depth=StairDimensionBand(
            0.28,
            0.28,
            0.28,
            EVIDENCE,
            AUTHORITY,
        ),
        riser_tread_rule=StairRiserTreadRule(
            StairRiserTreadRuleMode.ADOPTED_BAND,
            0.55,
            0.70,
            0.63,
            EVIDENCE,
            AUTHORITY,
        ),
        landing_depth=StairDimensionBand(
            1.0,
            1.0,
            1.0,
            EVIDENCE,
            AUTHORITY,
        ),
        flight_risers=StairFlightConstraint(
            17,
            17,
            17,
            EVIDENCE,
            AUTHORITY,
        ),
        preference_order=StairPreferenceOrder.LANDING_THEN_TREAD,
        lower_landing_ownership=ownership,
        upper_landing_ownership=ownership,
    )


def _solve(
    *,
    with_landings: bool = True,
    lower_datum: float = 0.0,
):
    request = _request(
        with_landings=with_landings,
        lower_datum=lower_datum,
    )
    result = solve_stair(request)
    if result.status is not StairSolveStatus.SOLVED:
        raise AssertionError(result.to_dict())
    assert result.assembly is not None
    return request, result.assembly


def _assembly(*, with_landings: bool = True):
    return _solve(with_landings=with_landings)[1]


def _stair_state(*, semantic_kind: str = "stair"):
    state = _coordinated_state()[3]
    proposal = replace(
        state.selected_schematic.option.proposal,
        components=tuple(
            replace(item, semantic_kind=semantic_kind)
            if item.component_id == "primary-support"
            else item
            for item in state.selected_schematic.option.proposal.components
        ),
    )
    option = replace(state.selected_schematic.option, proposal=proposal)
    selected = replace(state.selected_schematic, option=option)
    old_selected_ref = state.selected_schematic.ref
    components = tuple(
        replace(
            item,
            discipline=(
                DevelopmentDiscipline.CIRCULATION
                if item.component_id == "primary-support"
                else item.discipline
            ),
            schematic_dependency_refs=tuple(
                sorted(
                    selected.ref if ref == old_selected_ref else ref
                    for ref in item.schematic_dependency_refs
                )
            ),
        )
        for item in state.components
    )
    return replace(
        state,
        selected_schematic=selected,
        components=components,
        dependencies=tuple(
            replace(
                item,
                target_discipline=DevelopmentDiscipline.CIRCULATION,
            )
            if item.target_component_id == "primary-support"
            else item
            for item in state.dependencies
        ),
    )


def _map_point(
    placement: StairPlacement,
    local: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        placement.origin[index]
        + local[0] * placement.run_basis[index]
        + local[1] * placement.side_basis[index]
        + local[2] * placement.up_basis[index]
        for index in range(3)
    )


def _materialization_gate(
    request: StairSolveRequest,
    *,
    maturity: StairMaterializationMaturity = (
        StairMaterializationMaturity.DEVELOPED
    ),
    open_kinds: tuple[StairObligationKind, ...] = (),
    not_applicable_kinds: tuple[StairObligationKind, ...] = (),
) -> StairMaterializationGate:
    result = solve_stair(request)
    if result.status is not StairSolveStatus.SOLVED:
        raise AssertionError(result.to_dict())
    open_set = {StairObligationKind.HEADROOM, *open_kinds}
    not_applicable_set = set(not_applicable_kinds)
    if open_set & not_applicable_set:
        raise AssertionError("test gate dispositions overlap")
    resolutions = []
    for obligation in sorted(
        result.obligations,
        key=lambda item: item.kind.value,
    ):
        if obligation.kind in open_set:
            disposition = StairObligationDisposition.OPEN
            evidence_refs = ()
            authority_refs = ()
        else:
            disposition = (
                StairObligationDisposition.NOT_APPLICABLE
                if obligation.kind in not_applicable_set
                else StairObligationDisposition.SATISFIED
            )
            evidence_refs = EVIDENCE
            authority_refs = AUTHORITY
        resolutions.append(
            StairObligationResolution(
                obligation_ref=obligation.ref,
                kind=obligation.kind,
                disposition=disposition,
                evidence_refs=evidence_refs,
                authority_refs=authority_refs,
            )
        )
    return StairMaterializationGate(
        solve_result_digest=canonical_digest(result.to_dict()),
        maturity=maturity,
        obligation_resolutions=tuple(resolutions),
    )


def _raw_binding(
    state,
    request: StairSolveRequest,
    assembly,
    *,
    world_minimum_xz: tuple[float, float] = (10.0, 30.0),
    materialization_gate: StairMaterializationGate | None = None,
) -> StairDesignBinding:
    frame = FRAME_REF
    plan_binding = StairPlanEnvelopeDesignBinding(
        envelope=request.plan_envelope,
        minimum_world_point=(
            world_minimum_xz[0],
            request.lower_interface.datum,
            world_minimum_xz[1],
        ),
        run_basis=(0.0, 0.0, 1.0),
        side_basis=(1.0, 0.0, 0.0),
        up_basis=(0.0, 1.0, 0.0),
        relation_record_digest=SHA_C,
        length_unit=LengthUnit.METER,
        coordinate_frame_ref=frame,
        evidence_refs=EVIDENCE,
        authority_refs=AUTHORITY,
    )
    placement = plan_binding.derive_placement()
    lower_binding = StairInterfaceDesignBinding(
        interface=request.lower_interface,
        point=_map_point(placement, assembly.runs[0].start_point),
        relation_record_digest=SHA_A,
        length_unit=LengthUnit.METER,
        coordinate_frame_ref=frame,
        evidence_refs=EVIDENCE,
        authority_refs=AUTHORITY,
    )
    upper_binding = StairInterfaceDesignBinding(
        interface=request.upper_interface,
        point=_map_point(placement, assembly.runs[-1].end_point),
        relation_record_digest=SHA_B,
        length_unit=LengthUnit.METER,
        coordinate_frame_ref=frame,
        evidence_refs=EVIDENCE,
        authority_refs=AUTHORITY,
    )
    semantic_component = next(
        item
        for item in state.selected_schematic.option.proposal.components
        if item.component_id == "primary-support"
    )
    if materialization_gate is None:
        materialization_gate = _materialization_gate(request)
    return StairDesignBinding(
        binding_id="anonymous-stair-design-binding",
        branch=BranchRef(
            run=RunRef(
                project_id=state.project_id,
                run_id=state.run_id,
                base=state.base,
            ),
            branch_id="candidate-a",
            epoch=1,
        ),
        scope_digest=SHA_A,
        stage_id="stage-3",
        design_state_digest=state.state_digest,
        stage_subject_inventory_digest=SHA_D,
        component_id=semantic_component.component_id,
        component_identity_ref=semantic_component.identity_ref,
        component_semantic_kind=semantic_component.semantic_kind,
        solve_request_digest=request.digest,
        materialization_gate=materialization_gate,
        length_unit=LengthUnit.METER,
        coordinate_frame_ref=frame,
        lower_interface=lower_binding,
        upper_interface=upper_binding,
        plan_envelope=plan_binding,
        lower_landing_ownership=request.lower_landing_ownership,
        upper_landing_ownership=request.upper_landing_ownership,
        evidence_refs=EVIDENCE,
        authority_refs=AUTHORITY,
    )


def _binding(
    state,
    request: StairSolveRequest,
    assembly,
    *,
    world_minimum_xz: tuple[float, float] = (10.0, 30.0),
    materialization_gate: StairMaterializationGate | None = None,
):
    provisional = _raw_binding(
        state,
        request,
        assembly,
        world_minimum_xz=world_minimum_xz,
        materialization_gate=materialization_gate,
    )
    bound_components = tuple(
        replace(
            item,
            attributes=(
                *item.attributes,
                DevelopedAttribute(
                    key="stair_design_binding",
                    value_json=provisional.state_attribute_json,
                    source_claim_refs=(
                        "development-claim:stair-spatial-binding",
                    ),
                    evidence_refs=EVIDENCE,
                ),
            ),
        )
        if item.component_id == provisional.component_id
        else item
        for item in state.components
    )
    bound_state = replace(state, components=bound_components)
    return bound_state, replace(
        provisional,
        design_state_digest=bound_state.state_digest,
    )


def _placement() -> StairPlacement:
    # Local run maps to world +Z, local side to world +X, local up to world +Y.
    return StairPlacement(
        origin=(10.0, 2.0, 30.0),
        run_basis=(0.0, 0.0, 1.0),
        side_basis=(1.0, 0.0, 0.0),
        up_basis=(0.0, 1.0, 0.0),
    )


def _proposal():
    state = _stair_state()
    request, assembly = _solve()
    state, design_binding = _binding(state, request, assembly)
    proposal = compile_stair_geometry_proposal(
        state=state,
        request=request,
        assembly=assembly,
        design_binding=design_binding,
        profile=StairRealizationProfile(0.08, 0.12),
        commitment_refs=(),
        tolerance=GeometryTolerance(0.001, 0.001),
    )
    return state, proposal


class StairGeometryTests(unittest.TestCase):
    def test_rigid_placement_and_profile_roundtrip_without_authority(self) -> None:
        placement = _placement()
        profile = StairRealizationProfile(0.08, 0.12)
        no_landing_profile = StairRealizationProfile(0.08, None)

        self.assertEqual(
            StairPlacement.from_dict(placement.to_dict()),
            placement,
        )
        self.assertEqual(
            StairRealizationProfile.from_dict(profile.to_dict()),
            profile,
        )
        self.assertEqual(
            StairRealizationProfile.from_dict(
                no_landing_profile.to_dict()
            ),
            no_landing_profile,
        )
        self.assertIsNone(no_landing_profile.to_dict()["landing_thickness"])
        self.assertFalse(placement.to_dict()["scale_authority"])
        self.assertFalse(profile.to_dict()["structural_support_authority"])

        state = _stair_state()
        request, assembly = _solve()
        state, design_binding = _binding(state, request, assembly)
        self.assertEqual(
            StairDesignBinding.from_dict(design_binding.to_dict()),
            design_binding,
        )
        self.assertEqual(
            StairInterfaceDesignBinding.from_dict(
                design_binding.lower_interface.to_dict()
            ),
            design_binding.lower_interface,
        )
        self.assertEqual(
            StairPlanEnvelopeDesignBinding.from_dict(
                design_binding.plan_envelope.to_dict()
            ),
            design_binding.plan_envelope,
        )
        self.assertEqual(
            design_binding.to_dict()["schema"],
            "StairDesignBinding@2",
        )
        self.assertEqual(
            StairMaterializationGate.from_dict(
                design_binding.materialization_gate.to_dict()
            ),
            design_binding.materialization_gate,
        )
        self.assertEqual(
            design_binding.materialization_gate.maturity,
            StairMaterializationMaturity.DEVELOPED,
        )
        self.assertEqual(
            design_binding.materialization_gate.blocking_open_kinds,
            (),
        )
        headroom = next(
            item
            for item in design_binding.materialization_gate.obligation_resolutions
            if item.kind is StairObligationKind.HEADROOM
        )
        self.assertEqual(
            headroom.disposition,
            StairObligationDisposition.OPEN,
        )

    def test_open_materialization_obligations_cannot_emit_developed_geometry(
        self,
    ) -> None:
        for kind in (
            StairObligationKind.HOST_OPENING,
            StairObligationKind.SITE_SUPPORT,
            StairObligationKind.LOAD_PATH,
        ):
            with self.subTest(kind=kind):
                state = _stair_state()
                request, assembly = _solve()
                gate = _materialization_gate(
                    request,
                    maturity=StairMaterializationMaturity.RESERVATION,
                    open_kinds=(kind,),
                )
                state, design_binding = _binding(
                    state,
                    request,
                    assembly,
                    materialization_gate=gate,
                )
                with self.assertRaisesRegex(
                    StairGeometryError,
                    "reservation-only",
                ):
                    compile_stair_geometry_proposal(
                        state=state,
                        request=request,
                        assembly=assembly,
                        design_binding=design_binding,
                        profile=StairRealizationProfile(0.08, 0.12),
                        commitment_refs=(),
                        tolerance=GeometryTolerance(0.001, 0.001),
                    )

                with self.assertRaisesRegex(
                    StairGeometryError,
                    "OPEN materialization obligations",
                ):
                    replace(
                        gate,
                        maturity=StairMaterializationMaturity.DEVELOPED,
                    )

    def test_not_applicable_host_opening_requires_basis_and_may_develop(
        self,
    ) -> None:
        state = _stair_state()
        request, assembly = _solve()
        with self.assertRaisesRegex(
            StairGeometryError,
            "requires evidence and authority",
        ):
            StairObligationResolution(
                obligation_ref=solve_stair(request).obligations[0].ref,
                kind=StairObligationKind.HOST_OPENING,
                disposition=StairObligationDisposition.NOT_APPLICABLE,
            )

        gate = _materialization_gate(
            request,
            not_applicable_kinds=(StairObligationKind.HOST_OPENING,),
        )
        state, design_binding = _binding(
            state,
            request,
            assembly,
            materialization_gate=gate,
        )
        proposal = compile_stair_geometry_proposal(
            state=state,
            request=request,
            assembly=assembly,
            design_binding=design_binding,
            profile=StairRealizationProfile(0.08, 0.12),
            commitment_refs=(),
            tolerance=GeometryTolerance(0.001, 0.001),
        )
        self.assertTrue(proposal.operations)

    def test_reservation_semantic_cannot_masquerade_as_developed_geometry(
        self,
    ) -> None:
        state = _stair_state(semantic_kind="stair-reservation")
        request, assembly = _solve()
        state, design_binding = _binding(state, request, assembly)

        with self.assertRaisesRegex(
            StairGeometryError,
            "cannot masquerade",
        ):
            compile_stair_geometry_proposal(
                state=state,
                request=request,
                assembly=assembly,
                design_binding=design_binding,
                profile=StairRealizationProfile(0.08, 0.12),
                commitment_refs=(),
                tolerance=GeometryTolerance(0.001, 0.001),
            )

    def test_rejects_scale_shear_reflection_and_non_world_up(self) -> None:
        invalid = (
            {
                "run_basis": (2.0, 0.0, 0.0),
                "side_basis": (0.0, 0.0, -1.0),
                "up_basis": (0.0, 1.0, 0.0),
            },
            {
                "run_basis": (1.0, 0.0, 0.0),
                "side_basis": (1.0, 0.0, -1.0),
                "up_basis": (0.0, 1.0, 0.0),
            },
            {
                "run_basis": (1.0, 0.0, 0.0),
                "side_basis": (0.0, 0.0, 1.0),
                "up_basis": (0.0, 1.0, 0.0),
            },
            {
                "run_basis": (1.0, 0.0, 0.0),
                "side_basis": (0.0, 1.0, 0.0),
                "up_basis": (0.0, 0.0, 1.0),
            },
        )
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(StairGeometryError):
                StairPlacement(origin=(0.0, 0.0, 0.0), **row)

    def test_bakes_solver_local_z_up_into_identity_world_y_up(self) -> None:
        _, proposal = _proposal()
        operation = next(
            item
            for item in proposal.operations
            if item.op_id.endswith("tread-0000")
        )
        parameters = {
            item.name: json.loads(item.value_json)
            for item in operation.parameters
        }
        riser = 3.0 / 17.0

        self.assertEqual(proposal.frames[0].frame_id, "world")
        self.assertEqual(
            proposal.frames[0].transform_from_parent,
            proposal.frames[0].transform_from_parent.identity(),
        )
        self.assertEqual(parameters["vector"], [0.0, 0.08, 0.0])
        self.assertEqual(parameters["profile"][0][0], 10.0)
        self.assertAlmostEqual(
            parameters["profile"][0][1],
            riser - 0.08,
        )
        self.assertEqual(parameters["profile"][0][2], 31.0)
        self.assertEqual(parameters["profile"][1][2], 31.28)
        self.assertEqual(parameters["profile"][3][0], 11.0)

    def test_nonzero_lower_datum_is_applied_once_to_first_and_last_tread(
        self,
    ) -> None:
        state = _stair_state()
        request, assembly = _solve(lower_datum=0.15)
        state, design_binding = _binding(state, request, assembly)
        proposal = compile_stair_geometry_proposal(
            state=state,
            request=request,
            assembly=assembly,
            design_binding=design_binding,
            profile=StairRealizationProfile(0.08, 0.12),
            commitment_refs=(),
            tolerance=GeometryTolerance(0.001, 0.001),
        )
        operations = {item.op_id: item for item in proposal.operations}

        for tread in (assembly.treads[0], assembly.treads[-1]):
            operation = next(
                item
                for op_id, item in operations.items()
                if op_id.endswith(f"tread-{tread.ordinal:04d}")
            )
            parameters = {
                item.name: json.loads(item.value_json)
                for item in operation.parameters
            }
            world_bottom_y = parameters["profile"][0][1]
            world_top_y = world_bottom_y + parameters["vector"][1]

            self.assertAlmostEqual(world_top_y, tread.walking_datum)
            self.assertAlmostEqual(
                world_top_y - request.lower_interface.datum,
                tread.local_origin[2],
            )

    def test_proposal_has_stable_ids_and_compiles_and_realizes_generically(self) -> None:
        state, first = _proposal()
        _, second = _proposal()

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            first.semantic_bindings[0].object_ids,
            tuple(
                sorted(
                    object_id
                    for operation in first.operations
                    for object_id in operation.output_object_ids
                )
            ),
        )
        self.assertTrue(
            all(
                operation.semantic_binding_ids
                == (first.semantic_bindings[0].binding_id,)
                for operation in first.operations
            )
        )

        compilation = compile_geometry_program(state, first)
        self.assertEqual(
            compilation.receipt.status,
            GeometryCompileStatus.COMPILED,
        )
        assert compilation.program is not None
        realization = realize_geometry(
            compilation.program,
            workspace_id="anonymous-stair-workspace",
        )
        self.assertEqual(
            realization.receipt.status,
            RealizationStatus.REALIZED,
        )
        assert realization.scene is not None
        self.assertEqual(
            len(realization.scene.objects),
            len(_assembly().treads) + len(_assembly().landings),
        )

        request, assembly = _solve()
        moved_state, moved_binding = _binding(
            _stair_state(),
            request,
            assembly,
            world_minimum_xz=(20.0, 40.0),
        )
        with self.assertRaisesRegex(StairGeometryError, "exact design state"):
            compile_stair_geometry_proposal(
                state=state,
                request=request,
                assembly=assembly,
                design_binding=moved_binding,
                profile=StairRealizationProfile(0.1, 0.14),
                commitment_refs=(),
                tolerance=GeometryTolerance(0.001, 0.001),
            )
        moved = compile_stair_geometry_proposal(
            state=moved_state,
            request=request,
            assembly=assembly,
            design_binding=moved_binding,
            profile=StairRealizationProfile(0.1, 0.14),
            commitment_refs=(),
            tolerance=GeometryTolerance(0.001, 0.001),
        )
        self.assertNotEqual(first.proposal_digest, moved.proposal_digest)
        self.assertEqual(
            tuple(item.op_id for item in first.operations),
            tuple(item.op_id for item in moved.operations),
        )
        self.assertEqual(
            first.semantic_bindings[0].binding_id,
            moved.semantic_bindings[0].binding_id,
        )

    def test_fails_closed_on_unknown_component_or_sub_tolerance_solid(self) -> None:
        state = _stair_state()
        request, assembly = _solve()
        state, design_binding = _binding(state, request, assembly)
        common = {
            "state": state,
            "request": request,
            "assembly": assembly,
            "design_binding": design_binding,
            "commitment_refs": (),
            "tolerance": GeometryTolerance(0.001, 0.001),
        }
        with self.assertRaisesRegex(StairGeometryError, "absent"):
            compile_stair_geometry_proposal(
                **{
                    **common,
                    "design_binding": replace(
                        design_binding,
                        component_id="missing-stair",
                        component_identity_ref=(
                            "design-component:missing-stair"
                        ),
                    ),
                },
                profile=StairRealizationProfile(0.08, 0.12),
            )
        with self.assertRaisesRegex(StairGeometryError, "tolerance"):
            compile_stair_geometry_proposal(
                **common,
                profile=StairRealizationProfile(0.001, 0.12),
            )

        structural_state = _coordinated_state()[3]
        with self.assertRaisesRegex(StairGeometryError, "stair semantic"):
            compile_stair_geometry_proposal(
                **{
                    **common,
                    "state": structural_state,
                    "design_binding": replace(
                        design_binding,
                        design_state_digest=structural_state.state_digest,
                    ),
                },
                profile=StairRealizationProfile(0.08, 0.12),
            )

    def test_rejects_origin_drift_and_unit_disguise(self) -> None:
        parameters = inspect.signature(
            compile_stair_geometry_proposal
        ).parameters
        self.assertTrue(
            {
                "placement",
                "component_id",
                "length_unit",
                "evidence_refs",
            }.isdisjoint(parameters)
        )
        state = _stair_state()
        request, assembly = _solve()
        state, design_binding = _binding(state, request, assembly)
        _, independently_moved = _binding(
            _stair_state(),
            request,
            assembly,
            world_minimum_xz=(10.0, 99.0),
        )
        with self.assertRaisesRegex(StairGeometryError, "not owned"):
            compile_stair_geometry_proposal(
                state=state,
                request=request,
                assembly=assembly,
                design_binding=replace(
                    independently_moved,
                    design_state_digest=state.state_digest,
                ),
                profile=StairRealizationProfile(0.08, 0.12),
                commitment_refs=(),
                tolerance=GeometryTolerance(0.001, 0.001),
            )
        synchronized_unit_disguise = replace(
            design_binding,
            length_unit=LengthUnit.MILLIMETER,
            lower_interface=replace(
                design_binding.lower_interface,
                length_unit=LengthUnit.MILLIMETER,
            ),
            upper_interface=replace(
                design_binding.upper_interface,
                length_unit=LengthUnit.MILLIMETER,
            ),
            plan_envelope=replace(
                design_binding.plan_envelope,
                length_unit=LengthUnit.MILLIMETER,
            ),
        )
        with self.assertRaisesRegex(StairGeometryError, "solve-request length unit"):
            compile_stair_geometry_proposal(
                state=state,
                request=request,
                assembly=assembly,
                design_binding=synchronized_unit_disguise,
                profile=StairRealizationProfile(0.08, 0.12),
                commitment_refs=(),
                tolerance=GeometryTolerance(0.001, 0.001),
            )

    def test_rejects_branch_stage_scope_and_inventory_disguise(self) -> None:
        state = _stair_state()
        request, assembly = _solve()
        state, design_binding = _binding(state, request, assembly)
        foreign_branch = replace(
            design_binding.branch,
            branch_id="candidate-b",
            epoch=2,
        )
        disguises = (
            replace(design_binding, branch=foreign_branch),
            replace(design_binding, scope_digest=SHA_B),
            replace(design_binding, stage_id="stage-4"),
            replace(design_binding, stage_subject_inventory_digest=SHA_C),
        )

        for disguised in disguises:
            with self.subTest(binding=disguised), self.assertRaisesRegex(
                StairGeometryError,
                "not owned",
            ):
                compile_stair_geometry_proposal(
                    state=state,
                    request=request,
                    assembly=assembly,
                    design_binding=disguised,
                    profile=StairRealizationProfile(0.08, 0.12),
                    commitment_refs=(),
                    tolerance=GeometryTolerance(0.001, 0.001),
                )

    def test_landing_thickness_exists_exactly_for_owned_landings(self) -> None:
        state = _stair_state()
        owned_request, owned_assembly = _solve()
        owned_state, owned_binding = _binding(
            state,
            owned_request,
            owned_assembly,
        )
        owned_common = {
            "state": owned_state,
            "request": owned_request,
            "assembly": owned_assembly,
            "design_binding": owned_binding,
            "commitment_refs": (),
            "tolerance": GeometryTolerance(0.001, 0.001),
        }
        with self.assertRaisesRegex(StairGeometryError, "require"):
            compile_stair_geometry_proposal(
                **owned_common,
                profile=StairRealizationProfile(0.08, None),
            )
        no_landing_request, no_landing_assembly = _solve(
            with_landings=False
        )
        no_landing_state, no_landing_binding = _binding(
            state,
            no_landing_request,
            no_landing_assembly,
        )
        no_landing_common = {
            "state": no_landing_state,
            "request": no_landing_request,
            "assembly": no_landing_assembly,
            "design_binding": no_landing_binding,
            "commitment_refs": (),
            "tolerance": GeometryTolerance(0.001, 0.001),
        }
        with self.assertRaisesRegex(StairGeometryError, "absent"):
            compile_stair_geometry_proposal(
                **no_landing_common,
                profile=StairRealizationProfile(0.08, 0.12),
            )

        first = compile_stair_geometry_proposal(
            **no_landing_common,
            profile=StairRealizationProfile(0.08, None),
        )
        second = compile_stair_geometry_proposal(
            **no_landing_common,
            profile=StairRealizationProfile(0.08, None),
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertTrue(first.operations)
        self.assertTrue(
            all("-tread-" in item.op_id for item in first.operations)
        )
        compilation = compile_geometry_program(no_landing_state, first)
        self.assertEqual(
            compilation.receipt.status,
            GeometryCompileStatus.COMPILED,
        )


if __name__ == "__main__":
    unittest.main()
