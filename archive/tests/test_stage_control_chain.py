from __future__ import annotations

from dataclasses import replace

import pytest

import archive.archflow.control as control_api
from archive.archflow.capabilities.visual_inventory import (
    CoverageOutcome,
    PixelRegion,
    ROICoverageEntry,
    ROISelection,
    SourceDerivationKind,
    SourceImageEvidence,
    UnknownComponentQuestion,
    VisualSourceDisposition,
    VisualSourceDispositionKind,
    compile_visual_evidence_inventory,
)
from archive.archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    StageBaselineLevel,
    StageBaselineSourceSet,
    derive_stage_requirement_profile,
)
from archive.archflow.control.component_functions import (
    DEFAULT_COMPONENT_FUNCTION_POLICY,
    ComponentFunctionContract,
    ComponentFunctionId,
    FunctionApplicability,
    FunctionApplicabilityDecision,
    FunctionClaimStatus,
    FunctionEndpointBinding,
    FunctionObligationClaim,
    compile_component_function_ledger,
)
from archive.archflow.control.function_relations import (
    FunctionRelationEndpoint,
    FunctionRelationEndpointBinding,
    FunctionRelationEvidenceEnvelope,
    compile_function_relation_requirements,
)
from archive.archflow.control.requirements import RequirementBasisMode, RequirementTargetKind, StageCheckRequirement, StageRequirementProfile
from archive.archflow.control.semantic_capabilities import (
    bind_semantic_rule_packs,
    current_semantic_capability_policy,
)
from archive.archflow.control.stage_control_sources import (
    ComponentFunctionBaselineSource,
    StageControlSourceError,
    VisualInventoryBaselineSource,
)
from archive.archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryEntry,
    StageSubjectRoleObligation,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.relations.contracts import (
    ArchitecturalRelationKind,
    RelationProjection,
)
from archive.archflow.validation.contracts import CheckStatus
from archive.archflow.validation.stage_control import (
    check_component_function_baseline,
    check_visual_inventory_baseline,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="stage-control-test",
            run_id="run-001",
            base=ProjectVersionRef("stage-control-test", 0, SHA_A),
        ),
        branch_id="candidate-a",
        epoch=2,
    )


def record(name: str, digest: str = SHA_A) -> ProjectRecordRef:
    selected = branch()
    return ProjectRecordRef(
        project_id=selected.run.project_id,
        relative_path=(
            f"runs/{selected.run.run_id}/branches/{selected.branch_id}/"
            f"records/{name}.json"
        ),
        sha256=digest,
    )


def visual_receipt():  # type: ignore[no-untyped-def]
    return compile_visual_evidence_inventory(
        source_disposition=VisualSourceDisposition(
            kind=VisualSourceDispositionKind.TEXT_ONLY,
            source_refs=("evidence:visual-disposition",),
            authority_refs=("authority:project-architect",),
        ),
        source_images=(),
        rois=(),
    )


def inventory() -> StageSubjectInventory:
    selected_branch = branch()
    policy = current_semantic_capability_policy()
    entries = tuple(
        StageSubjectInventoryEntry(
            component_id=component_id,
            identity_ref=f"design-component:{component_id}",
            parent_component_id=parent_component_id,
            semantic_kind="generic-stage-root",
            component_digest=component_digest,
            geometry_object_ids=(f"object-{component_id}",),
            binding_ids=(),
            role_obligations=tuple(
                StageSubjectRoleObligation(
                    role=role,
                    disposition=StageSubjectDisposition.REQUIRED,
                    target_refs=(f"design-component:{component_id}",),
                    evidence_refs=(
                        f"evidence:{component_id}-{role.value}",
                    ),
                    authority_refs=(
                        f"authority:{component_id}-{role.value}",
                    ),
                )
                for role in sorted(
                    BASELINE_LEVEL_ROLES[StageBaselineLevel.SPATIAL],
                    key=lambda item: item.value,
                )
            ),
        )
        for component_id, parent_component_id, component_digest in (
            ("root", None, SHA_B),
            ("boundary", "root", "1" * 64),
        )
    )
    bindings = tuple(
        binding
        for entry in entries
        for binding in bind_semantic_rule_packs(
            policy=policy,
            branch=selected_branch,
            stage_id="stage-2",
            stage_subject_digest=SHA_A,
            component_ref=entry.identity_ref,
            component_digest=entry.component_digest,
            semantic_kind=entry.semantic_kind,
            baseline_level=StageBaselineLevel.SPATIAL,
        )
    )
    visual = visual_receipt()
    return StageSubjectInventory(
        inventory_id="stage-control-subjects",
        branch=selected_branch,
        stage_id="stage-2",
        stage_subject_ref="stage-subject:stage-2",
        stage_subject_digest=SHA_A,
        baseline_level=StageBaselineLevel.SPATIAL,
        component_proposal_ref=record("proposal", SHA_A),
        component_proposal_digest=SHA_A,
        component_index_ref=record("index", SHA_B),
        component_index_digest=SHA_B,
        entries=entries,
        visual_inventory_ref=record("visual", "c" * 64),
        visual_inventory_digest=visual.inventory_digest,
        semantic_policy_ref=record("semantic-policy", "d" * 64),
        semantic_policy=policy,
        semantic_rule_pack_bindings=bindings,
    )


