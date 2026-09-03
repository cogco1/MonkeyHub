from __future__ import annotations

from dataclasses import replace

import pytest

from archive.archflow.control.baseline import (
    ComponentLineageBaselineSource,
    MaterialBindingBaselineSource,
    SpatialLayoutBaselineSource,
    StageBaselineCoverageReceipt,
    StageBaselineLevel,
    StageBaselineRole,
    StageBaselineSourceSet,
    StageBaselineStatus,
    compile_stage_baseline_coverage,
    derive_stage_baseline_check_receipts,
    derive_stage_requirement_profile,
)
from archive.archflow.control.requirements import RequirementBasisMode, RequirementTargetKind, StageCheckRequirement, StageRequirementProfile
from archive.archflow.control.stage_artifacts import (
    ArtifactShaBinding,
    RecordDigestBinding,
    StageArtifactClaim,
    StageArtifactStatus,
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
)
from archive.archflow.materials.ledger import MaterialIntent, MaterialLedger
from archflow.project.refs import ProjectRecordRef
from archive.archflow.relations.authoring import (
    RelationAnswerStatus,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisKind,
    RelationBasisUse,
    RelationDerivationAnswer,
    RelationDerivationQuestion,
    RelationEpistemicStatus,
    RelationProposalSpec,
    RelationRuleEnvelope,
    RelationRuleProposalSpec,
)
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
    ArchitecturalRelationKind,
    RelationParticipant,
    RelationProjection,
)
from archive.archflow.runtime.stage_artifact_chain import (
    StageArtifactChainError,
    compile_relation_realization_baseline_source,
    compile_stage_artifact_claim,
)
from archive.archflow.runtime.stage_control_chain import (
    finalize_stage_control_chain,
    prepare_stage_control_chain,
)
from archflow.state.stage_workflow import DesignPhase
from archive.archflow.state.design_maturity import DeliverableRole, PhaseGateReceipt, StageEntryProof
from archflow.state.model import ArtifactRef
from archive.archflow.validation.relation_realization import check_relation_realization
from archive.archflow.validation.check_bridges import (
    ComponentLineageCheckProfile,
    SpatialLayoutCheckProfile,
)
from archive.archflow.validation.component_lineage import (
    OperationDisposition,
    OperationLineageResolution,
    PredecessorOperationDisposition,
    StageOperation,
    StageOperationRef,
    compile_stage_component_coverage,
)
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.spatial import (
    AABB,
    HostRegion,
    SpatialElement,
    SpatialElementKind,
    normalize_spatial_validation_input,
)

from archive.tests.test_stage_artifact_chain import _realization_inputs, _sha
from tests.test_stage_control_runtime import (
    AUTHORITY_REF,
    SOURCE_REF,
    _proposal,
    _raw_inputs,
    _verification_receipts,
)


def _record(branch, name: str, digest: str | None = None) -> ProjectRecordRef:  # type: ignore[no-untyped-def]
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/"
            f"records/{name}.json"
        ),
        sha256=_sha(f"record:{name}") if digest is None else digest,
    )


def _binding(branch, name: str, content_digest: str) -> RecordDigestBinding:  # type: ignore[no-untyped-def]
    return RecordDigestBinding(
        record_ref=_record(branch, name),
        content_digest=content_digest,
    )


