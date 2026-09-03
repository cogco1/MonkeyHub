from __future__ import annotations

import unittest
from dataclasses import replace

from archive.archflow.capabilities.stair_solver import (
    StairDimensionBand,
    StairDimensionValue,
    StairFlightConstraint,
    StairInterface,
    StairInterfaceRole,
    StairLayout,
    StairPlanEnvelope,
    StairPreferenceOrder,
    StairRiserTreadRule,
    StairRiserTreadRuleMode,
    StairSolveRequest,
    StairSolveStatus,
    StairTerminalLandingOwnership,
    solve_stair,
)
from archive.archflow.control.baseline import BASELINE_LEVEL_ROLES, CadReadbackBaselineSource, ComponentLineageBaselineSource, MaterialBindingBaselineSource, RelationTopologyBaselineSource, SpatialLayoutBaselineSource, StageBaselineError, StageBaselineCoverageReceipt, StageBaselineLevel, StageBaselineRole, StageBaselineSourceSet, StageBaselineStatus, baseline_level_for_design_phase, compile_stage_baseline_coverage, derive_stage_requirement_profile
from archflow.control.requirements import RequirementBasisMode, RequirementTargetKind, StageCheckRequirement, StageRequirementProfile
from archflow.control.stage_closure import StageClosureStatus, compile_composite_stage_closure
from archive.archflow.control.stage_subjects import StageSubjectDisposition, StageSubjectInventory, StageSubjectInventoryEntry, StageSubjectRoleObligation
from archive.archflow.control.check_requirements import assembly_stage_requirement, cad_readback_stage_requirement, component_lineage_stage_requirement, material_binding_stage_requirement, spatial_layout_stage_requirement
from archive.archflow.control.semantic_capabilities import bind_semantic_rule_packs, current_semantic_capability_policy
from archive.archflow.control.baseline import (
    VerticalCirculationBaselineSource,
    check_vertical_circulation_baseline_maturity,
    required_vertical_circulation_maturity,
)
from archive.archflow.control.check_requirements import (
    relation_authoring_stage_requirements,
    vertical_circulation_stage_requirement,
)
from archive.archflow.control.relation_checks import check_relation_coverage
from archive.archflow.control.relation_promotion import promote_verified_relation_graph
from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import BranchRef, ProjectRecordRef
from archflow.state.stage_workflow import DesignPhase
from archflow.state.geometry_program import LengthUnit
from archive.archflow.materials.binding import validate_material_bindings
from archive.archflow.relations.authoring import (
    RelationAnswerStatus,
    RelationAuthoringContext,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisKind,
    RelationBasisUse,
    RelationDerivationAnswer,
    RelationDerivationQuestion,
    RelationProposalSpec,
    RelationRuleEnvelope,
    RelationRuleProposalSpec,
    compile_relation_authoring,
)
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
    ArchitecturalRelationKind,
    RelationEpistemicStatus,
    RelationParticipant,
    RelationProjection,
)
from archive.archflow.validation.assembly import (
    AssemblyObligationDisposition,
    AssemblyProfile,
    RelationshipKind,
    check_assembly,
)
from archive.archflow.validation.cad_readback import (
    CadBoundingBox,
    CadObjectReadback,
    CadObjectRequirement,
    CadReadbackProfile,
    CadReadbackSnapshot,
    CadUpAxis,
    validate_cad_readback,
)
from archive.archflow.validation.check_bridges import (
    bridge_component_lineage_receipt,
    bridge_spatial_validation_receipt,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.spatial import (
    normalize_spatial_validation_input,
    validate_spatial_layout,
)
from archive.archflow.validation.vertical_circulation import (
    VerticalCirculationMaturity,
)
from tests.test_assembly_validation import passing_profile
from tests.test_cad_readback_contracts import (
    profile as cad_profile_fixture,
    snapshot as cad_snapshot_fixture,
)
from tests.test_check_receipt_bridges import (
    SHA_A,
    SHA_B,
    SUBJECT_DIGEST,
    branch,
    lineage_profile,
    lineage_receipt,
    spatial_kwargs,
    spatial_profile,
)
from tests.test_material_binding import (
    ledger as material_ledger_fixture,
    profile as material_profile_fixture,
    snapshot as material_snapshot_fixture,
)
from archive.tests.test_vertical_circulation import (
    aabb as circulation_aabb_fixture,
    assembly_witness as circulation_assembly_fixture,
    contract as circulation_contract_fixture,
    geometry_witness as circulation_geometry_fixture,
)


def physical_sources(
    *,
    include_opening: bool = True,
    include_load_path: bool = True,
    stage_subject_source_digest: str = SUBJECT_DIGEST,
) -> StageBaselineSourceSet:
    assembly = passing_profile(
        stage_subject_source_digest=stage_subject_source_digest
    )
    omitted_kinds = {
        kind
        for include, kind in (
            (include_opening, RelationshipKind.OPENING_CLEAR),
            (include_load_path, RelationshipKind.LOAD_PATH_TO_FOUNDATION),
        )
        if not include
    }
    assembly = replace(
        assembly,
        requirements=tuple(
            item
            for item in assembly.requirements
            if (
                include_opening
                or item.kind is not RelationshipKind.OPENING_CLEAR
            )
            and (
                include_load_path
                or item.kind is not RelationshipKind.LOAD_PATH_TO_FOUNDATION
            )
        ),
        coverage_manifest=replace(
            assembly.coverage_manifest,
            obligations=tuple(
                replace(
                    item,
                    disposition=(
                        AssemblyObligationDisposition.NOT_APPLICABLE
                    ),
                    relationship_kind=None,
                    endpoint_index=None,
                    requirement_id=None,
                )
                if item.relationship_kind in omitted_kinds
                else item
                for item in assembly.coverage_manifest.obligations
            ),
        ),
    )
    return StageBaselineSourceSet(
        component_lineage=(
            ComponentLineageBaselineSource(
                profile=lineage_profile(),
                source_receipt=lineage_receipt(),
            ),
        ),
        spatial_layout=(
            SpatialLayoutBaselineSource(
                profile=spatial_profile(),
                validator_input=normalize_spatial_validation_input(
                    **spatial_kwargs()
                ),
            ),
        ),
        assembly=(assembly,),
    )


def legacy_sources(
    sources: StageBaselineSourceSet,
) -> StageBaselineSourceSet:
    payload = sources.to_dict()
    payload["schema"] = StageBaselineSourceSet.LEGACY_SCHEMA
    payload.pop("relation_topology")
    payload.pop("relation_realization")
    payload.pop("relation_inheritance")
    payload.pop("visual_inventory")
    payload.pop("component_functions")
    payload.pop("vertical_circulation", None)
    content = {
        key: value
        for key, value in payload.items()
        if key != "source_set_digest"
    }
    payload["source_set_digest"] = canonical_digest(content)
    return StageBaselineSourceSet.from_dict(payload)


def requirement_and_receipts(
    sources: StageBaselineSourceSet,
    *,
    level: StageBaselineLevel = StageBaselineLevel.SPATIAL,
) -> tuple[tuple[StageCheckRequirement, ...], tuple[CheckReceiptEnvelope, ...]]:
    requirements: list[StageCheckRequirement] = []
    receipts: list[CheckReceiptEnvelope] = []
    for source in sources.component_lineage:
        requirements.append(component_lineage_stage_requirement(source.profile))
        receipts.append(
            bridge_component_lineage_receipt(
                source.profile,
                source.source_receipt,
                stage_subject_digest=SUBJECT_DIGEST,
            )
        )
    for source in sources.spatial_layout:
        spatial_input = source.validator_input
        source_receipt = validate_spatial_layout(
            elements=spatial_input.elements,
            host_regions=spatial_input.host_regions,
            required_component_ids=spatial_input.required_component_ids,
            opening_clear_regions=spatial_input.opening_clear_regions,
            minimum_column_wall_clearance=(
                spatial_input.minimum_column_wall_clearance
            ),
            linear_tolerance=spatial_input.linear_tolerance,
            intersection_volume_tolerance=(
                spatial_input.intersection_volume_tolerance
            ),
            length_unit=spatial_input.length_unit,
        )
        requirements.append(spatial_layout_stage_requirement(source.profile))
        receipts.append(
            bridge_spatial_validation_receipt(
                source.profile,
                source_receipt,
                stage_subject_digest=SUBJECT_DIGEST,
            )
        )
    for source in sources.assembly:
        requirements.append(assembly_stage_requirement(source))
        receipts.append(
            check_assembly(
                source,
                branch=branch(),
                scope_digest=SHA_B,
                stage_subject_digest=SUBJECT_DIGEST,
            )
        )
    for source in sources.material_binding:
        requirements.append(material_binding_stage_requirement(source.profile))
        receipts.append(
            validate_material_bindings(
                source.profile,
                source.ledger,
                source.snapshot,
                stage_subject_digest=SUBJECT_DIGEST,
            )
        )
    for source in sources.cad_readback:
        requirements.append(cad_readback_stage_requirement(source.profile))
        receipts.append(
            validate_cad_readback(
                source.profile,
                source.snapshot,
                stage_subject_digest=SUBJECT_DIGEST,
            )
        )
    required_maturity = required_vertical_circulation_maturity(level)
    if required_maturity is not None:
        for source in sources.vertical_circulation:
            requirements.append(
                vertical_circulation_stage_requirement(
                    source.contract,
                    required_maturity=required_maturity,
                )
            )
            receipts.append(
                check_vertical_circulation_baseline_maturity(
                    source,
                    required_maturity=required_maturity,
                )
            )
    return tuple(requirements), tuple(receipts)


def profile(
    requirements: tuple[StageCheckRequirement, ...],
    *,
    selected_branch: BranchRef | None = None,
) -> StageRequirementProfile:
    return StageRequirementProfile(
        profile_id="baseline-stage-profile",
        typology_id="synthetic-building",
        stage_id="stage-2",
        branch=branch() if selected_branch is None else selected_branch,
        predecessor_state_digest=SHA_A,
        scope_digest=SHA_B,
        stage_subject_ref="deliverable:stage-2",
        requirements=requirements,
    )


def role_target_refs(
    sources: StageBaselineSourceSet,
) -> dict[StageBaselineRole, tuple[str, ...]]:
    targets: dict[StageBaselineRole, set[str]] = {
        role: set() for role in StageBaselineRole
    }
    for source in sources.component_lineage:
        targets[StageBaselineRole.COMPONENT_LINEAGE].update(
            f"component:{operation.ref.component_id}"
            for operation in source.source_receipt.predecessor_operations
        )
    for source in sources.spatial_layout:
        targets[StageBaselineRole.SPATIAL_ENVELOPE].update(
            f"component:{component_id}"
            for component_id in source.validator_input.required_component_ids
        )
    for source in sources.assembly:
        targets[StageBaselineRole.ASSEMBLY_RELATIONSHIPS].update(
            source.coverage_manifest.stage_subject_refs
        )
        targets[StageBaselineRole.OPENING_CLEARANCE].update(
            ref
            for requirement in source.requirements
            if requirement.kind is RelationshipKind.OPENING_CLEAR
            for ref in requirement.subject_refs
        )
        targets[StageBaselineRole.LOAD_PATH].update(
            ref
            for requirement in source.requirements
            if requirement.kind
            in {
                RelationshipKind.SUPPORT,
                RelationshipKind.VERTICAL_SUPPORT_CHAIN,
                RelationshipKind.LOAD_PATH_TO_FOUNDATION,
            }
            for ref in requirement.subject_refs
        )
    for source in sources.material_binding:
        targets[StageBaselineRole.MATERIAL_BINDING].update(
            requirement.semantic_subject_ref
            for requirement in source.profile.requirements
        )
    for source in sources.cad_readback:
        targets[StageBaselineRole.CAD_READBACK].update(
            requirement.object_ref
            for requirement in source.profile.object_requirements
        )
    return {
        role: tuple(sorted(refs))
        for role, refs in targets.items()
    }


def subject_inventory(
    sources: StageBaselineSourceSet,
    *,
    level: StageBaselineLevel,
    obligation_overrides: dict[
        StageBaselineRole,
        StageSubjectRoleObligation,
    ]
    | None = None,
) -> StageSubjectInventory:
    targets = role_target_refs(sources)
    overrides = {} if obligation_overrides is None else obligation_overrides
    obligations = tuple(
        overrides.get(
            role,
            StageSubjectRoleObligation(
                role=role,
                disposition=StageSubjectDisposition.REQUIRED,
                target_refs=(
                    targets[role]
                    or (f"component:uncovered-{role.value}",)
                ),
                evidence_refs=("evidence:stage-inventory",),
                authority_refs=("authority:stage-inventory",),
            ),
        )
        for role in sorted(BASELINE_LEVEL_ROLES[level])
    )
    selected_branch = branch()
    record_prefix = (
        f"runs/{selected_branch.run.run_id}/branches/"
        f"{selected_branch.branch_id}/records"
    )
    return StageSubjectInventory(
        inventory_id=f"stage-2-{level.value}-subjects",
        branch=selected_branch,
        stage_id="stage-2",
        stage_subject_ref="deliverable:stage-2",
        stage_subject_digest=SUBJECT_DIGEST,
        baseline_level=level,
        component_proposal_ref=ProjectRecordRef(
            project_id=selected_branch.run.project_id,
            relative_path=f"{record_prefix}/component-proposal.json",
            sha256=SHA_A,
        ),
        component_proposal_digest=SHA_A,
        component_index_ref=ProjectRecordRef(
            project_id=selected_branch.run.project_id,
            relative_path=f"{record_prefix}/component-index.json",
            sha256=SHA_B,
        ),
        component_index_digest=SHA_B,
        entries=(
            StageSubjectInventoryEntry(
                component_id="stage-root",
                identity_ref="design-component:stage-root",
                parent_component_id=None,
                semantic_kind="stage-root",
                component_digest=SUBJECT_DIGEST,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=obligations,
            ),
        ),
    )


def circulation_contract_for_baseline(
    maturity: VerticalCirculationMaturity,
    *,
    complete: bool = True,
):
    fixture_maturity = (
        VerticalCirculationMaturity.RESOLVED_PATH
        if maturity is VerticalCirculationMaturity.RESERVATION
        else maturity
    )
    raw = circulation_contract_fixture(
        fixture_maturity,
        geometry=(
            circulation_geometry_fixture()
            if complete and maturity is not VerticalCirculationMaturity.RESERVATION
            else None
        ),
        reservation_aabb=circulation_aabb_fixture(),
        assembly=(
            circulation_assembly_fixture()
            if complete and maturity is VerticalCirculationMaturity.ASSEMBLY
            else None
        ),
    )
    if maturity is VerticalCirculationMaturity.RESERVATION:
        raw = replace(
            raw,
            maturity=maturity,
            artifact_sha256=None,
            program_digest=None,
            readback_digest=None,
            solver_tread_refs=(),
            solver_landing_refs=(),
            object_bindings=(),
            interface_host_bindings=(),
            geometry_witness=None,
            assembly_witness=None,
        )
    identity_ref = "design-component:stair-01"
    object_bindings = tuple(
        replace(item, component_ref=identity_ref)
        for item in raw.object_bindings
    )
    geometry = raw.geometry_witness
    if geometry is not None:
        geometry = replace(
            geometry,
            branch=branch(),
            scope_digest=SHA_B,
            stage_id="stage-2",
            stage_subject_ref="deliverable:stage-2",
            stage_subject_digest=SUBJECT_DIGEST,
            object_bindings=tuple(
                replace(item, component_ref=identity_ref)
                for item in geometry.object_bindings
            ),
            path_samples=tuple(
                replace(item, component_ref=identity_ref)
                for item in geometry.path_samples
            ),
            landings=tuple(
                replace(item, component_ref=identity_ref)
                for item in geometry.landings
            ),
        )
    assembly = raw.assembly_witness
    if assembly is not None:
        def rebind_nested_receipt(receipt):
            if receipt is None:
                return None
            rebound_subjects = tuple(
                sorted(
                    identity_ref
                    if item == "component:stair-01"
                    else "deliverable:stage-2"
                    if item == "stage-subject:stair-01"
                    else item
                    for item in receipt.subject_refs
                )
            )
            return replace(
                receipt,
                branch=branch(),
                scope_digest=SHA_B,
                subject_refs=rebound_subjects,
                subject_digest=SUBJECT_DIGEST,
            )

        assembly = replace(
            assembly,
            branch=branch(),
            scope_digest=SHA_B,
            stage_id="stage-2",
            stage_subject_ref="deliverable:stage-2",
            stage_subject_digest=SUBJECT_DIGEST,
            support_receipt=rebind_nested_receipt(assembly.support_receipt),
            load_path_receipt=rebind_nested_receipt(assembly.load_path_receipt),
            host_cut_receipt=rebind_nested_receipt(assembly.host_cut_receipt),
            underpass_receipt=rebind_nested_receipt(assembly.underpass_receipt),
        )
    return replace(
        raw,
        branch=branch(),
        scope_digest=SHA_B,
        stage_id="stage-2",
        stage_subject_ref="deliverable:stage-2",
        stage_subject_digest=SUBJECT_DIGEST,
        component_refs=(identity_ref,),
        aabb_precheck=(
            None
            if raw.aabb_precheck is None
            else replace(raw.aabb_precheck, component_ref=identity_ref)
        ),
        object_bindings=object_bindings,
        geometry_witness=geometry,
        assembly_witness=assembly,
    )


def circulation_solver_pair(contract):
    evidence_refs = contract.criteria.evidence_refs
    authority_refs = contract.criteria.adoption_refs

    def solver_interface(value, role):
        return StairInterface(
            role=role,
            interface_ref=value.interface_ref,
            relation_ref=value.relation_ref,
            level_ref=value.level_ref,
            datum_fact_ref=value.datum_fact_ref,
            datum=value.datum,
        )

    request = StairSolveRequest(
        request_id=f"{contract.contract_id}-solve",
        length_unit=LengthUnit.METER,
        lower_interface=solver_interface(
            contract.lower_interface,
            StairInterfaceRole.LOWER,
        ),
        upper_interface=solver_interface(
            contract.upper_interface,
            StairInterfaceRole.UPPER,
        ),
        plan_envelope=StairPlanEnvelope(
            envelope_ref=f"envelope:{contract.contract_id}",
            relation_ref=f"relation:{contract.contract_id}-envelope",
            minimum=(0.0, 0.0),
            maximum=(8.0, 2.0),
            evidence_refs=evidence_refs,
            authority_refs=authority_refs,
        ),
        layout=StairLayout.STRAIGHT,
        width=StairDimensionValue(1.2, evidence_refs, authority_refs),
        riser_height=StairDimensionBand(
            0.75,
            0.75,
            0.75,
            evidence_refs,
            authority_refs,
        ),
        tread_depth=StairDimensionBand(
            1.0,
            1.0,
            1.0,
            evidence_refs,
            authority_refs,
        ),
        riser_tread_rule=StairRiserTreadRule(
            StairRiserTreadRuleMode.NOT_APPLICABLE,
            None,
            None,
            None,
            evidence_refs,
            authority_refs,
        ),
        landing_depth=StairDimensionBand(
            1.2,
            1.2,
            1.2,
            evidence_refs,
            authority_refs,
        ),
        flight_risers=StairFlightConstraint(
            4,
            4,
            4,
            evidence_refs,
            authority_refs,
        ),
        preference_order=StairPreferenceOrder.LANDING_THEN_TREAD,
        lower_landing_ownership=StairTerminalLandingOwnership(
            contract.lower_landing_ownership.value
        ),
        upper_landing_ownership=StairTerminalLandingOwnership(
            contract.upper_landing_ownership.value
        ),
    )
    result = solve_stair(request)
    if result.status is not StairSolveStatus.SOLVED or result.assembly is None:
        raise AssertionError(result.to_dict())
    if (
        contract.maturity is not VerticalCirculationMaturity.RESERVATION
        and len(result.assembly.treads) != len(contract.solver_tread_refs)
    ):
        raise AssertionError("fixture solver tread denominator changed")
    return request, result


def circulation_cad_source(contract) -> CadReadbackBaselineSource:
    layer_ref = "cad-layer:vertical-circulation"
    bindings = (*contract.object_bindings, *contract.interface_host_bindings)
    envelope = CadBoundingBox(
        minimum=(-100.0, -100.0, -100.0),
        maximum=(100.0, 100.0, 100.0),
    )
    requirements = tuple(
        CadObjectRequirement(
            requirement_id=f"vertical-circulation-{ordinal:03d}",
            object_ref=binding.object_ref,
            operation_ref=binding.operation_ref,
            required_layer_ref=layer_ref,
            required_attributes=(("binding", binding.ref),),
            predecessor_envelope=envelope,
        )
        for ordinal, binding in enumerate(bindings)
    )
    profile_value = CadReadbackProfile(
        profile_id=f"{contract.contract_id}-readback",
        branch=contract.branch,
        stage_id=contract.stage_id,
        scope_digest=contract.scope_digest,
        program_digest=contract.program_digest,
        length_unit="meter",
        up_axis=CadUpAxis(contract.up_axis.value),
        required_layer_refs=(layer_ref,),
        object_requirements=requirements,
    )
    objects = tuple(
        CadObjectReadback(
            object_ref=requirement.object_ref,
            operation_ref=requirement.operation_ref,
            layer_ref=layer_ref,
            attributes=requirement.required_attributes,
            world_bbox=CadBoundingBox(
                minimum=(-1.0, -1.0, -1.0),
                maximum=(1.0, 1.0, 1.0),
            ),
        )
        for requirement in requirements
    )
    snapshot_value = CadReadbackSnapshot(
        project_id=contract.branch.run.project_id,
        branch=contract.branch,
        stage_id=contract.stage_id,
        profile_digest=profile_value.profile_digest,
        program_digest=profile_value.program_digest,
        length_unit=profile_value.length_unit,
        up_axis=profile_value.up_axis,
        declared_layer_refs=(layer_ref,),
        operation_refs=tuple(item.operation_ref for item in objects),
        objects=objects,
    )
    return CadReadbackBaselineSource(
        profile=profile_value,
        snapshot=snapshot_value,
    )


def circulation_source_for_baseline(
    contract,
    *,
    readback_source: CadReadbackBaselineSource | None = None,
) -> VerticalCirculationBaselineSource:
    request, result = circulation_solver_pair(contract)
    solver_digest = canonical_digest(result.to_dict())
    solver_tread_refs = (
        ()
        if contract.maturity is VerticalCirculationMaturity.RESERVATION
        else result.tread_refs
    )
    solver_landing_refs = (
        ()
        if contract.maturity is VerticalCirculationMaturity.RESERVATION
        else result.landing_refs
    )
    tread_ref_map = (
        {}
        if not solver_tread_refs
        else dict(
            zip(contract.solver_tread_refs, solver_tread_refs, strict=True)
        )
    )
    landing_ref_by_role = (
        {}
        if result.assembly is None
        else {
            landing.role.value: landing_ref
            for landing, landing_ref in zip(
                result.assembly.landings,
                result.landing_refs,
                strict=True,
            )
        }
    )
    unit_ref = f"unit:{request.length_unit.value}"
    program_digest = (
        contract.program_digest
        if readback_source is None
        else readback_source.profile.program_digest
    )
    readback_digest = (
        contract.readback_digest
        if readback_source is None
        else readback_source.snapshot.snapshot_digest
    )

    geometry = contract.geometry_witness
    if geometry is not None:
        geometry = replace(
            geometry,
            design_state_digest=SHA_A,
            program_digest=program_digest,
            readback_digest=readback_digest,
            solver_result_digest=solver_digest,
            solver_tread_refs=solver_tread_refs,
            length_unit_ref=unit_ref,
            path_samples=tuple(
                replace(
                    item,
                    solver_tread_ref=(
                        None
                        if item.solver_tread_ref is None
                        else tread_ref_map[item.solver_tread_ref]
                    ),
                )
                for item in geometry.path_samples
            ),
            landings=tuple(
                replace(
                    item,
                    landing_ref=landing_ref_by_role.get(
                        item.role.value,
                        item.landing_ref,
                    ),
                )
                for item in geometry.landings
            ),
        )
    assembly = contract.assembly_witness
    if assembly is not None:
        def rebind_solver_refs(values):
            return tuple(
                sorted(
                    landing_ref_by_role.get(
                        item.removeprefix("landing:"),
                        tread_ref_map.get(item, item),
                    )
                    for item in values
                )
            )

        def rebind_assembly_receipt(receipt):
            if receipt is None:
                return None
            return replace(
                receipt,
                subject_refs=rebind_solver_refs(receipt.subject_refs),
                coverage_denominator=rebind_solver_refs(
                    receipt.coverage_denominator
                ),
                covered_refs=rebind_solver_refs(receipt.covered_refs),
            )

        assembly = replace(
            assembly,
            design_state_digest=SHA_A,
            program_digest=program_digest,
            readback_digest=readback_digest,
            solver_result_digest=solver_digest,
            support_receipt=rebind_assembly_receipt(
                assembly.support_receipt
            ),
            load_path_receipt=rebind_assembly_receipt(
                assembly.load_path_receipt
            ),
            host_cut_receipt=rebind_assembly_receipt(
                assembly.host_cut_receipt
            ),
            underpass_receipt=rebind_assembly_receipt(
                assembly.underpass_receipt
            ),
        )

    rebound = replace(
        contract,
        design_state_digest=SHA_A,
        program_digest=program_digest,
        readback_digest=readback_digest,
        solver_result_digest=solver_digest,
        solver_tread_refs=solver_tread_refs,
        solver_landing_refs=solver_landing_refs,
        length_unit_ref=unit_ref,
        geometry_witness=geometry,
        assembly_witness=assembly,
    )
    return VerticalCirculationBaselineSource(
        contract=rebound,
        solve_request=request,
        solve_result=result,
        readback_source_digest=(
            None if readback_source is None else readback_source.source_digest
        ),
    )


def sources_with_circulation(
    contract,
    *,
    base: StageBaselineSourceSet | None = None,
) -> StageBaselineSourceSet:
    readback_source = (
        None
        if contract.maturity is VerticalCirculationMaturity.RESERVATION
        else circulation_cad_source(contract)
    )
    source = circulation_source_for_baseline(
        contract,
        readback_source=readback_source,
    )
    return replace(
        physical_sources() if base is None else base,
        cad_readback=(
            () if readback_source is None else (readback_source,)
        ),
        vertical_circulation=(source,),
    )


def inventory_with_semantic_kinds(
    sources: StageBaselineSourceSet,
    *,
    level: StageBaselineLevel,
    semantic_kinds: tuple[str, ...],
    typed_vertical: bool = True,
) -> StageSubjectInventory:
    base = subject_inventory(sources, level=level)
    obligations = base.entries[0].role_obligations
    entries = [base.entries[0]]
    for ordinal, semantic_kind in enumerate(semantic_kinds, start=1):
        component_id = f"stair-{ordinal:02d}"
        vertical_obligation = (
            StageSubjectRoleObligation(
                role=StageBaselineRole.VERTICAL_CIRCULATION,
                disposition=StageSubjectDisposition.REQUIRED,
                target_refs=(f"design-component:{component_id}",),
                evidence_refs=("evidence:typed-vertical-circulation",),
                authority_refs=("authority:typed-vertical-circulation",),
            ),
        ) if typed_vertical else ()
        entries.append(
            StageSubjectInventoryEntry(
                component_id=component_id,
                identity_ref=f"design-component:{component_id}",
                parent_component_id="stage-root",
                semantic_kind=semantic_kind,
                component_digest=(SHA_A if ordinal % 2 else SHA_B),
                geometry_object_ids=(f"stair-object-{ordinal:02d}",),
                binding_ids=(f"stair-binding-{ordinal:02d}",),
                role_obligations=tuple(sorted(
                    (*obligations, *vertical_obligation),
                    key=lambda item: item.role.value,
                )),
            )
        )
    return replace(
        base,
        inventory_id=f"{base.inventory_id}-semantic-fixture",
        entries=tuple(entries),
    )


def circulation_only_inventory(
    sources: StageBaselineSourceSet,
    *,
    level: StageBaselineLevel,
    semantic_kind: str = "stair-reservation",
) -> StageSubjectInventory:
    inventory = inventory_with_semantic_kinds(
        sources,
        level=level,
        semantic_kinds=(semantic_kind,),
    )
    return replace(
        inventory,
        entries=tuple(
            replace(
                entry,
                role_obligations=tuple(
                    obligation
                    if obligation.role
                    in {
                        StageBaselineRole.COMPONENT_LINEAGE,
                        StageBaselineRole.SPATIAL_ENVELOPE,
                        StageBaselineRole.VERTICAL_CIRCULATION,
                    }
                    else replace(
                        obligation,
                        disposition=StageSubjectDisposition.NOT_APPLICABLE,
                        target_refs=(),
                        evidence_refs=("evidence:terminal-topology",),
                        authority_refs=("authority:terminal-topology",),
                    )
                    for obligation in entry.role_obligations
                ),
            )
            for entry in inventory.entries
        ),
    )


def current_circulation_inventory(
    sources: StageBaselineSourceSet,
    *,
    level: StageBaselineLevel,
    semantic_kind: str = "stair-reservation",
) -> StageSubjectInventory:
    """Upgrade the focused circulation fixture to the current policy."""

    legacy = circulation_only_inventory(
        sources,
        level=level,
        semantic_kind=semantic_kind,
    )
    policy = current_semantic_capability_policy()
    bindings = tuple(
        binding
        for entry in legacy.entries
        for binding in bind_semantic_rule_packs(
            policy=policy,
            branch=legacy.branch,
            stage_id=legacy.stage_id,
            stage_subject_digest=legacy.stage_subject_digest,
            component_ref=entry.identity_ref,
            component_digest=entry.component_digest,
            semantic_kind=entry.semantic_kind,
            baseline_level=legacy.baseline_level,
        )
    )
    binding_by_component = {
        binding.component_ref: binding for binding in bindings
    }
    entries = tuple(
        replace(
            entry,
            role_obligations=tuple(
                sorted(
                    (
                        *(
                            item
                            for item in entry.role_obligations
                            if item.role
                            is not StageBaselineRole.VERTICAL_CIRCULATION
                        ),
                        *(
                            (
                                StageSubjectRoleObligation(
                                    role=(
                                        StageBaselineRole.VERTICAL_CIRCULATION
                                    ),
                                    disposition=(
                                        StageSubjectDisposition.REQUIRED
                                    ),
                                    target_refs=(entry.identity_ref,),
                                    evidence_refs=(binding.basis_ref,),
                                    authority_refs=(binding.authority_ref,),
                                ),
                            )
                            if (
                                binding := binding_by_component.get(
                                    entry.identity_ref
                                )
                            )
                            else ()
                        ),
                    ),
                    key=lambda item: item.role.value,
                )
            ),
        )
        for entry in legacy.entries
    )
    return replace(
        legacy,
        entries=entries,
        visual_inventory_ref=ProjectRecordRef(
            project_id=legacy.branch.run.project_id,
            relative_path=(
                f"runs/{legacy.branch.run.run_id}/branches/"
                f"{legacy.branch.branch_id}/records/visual-inventory.json"
            ),
            sha256="f" * 64,
        ),
        visual_inventory_digest="f" * 64,
        semantic_policy_ref=ProjectRecordRef(
            project_id=legacy.branch.run.project_id,
            relative_path=(
                f"runs/{legacy.branch.run.run_id}/branches/"
                f"{legacy.branch.branch_id}/records/semantic-policy.json"
            ),
            sha256="e" * 64,
        ),
        semantic_policy=policy,
        semantic_rule_pack_bindings=bindings,
    )


def bind_terminal_topology(
    sources: StageBaselineSourceSet,
    inventory: StageSubjectInventory,
) -> tuple[
    StageBaselineSourceSet,
    tuple[StageCheckRequirement, ...],
    tuple[CheckReceiptEnvelope, ...],
]:
    contract = sources.vertical_circulation[0].contract
    stair_ref = contract.component_refs[0]
    stair_entry = next(
        item for item in inventory.entries if item.identity_ref == stair_ref
    )
    question = RelationDerivationQuestion(
        question_id="stair-terminals",
        projection=RelationProjection.ACCESS,
        scenario_ref="scenario:vertical-circulation",
        subject_refs=(stair_ref,),
        target_refs=tuple(sorted((
            contract.lower_interface.level_ref,
            contract.upper_interface.level_ref,
        ))),
        allowed_relation_kinds=(ArchitecturalRelationKind.ACCESS,),
        rule_envelopes=(
            RelationRuleEnvelope(
                relation_kind=ArchitecturalRelationKind.ACCESS,
                subject_role="from",
                counted_role="to",
                minimum_count=2,
                maximum_count=2,
            ),
        ),
        basis_ids=("terminal-policy", "terminal-topology"),
        prompt="Bind both stair terminals to retained level interfaces.",
    )
    bases = tuple(
        RelationBasisBinding(
            basis_id=basis_id,
            basis_kind=basis_kind,
            basis_use=basis_use,
            question_refs=(question.ref,),
            allowed_relation_kinds=(ArchitecturalRelationKind.ACCESS,),
            epistemic_status=RelationEpistemicStatus.DERIVED,
            evidence_refs=("evidence:terminal-topology",),
            authority_refs=("authority:terminal-topology",),
            summary="The retained topology binds the stair to both levels.",
        )
        for basis_id, basis_kind, basis_use in (
            ("terminal-policy", RelationBasisKind.HUMAN, RelationBasisUse.POLICY),
            ("terminal-topology", RelationBasisKind.RAG, RelationBasisUse.TOPOLOGY),
        )
    )
    component_nodes = tuple(
        ArchitecturalNode(
            node_ref=entry.identity_ref,
            node_kind=ArchitecturalNodeKind.COMPONENT,
            semantic_kind=entry.semantic_kind,
            stage_id=inventory.stage_id,
            source_refs=(f"stage-subject-entry:{entry.entry_digest}",),
        )
        for entry in inventory.entries
    )
    level_nodes = tuple(
        ArchitecturalNode(
            node_ref=level_ref,
            node_kind=ArchitecturalNodeKind.INTERFACE,
            semantic_kind="level-interface",
            stage_id=inventory.stage_id,
            source_refs=("evidence:terminal-topology",),
        )
        for level_ref in (
            contract.lower_interface.level_ref,
            contract.upper_interface.level_ref,
        )
    )
    context = RelationAuthoringContext(
        context_id="stair-terminal-topology",
        branch=inventory.branch,
        stage_id=inventory.stage_id,
        state_digest=SHA_A,
        scope_digest=SHA_B,
        stage_subject_digest=inventory.stage_subject_digest,
        subject_inventory_ref=f"stage-subject-inventory:{inventory.inventory_digest}",
        subject_inventory_digest=inventory.inventory_digest,
        nodes=(*component_nodes, *level_nodes),
        questions=(question,),
        bases=bases,
    )
    relations = tuple(
        RelationProposalSpec(
            relation_id=f"access-{label}",
            question_refs=(question.ref,),
            kind=ArchitecturalRelationKind.ACCESS,
            participants=(
                RelationParticipant(role="from", node_ref=stair_ref),
                RelationParticipant(role="to", node_ref=level_ref),
            ),
            scenario_ref=question.scenario_ref,
            basis_ids=("terminal-topology",),
        )
        for label, level_ref in (
            ("lower", contract.lower_interface.level_ref),
            ("upper", contract.upper_interface.level_ref),
        )
    )
    rule = RelationRuleProposalSpec(
        rule_id="stair-terminal-count",
        question_refs=(question.ref,),
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind=stair_entry.semantic_kind,
        relation_kind=ArchitecturalRelationKind.ACCESS,
        subject_role="from",
        counted_role="to",
        minimum_count=2,
        maximum_count=2,
        scenario_ref=question.scenario_ref,
        basis_ids=("terminal-policy",),
    )
    proposal = RelationAuthoringProposal(
        context_digest=context.context_digest,
        answers=(
            RelationDerivationAnswer(
                question_ref=question.ref,
                status=RelationAnswerStatus.PROPOSED,
                relation_ids=tuple(sorted(item.relation_id for item in relations)),
                rule_ids=(rule.rule_id,),
                rationale="Both exact terminal relations are retained.",
            ),
        ),
        relations=relations,
        rules=(rule,),
    )
    compilation = compile_relation_authoring(context, proposal)
    open_requirements = relation_authoring_stage_requirements(
        context,
        compilation,
        inventory,
    )
    verification_requirement = next(
        item
        for item in open_requirements
        if item.requirement_id == "relation-verification-stair-terminals"
    )
    verification_receipt = CheckReceiptEnvelope(
        check_id=verification_requirement.requirement_id,
        checker_id=verification_requirement.checker_id,
        checker_version="1.0.0",
        branch=inventory.branch,
        scope_digest=SHA_B,
        subject_refs=verification_requirement.denominator_refs,
        subject_digest=inventory.stage_subject_digest,
        status=CheckStatus.PASS,
        source_refs=verification_requirement.required_source_refs,
        authority_refs=verification_requirement.required_authority_refs,
        coverage_denominator=verification_requirement.denominator_refs,
        covered_refs=verification_requirement.denominator_refs,
    )
    promotion = promote_verified_relation_graph(
        context,
        compilation,
        inventory,
        (verification_receipt,),
    )
    topology = RelationTopologyBaselineSource(
        context=context,
        compilation=compilation,
        promotion=promotion,
    )
    requirements = relation_authoring_stage_requirements(
        context,
        compilation,
        inventory,
        promotion=promotion,
    )
    _manifest, coverage_receipt = check_relation_coverage(
        promotion.graph,
        compilation.policy,
        inventory,
        compilation.slots,
        promotion=promotion.receipt,
    )
    return (
        replace(sources, relation_topology=(topology,)),
        requirements,
        (verification_receipt, coverage_receipt),
    )


def compile_baseline(
    sources: StageBaselineSourceSet,
    *,
    level: StageBaselineLevel,
):
    requirements, receipts = requirement_and_receipts(
        sources,
        level=level,
    )
    selected_profile = profile(requirements)
    return compile_stage_baseline_coverage(
        selected_profile,
        level=level,
        sources=sources,
        subject_digest=SUBJECT_DIGEST,
        subject_inventory=subject_inventory(sources, level=level),
        check_receipts=receipts,
    )


class StageBaselineTests(unittest.TestCase):
    def test_spatial_source_binds_exact_replayable_input(self) -> None:
        source = physical_sources().spatial_layout[0]

        with self.assertRaisesRegex(
            StageBaselineError,
            "input digest does not match validator input",
        ):
            SpatialLayoutBaselineSource(
                profile=replace(source.profile, input_digest="f" * 64),
                validator_input=source.validator_input,
            )

    def test_current_spatial_sources_require_relation_topology(self) -> None:
        sources = physical_sources()
        inventory = subject_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
        )
        requirements, receipts = requirement_and_receipts(sources)
        receipt = compile_stage_baseline_coverage(
            profile(requirements),
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )

        self.assertIs(receipt.status, StageBaselineStatus.OPEN)
        self.assertEqual(
            receipt.stage_subject_inventory_digest,
            inventory.inventory_digest,
        )
        self.assertEqual(
            receipt.to_dict()["schema"],
            "StageBaselineCoverageReceipt@3",
        )
        self.assertEqual(
            receipt.missing_roles,
            (StageBaselineRole.ASSEMBLY_RELATIONSHIPS,),
        )
        self.assertEqual(
            {item.role for item in receipt.coverage},
            {
                StageBaselineRole.COMPONENT_LINEAGE,
                StageBaselineRole.SPATIAL_ENVELOPE,
                StageBaselineRole.OPENING_CLEARANCE,
                StageBaselineRole.LOAD_PATH,
            },
        )
        self.assertTrue(
            all(item.source_digests for item in receipt.coverage)
        )
        self.assertEqual(
            StageBaselineCoverageReceipt.from_dict(receipt.to_dict()),
            receipt,
        )

        self.assertEqual(
            StageBaselineSourceSet.from_dict(sources.to_dict()),
            sources,
        )

    def test_exact_legacy_spatial_source_replays_read_only(self) -> None:
        sources = legacy_sources(physical_sources())
        inventory = subject_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
        )
        requirements, receipts = requirement_and_receipts(sources)
        receipt = compile_stage_baseline_coverage(
            profile(requirements),
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )

        self.assertTrue(sources.is_legacy_read_only)
        self.assertIs(receipt.status, StageBaselineStatus.SATISFIED)
        self.assertEqual(receipt.missing_roles, ())

        with self.assertRaisesRegex(
            StageBaselineError,
            "legacy stage baseline sources are read-only",
        ):
            derive_stage_requirement_profile(
                profile(requirements),
                level=StageBaselineLevel.SPATIAL,
                sources=sources,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=inventory,
            )

        legacy_payload = receipt.to_dict()
        legacy_payload["schema"] = "StageBaselineCoverageReceipt@2"
        del legacy_payload["stage_subject_inventory_digest"]
        legacy = StageBaselineCoverageReceipt.from_dict(legacy_payload)
        self.assertIsNone(legacy.stage_subject_inventory_digest)
        self.assertEqual(legacy.to_dict(), legacy_payload)

    def test_exact_inventory_subject_digest_is_required(self) -> None:
        sources = physical_sources()
        requirements, receipts = requirement_and_receipts(sources)
        inventory = replace(
            subject_inventory(
                sources,
                level=StageBaselineLevel.SPATIAL,
            ),
            stage_subject_digest=SHA_A,
        )

        with self.assertRaisesRegex(
            StageBaselineError,
            "stage_subject_digest does not match",
        ):
            compile_stage_baseline_coverage(
                profile(requirements),
                level=StageBaselineLevel.SPATIAL,
                sources=sources,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=inventory,
                check_receipts=receipts,
            )

    def test_synchronized_assembly_shrink_stays_open_against_inventory(self) -> None:
        complete_sources = physical_sources()
        inventory = subject_inventory(
            complete_sources,
            level=StageBaselineLevel.SPATIAL,
        )
        reduced_sources = physical_sources(include_opening=False)
        requirements, receipts = requirement_and_receipts(reduced_sources)
        receipt = compile_stage_baseline_coverage(
            profile(requirements),
            level=StageBaselineLevel.SPATIAL,
            sources=reduced_sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )

        self.assertIs(receipts[-1].status, CheckStatus.PASS)
        self.assertIs(receipt.status, StageBaselineStatus.OPEN)
        self.assertIn(
            StageBaselineRole.OPENING_CLEARANCE,
            receipt.missing_roles,
        )

    def test_missing_spatial_inventory_target_stays_open(self) -> None:
        sources = physical_sources()
        targets = role_target_refs(sources)[
            StageBaselineRole.SPATIAL_ENVELOPE
        ]
        inventory = subject_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
            obligation_overrides={
                StageBaselineRole.SPATIAL_ENVELOPE: (
                    StageSubjectRoleObligation(
                        role=StageBaselineRole.SPATIAL_ENVELOPE,
                        disposition=StageSubjectDisposition.REQUIRED,
                        target_refs=tuple(
                            sorted((*targets, "component:missing-spatial"))
                        ),
                        evidence_refs=("evidence:stage-inventory",),
                        authority_refs=("authority:stage-inventory",),
                    )
                ),
            },
        )
        requirements, receipts = requirement_and_receipts(sources)
        receipt = compile_stage_baseline_coverage(
            profile(requirements),
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )

        self.assertIs(receipt.status, StageBaselineStatus.OPEN)
        self.assertIn(
            StageBaselineRole.SPATIAL_ENVELOPE,
            receipt.missing_roles,
        )

    def test_unapproved_not_applicable_obligation_stays_open(self) -> None:
        sources = physical_sources()
        inventory = subject_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
            obligation_overrides={
                StageBaselineRole.SPATIAL_ENVELOPE: (
                    StageSubjectRoleObligation(
                        role=StageBaselineRole.SPATIAL_ENVELOPE,
                        disposition=(
                            StageSubjectDisposition.NOT_APPLICABLE
                        ),
                        target_refs=(),
                        evidence_refs=("source:unapproved-spatial-na",),
                        authority_refs=("authority:unapproved-spatial-na",),
                    )
                ),
            },
        )
        requirements, receipts = requirement_and_receipts(sources)
        receipt = compile_stage_baseline_coverage(
            profile(requirements),
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )

        self.assertIs(receipt.status, StageBaselineStatus.OPEN)
        self.assertIn(
            StageBaselineRole.SPATIAL_ENVELOPE,
            receipt.missing_roles,
        )

    def test_missing_typed_opening_and_load_path_stays_open(self) -> None:
        receipt = compile_baseline(
            physical_sources(
                include_opening=False,
                include_load_path=False,
            ),
            level=StageBaselineLevel.SPATIAL,
        )

        self.assertIs(receipt.status, StageBaselineStatus.OPEN)
        self.assertEqual(
            receipt.missing_roles,
            (
                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                StageBaselineRole.LOAD_PATH,
                StageBaselineRole.OPENING_CLEARANCE,
            ),
        )

    def test_uncovered_subject_obligation_cannot_credit_baseline_roles(self) -> None:
        sources = physical_sources()
        assembly = sources.assembly[0]
        incomplete = replace(
            assembly,
            coverage_manifest=replace(
                assembly.coverage_manifest,
                obligations=tuple(
                    replace(item, requirement_id="omitted-requirement")
                    if item.obligation_id == "clear-region-source"
                    else item
                    for item in assembly.coverage_manifest.obligations
                ),
            ),
        )
        incomplete_sources = replace(sources, assembly=(incomplete,))

        receipt = compile_baseline(
            incomplete_sources,
            level=StageBaselineLevel.SPATIAL,
        )

        self.assertIs(receipt.status, StageBaselineStatus.OPEN)
        self.assertTrue(
            {
                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                StageBaselineRole.OPENING_CLEARANCE,
                StageBaselineRole.LOAD_PATH,
            }
            <= set(receipt.missing_roles)
        )

    def test_fake_checker_and_denominator_can_close_closure_not_baseline(self) -> None:
        sources = physical_sources()
        valid_requirements, valid_receipts = requirement_and_receipts(sources)
        component, spatial, _assembly = valid_requirements
        fake_assembly = StageCheckRequirement(
            requirement_id="fake-assembly",
            checker_id="assembly-relationship-checker",
            target_kind=RequirementTargetKind.ASSEMBLY,
            basis_mode=RequirementBasisMode.UNIVERSAL,
            denominator_refs=(
                "fake:assembly",
                "assembly-requirement:support:x:not-a-digest",
                "assembly-requirement:opening_clear:x:not-a-digest",
                "assembly-requirement:load_path_to_foundation:x:not-a-digest",
            ),
        )
        fake_receipt = CheckReceiptEnvelope(
            check_id=fake_assembly.requirement_id,
            checker_id=fake_assembly.checker_id,
            checker_version="1.0.0",
            branch=branch(),
            scope_digest=SHA_B,
            subject_refs=fake_assembly.denominator_refs,
            subject_digest=SUBJECT_DIGEST,
            status=CheckStatus.PASS,
            coverage_denominator=fake_assembly.denominator_refs,
            covered_refs=fake_assembly.denominator_refs,
        )
        selected_profile = profile((component, spatial, fake_assembly))
        receipts = (*valid_receipts[:2], fake_receipt)

        closure = compile_composite_stage_closure(
            selected_profile,
            subject_digest=SUBJECT_DIGEST,
            check_receipts=receipts,
        )
        baseline = compile_stage_baseline_coverage(
            selected_profile,
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=subject_inventory(
                sources,
                level=StageBaselineLevel.SPATIAL,
            ),
            check_receipts=receipts,
        )

        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        self.assertIs(baseline.status, StageBaselineStatus.OPEN)
        self.assertEqual(
            set(baseline.missing_roles),
            {
                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                StageBaselineRole.OPENING_CLEARANCE,
                StageBaselineRole.LOAD_PATH,
            },
        )

    def test_unverified_no_opening_applicability_cannot_close_role(self) -> None:
        sources = physical_sources(include_opening=False)
        requirements, receipts = requirement_and_receipts(sources)
        no_opening = StageCheckRequirement(
            requirement_id="opening-not-applicable",
            checker_id="opening-clearance-applicability-checker",
            target_kind=RequirementTargetKind.RELATION,
            basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
            denominator_refs=("applicability:no-openings",),
            required_authority_refs=("authority:approved-no-openings",),
            allow_not_applicable=True,
        )
        selected_profile = profile((*requirements, no_opening))
        baseline = compile_stage_baseline_coverage(
            selected_profile,
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=subject_inventory(
                sources,
                level=StageBaselineLevel.SPATIAL,
            ),
            check_receipts=receipts,
        )

        self.assertIs(baseline.status, StageBaselineStatus.OPEN)
        self.assertIn(
            StageBaselineRole.OPENING_CLEARANCE,
            baseline.missing_roles,
        )

    def test_levels_add_recomputed_material_then_cad_readback(self) -> None:
        base_sources = physical_sources()
        material_ledger = material_ledger_fixture()
        material_profile = replace(
            material_profile_fixture(material_ledger),
            branch=branch(),
            scope_digest=SHA_B,
        )
        material_source = MaterialBindingBaselineSource(
            profile=material_profile,
            ledger=material_ledger,
            snapshot=material_snapshot_fixture(material_profile),
        )
        cad_profile = replace(
            cad_profile_fixture(),
            branch=branch(),
            stage_id="stage-2",
            scope_digest=SHA_B,
        )
        cad_source = CadReadbackBaselineSource(
            profile=cad_profile,
            snapshot=cad_snapshot_fixture(cad_profile),
        )
        developed_sources = legacy_sources(replace(
            base_sources,
            material_binding=(material_source,),
        ))
        coordinated_sources = legacy_sources(replace(
            developed_sources,
            cad_readback=(cad_source,),
        ))

        self.assertEqual(
            StageBaselineSourceSet.from_dict(
                coordinated_sources.to_dict()
            ),
            coordinated_sources,
        )

        developed = compile_baseline(
            developed_sources,
            level=StageBaselineLevel.DEVELOPED,
        )
        coordinated = compile_baseline(
            coordinated_sources,
            level=StageBaselineLevel.COORDINATED,
        )

        self.assertIs(developed.status, StageBaselineStatus.SATISFIED)
        self.assertIs(coordinated.status, StageBaselineStatus.SATISFIED)

    def test_vertical_semantic_classifier_uses_inventory_identity_refs(self) -> None:
        positive_kinds = (
            "stair",
            "STAIRCASE",
            "spiral-stair-reservation",
            "exterior-stair-envelope",
            "vertical_circulation",
        )
        negative_kinds = (
            "escalator-envelope",
            "chair-storage",
            "circulation-core",
            "upstairs-room",
        )
        sources = physical_sources()
        requirements, receipts = requirement_and_receipts(sources)

        for semantic_kind in positive_kinds:
            with self.subTest(semantic_kind=semantic_kind):
                inventory = inventory_with_semantic_kinds(
                    sources,
                    level=StageBaselineLevel.SPATIAL,
                    semantic_kinds=(semantic_kind,),
                )
                baseline = compile_stage_baseline_coverage(
                    profile(requirements),
                    level=StageBaselineLevel.SPATIAL,
                    sources=sources,
                    subject_digest=SUBJECT_DIGEST,
                    subject_inventory=inventory,
                    check_receipts=receipts,
                )
                self.assertIn(
                    StageBaselineRole.VERTICAL_CIRCULATION,
                    baseline.required_roles,
                )
                self.assertIn(
                    StageBaselineRole.VERTICAL_CIRCULATION,
                    baseline.missing_roles,
                )

        for semantic_kind in negative_kinds:
            with self.subTest(semantic_kind=semantic_kind):
                inventory = inventory_with_semantic_kinds(
                    sources,
                    level=StageBaselineLevel.SPATIAL,
                    semantic_kinds=(semantic_kind,),
                    typed_vertical=False,
                )
                baseline = compile_stage_baseline_coverage(
                    profile(requirements),
                    level=StageBaselineLevel.SPATIAL,
                    sources=sources,
                    subject_digest=SUBJECT_DIGEST,
                    subject_inventory=inventory,
                    check_receipts=receipts,
                )
                self.assertNotIn(
                    StageBaselineRole.VERTICAL_CIRCULATION,
                    baseline.required_roles,
                )

    def test_spatial_reservation_is_recomputed_and_covers_dynamic_role(self) -> None:
        circulation = circulation_contract_for_baseline(
            VerticalCirculationMaturity.RESERVATION
        )
        sources = sources_with_circulation(circulation)
        inventory = circulation_only_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
            semantic_kind="spiral-stair-reservation",
        )
        sources, relation_requirements, relation_receipts = bind_terminal_topology(
            sources,
            inventory,
        )
        requirements, receipts = requirement_and_receipts(
            sources,
            level=StageBaselineLevel.SPATIAL,
        )
        requirements = tuple(sorted(
            (*requirements, *relation_requirements),
            key=lambda item: item.requirement_id,
        ))
        receipts = tuple((*receipts, *relation_receipts))

        baseline = compile_stage_baseline_coverage(
            profile(requirements),
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )

        self.assertIn(
            StageBaselineRole.VERTICAL_CIRCULATION,
            {item.role for item in baseline.coverage},
        )
        self.assertNotIn(
            StageBaselineRole.VERTICAL_CIRCULATION,
            baseline.missing_roles,
        )
        maturity_receipt = next(
            item
            for item in receipts
            if item.checker_id
            == "vertical-circulation-stage-maturity-checker"
        )
        self.assertIs(maturity_receipt.status, CheckStatus.PASS)
        self.assertIn(
            "not-walkable",
            maturity_receipt.findings[0].code,
        )
        self.assertEqual(
            StageBaselineSourceSet.from_dict(sources.to_dict()),
            sources,
        )

    def test_current_stair_rules_are_exact_stage_check_denominator(self) -> None:
        circulation = circulation_contract_for_baseline(
            VerticalCirculationMaturity.RESERVATION
        )
        sources = sources_with_circulation(circulation)
        inventory = current_circulation_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
        )
        sources, relation_requirements, relation_receipts = bind_terminal_topology(
            sources,
            inventory,
        )
        project_requirements, raw_receipts = requirement_and_receipts(
            sources,
            level=StageBaselineLevel.SPATIAL,
        )
        project_requirements = tuple(
            item
            for item in project_requirements
            if item.checker_id
            != "vertical-circulation-stage-maturity-checker"
        )
        base_profile = profile(
            tuple(
                sorted(
                    (*project_requirements, *relation_requirements),
                    key=lambda item: item.requirement_id,
                )
            )
        )
        derived_profile = derive_stage_requirement_profile(
            base_profile,
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
        )
        binding = inventory.semantic_rule_pack_bindings[0]
        requirement = next(
            item
            for item in derived_profile.requirements
            if item.checker_id
            == "vertical-circulation-stage-maturity-checker"
        )
        self.assertTrue(
            {binding.ref, *binding.rule_refs}.issubset(
                set(requirement.denominator_refs)
            )
        )
        raw = next(
            item
            for item in raw_receipts
            if item.checker_id
            == "vertical-circulation-stage-maturity-checker"
        )
        bound = replace(
            raw,
            subject_refs=requirement.denominator_refs,
            source_refs=requirement.required_source_refs,
            authority_refs=requirement.required_authority_refs,
            coverage_denominator=requirement.denominator_refs,
            covered_refs=requirement.denominator_refs,
        )
        receipts = tuple(
            sorted(
                (
                    *(
                        item
                        for item in raw_receipts
                        if item.checker_id
                        != "vertical-circulation-stage-maturity-checker"
                    ),
                    bound,
                    *relation_receipts,
                ),
                key=lambda item: item.check_id,
            )
        )
        covered = compile_stage_baseline_coverage(
            derived_profile,
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )
        self.assertNotIn(
            StageBaselineRole.VERTICAL_CIRCULATION,
            covered.missing_roles,
        )

        omitted = binding.rule_refs[0]
        narrowed = tuple(
            ref for ref in requirement.denominator_refs if ref != omitted
        )
        forged = replace(
            bound,
            subject_refs=narrowed,
            coverage_denominator=narrowed,
            covered_refs=narrowed,
        )
        rejected = compile_stage_baseline_coverage(
            derived_profile,
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=tuple(
                forged if item.check_id == bound.check_id else item
                for item in receipts
            ),
        )
        self.assertIn(
            StageBaselineRole.VERTICAL_CIRCULATION,
            rejected.missing_roles,
        )

    def test_terminal_relation_must_exist_in_exact_retained_topology(self) -> None:
        sources = sources_with_circulation(
            circulation_contract_for_baseline(
                VerticalCirculationMaturity.RESERVATION
            )
        )
        inventory = circulation_only_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
        )
        sources, relation_requirements, relation_receipts = bind_terminal_topology(
            sources,
            inventory,
        )
        source = sources.vertical_circulation[0]
        invented_contract = replace(
            source.contract,
            lower_interface=replace(
                source.contract.lower_interface,
                relation_ref="architectural-relation:invented-lower",
            ),
        )
        invented_source = circulation_source_for_baseline(invented_contract)
        sources = replace(sources, vertical_circulation=(invented_source,))
        requirements, receipts = requirement_and_receipts(sources)
        requirements = tuple(sorted(
            (*requirements, *relation_requirements),
            key=lambda item: item.requirement_id,
        ))
        receipts = tuple((*receipts, *relation_receipts))

        with self.assertRaisesRegex(StageBaselineError, "retained topology relation"):
            compile_stage_baseline_coverage(
                profile(requirements),
                level=StageBaselineLevel.SPATIAL,
                sources=sources,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=inventory,
                check_receipts=receipts,
            )

    def test_vertical_maturity_progression_is_stage_owned(self) -> None:
        cases = (
            (
                StageBaselineLevel.SPATIAL,
                VerticalCirculationMaturity.RESERVATION,
                True,
            ),
            (
                StageBaselineLevel.SPATIAL,
                VerticalCirculationMaturity.RESOLVED_PATH,
                True,
            ),
            (
                StageBaselineLevel.DEVELOPED,
                VerticalCirculationMaturity.RESERVATION,
                False,
            ),
            (
                StageBaselineLevel.DEVELOPED,
                VerticalCirculationMaturity.RESOLVED_PATH,
                False,
            ),
            (
                StageBaselineLevel.COORDINATED,
                VerticalCirculationMaturity.RESOLVED_PATH,
                False,
            ),
            (
                StageBaselineLevel.COORDINATED,
                VerticalCirculationMaturity.ASSEMBLY,
                False,
            ),
        )

        for level, maturity, expected_covered in cases:
            with self.subTest(level=level, maturity=maturity):
                circulation = circulation_contract_for_baseline(maturity)
                sources = sources_with_circulation(circulation)
                inventory = circulation_only_inventory(
                    sources,
                    level=level,
                    semantic_kind="exterior-stair-envelope",
                )
                sources, relation_requirements, relation_receipts = bind_terminal_topology(
                    sources,
                    inventory,
                )
                requirements, receipts = requirement_and_receipts(
                    sources,
                    level=level,
                )
                requirements = tuple(sorted(
                    (*requirements, *relation_requirements),
                    key=lambda item: item.requirement_id,
                ))
                receipts = tuple((*receipts, *relation_receipts))
                baseline = compile_stage_baseline_coverage(
                    profile(requirements),
                    level=level,
                    sources=sources,
                    subject_digest=SUBJECT_DIGEST,
                    subject_inventory=inventory,
                    check_receipts=receipts,
                )
                covered = {
                    item.role for item in baseline.coverage
                }
                self.assertEqual(
                    StageBaselineRole.VERTICAL_CIRCULATION in covered,
                    expected_covered,
                )
                if (
                    level is not StageBaselineLevel.SPATIAL
                    and maturity
                    is required_vertical_circulation_maturity(level)
                ):
                    receipt = next(
                        item
                        for item in receipts
                        if item.checker_id
                        == "vertical-circulation-stage-maturity-checker"
                    )
                    self.assertIs(receipt.status, CheckStatus.UNKNOWN)
                    self.assertIn(
                        "vertical-circulation-producer-replay-unknown",
                        {item.code for item in receipt.findings},
                    )
                self.assertEqual(
                    required_vertical_circulation_maturity(level),
                    {
                        StageBaselineLevel.SPATIAL: (
                            VerticalCirculationMaturity.RESERVATION
                        ),
                        StageBaselineLevel.DEVELOPED: (
                            VerticalCirculationMaturity.ASSEMBLY
                        ),
                        StageBaselineLevel.COORDINATED: (
                            VerticalCirculationMaturity.ASSEMBLY
                        ),
                    }[level],
                )

    def test_missing_circulation_subject_and_fake_pass_stay_open(self) -> None:
        circulation = circulation_contract_for_baseline(
            VerticalCirculationMaturity.RESERVATION
        )
        contradictory = replace(
            circulation,
            aabb_precheck=replace(
                circulation.aabb_precheck,
                maximum=(4.5, 1.0, 2.5),
            ),
        )
        sources = sources_with_circulation(contradictory)
        inventory = inventory_with_semantic_kinds(
            sources,
            level=StageBaselineLevel.SPATIAL,
            semantic_kinds=(
                "spiral-stair-reservation",
                "exterior-stair-envelope",
            ),
        )
        requirements, real_receipts = requirement_and_receipts(sources)
        circulation_requirement = next(
            item
            for item in requirements
            if item.checker_id
            == "vertical-circulation-stage-maturity-checker"
        )
        fake_pass = CheckReceiptEnvelope(
            check_id=circulation_requirement.requirement_id,
            checker_id=circulation_requirement.checker_id,
            checker_version="fake",
            branch=branch(),
            scope_digest=SHA_B,
            subject_refs=circulation_requirement.denominator_refs,
            subject_digest=SUBJECT_DIGEST,
            status=CheckStatus.PASS,
            coverage_denominator=circulation_requirement.denominator_refs,
            covered_refs=circulation_requirement.denominator_refs,
        )
        receipts = tuple(
            fake_pass
            if item.check_id == circulation_requirement.requirement_id
            else item
            for item in real_receipts
        )
        selected_profile = profile(requirements)

        closure = compile_composite_stage_closure(
            selected_profile,
            subject_digest=SUBJECT_DIGEST,
            check_receipts=receipts,
        )
        self.assertIs(closure.status, StageClosureStatus.OPEN)
        with self.assertRaisesRegex(StageBaselineError, "every stair instance"):
            compile_stage_baseline_coverage(
                selected_profile,
                level=StageBaselineLevel.SPATIAL,
                sources=sources,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=inventory,
                check_receipts=receipts,
            )

    def test_reservation_keeps_unknown_solver_without_realized_claims(self) -> None:
        contract = replace(
            circulation_contract_for_baseline(
                VerticalCirculationMaturity.RESERVATION
            ),
            design_state_digest=SHA_A,
            length_unit_ref="unit:meter",
        )
        request, _solved = circulation_solver_pair(contract)
        request = replace(request, width=None)
        result = solve_stair(request)
        self.assertIs(result.status, StairSolveStatus.UNKNOWN)
        self.assertIsNone(result.assembly)
        contract = replace(
            contract,
            solver_result_digest=canonical_digest(result.to_dict()),
        )
        source = VerticalCirculationBaselineSource(
            contract=contract,
            solve_request=request,
            solve_result=result,
            readback_source_digest=None,
        )
        sources = replace(
            physical_sources(),
            vertical_circulation=(source,),
        )
        inventory = circulation_only_inventory(
            sources,
            level=StageBaselineLevel.SPATIAL,
            semantic_kind="stair-reservation",
        )
        sources, relation_requirements, relation_receipts = bind_terminal_topology(
            sources,
            inventory,
        )
        requirements, receipts = requirement_and_receipts(
            sources,
            level=StageBaselineLevel.SPATIAL,
        )
        requirements = tuple(sorted(
            (*requirements, *relation_requirements),
            key=lambda item: item.requirement_id,
        ))
        receipts = tuple((*receipts, *relation_receipts))
        baseline = compile_stage_baseline_coverage(
            profile(requirements),
            level=StageBaselineLevel.SPATIAL,
            sources=sources,
            subject_digest=SUBJECT_DIGEST,
            subject_inventory=inventory,
            check_receipts=receipts,
        )

        self.assertIn(
            StageBaselineRole.VERTICAL_CIRCULATION,
            {item.role for item in baseline.coverage},
        )
        self.assertIsNone(contract.artifact_sha256)
        self.assertIsNone(contract.program_digest)
        self.assertIsNone(contract.readback_digest)
        self.assertEqual(contract.solver_tread_refs, ())
        self.assertEqual(contract.solver_landing_refs, ())
        self.assertEqual(contract.object_bindings, ())

    def test_vertical_source_rejects_crossed_solver_design_and_readback(self) -> None:
        reservation = circulation_contract_for_baseline(
            VerticalCirculationMaturity.RESERVATION
        )
        reservation_source = circulation_source_for_baseline(reservation)
        with self.assertRaisesRegex(StageBaselineError, "solver result"):
            replace(
                reservation_source,
                contract=replace(
                    reservation_source.contract,
                    solver_result_digest=SHA_B,
                ),
            )

        crossed_design_source = replace(
            reservation_source,
            contract=replace(
                reservation_source.contract,
                design_state_digest="f" * 64,
            ),
        )
        crossed_design_sources = replace(
            physical_sources(),
            vertical_circulation=(crossed_design_source,),
        )
        inventory = inventory_with_semantic_kinds(
            crossed_design_sources,
            level=StageBaselineLevel.SPATIAL,
            semantic_kinds=("stair-reservation",),
        )
        requirements, receipts = requirement_and_receipts(
            crossed_design_sources,
            level=StageBaselineLevel.SPATIAL,
        )
        with self.assertRaisesRegex(StageBaselineError, "design state digest"):
            compile_stage_baseline_coverage(
                profile(requirements),
                level=StageBaselineLevel.SPATIAL,
                sources=crossed_design_sources,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=inventory,
                check_receipts=receipts,
            )

        resolved = circulation_contract_for_baseline(
            VerticalCirculationMaturity.RESOLVED_PATH
        )
        resolved_sources = sources_with_circulation(resolved)
        resolved_source = resolved_sources.vertical_circulation[0]
        geometry = replace(
            resolved_source.contract.geometry_witness,
            readback_digest="f" * 64,
        )
        crossed_readback_source = replace(
            resolved_source,
            contract=replace(
                resolved_source.contract,
                readback_digest="f" * 64,
                geometry_witness=geometry,
            ),
        )
        crossed_readback_sources = replace(
            resolved_sources,
            vertical_circulation=(crossed_readback_source,),
        )
        inventory = circulation_only_inventory(
            crossed_readback_sources,
            level=StageBaselineLevel.DEVELOPED,
            semantic_kind="stair",
        )
        crossed_readback_sources, relation_requirements, relation_receipts = (
            bind_terminal_topology(crossed_readback_sources, inventory)
        )
        requirements, receipts = requirement_and_receipts(
            crossed_readback_sources,
            level=StageBaselineLevel.DEVELOPED,
        )
        requirements = tuple(sorted(
            (*requirements, *relation_requirements),
            key=lambda item: item.requirement_id,
        ))
        receipts = tuple((*receipts, *relation_receipts))
        with self.assertRaisesRegex(StageBaselineError, "readback digest"):
            compile_stage_baseline_coverage(
                profile(requirements),
                level=StageBaselineLevel.DEVELOPED,
                sources=crossed_readback_sources,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=inventory,
                check_receipts=receipts,
            )

    def test_non_circulation_inventory_rejects_foreign_typed_source(self) -> None:
        source = circulation_source_for_baseline(
            circulation_contract_for_baseline(
                VerticalCirculationMaturity.RESERVATION
            )
        )
        sources = replace(
            physical_sources(),
            vertical_circulation=(source,),
        )
        inventory = inventory_with_semantic_kinds(
            sources,
            level=StageBaselineLevel.SPATIAL,
            semantic_kinds=("escalator-envelope",),
            typed_vertical=False,
        )
        requirements, receipts = requirement_and_receipts(sources)

        with self.assertRaisesRegex(
            StageBaselineError,
            "non-circulation subject",
        ):
            compile_stage_baseline_coverage(
                profile(requirements),
                level=StageBaselineLevel.SPATIAL,
                sources=sources,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=inventory,
                check_receipts=receipts,
            )

    def test_previous_source_set_schema_is_exact_read_only_replay(self) -> None:
        sources = physical_sources()
        payload = sources.to_dict()
        payload["schema"] = StageBaselineSourceSet.PREVIOUS_SCHEMA
        payload.pop("visual_inventory")
        payload.pop("component_functions")
        content = {
            key: value
            for key, value in payload.items()
            if key != "source_set_digest"
        }
        payload["source_set_digest"] = canonical_digest(content)

        replay = StageBaselineSourceSet.from_dict(payload)

        self.assertTrue(replay.is_legacy_read_only)
        self.assertEqual(replay.to_dict(), payload)
        requirements, _receipts = requirement_and_receipts(replay)
        with self.assertRaisesRegex(
            StageBaselineError,
            "read-only",
        ):
            derive_stage_requirement_profile(
                profile(requirements),
                level=StageBaselineLevel.SPATIAL,
                sources=replay,
                subject_digest=SUBJECT_DIGEST,
                subject_inventory=subject_inventory(
                    replay,
                    level=StageBaselineLevel.SPATIAL,
                ),
            )

    def test_controller_phase_mapping_is_exhaustive_and_framework_owned(self) -> None:
        self.assertEqual(
            set(DesignPhase),
            {
                DesignPhase.RESEARCH_BRIEF,
                DesignPhase.PROGRAMMING,
                DesignPhase.SITE_RESOURCE_COORDINATION,
                DesignPhase.SCHEMATIC_DESIGN,
                DesignPhase.DESIGN_DEVELOPMENT,
                DesignPhase.CANDIDATE_COORDINATION,
                DesignPhase.EXECUTION_READY,
            },
        )
        self.assertIs(
            baseline_level_for_design_phase(DesignPhase.RESEARCH_BRIEF),
            StageBaselineLevel.PRE_GEOMETRY,
        )
        self.assertIs(
            baseline_level_for_design_phase(DesignPhase.SCHEMATIC_DESIGN),
            StageBaselineLevel.SPATIAL,
        )
        self.assertIs(
            baseline_level_for_design_phase(DesignPhase.DESIGN_DEVELOPMENT),
            StageBaselineLevel.DEVELOPED,
        )
        self.assertIs(
            baseline_level_for_design_phase(DesignPhase.CANDIDATE_COORDINATION),
            StageBaselineLevel.COORDINATED,
        )


if __name__ == "__main__":
    unittest.main()