def satisfied_ledger(subjects: StageSubjectInventory):  # type: ignore[no-untyped-def]
    function_id = ComponentFunctionId.ENCLOSE_SPACE
    spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(function_id)
    entries = {item.component_id: item for item in subjects.entries}
    contracts = []
    for component_id, target_id in (("boundary", "root"), ("root", "boundary")):
        entry = entries[component_id]
        target = entries[target_id]
        bindings = tuple(
            FunctionEndpointBinding(
                role=role.role,
                endpoint_refs=(
                    (entry.identity_ref,)
                    if role.component_slot
                    else (target.identity_ref,)
                ),
            )
            for role in spec.endpoint_roles
        )
        claim = FunctionObligationClaim(
            obligation_ref=spec.obligation_ref,
            endpoint_bindings=bindings,
            maturity=spec.required_maturity,
            status=FunctionClaimStatus.PASS,
            evidence_refs=(f"evidence:{component_id}-enclosure",),
            authority_refs=(f"authority:{component_id}-enclosure",),
            contradiction_refs=(),
        )
        contracts.append(
            ComponentFunctionContract(
                contract_id=f"{component_id}-functions",
                branch=subjects.branch,
                stage_id=subjects.stage_id,
                subject_inventory_digest=subjects.inventory_digest,
                component_ref=entry.identity_ref,
                component_digest=entry.component_digest,
                applicability_decisions=tuple(
                    FunctionApplicabilityDecision(
                        function_id=item,
                        applicability=(
                            FunctionApplicability.REQUIRED
                            if item is function_id
                            else FunctionApplicability.NOT_APPLICABLE
                        ),
                        evidence_refs=claim.evidence_refs,
                        authority_refs=claim.authority_refs,
                    )
                    for item in ComponentFunctionId
                ),
                claims=(claim,),
            )
        )
    return compile_component_function_ledger(
        ledger_id="stage-2-functions",
        inventory=subjects,
        contracts=tuple(contracts),
    )


def relation_requirements(subjects, ledger):  # type: ignore[no-untyped-def]
    entries = {item.identity_ref: item for item in subjects.entries}
    envelopes = []
    for row in ledger.rows:
        evaluation = row.evaluations[0]
        bindings = []
        for role in evaluation.endpoint_roles:
            endpoint_ref = next(
                item.endpoint_refs[0]
                for item in evaluation.endpoint_bindings
                if item.role == role.role
            )
            endpoint = entries[endpoint_ref]
            bindings.append(
                FunctionRelationEndpointBinding(
                    function_role=role.role,
                    relation_role=(
                        "container" if role.component_slot else "contained"
                    ),
                    endpoints=(
                        FunctionRelationEndpoint(
                            component_ref=endpoint.identity_ref,
                            component_digest=endpoint.component_digest,
                        ),
                    ),
                )
            )
        envelopes.append(
            FunctionRelationEvidenceEnvelope(
                envelope_id=f"{row.component_ref.rsplit(':', 1)[-1]}-enclosure-relation",
                branch=subjects.branch,
                stage_id=subjects.stage_id,
                subject_inventory_digest=subjects.inventory_digest,
                function_ledger_ref=ledger.ledger_ref,
                function_ledger_digest=ledger.ledger_digest,
                component_ref=row.component_ref,
                component_digest=row.component_digest,
                functional_obligation_ref=evaluation.obligation_ref,
                projection=RelationProjection.COMPOSITION,
                relation_kind=ArchitecturalRelationKind.PRIMARY_CONTAINS,
                scenario_ref="scenario:enclosure",
                endpoint_bindings=tuple(bindings),
                counted_function_role="enclosed_space",
                basis_ids=("enclosure-relation-basis",),
                evidence_refs=(f"evidence:{row.component_ref}-relation",),
                authority_refs=(f"authority:{row.component_ref}-relation",),
                prompt="Identify the explicit enclosure adjacency.",
            )
        )
    return compile_function_relation_requirements(
        set_id="stage-2-function-relations",
        ledger=ledger,
        inventory=subjects,
        envelopes=tuple(envelopes),
    )