def _complete_stage_sources(prepared, finalized, realization):  # type: ignore[no-untyped-def]
    inventory = prepared.inventory
    branch = inventory.branch
    operations = tuple(
        sorted(
            (
                StageOperation(
                    ref=StageOperationRef(
                        component_id=entry.component_id,
                        operation_id="retained-stage-shape",
                    ),
                    fingerprint=entry.component_digest,
                )
                for entry in inventory.entries
            ),
            key=lambda item: item.identity,
        )
    )
    lineage_receipt = compile_stage_component_coverage(
        predecessor_stage_id=DesignPhase.SCHEMATIC_DESIGN.value,
        successor_stage_id=inventory.stage_id,
        predecessor_operations=operations,
        successor_operations=operations,
        lineage_resolutions=tuple(
            OperationLineageResolution(
                predecessor_ref=item.ref,
                successor_refs=(item.ref,),
                lineage_refs=(f"lineage:{item.component_id}",),
            )
            for item in operations
        ),
        dispositions=tuple(
            PredecessorOperationDisposition(
                predecessor_ref=item.ref,
                disposition=OperationDisposition.VERIFIED_UNCHANGED,
                relational_revalidation_refs=(
                    f"revalidation:{item.component_id}",
                ),
            )
            for item in operations
        ),
    )
    lineage_profile = ComponentLineageCheckProfile(
        profile_id="stage3-component-lineage",
        branch=branch,
        scope_digest=prepared.relation_context.scope_digest,
        predecessor_stage_id=lineage_receipt.predecessor_stage_id,
        successor_stage_id=lineage_receipt.successor_stage_id,
        predecessor_operations=lineage_receipt.predecessor_operations,
        denominator_refs=tuple(
            sorted(entry.identity_ref for entry in inventory.entries)
        ),
    )
    lineage_source = ComponentLineageBaselineSource(
        profile=lineage_profile,
        source_receipt=lineage_receipt,
    )

    host = HostRegion("stage-host", AABB((-10.0, -10.0, -1.0), (10.0, 10.0, 10.0)))
    bounds = {
        "building": AABB((-4.0, -4.0, 0.0), (0.0, 4.0, 4.0)),
        "exterior-stair-east": AABB((1.0, -1.0, 0.0), (3.0, 1.0, 1.0)),
    }
    spatial_input = normalize_spatial_validation_input(
        elements=tuple(
            SpatialElement(
                element_id=f"stage-element-{entry.component_id}",
                component_id=entry.component_id,
                kind=SpatialElementKind.OTHER,
                bounds=bounds[entry.component_id],
                host_region_id=host.region_id,
            )
            for entry in inventory.entries
        ),
        host_regions=(host,),
        required_component_ids=tuple(
            sorted(entry.component_id for entry in inventory.entries)
        ),
        length_unit="meter",
    )
    spatial_source = SpatialLayoutBaselineSource(
        profile=SpatialLayoutCheckProfile(
            profile_id="stage3-spatial-layout",
            branch=branch,
            scope_digest=prepared.relation_context.scope_digest,
            stage_id=inventory.stage_id,
            input_digest=spatial_input.input_digest,
            denominator_refs=tuple(
                sorted(entry.identity_ref for entry in inventory.entries)
            ),
        ),
        validator_input=spatial_input,
    )

    material_ledger = MaterialLedger(
        intents=(
            MaterialIntent(
                material_id="stone",
                label="Evidence-bound stone",
                source_refs=(SOURCE_REF,),
            ),
        ),
        assignments=tuple(
            sorted((entry.component_id, "stone") for entry in inventory.entries)
        ),
    )
    material_requirements = tuple(
        MaterialBindingRequirement(
            requirement_id=f"material-{entry.component_id}",
            semantic_subject_ref=entry.identity_ref,
            ledger_component_id=entry.component_id,
            material_id="stone",
            material_intent_ref="material-intent:stone",
            geometry_object_refs=(f"cad-object:object-{entry.component_id}",),
        )
        for entry in inventory.entries
    )
    material_profile = MaterialBindingProfile(
        profile_id="stage3-material-bindings",
        branch=branch,
        scope_digest=prepared.relation_context.scope_digest,
        ledger_ref="material-ledger:stage3",
        ledger_digest=canonical_digest(material_ledger.to_dict()),
        requirements=material_requirements,
    )
    material_snapshot = MaterialBindingSnapshot(
        branch=branch,
        scope_digest=material_profile.scope_digest,
        profile_digest=material_profile.profile_digest,
        ledger_ref=material_profile.ledger_ref,
        ledger_digest=material_profile.ledger_digest,
        observations=tuple(
            MaterialBindingObservation(
                semantic_subject_ref=item.semantic_subject_ref,
                resolution=MaterialBindingResolution.RESOLVED,
                ledger_component_id=item.ledger_component_id,
                material_intent_ref=item.material_intent_ref,
                geometry_object_refs=item.geometry_object_refs,
            )
            for item in material_requirements
        ),
        reported_summary_passed=True,
    )
    material_source = MaterialBindingBaselineSource(
        profile=material_profile,
        ledger=material_ledger,
        snapshot=material_snapshot,
    )
    return StageBaselineSourceSet(
        component_lineage=(lineage_source,),
        spatial_layout=(spatial_source,),
        material_binding=(material_source,),
        visual_inventory=finalized.baseline_sources.visual_inventory,
        component_functions=finalized.baseline_sources.component_functions,
        relation_topology=finalized.baseline_sources.relation_topology,
        relation_realization=(realization,),
    )


