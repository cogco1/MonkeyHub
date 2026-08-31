from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.control import (
    BASELINE_LEVEL_ROLES,
    CadReadbackBaselineSource,
    ComponentLineageBaselineSource,
    MaterialBindingBaselineSource,
    RequirementBasisMode,
    RequirementTargetKind,
    SpatialLayoutBaselineSource,
    StageBaselineError,
    StageBaselineCoverageReceipt,
    StageBaselineLevel,
    StageBaselineRole,
    StageBaselineSourceSet,
    StageBaselineStatus,
    StageCheckRequirement,
    StageClosureStatus,
    StageRequirementProfile,
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryEntry,
    StageSubjectRoleObligation,
    assembly_stage_requirement,
    baseline_level_for_design_phase,
    cad_readback_stage_requirement,
    compile_composite_stage_closure,
    compile_stage_baseline_coverage,
    derive_stage_requirement_profile,
    component_lineage_stage_requirement,
    material_binding_stage_requirement,
    spatial_layout_stage_requirement,
)
from archflow.contracts.canonical import canonical_digest
from archflow.project import BranchRef, ProjectRecordRef
from archflow.state import DesignPhase
from archflow.materials.binding import validate_material_bindings
from archflow.validation.assembly import (
    AssemblyObligationDisposition,
    AssemblyProfile,
    RelationshipKind,
    check_assembly,
)
from archflow.validation.cad_readback import validate_cad_readback
from archflow.validation.check_bridges import (
    bridge_component_lineage_receipt,
    bridge_spatial_validation_receipt,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archflow.validation.spatial import (
    normalize_spatial_validation_input,
    validate_spatial_layout,
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
    content = {
        key: value
        for key, value in payload.items()
        if key != "source_set_digest"
    }
    payload["source_set_digest"] = canonical_digest(content)
    return StageBaselineSourceSet.from_dict(payload)


def requirement_and_receipts(
    sources: StageBaselineSourceSet,
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


def compile_baseline(
    sources: StageBaselineSourceSet,
    *,
    level: StageBaselineLevel,
):
    requirements, receipts = requirement_and_receipts(sources)
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