def test_current_sources_round_trip_and_checks_pass() -> None:
    subjects = inventory()
    visual = visual_receipt()
    visual_source = VisualInventoryBaselineSource(
        branch=subjects.branch,
        stage_id=subjects.stage_id,
        stage_subject_inventory_digest=subjects.inventory_digest,
        inventory_ref=subjects.visual_inventory_ref,
        inventory=visual,
    )
    ledger = satisfied_ledger(subjects)
    requirements = relation_requirements(subjects, ledger)
    function_source = ComponentFunctionBaselineSource(
        ledger_ref=record("function-ledger", "e" * 64),
        ledger=ledger,
        relation_requirements_ref=record("function-relations", "2" * 64),
        relation_requirements=requirements,
    )
    sources = StageBaselineSourceSet(
        visual_inventory=(visual_source,),
        component_functions=(function_source,),
    )

    assert StageBaselineSourceSet.from_dict(sources.to_dict()) == sources
    assert sources.to_dict()["schema"] == "StageBaselineSourceSet@4"
    assert (
        check_visual_inventory_baseline(
            visual_source,
            subjects,
            scope_digest=SHA_B,
            subject_digest=SHA_A,
        ).status
        is CheckStatus.PASS
    )
    assert (
        check_component_function_baseline(
            function_source,
            subjects,
            scope_digest=SHA_B,
            subject_digest=SHA_A,
        ).status
        is CheckStatus.PASS
    )


def test_sparse_profile_cannot_omit_visual_or_function_requirements() -> None:
    subjects = inventory()
    empty_sources = StageBaselineSourceSet()
    base = StageRequirementProfile(
        profile_id="stage-2-profile",
        typology_id="generic",
        stage_id="stage-2",
        branch=subjects.branch,
        predecessor_state_digest=SHA_A,
        scope_digest=SHA_B,
        stage_subject_ref=subjects.stage_subject_ref,
        requirements=(
            StageCheckRequirement(
                requirement_id="project-check",
                checker_id="project-checker",
                target_kind=RequirementTargetKind.STAGE,
                basis_mode=RequirementBasisMode.UNIVERSAL,
                denominator_refs=("stage-subject:stage-2",),
            ),
        ),
    )
    derived = derive_stage_requirement_profile(
        base,
        level=StageBaselineLevel.SPATIAL,
        sources=empty_sources,
        subject_digest=SHA_A,
        subject_inventory=subjects,
    )
    checker_ids = {item.checker_id for item in derived.requirements}
    assert "visual-inventory-validator" in checker_ids
    assert "component-function-validator" in checker_ids


def test_orphan_ledger_fails_and_cross_branch_source_is_rejected() -> None:
    subjects = inventory()
    orphan = compile_component_function_ledger(
        ledger_id="orphan-functions",
        inventory=subjects,
        contracts=(),
    )
    source = ComponentFunctionBaselineSource(
        ledger_ref=record("orphan-ledger", "f" * 64),
        ledger=orphan,
    )
    assert (
        check_component_function_baseline(
            source,
            subjects,
            scope_digest=SHA_B,
            subject_digest=SHA_A,
        ).status
        is CheckStatus.FAIL
    )

    foreign_ref = replace(
        record("visual"),
        relative_path="runs/run-001/branches/other/records/visual.json",
    )
    with pytest.raises(StageControlSourceError):
        VisualInventoryBaselineSource(
            branch=subjects.branch,
            stage_id=subjects.stage_id,
            stage_subject_inventory_digest=subjects.inventory_digest,
            inventory_ref=foreign_ref,
            inventory=visual_receipt(),
        )


def test_unresolved_visual_component_question_keeps_stage_check_open() -> None:
    unknown = compile_visual_evidence_inventory(
        source_disposition=VisualSourceDisposition(
            kind=VisualSourceDispositionKind.VISUAL_SOURCES,
            source_refs=("evidence:facade-image",),
            authority_refs=("authority:project-architect",),
        ),
        source_images=(
            SourceImageEvidence(
                image_id="facade-image",
                exact_sha256="3" * 64,
                width_px=1200,
                height_px=800,
                derivation_kind=SourceDerivationKind.ORIGINAL,
                derivation_refs=(),
            ),
        ),
        rois=(
            PixelRegion(
                roi_id="unknown-opening",
                image_id="facade-image",
                source_width_px=1200,
                source_height_px=800,
                x_px=100,
                y_px=100,
                width_px=200,
                height_px=300,
                selection=ROISelection.SELECTED,
            ),
        ),
        unknown_questions=(
            UnknownComponentQuestion(
                question_id="opening-or-stain",
                roi_id="unknown-opening",
                question="Is this region an opening or surface staining?",
                evidence_refs=("roi:unknown-opening",),
            ),
        ),
        coverage_entries=(
            ROICoverageEntry(
                roi_id="unknown-opening",
                outcome=CoverageOutcome.UNKNOWN_QUESTION,
                target_ref="opening-or-stain",
            ),
        ),
    )
    subjects = replace(
        inventory(),
        visual_inventory_digest=unknown.inventory_digest,
    )
    source = VisualInventoryBaselineSource(
        branch=subjects.branch,
        stage_id=subjects.stage_id,
        stage_subject_inventory_digest=subjects.inventory_digest,
        inventory_ref=subjects.visual_inventory_ref,
        inventory=unknown,
    )

    receipt = check_visual_inventory_baseline(
        source,
        subjects,
        scope_digest=SHA_B,
        subject_digest=SHA_A,
    )

    assert receipt.status is CheckStatus.FAIL
    assert "visual-component-question-open" in {
        finding.code for finding in receipt.findings
    }