def _compile_stage_evidence(prepared, sources):  # type: ignore[no-untyped-def]
    inventory = prepared.inventory
    project_requirement = StageCheckRequirement(
        requirement_id="project-stage3-declaration",
        checker_id="project-stage3-declaration-checker",
        target_kind=RequirementTargetKind.STAGE,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=(inventory.stage_subject_ref,),
    )
    base_profile = StageRequirementProfile(
        profile_id="stage3-complete-profile",
        typology_id="generic-functional-building",
        stage_id=inventory.stage_id,
        branch=inventory.branch,
        predecessor_state_digest=inventory.stage_subject_digest,
        scope_digest=prepared.relation_context.scope_digest,
        stage_subject_ref=inventory.stage_subject_ref,
        requirements=(project_requirement,),
    )
    profile = derive_stage_requirement_profile(
        base_profile,
        level=inventory.baseline_level,
        sources=sources,
        subject_digest=inventory.stage_subject_digest,
        subject_inventory=inventory,
    )
    project_receipt = CheckReceiptEnvelope(
        check_id=project_requirement.requirement_id,
        checker_id=project_requirement.checker_id,
        checker_version="1.0.0",
        branch=inventory.branch,
        scope_digest=prepared.relation_context.scope_digest,
        subject_refs=project_requirement.denominator_refs,
        subject_digest=inventory.stage_subject_digest,
        status=CheckStatus.PASS,
        coverage_denominator=project_requirement.denominator_refs,
        covered_refs=project_requirement.denominator_refs,
    )
    receipts = tuple(
        sorted(
            (
                project_receipt,
                *derive_stage_baseline_check_receipts(
                    profile,
                    sources=sources,
                    subject_digest=inventory.stage_subject_digest,
                    subject_inventory=inventory,
                ),
            ),
            key=lambda item: item.receipt_digest,
        )
    )
    coverage = compile_stage_baseline_coverage(
        profile,
        level=inventory.baseline_level,
        sources=sources,
        subject_digest=inventory.stage_subject_digest,
        subject_inventory=inventory,
        check_receipts=receipts,
    )
    closure = compile_composite_stage_closure(
        profile,
        subject_digest=inventory.stage_subject_digest,
        check_receipts=receipts,
    )
    return profile, receipts, coverage, closure


def _add_project_relation_denominator(raw: dict[str, object]) -> None:
    stage_id = raw["stage_id"]
    assert isinstance(stage_id, str)
    foundation_ref = "design-system:foundation"
    raw["relation_nodes"] = (
        *raw["relation_nodes"],
        ArchitecturalNode(
            node_ref=foundation_ref,
            node_kind=ArchitecturalNodeKind.SYSTEM,
            semantic_kind="foundation-system",
            stage_id=stage_id,
            source_refs=(SOURCE_REF,),
        ),
    )
    question_specs = (
        (
            "building-support",
            RelationProjection.SUPPORT,
            "design-component:building",
            foundation_ref,
            ArchitecturalRelationKind.SUPPORT,
            "supported",
            "supporter",
        ),
        (
            "stair-support",
            RelationProjection.SUPPORT,
            "design-component:exterior-stair-east",
            "design-component:building",
            ArchitecturalRelationKind.SUPPORT,
            "supported",
            "supporter",
        ),
        (
            "building-access",
            RelationProjection.ACCESS,
            "design-component:building",
            "design-component:exterior-stair-east",
            ArchitecturalRelationKind.ACCESS,
            "from",
            "to",
        ),
        (
            "stair-access",
            RelationProjection.ACCESS,
            "design-component:exterior-stair-east",
            "design-component:building",
            ArchitecturalRelationKind.ACCESS,
            "from",
            "to",
        ),
    )
    questions = []
    bases = []
    for (
        question_id,
        projection,
        subject_ref,
        target_ref,
        kind,
        subject_role,
        counted_role,
    ) in question_specs:
        policy_basis_id = f"{question_id}-policy"
        topology_basis_id = f"{question_id}-topology"
        question = RelationDerivationQuestion(
            question_id=question_id,
            projection=projection,
            scenario_ref="scenario:stage3-functional-continuity",
            subject_refs=(subject_ref,),
            target_refs=(target_ref,),
            allowed_relation_kinds=(kind,),
            rule_envelopes=(
                RelationRuleEnvelope(
                    relation_kind=kind,
                    subject_role=subject_role,
                    counted_role=counted_role,
                    minimum_count=1,
                    maximum_count=None,
                ),
            ),
            basis_ids=(policy_basis_id, topology_basis_id),
            prompt="Resolve this evidence-bound functional relation.",
            rule_subject_refs=(subject_ref,),
        )
        questions.append(question)
        for basis_id, basis_use in (
            (policy_basis_id, RelationBasisUse.POLICY),
            (topology_basis_id, RelationBasisUse.TOPOLOGY),
        ):
            bases.append(
                RelationBasisBinding(
                    basis_id=basis_id,
                    basis_kind=(
                        RelationBasisKind.HUMAN
                        if basis_use is RelationBasisUse.POLICY
                        else RelationBasisKind.RAG
                    ),
                    basis_use=basis_use,
                    question_refs=(question.ref,),
                    allowed_relation_kinds=(kind,),
                    epistemic_status=RelationEpistemicStatus.DERIVED,
                    evidence_refs=(SOURCE_REF,),
                    authority_refs=(AUTHORITY_REF,),
                    summary="Project evidence for a required Stage 3 relation.",
                )
            )
    raw["relation_questions"] = tuple(questions)
    raw["relation_bases"] = (*raw["relation_bases"], *bases)


def _answer_project_questions(prepared, base):  # type: ignore[no-untyped-def]
    relations = list(base.relations)
    rules = list(base.rules)
    answers = list(base.answers)
    inventory_by_ref = {
        item.identity_ref: item for item in prepared.inventory.entries
    }
    basis_by_id = {
        item.basis_id: item for item in prepared.relation_context.bases
    }
    for question in prepared.relation_context.questions:
        if not question.question_id in {
            "building-support",
            "stair-support",
            "building-access",
            "stair-access",
        }:
            continue
        envelope = question.rule_envelopes[0]
        relation_id = f"{question.question_id}-relation"
        rule_id = f"{question.question_id}-rule"
        relations.append(
            RelationProposalSpec(
                relation_id=relation_id,
                question_refs=(question.ref,),
                kind=envelope.relation_kind,
                participants=(
                    RelationParticipant(
                        envelope.subject_role,
                        question.subject_refs[0],
                    ),
                    RelationParticipant(
                        envelope.counted_role,
                        question.target_refs[0],
                    ),
                ),
                scenario_ref=question.scenario_ref,
                basis_ids=tuple(
                    basis_id
                    for basis_id in question.basis_ids
                    if basis_by_id[basis_id].basis_use
                    is RelationBasisUse.TOPOLOGY
                ),
            )
        )
        subject = inventory_by_ref[question.subject_refs[0]]
        rules.append(
            RelationRuleProposalSpec(
                rule_id=rule_id,
                question_refs=(question.ref,),
                node_kind=ArchitecturalNodeKind.COMPONENT,
                semantic_kind=subject.semantic_kind,
                relation_kind=envelope.relation_kind,
                subject_role=envelope.subject_role,
                counted_role=envelope.counted_role,
                minimum_count=envelope.minimum_count,
                maximum_count=envelope.maximum_count,
                scenario_ref=question.scenario_ref,
                basis_ids=tuple(
                    basis_id
                    for basis_id in question.basis_ids
                    if basis_by_id[basis_id].basis_use
                    is RelationBasisUse.POLICY
                ),
            )
        )
        answers.append(
            RelationDerivationAnswer(
                question_ref=question.ref,
                status=RelationAnswerStatus.PROPOSED,
                relation_ids=(relation_id,),
                rule_ids=(rule_id,),
                rationale="Bind the required project functional relation.",
            )
        )
    return RelationAuthoringProposal(
        context_digest=prepared.relation_context.context_digest,
        answers=tuple(answers),
        relations=tuple(relations),
        rules=tuple(rules),
    )