def test_satisfied_rows_without_relation_requirement_set_fail_closed() -> None:
    subjects = inventory()
    source = ComponentFunctionBaselineSource(
        ledger_ref=record("function-ledger", "e" * 64),
        ledger=satisfied_ledger(subjects),
    )
    assert ComponentFunctionBaselineSource.from_dict(source.to_dict()) == source

    receipt = check_component_function_baseline(
        source,
        subjects,
        scope_digest=SHA_B,
        subject_digest=SHA_A,
    )

    assert receipt.status is CheckStatus.FAIL
    assert "function-relation-requirements-missing" in {
        item.code for item in receipt.findings
    }


def test_relation_requirement_set_rejects_stale_and_cross_branch_bindings() -> None:
    subjects = inventory()
    ledger = satisfied_ledger(subjects)
    requirements = relation_requirements(subjects, ledger)
    relation_ref = record("function-relations", "2" * 64)

    with pytest.raises(StageControlSourceError, match="branch, stage, or inventory"):
        ComponentFunctionBaselineSource(
            ledger_ref=record("function-ledger", "e" * 64),
            ledger=ledger,
            relation_requirements_ref=relation_ref,
            relation_requirements=replace(
                requirements,
                subject_inventory_digest=SHA_A,
            ),
        )
    with pytest.raises(StageControlSourceError, match="branch, stage, or inventory"):
        ComponentFunctionBaselineSource(
            ledger_ref=record("function-ledger", "e" * 64),
            ledger=ledger,
            relation_requirements_ref=relation_ref,
            relation_requirements=replace(
                requirements,
                branch=replace(subjects.branch, branch_id="other"),
            ),
        )
    with pytest.raises(StageControlSourceError, match="stale"):
        ComponentFunctionBaselineSource(
            ledger_ref=record("function-ledger", "e" * 64),
            ledger=ledger,
            relation_requirements_ref=relation_ref,
            relation_requirements=replace(
                requirements,
                function_ledger_digest=SHA_A,
            ),
        )
    with pytest.raises(StageControlSourceError, match="stale"):
        ComponentFunctionBaselineSource(
            ledger_ref=record("function-ledger", "e" * 64),
            ledger=ledger,
            relation_requirements_ref=relation_ref,
            relation_requirements=replace(
                requirements,
                function_ledger_ref="function-ledger:other",
            ),
        )
    with pytest.raises(StageControlSourceError, match="independent P036 record"):
        ComponentFunctionBaselineSource(
            ledger_ref=record("function-ledger", "e" * 64),
            ledger=ledger,
            relation_requirements_ref=record(
                "function-ledger",
                "3" * 64,
            ),
            relation_requirements=requirements,
        )


def test_relation_requirement_set_duplicate_and_serialized_tamper_fail_closed() -> None:
    subjects = inventory()
    ledger = satisfied_ledger(subjects)
    requirements = relation_requirements(subjects, ledger)
    source = ComponentFunctionBaselineSource(
        ledger_ref=record("function-ledger", "e" * 64),
        ledger=ledger,
        relation_requirements_ref=record("function-relations", "2" * 64),
        relation_requirements=requirements,
    )
    payload = source.to_dict()
    payload["relation_requirements"]["set_digest"] = SHA_A
    with pytest.raises(ValueError, match="digest changed"):
        ComponentFunctionBaselineSource.from_dict(payload)

    object.__setattr__(
        requirements,
        "requirements",
        (*requirements.requirements, requirements.requirements[0]),
    )
    receipt = check_component_function_baseline(
        source,
        subjects,
        scope_digest=SHA_B,
        subject_digest=SHA_A,
    )
    assert receipt.status is CheckStatus.FAIL
    assert "function-relation-obligation-duplicate" in {
        item.code for item in receipt.findings
    }