def test_developed_stair_without_vertical_source_stays_open() -> None:
    raw = _raw_inputs(
        stage_id=DesignPhase.DESIGN_DEVELOPMENT.value,
        baseline_level=StageBaselineLevel.DEVELOPED,
    )
    _add_project_relation_denominator(raw)
    prepared = prepare_stage_control_chain(**raw)
    proposal = _answer_project_questions(prepared, _proposal(prepared))
    branch = prepared.inventory.branch
    finalized = finalize_stage_control_chain(
        prepared=prepared,
        relation_proposal=proposal,
        function_ledger_ref=_record(branch, "function-ledger"),
        relation_requirements_ref=_record(branch, "function-requirements"),
        verification_receipts=_verification_receipts(prepared, proposal),
    )
    topology = finalized.topology_source
    program, readback, manifest, independent_receipts = _realization_inputs(
        topology
    )
    realization = compile_relation_realization_baseline_source(
        topology,
        program=program,
        readback=readback,
        manifest=manifest,
        verification_receipts=independent_receipts,
    )
    sources = _complete_stage_sources(prepared, finalized, realization)

    _profile, _receipts, coverage, _closure = _compile_stage_evidence(
        prepared,
        sources,
    )

    assert coverage.status is StageBaselineStatus.OPEN
    assert StageBaselineRole.VERTICAL_CIRCULATION in coverage.missing_roles


def test_visual_function_relation_realization_artifact_and_cad_guard_chain() -> None:
    raw = _raw_inputs(
        stage_id=DesignPhase.DESIGN_DEVELOPMENT.value,
        baseline_level=StageBaselineLevel.DEVELOPED,
        access_semantic_kind="exterior-access-assembly",
    )
    _add_project_relation_denominator(raw)
    prepared = prepare_stage_control_chain(**raw)
    proposal = _answer_project_questions(prepared, _proposal(prepared))
    branch = prepared.inventory.branch
    function_ledger_ref = _record(branch, "function-ledger")
    function_requirements_ref = _record(branch, "function-requirements")
    finalized = finalize_stage_control_chain(
        prepared=prepared,
        relation_proposal=proposal,
        function_ledger_ref=function_ledger_ref,
        relation_requirements_ref=function_requirements_ref,
        verification_receipts=_verification_receipts(prepared, proposal),
    )

    topology = finalized.topology_source
    program, readback, manifest, independent_receipts = _realization_inputs(
        topology
    )
    realization = compile_relation_realization_baseline_source(
        topology,
        program=program,
        readback=readback,
        manifest=manifest,
        verification_receipts=independent_receipts,
    )
    sources = _complete_stage_sources(prepared, finalized, realization)
    realization_check = check_relation_realization(
        realization.graph,
        realization.manifest,
        realization.program,
        realization.readback,
        verification_receipts=realization.verification_receipts,
    )

    profile, stage_receipts, coverage, closure = _compile_stage_evidence(
        prepared,
        sources,
    )
    assert coverage.status is StageBaselineStatus.SATISFIED
    assert closure.status is StageClosureStatus.SATISFIED

    predecessor = replace(branch, epoch=branch.epoch - 1)
    gate = PhaseGateReceipt(
        receipt_id="schematic-stage-exit",
        request_id="enter-design-development",
        branch=predecessor,
        base_state_digest=_sha("schematic-state"),
        from_phase=DesignPhase.SCHEMATIC_DESIGN,
        to_phase=DesignPhase.DESIGN_DEVELOPMENT,
        required_roles=(
            DeliverableRole.SCHEMATIC_OPTIONS,
            DeliverableRole.SCHEMATIC_SELECTION,
        ),
        accepted_deliverable_refs=(
            "deliverable:schematic-options",
            "deliverable:schematic-selection",
        ),
    )
    proof = StageEntryProof(
        phase_gate=gate,
        stage_exit_checkpoint_ref=_record(branch, "stage-exit-checkpoint"),
        stage_exit_proof_digest=_sha("stage-exit-proof"),
        predecessor_checkpoint_digest=_sha("predecessor-checkpoint"),
        successor_checkpoint_digest=_sha("successor-checkpoint"),
        successor_branch=branch,
    )

    program_record_sha = _sha("program-record")
    program_ref = ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records/"
            f"{prepared.inventory.stage_id}-geometry-program-"
            f"{program_record_sha}.json"
        ),
        sha256=program_record_sha,
    )
    program_record = RecordDigestBinding(program_ref, program.program_digest)
    artifact_sha = _sha("stage3-candidate-3dm")
    artifact = ArtifactShaBinding(
        ArtifactRef(
            artifact_id="stage3-candidate",
            uri=(
                f"project://{branch.run.project_id}/runs/{branch.run.run_id}/"
                f"branches/{branch.branch_id}/artifacts/stage3-candidate.3dm"
            ),
            media_type="model/vnd.rhino",
            sha256=artifact_sha,
        ),
        artifact_sha,
    )
    topology_records = (
        _binding(branch, "relation-topology", topology.source_digest),
    )
    realization_records = (
        _binding(branch, "relation-realization", realization.source_digest),
    )
    all_realization_receipts = tuple(
        sorted(
            (realization_check, *independent_receipts),
            key=lambda item: item.receipt_digest,
        )
    )
    receipt_records = tuple(
        _binding(
            branch,
            f"relation-receipt-{index:02d}",
            receipt.receipt_digest,
        )
        for index, receipt in enumerate(all_realization_receipts, start=1)
    )
    stage_check_records = tuple(
        _binding(
            branch,
            f"stage-check-{index:02d}",
            receipt.receipt_digest,
        )
        for index, receipt in enumerate(stage_receipts, start=1)
    )

    claim_kwargs = dict(
        claim_id="complete-stage3-chain",
        stage_entry_proof=proof,
        stage_entry_proof_record=_binding(
            branch,
            "stage-entry-proof",
            proof.proof_digest,
        ),
        artifact=artifact,
        geometry_program_record=program_record,
        component_index_record=_binding(
            branch,
            "component-index",
            prepared.inventory.component_index_digest,
        ),
        stage_subject_inventory_record=_binding(
            branch,
            "stage-subject-inventory",
            prepared.inventory.inventory_digest,
        ),
        stage_subject_inventory=prepared.inventory,
        function_ledger_record=RecordDigestBinding(
            function_ledger_ref,
            prepared.function_ledger.ledger_digest,
        ),
        function_ledger=prepared.function_ledger,
        function_requirement_record=RecordDigestBinding(
            function_requirements_ref,
            prepared.relation_requirements.set_digest,
        ),
        function_requirements=prepared.relation_requirements,
        stage_requirement_profile_record=_binding(
            branch,
            "stage-requirement-profile",
            profile.profile_digest,
        ),
        stage_requirement_profile=profile,
        stage_check_receipt_records=stage_check_records,
        stage_check_receipts=stage_receipts,
        baseline_sources_record=_binding(
            branch,
            "baseline-sources",
            sources.source_set_digest,
        ),
        baseline_sources=sources,
        baseline_coverage_record=_binding(
            branch,
            "baseline-coverage",
            coverage.receipt_digest,
        ),
        baseline_coverage=coverage,
        stage_closure_record=_binding(
            branch,
            "stage-closure",
            closure.receipt_digest,
        ),
        stage_closure=closure,
        cad_readback_record=_binding(
            branch,
            "cad-readback",
            readback.snapshot_digest,
        ),
        topology_source_records=topology_records,
        realization_source_records=realization_records,
        realization_receipt_records=receipt_records,
    )
    claim = compile_stage_artifact_claim(**claim_kwargs)

    assert claim.status is StageArtifactStatus.STAGE3_VERIFIED_CANDIDATE
    assert claim.component_index != claim.stage_subject_inventory
    assert StageArtifactClaim.from_dict(claim.to_dict()) == claim

    forged_row = replace(
        coverage.coverage[0],
        source_digests=(_sha("forged-baseline-source"),),
    )
    forged_coverage = replace(
        coverage,
        coverage=(forged_row, *coverage.coverage[1:]),
    )
    forged_kwargs = {
        **claim_kwargs,
        "baseline_coverage_record": _binding(
            branch,
            "forged-baseline-coverage",
            forged_coverage.receipt_digest,
        ),
        "baseline_coverage": forged_coverage,
    }
    with pytest.raises(
        StageArtifactChainError,
        match="baseline coverage was not recomputed",
    ):
        compile_stage_artifact_claim(**forged_kwargs)
