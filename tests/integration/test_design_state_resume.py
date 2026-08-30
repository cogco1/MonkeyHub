from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.contracts.canonical import canonical_digest
from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    ComponentLineageBaselineSource,
    SpatialLayoutBaselineSource,
    StageBaselineCoverageReceipt,
    StageBaselineStatus,
    StageBaselineRole,
    StageBaselineSourceSet,
    baseline_level_for_design_phase,
    compile_stage_baseline_coverage,
)
from archflow.control.check_requirements import (
    assembly_stage_requirement,
    component_lineage_stage_requirement,
    spatial_layout_stage_requirement,
)
from archflow.project import (
    BranchRef,
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
)
from archflow.control.profile import StageRequirementProfileBinding
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.stage_closure import compile_composite_stage_closure
from archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectRoleObligation,
)
from archflow.runtime import (
    DesignControllerCheckpoint,
    DesignControllerError,
    ProjectControllerArchiveAdapter,
)
from archflow.runtime.component_index import ComponentIndex, ComponentIndexEntry
from archflow.runtime.design_controller import StageExitArchiveBundle
from archflow.runtime.event_log import DesignEvent, EventDecision
from archflow.runtime.state_reducer import (
    canonical_state_to_dict,
    make_initialization_event,
)
from archflow.runtime.stage_subject_inventory import (
    compile_stage_subject_inventory,
)
from archflow.state import initialize_canonical_project
from archflow.state.design_state import (
    DesignStateTree,
    compile_tree_phase_change,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.site_context import SiteBounds
from archflow.state.spatial import (
    ComponentMaturity,
    DesignComponent,
    MassingVolume,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
)
from archflow.validation.check_bridges import (
    bridge_component_lineage_receipt,
    bridge_spatial_validation_receipt,
)
from archflow.validation.assembly import (
    AssemblyObligationDisposition,
    RelationshipKind,
    check_assembly,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archflow.validation.spatial import validate_spatial_layout
from tests.test_design_controller import (
    _checkpoint,
    _phase_ready_checkpoint,
)
from tests.test_stage_baseline import physical_sources


def _bind_checkpoint(
    run,
    *,
    event_ref: str,
    epoch: int | None = None,
    decision_context_refs: tuple[str, ...] = (),
    template: DesignControllerCheckpoint | None = None,
) -> DesignControllerCheckpoint:
    if template is None:
        template, _ = _checkpoint()
    branch = BranchRef(
        run=run,
        branch_id=template.tree.branch.branch_id,
        epoch=(
            template.tree.branch.epoch
            if epoch is None
            else epoch
        ),
    )
    tree = DesignStateTree(
        branch=branch,
        nodes=tuple(
            replace(
                node,
                operational_state=replace(
                    node.operational_state,
                    branch=branch,
                ),
            )
            for node in template.tree.nodes
        ),
        interfaces=template.tree.interfaces,
    )
    target = tree.node(template.target_node_ref)
    deliverables = tuple(
        replace(
            item,
            branch=branch,
            base_state_digest=target.operational_state.state_digest,
        )
        for item in template.maturity.deliverables
    )
    return replace(
        template,
        tree=tree,
        maturity=replace(
            template.maturity,
            branch=branch,
            operational_state_digest=(
                target.operational_state.state_digest
            ),
            deliverables=deliverables,
        ),
        history_event_refs=(event_ref,),
        decision_context_refs=decision_context_refs,
    )


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _project_record_payload(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _project_record_from_payload(value: dict[str, object]) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=value["project_id"],
        relative_path=value["relative_path"],
        sha256=value["sha256"],
        media_type=value["media_type"],
    )


def _mechanical_stage_subject_sources(
    checkpoint: DesignControllerCheckpoint,
    *,
    source_ref: str,
) -> tuple[SpatialOptionProposal, ComponentIndex]:
    evidence = (source_ref,)
    component = DesignComponent(
        component_id="stage-root",
        parent_component_id=None,
        semantic_kind="stage-root",
        intent="Bind the exact durable stage subject universe.",
        maturity=ComponentMaturity.SCHEMATIC,
        revision=0,
        volume_ids=("stage-volume",),
        unresolved_child_roles=(),
        source_refs=evidence,
    )
    proposal = SpatialOptionProposal(
        option_id="durable-stage-subjects",
        label="Durable stage subject fixture",
        program_scenario_ref=None,
        footprint_range_ref=None,
        grid_basis=SpatialGridBasis(1.0, "square-meter", evidence),
        footprint_cells=((0, 0),),
        levels=(SpatialLevel("stage-level", 0, 1, evidence),),
        volumes=(
            MassingVolume(
                "stage-volume",
                SiteBounds((0, 0, 0), (0, 0, 0)),
                ("stage-level",),
                evidence,
            ),
        ),
        zones=(
            SpatialZone(
                "stage-zone",
                ("program-node:stage",),
                ("stage-level",),
                ("stage-volume",),
                evidence,
            ),
        ),
        components=(component,),
        connections=(),
        constraint_responses=(),
        typology_hypothesis="Generic durable stage fixture",
        palette_refs=(),
        rationale="Exercise exact P036 stage-subject replay.",
        responds_to_refs=(checkpoint.maturity.deliverables[0].ref,),
        expert_advice_refs=(),
        evidence_refs=evidence,
    )
    branch = checkpoint.maturity.branch
    index = ComponentIndex(
        project_id=branch.run.project_id,
        run_id=branch.run.run_id,
        base=branch.run.base,
        design_state_digest=checkpoint.maturity.operational_state_digest,
        component_proposal_digest=proposal.proposal_digest,
        geometry_program_digest=_digest("durable-stage-geometry-program"),
        control_tree_digest=checkpoint.tree.tree_digest,
        control_context_digest=_digest("durable-stage-control-context"),
        lifecycle_receipt_digest=_digest("durable-stage-lifecycle"),
        control_target_node_ref=checkpoint.target_node_ref,
        entries=(
            ComponentIndexEntry(
                component=component,
                geometry_object_ids=(),
                binding_ids=(),
                dependency_ids=(),
                task_ids=(),
                source_refs=evidence,
            ),
        ),
        dependencies=(),
        tasks=(),
    )
    return proposal, index


def _stage_role_target_refs(
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
    return {
        role: tuple(sorted(refs))
        for role, refs in targets.items()
    }


def _stage_exit_inputs(
    checkpoint: DesignControllerCheckpoint,
    *,
    source_ref: str,
    authority_ref: str,
    component_proposal: SpatialOptionProposal,
    component_proposal_ref: ProjectRecordRef,
    component_index: ComponentIndex,
    component_index_ref: ProjectRecordRef,
    claim_basis: tuple[str, str, str, str, str] | None = None,
):
    branch = checkpoint.maturity.branch
    stage_id = checkpoint.maturity.phase.value
    subject_digest = checkpoint.maturity.operational_state_digest
    scope_digest = _digest("durable-stage-exit-scope")
    base = physical_sources(
        stage_subject_source_digest=subject_digest,
    )
    lineage = base.component_lineage[0]
    lineage_profile = replace(
        lineage.profile,
        branch=branch,
        scope_digest=scope_digest,
        predecessor_stage_id="prior-stage",
        successor_stage_id=stage_id,
    )
    lineage_receipt = replace(
        lineage.source_receipt,
        predecessor_stage_id="prior-stage",
        successor_stage_id=stage_id,
    )
    spatial = base.spatial_layout[0]
    spatial_profile = replace(
        spatial.profile,
        branch=branch,
        scope_digest=scope_digest,
        stage_id=stage_id,
    )
    spatial_input = spatial.validator_input
    spatial_receipt = validate_spatial_layout(
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
    base_assembly = base.assembly[0]
    assembly = replace(
        base_assembly,
        requirements=tuple(
            replace(
                item,
                evidence_refs=(source_ref,) if item.evidence_refs else (),
                authority_refs=(
                    (authority_ref,) if item.authority_refs else ()
                ),
            )
            for item in base_assembly.requirements
        ),
        coverage_manifest=replace(
            base_assembly.coverage_manifest,
            obligations=tuple(
                replace(
                    item,
                    evidence_refs=(
                        (source_ref,) if item.evidence_refs else ()
                    ),
                    authority_refs=(
                        (authority_ref,) if item.authority_refs else ()
                    ),
                )
                for item in base_assembly.coverage_manifest.obligations
            ),
            relation_candidates=tuple(
                replace(
                    item,
                    evidence_refs=(
                        (source_ref,) if item.evidence_refs else ()
                    ),
                    authority_refs=(
                        (authority_ref,) if item.authority_refs else ()
                    ),
                )
                for item in (
                    base_assembly.coverage_manifest.relation_candidates
                )
            ),
        ),
    )
    sources = StageBaselineSourceSet(
        component_lineage=(
            ComponentLineageBaselineSource(
                lineage_profile,
                lineage_receipt,
            ),
        ),
        spatial_layout=(
            SpatialLayoutBaselineSource(
                spatial_profile,
                spatial_input,
            ),
        ),
        assembly=(assembly,),
    )
    requirements = (
        component_lineage_stage_requirement(lineage_profile),
        spatial_layout_stage_requirement(spatial_profile),
        assembly_stage_requirement(sources.assembly[0]),
    )
    receipts = (
        bridge_component_lineage_receipt(
            lineage_profile,
            lineage_receipt,
            stage_subject_digest=subject_digest,
        ),
        bridge_spatial_validation_receipt(
            spatial_profile,
            spatial_receipt,
            stage_subject_digest=subject_digest,
        ),
        check_assembly(
            assembly,
            branch=branch,
            scope_digest=scope_digest,
            stage_subject_digest=subject_digest,
        ),
    )
    if claim_basis is not None:
        (
            claim_ref,
            applicability_ref,
            adoption_ref,
            claim_source_ref,
            claim_authority_ref,
        ) = claim_basis
        claim_requirement = StageCheckRequirement(
            requirement_id="durable-claim-bound-check",
            checker_id="durable-claim-bound-checker",
            target_kind=RequirementTargetKind.ARTIFACT,
            basis_mode=RequirementBasisMode.CLAIM_BOUND,
            denominator_refs=("artifact:claim-bound-stage-exit",),
            required_claim_refs=(claim_ref,),
            required_applicability_refs=(applicability_ref,),
            required_adoption_refs=(adoption_ref,),
            required_source_refs=(claim_source_ref,),
            required_authority_refs=(claim_authority_ref,),
        )
        requirements = (*requirements, claim_requirement)
        receipts = (
            *receipts,
            CheckReceiptEnvelope(
                check_id=claim_requirement.requirement_id,
                checker_id=claim_requirement.checker_id,
                checker_version="claim-bound-fixture@1",
                branch=branch,
                scope_digest=scope_digest,
                subject_refs=claim_requirement.denominator_refs,
                subject_digest=subject_digest,
                status=CheckStatus.PASS,
                claim_refs=claim_requirement.required_claim_refs,
                applicability_refs=(
                    claim_requirement.required_applicability_refs
                ),
                adoption_refs=claim_requirement.required_adoption_refs,
                source_refs=claim_requirement.required_source_refs,
                authority_refs=claim_requirement.required_authority_refs,
                coverage_denominator=claim_requirement.denominator_refs,
                covered_refs=claim_requirement.denominator_refs,
            ),
        )
    profile = StageRequirementProfile(
        profile_id="durable-stage-exit-profile",
        typology_id="generic-stage-exit-test",
        stage_id=stage_id,
        branch=branch,
        predecessor_state_digest=subject_digest,
        scope_digest=scope_digest,
        stage_subject_ref=checkpoint.maturity.deliverables[0].ref,
        requirements=requirements,
    )
    baseline_level = baseline_level_for_design_phase(
        checkpoint.maturity.phase
    )
    targets = _stage_role_target_refs(sources)
    inventory = compile_stage_subject_inventory(
        inventory_id="durable-stage-subject-inventory",
        branch=branch,
        stage_id=stage_id,
        stage_subject_ref=profile.stage_subject_ref,
        stage_subject_digest=subject_digest,
        baseline_level=baseline_level,
        component_proposal=component_proposal,
        component_proposal_ref=component_proposal_ref,
        component_index=component_index,
        component_index_ref=component_index_ref,
        role_obligations={
            "stage-root": tuple(
                StageSubjectRoleObligation(
                    role=role,
                    disposition=StageSubjectDisposition.REQUIRED,
                    target_refs=targets[role],
                    evidence_refs=(source_ref,),
                    authority_refs=(authority_ref,),
                )
                for role in sorted(BASELINE_LEVEL_ROLES[baseline_level])
            ),
        },
    )
    closure = compile_composite_stage_closure(
        profile,
        subject_digest=subject_digest,
        check_receipts=receipts,
    )
    baseline_coverage = compile_stage_baseline_coverage(
        profile,
        level=baseline_level,
        sources=sources,
        subject_digest=subject_digest,
        subject_inventory=inventory,
        check_receipts=receipts,
    )
    return (
        profile,
        sources,
        receipts,
        closure,
        baseline_coverage,
        inventory,
    )


def _advance_checkpoint(
    previous: DesignControllerCheckpoint,
    *,
    event_ref: str,
    next_phase: DesignPhase = DesignPhase.DESIGN_DEVELOPMENT,
) -> DesignControllerCheckpoint:
    transition = compile_tree_phase_change(
        previous.tree,
        next_phase=next_phase.value,
    )
    target_ref = transition.remap(previous.target_node_ref)
    target = transition.tree.node(target_ref)
    return DesignControllerCheckpoint(
        tree=transition.tree,
        target_node_ref=target_ref,
        maturity=replace(
            previous.maturity,
            branch=transition.tree.branch,
            operational_state_digest=target.operational_state.state_digest,
            phase=next_phase,
            invalidated_refs=(),
            revalidation_required_refs=(),
        ),
        status=previous.status,
        iteration=previous.iteration,
        max_iterations=previous.max_iterations,
        history_event_refs=(*previous.history_event_refs, event_ref),
        decision_context_refs=previous.decision_context_refs,
        recent_action_digests=previous.recent_action_digests,
        reopened_node_refs=(),
    )


def _prepare_stage_exit(
    root: Path,
    *,
    project_id: str,
    legacy_previous: bool = False,
    claim_bound: bool = False,
    anchor_proposal: bool = True,
):
    canonical = initialize_canonical_project(project_id)
    initial_event, sealed = make_initialization_event(
        canonical,
        actor_id="system",
    )
    repository = FilesystemProjectRepository.initialize(
        root,
        project_id=project_id,
        initial_state=canonical_state_to_dict(sealed),
    )
    run = repository.create_run("run-001")
    previous = _bind_checkpoint(
        run,
        event_ref=initial_event.event_id,
        template=_phase_ready_checkpoint(),
    )
    adapter = ProjectControllerArchiveAdapter(
        repository,
        branch=previous.tree.branch,
    )
    adapter.event_log.append(initial_event)
    destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=run.run_id,
        branch_id=previous.tree.branch.branch_id,
    )
    research_run = repository.create_run("research-001")
    research_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=research_run.run_id,
    )
    source_record = repository.put_json(
        run=research_run,
        destination=research_destination,
        record_kind="stage-requirement-source",
        payload={
            "schema": "StageRequirementBasisRecord@1",
            "kind": "source",
            "project_id": run.project_id,
            "run_id": research_run.run_id,
        },
    )
    authority_record = repository.put_json(
        run=research_run,
        destination=research_destination,
        record_kind="stage-profile-authorization",
        payload={
            "schema": "StageProfileAuthorization@1",
            "kind": "authority",
            "project_id": run.project_id,
            "run_id": research_run.run_id,
            "stage_id": previous.maturity.phase.value,
        },
    )
    claim_record = repository.put_json(
        run=research_run,
        destination=research_destination,
        record_kind="stage-requirement-claim",
        payload={
            "schema": "StageRequirementBasisRecord@1",
            "kind": "claim",
            "project_id": run.project_id,
            "run_id": research_run.run_id,
        },
    )
    applicability_record = repository.put_json(
        run=research_run,
        destination=research_destination,
        record_kind="stage-requirement-applicability",
        payload={
            "schema": "StageRequirementBasisRecord@1",
            "kind": "applicability",
            "project_id": run.project_id,
            "run_id": research_run.run_id,
        },
    )
    adoption_record = repository.put_json(
        run=research_run,
        destination=research_destination,
        record_kind="stage-requirement-adoption",
        payload={
            "schema": "StageRequirementBasisRecord@1",
            "kind": "adoption",
            "project_id": run.project_id,
            "run_id": research_run.run_id,
        },
    )
    component_proposal, component_index = (
        _mechanical_stage_subject_sources(
            previous,
            source_ref=source_record.uri,
        )
    )
    component_proposal_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-component-proposal",
        payload=component_proposal.to_dict(),
    )
    component_index_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-component-index",
        payload=component_index.to_dict(),
    )
    if anchor_proposal:
        stage_deliverable = previous.maturity.deliverables[0]
        previous = replace(
            previous,
            maturity=replace(
                previous.maturity,
                deliverables=(
                    replace(
                        stage_deliverable,
                        base_state_digest=(
                            previous.maturity.operational_state_digest
                        ),
                        evidence_refs=tuple(
                            sorted(
                                {
                                    *stage_deliverable.evidence_refs,
                                    component_proposal_record.uri,
                                }
                            )
                        ),
                    ),
                    *previous.maturity.deliverables[1:],
                ),
            ),
        )
    if legacy_previous:
        previous_record = repository.put_json(
            run=run,
            destination=destination,
            record_kind="legacy-design-controller",
            payload={
                "schema": adapter.LEGACY_CHECKPOINT_SCHEMA,
                "project_id": run.project_id,
                "run_id": run.run_id,
                "branch_id": previous.tree.branch.branch_id,
                "branch_epoch": previous.tree.branch.epoch,
                "run_base": {
                    "project_id": run.base.project_id,
                    "version": run.base.version,
                    "state_sha256": run.base.require_digest(),
                },
                "checkpoint_digest": previous.checkpoint_digest,
                "event_count": 1,
                "event_head_sha256": initial_event.event_sha256,
                "checkpoint": previous.to_dict(),
            },
        )
    else:
        previous_record = adapter.save_checkpoint(previous)
    (
        profile,
        baseline_sources,
        check_receipts,
        closure,
        baseline_coverage,
        stage_subject_inventory,
    ) = _stage_exit_inputs(
        previous,
        source_ref=source_record.uri,
        authority_ref=authority_record.uri,
        component_proposal=component_proposal,
        component_proposal_ref=component_proposal_record,
        component_index=component_index,
        component_index_ref=component_index_record,
        claim_basis=(
            claim_record.uri,
            applicability_record.uri,
            adoption_record.uri,
            source_record.uri,
            authority_record.uri,
        )
        if claim_bound
        else None,
    )
    stage_subject_inventory_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-subject-inventory",
        payload=stage_subject_inventory.to_dict(),
    )
    profile_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-requirement-profile",
        payload=profile.to_dict(),
    )
    binding = StageRequirementProfileBinding(
        binding_id="durable-stage-profile-binding",
        profile_id=profile.profile_id,
        profile_digest=profile.profile_digest,
        branch=previous.tree.branch,
        stage_id=profile.stage_id,
        stage_subject_ref=profile.stage_subject_ref,
        subject_digest=closure.subject_digest,
        profile_ref=ProjectRecordRef(
            project_id=profile_record.project_id,
            relative_path=profile_record.relative_path,
            sha256=profile.profile_digest,
            media_type=profile_record.media_type,
        ),
        stage_subject_inventory_ref=stage_subject_inventory_record,
        stage_subject_inventory_digest=(
            stage_subject_inventory.inventory_digest
        ),
        authority_refs=(authority_record,),
    )
    binding_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-profile-binding",
        payload=binding.to_dict(),
    )
    closure_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-closure",
        payload=closure.to_dict(),
    )
    check_records = tuple(
        repository.put_json(
            run=run,
            destination=destination,
            record_kind=f"stage-check-{index:03d}",
            payload=receipt.to_dict(),
        )
        for index, receipt in enumerate(check_receipts)
    )
    baseline_sources_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-baseline-sources",
        payload=baseline_sources.to_dict(),
    )
    baseline_coverage_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="stage-baseline-coverage",
        payload=baseline_coverage.to_dict(),
    )
    bundle = StageExitArchiveBundle(
        predecessor_checkpoint_ref=previous_record,
        profile_binding_ref=binding_record,
        profile_ref=profile_record,
        closure_ref=closure_record,
        baseline_sources_ref=baseline_sources_record,
        baseline_coverage_ref=baseline_coverage_record,
        stage_subject_inventory_ref=stage_subject_inventory_record,
        component_proposal_ref=component_proposal_record,
        component_index_ref=component_index_record,
        check_receipt_refs=check_records,
        requirement_basis_refs=(
            (
                claim_record,
                applicability_record,
                adoption_record,
                source_record,
                authority_record,
            )
            if claim_bound
            else (source_record, authority_record)
        ),
    )

    phase_event = DesignEvent.create(
        sequence=1,
        project_id=run.project_id,
        event_type="controller.phase-advanced",
        decision=EventDecision.ACCEPTED,
        actor_id="controller",
        authority_id="controller",
        prior_event_sha256=initial_event.event_sha256,
        prior_state=run.base,
        proposed_delta={
            "from_phase": previous.maturity.phase.value,
            "to_phase": DesignPhase.DESIGN_DEVELOPMENT.value,
        },
        evidence_refs=(),
        validation_receipt_refs=("validation://stage-exit/closure",),
        commit_receipt_ref=None,
        artifact_refs=(),
        reducer_version="controller-test@1",
        resulting_state=run.base,
    )
    adapter.event_log.append(phase_event)
    advanced = _advance_checkpoint(
        previous,
        event_ref=phase_event.event_id,
    )
    archive_adapter = ProjectControllerArchiveAdapter(
        repository,
        branch=advanced.tree.branch,
    )
    return repository, archive_adapter, previous, advanced, bundle


def _proof_checkpoint_payload(
    adapter: ProjectControllerArchiveAdapter,
    checkpoint: DesignControllerCheckpoint,
    *,
    checkpoint_schema: str,
    proof: dict[str, object],
) -> dict[str, object]:
    events = adapter.event_log.records()
    if checkpoint.history_event_refs != tuple(item.event_id for item in events):
        raise AssertionError("fixture checkpoint does not bind the event chain")
    run = checkpoint.tree.branch.run
    return {
        "schema": checkpoint_schema,
        "project_id": run.project_id,
        "run_id": run.run_id,
        "branch_id": checkpoint.tree.branch.branch_id,
        "branch_epoch": checkpoint.tree.branch.epoch,
        "run_base": {
            "project_id": run.base.project_id,
            "version": run.base.version,
            "state_sha256": run.base.require_digest(),
        },
        "checkpoint_digest": checkpoint.checkpoint_digest,
        "event_count": len(events),
        "event_head_sha256": events[-1].event_sha256,
        "checkpoint": checkpoint.to_dict(),
        "stage_exit_proof": proof,
    }


def _persist_true_legacy_stage_exit_checkpoint(
    repository: FilesystemProjectRepository,
    adapter: ProjectControllerArchiveAdapter,
    previous: DesignControllerCheckpoint,
    advanced: DesignControllerCheckpoint,
    bundle: StageExitArchiveBundle,
    *,
    record_kind: str = "true-legacy-stage-exit-checkpoint-v2",
) -> tuple[
    ProjectRecordRef,
    dict[str, object],
    dict[str, object],
]:
    """Persist the actual pre-inventory @2 binding, baseline, bundle and proof."""

    run = advanced.tree.branch.run
    destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=run.run_id,
        branch_id=advanced.tree.branch.branch_id,
    )

    current_binding_payload = repository.load_json(bundle.profile_binding_ref)
    legacy_binding_payload = copy.deepcopy(current_binding_payload)
    legacy_binding_payload["schema"] = StageRequirementProfileBinding.LEGACY_SCHEMA
    legacy_binding_payload.pop("stage_subject_inventory_ref")
    legacy_binding_payload.pop("stage_subject_inventory_digest")
    legacy_binding = StageRequirementProfileBinding.from_dict(
        legacy_binding_payload
    )
    if not legacy_binding.is_legacy_read_only:
        raise AssertionError("legacy binding fixture is not read-only")
    legacy_binding_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="legacy-stage-profile-binding-v1",
        payload=legacy_binding_payload,
    )

    current_baseline_payload = repository.load_json(
        bundle.baseline_coverage_ref
    )
    legacy_baseline_payload = copy.deepcopy(current_baseline_payload)
    legacy_baseline_payload["schema"] = (
        StageBaselineCoverageReceipt.LEGACY_SCHEMA
    )
    legacy_baseline_payload.pop("stage_subject_inventory_digest")
    legacy_baseline = StageBaselineCoverageReceipt.from_dict(
        legacy_baseline_payload
    )
    if legacy_baseline.stage_subject_inventory_digest is not None:
        raise AssertionError("legacy baseline fixture retained an inventory")
    legacy_baseline_record = repository.put_json(
        run=run,
        destination=destination,
        record_kind="legacy-stage-baseline-coverage-v2",
        payload=legacy_baseline_payload,
    )

    current_bundle_payload = bundle.to_dict()
    legacy_bundle_content = copy.deepcopy(current_bundle_payload)
    legacy_bundle_content.pop("bundle_digest")
    legacy_bundle_content["schema"] = bundle.LEGACY_SCHEMA
    legacy_bundle_content["profile_binding_ref"] = _project_record_payload(
        legacy_binding_record
    )
    legacy_bundle_content["baseline_coverage_ref"] = _project_record_payload(
        legacy_baseline_record
    )
    legacy_bundle_content.pop("stage_subject_inventory_ref")
    legacy_bundle_content.pop("component_proposal_ref")
    legacy_bundle_content.pop("component_index_ref")
    legacy_bundle_payload = {
        **legacy_bundle_content,
        "bundle_digest": canonical_digest(legacy_bundle_content),
    }

    current_proof = adapter._admit_stage_exit(  # noqa: SLF001
        previous,
        advanced,
        bundle,
    )
    legacy_proof_content = copy.deepcopy(current_proof)
    legacy_proof_content.pop("proof_digest")
    legacy_proof_content["schema"] = adapter.LEGACY_STAGE_EXIT_PROOF_SCHEMA
    legacy_proof_content["bundle"] = legacy_bundle_payload
    legacy_proof_content["profile_binding_digest"] = (
        legacy_binding.binding_digest
    )
    legacy_proof_content["baseline_coverage_digest"] = (
        legacy_baseline.receipt_digest
    )
    legacy_proof_content.pop("stage_subject_inventory_digest")
    legacy_proof_payload = {
        **legacy_proof_content,
        "proof_digest": canonical_digest(legacy_proof_content),
    }
    checkpoint_payload = _proof_checkpoint_payload(
        adapter,
        advanced,
        checkpoint_schema=adapter.PROOF_CHECKPOINT_SCHEMA,
        proof=legacy_proof_payload,
    )
    record = repository.put_json(
        run=run,
        destination=destination,
        record_kind=record_kind,
        payload=checkpoint_payload,
    )
    return record, checkpoint_payload, legacy_bundle_payload


class DurableDesignStateResumeTests(unittest.TestCase):
    def test_restart_reloads_checkpoint_from_one_project_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-resume"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-resume"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-resume",
                initial_state=canonical_state_to_dict(sealed),
            )
            run = repository.create_run("run-001")
            checkpoint = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
            )
            adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint.tree.branch,
            )

            adapter.event_log.append(initial_event)
            record = adapter.save_checkpoint(checkpoint)
            self.assertEqual(
                adapter.save_checkpoint(checkpoint),
                record,
            )

            del adapter
            del repository
            reopened = FilesystemProjectRepository.open(root)
            durable_run = reopened.load_run("run-001")
            adapter = ProjectControllerArchiveAdapter(
                reopened,
                branch=BranchRef(
                    run=durable_run,
                    branch_id=checkpoint.tree.branch.branch_id,
                    epoch=checkpoint.tree.branch.epoch,
                ),
            )
            resumed = adapter.load_latest_checkpoint()

            self.assertEqual(resumed.record_ref, record)
            self.assertEqual(resumed.checkpoint, checkpoint)
            self.assertEqual(resumed.event_chain, (initial_event,))
            self.assertEqual(
                reopened.read_head(),
                initial_event.resulting_state,
            )
            self.assertEqual(reopened.verify().orphan_paths, ())

    def test_phase_exit_archive_requires_and_retains_exact_p036_proof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-stage-exit",
                    project_id="controller-stage-exit",
                )
            )

            with self.assertRaisesRegex(
                DesignControllerError,
                "requires a P036 stage-exit bundle",
            ):
                adapter.save_checkpoint(advanced)

            record = adapter.save_checkpoint(
                advanced,
                stage_exit_bundle=bundle,
            )
            payload = repository.load_json(record)
            self.assertEqual(
                payload["schema"],
                adapter.CHECKPOINT_SCHEMA,
            )
            proof = payload["stage_exit_proof"]
            self.assertEqual(proof["bundle"], bundle.to_dict())
            self.assertEqual(len(proof["proof_digest"]), 64)
            inventory = StageSubjectInventory.from_dict(
                repository.load_json(bundle.stage_subject_inventory_ref)
            )
            component_proposal = SpatialOptionProposal.from_dict(
                repository.load_json(bundle.component_proposal_ref)
            )
            component_index = ComponentIndex.from_dict(
                repository.load_json(bundle.component_index_ref)
            )
            binding = StageRequirementProfileBinding.from_dict(
                repository.load_json(bundle.profile_binding_ref)
            )
            self.assertEqual(
                proof["stage_subject_inventory_digest"],
                inventory.inventory_digest,
            )
            self.assertEqual(
                binding.stage_subject_inventory_ref,
                bundle.stage_subject_inventory_ref,
            )
            self.assertEqual(
                inventory.component_proposal_digest,
                component_proposal.proposal_digest,
            )
            self.assertEqual(
                inventory.component_index_digest,
                component_index.index_digest,
            )
            self.assertIn(
                bundle.component_proposal_ref.uri,
                previous.maturity.deliverables[0].evidence_refs,
            )
            self.assertEqual(len(proof["baseline_sources_digest"]), 64)
            self.assertEqual(len(proof["baseline_coverage_digest"]), 64)
            self.assertTrue(
                all(
                    ref.relative_path.startswith("runs/research-001/")
                    for ref in bundle.requirement_basis_refs
                )
            )
            assembly_payloads = tuple(
                repository.load_json(ref)
                for ref in bundle.check_receipt_refs
                if repository.load_json(ref).get("checker_id")
                == "assembly-relationship-checker"
            )
            self.assertEqual(
                tuple(item["checker_version"] for item in assembly_payloads),
                ("1.0.0",),
            )
            self.assertEqual(
                proof["previous_checkpoint_digest"],
                previous.checkpoint_digest,
            )
            self.assertEqual(
                proof["next_checkpoint_digest"],
                advanced.checkpoint_digest,
            )
            self.assertEqual(
                adapter.save_checkpoint(advanced),
                record,
            )
            self.assertEqual(
                adapter.load_latest_checkpoint().checkpoint,
                advanced,
            )

            repository.put_json(
                run=previous.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=previous.tree.branch.run.run_id,
                    branch_id=previous.tree.branch.branch_id,
                ),
                record_kind="duplicate-stage-exit-current-epoch",
                payload=payload,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "ambiguous latest checkpoint lineage",
            ):
                adapter.load_latest_checkpoint()

    def test_phase_exit_archive_rejects_unreadable_or_leaking_refs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-stage-exit-invalid",
                    project_id="controller-stage-exit-invalid",
                )
            )
            prefix = (
                f"runs/{previous.tree.branch.run.run_id}/branches/"
                f"{previous.tree.branch.branch_id}/records"
            )
            invalid = (
                (
                    replace(
                        bundle,
                        closure_ref=ProjectRecordRef(
                            project_id=bundle.closure_ref.project_id,
                            relative_path=f"{prefix}/missing-closure.json",
                            sha256="0" * 64,
                        ),
                    ),
                    "P036 readback failed",
                ),
                (
                    replace(
                        bundle,
                        closure_ref=ProjectRecordRef(
                            project_id=bundle.closure_ref.project_id,
                            relative_path=(
                                f"runs/{previous.tree.branch.run.run_id}/"
                                "branches/other-option/records/closure.json"
                            ),
                            sha256=bundle.closure_ref.sha256,
                        ),
                    ),
                    "another branch",
                ),
                (
                    replace(
                        bundle,
                        closure_ref=replace(
                            bundle.closure_ref,
                            sha256="f" * 64,
                        ),
                    ),
                    "P036 readback failed",
                ),
                (
                    replace(
                        bundle,
                        baseline_sources_ref=ProjectRecordRef(
                            project_id=(
                                bundle.baseline_sources_ref.project_id
                            ),
                            relative_path=(
                                f"{prefix}/missing-baseline-sources.json"
                            ),
                            sha256="0" * 64,
                        ),
                    ),
                    "P036 readback failed",
                ),
                (
                    replace(
                        bundle,
                        stage_subject_inventory_ref=replace(
                            bundle.stage_subject_inventory_ref,
                            sha256="f" * 64,
                        ),
                    ),
                    "P036 readback failed",
                ),
                (
                    replace(
                        bundle,
                        requirement_basis_refs=(
                            bundle.requirement_basis_refs[0],
                        ),
                    ),
                    "requirement basis refs are not exact P036 records",
                ),
                (
                    replace(
                        bundle,
                        requirement_basis_refs=tuple(
                            replace(ref, sha256="f" * 64)
                            if index == 0
                            else ref
                            for index, ref in enumerate(
                                bundle.requirement_basis_refs
                            )
                        ),
                    ),
                    "P036 readback failed",
                ),
            )
            for invalid_bundle, message in invalid:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(
                        DesignControllerError,
                        message,
                    ):
                        adapter.save_checkpoint(
                            advanced,
                            stage_exit_bundle=invalid_bundle,
                        )

            with self.assertRaisesRegex(
                DesignControllerError,
                "cross project identity",
            ):
                replace(
                    bundle,
                    requirement_basis_refs=(
                        *bundle.requirement_basis_refs,
                        ProjectRecordRef(
                            project_id="another-project",
                            relative_path="runs/research/records/basis.json",
                            sha256="0" * 64,
                        ),
                    ),
                )

            binding = StageRequirementProfileBinding.from_dict(
                repository.load_json(bundle.profile_binding_ref)
            )
            missing_authority = replace(
                binding.authority_refs[0],
                relative_path=(
                    "runs/research-001/records/"
                    "missing-profile-authorization.json"
                ),
                sha256="0" * 64,
            )
            invalid_binding = replace(
                binding,
                authority_refs=(missing_authority,),
            )
            invalid_binding_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=previous.tree.branch.run.run_id,
                    branch_id=previous.tree.branch.branch_id,
                ),
                record_kind="stage-profile-binding-missing-authority",
                payload=invalid_binding.to_dict(),
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "profile authority_ref P036 readback failed",
            ):
                adapter.save_checkpoint(
                    advanced,
                    stage_exit_bundle=replace(
                        bundle,
                        profile_binding_ref=invalid_binding_record,
                    ),
                )

    def test_phase_exit_rejects_unanchored_component_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _repository, adapter, _previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-stage-exit-unanchored",
                    project_id="controller-stage-exit-unanchored",
                    anchor_proposal=False,
                )
            )

            with self.assertRaisesRegex(
                DesignControllerError,
                "component proposal.*predecessor|proposal.*anchored",
            ):
                adapter.save_checkpoint(
                    advanced,
                    stage_exit_bundle=bundle,
                )

    def test_phase_exit_archive_replays_baseline_against_real_receipts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-stage-exit-replay",
                    project_id="controller-stage-exit-replay",
                )
            )
            destination = PersistenceDestination(
                PersistenceArea.RUN_BRANCH,
                run_id=previous.tree.branch.run.run_id,
                branch_id=previous.tree.branch.branch_id,
            )
            profile = StageRequirementProfile.from_dict(
                repository.load_json(bundle.profile_ref)
            )
            receipts = tuple(
                CheckReceiptEnvelope.from_dict(repository.load_json(ref))
                for ref in bundle.check_receipt_refs
            )
            assembly_index = next(
                index
                for index, receipt in enumerate(receipts)
                if receipt.checker_id == "assembly-relationship-checker"
            )
            forged_receipt = replace(
                receipts[assembly_index],
                checker_version="forged-summary-pass@1",
            )
            forged_receipt_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="forged-assembly-summary-pass",
                payload=forged_receipt.to_dict(),
            )
            forged_receipts = tuple(
                forged_receipt if index == assembly_index else receipt
                for index, receipt in enumerate(receipts)
            )
            forged_closure = compile_composite_stage_closure(
                profile,
                subject_digest=previous.maturity.operational_state_digest,
                check_receipts=forged_receipts,
            )
            forged_closure_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="forged-stage-closure",
                payload=forged_closure.to_dict(),
            )
            forged_check_refs = tuple(
                forged_receipt_record
                if repository.load_json(ref).get("checker_id")
                == "assembly-relationship-checker"
                else ref
                for ref in bundle.check_receipt_refs
            )
            forged_bundle = replace(
                bundle,
                closure_ref=forged_closure_record,
                check_receipt_refs=forged_check_refs,
            )

            with self.assertRaisesRegex(
                DesignControllerError,
                "baseline is not satisfied after P036 replay",
            ):
                adapter.save_checkpoint(
                    advanced,
                    stage_exit_bundle=forged_bundle,
                )

            sources = StageBaselineSourceSet.from_dict(
                repository.load_json(bundle.baseline_sources_ref)
            )
            tampered_assembly = replace(
                sources.assembly[0],
                linear_tolerance=(
                    sources.assembly[0].linear_tolerance * 2.0
                ),
            )
            tampered_sources = replace(
                sources,
                assembly=(tampered_assembly,),
            )
            tampered_sources_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="tampered-stage-baseline-sources",
                payload=tampered_sources.to_dict(),
            )

            with self.assertRaisesRegex(
                DesignControllerError,
                "baseline is not satisfied after P036 replay",
            ):
                adapter.save_checkpoint(
                    advanced,
                    stage_exit_bundle=replace(
                        bundle,
                        baseline_sources_ref=tampered_sources_record,
                    ),
                )

    def test_synchronized_assembly_shrink_is_open_against_inventory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-stage-exit-shrink",
                    project_id="controller-stage-exit-shrink",
                )
            )
            destination = PersistenceDestination(
                PersistenceArea.RUN_BRANCH,
                run_id=previous.tree.branch.run.run_id,
                branch_id=previous.tree.branch.branch_id,
            )
            profile = StageRequirementProfile.from_dict(
                repository.load_json(bundle.profile_ref)
            )
            sources = StageBaselineSourceSet.from_dict(
                repository.load_json(bundle.baseline_sources_ref)
            )
            inventory = StageSubjectInventory.from_dict(
                repository.load_json(bundle.stage_subject_inventory_ref)
            )
            original_assembly = sources.assembly[0]
            reduced_assembly = replace(
                original_assembly,
                requirements=tuple(
                    requirement
                    for requirement in original_assembly.requirements
                    if requirement.kind is not RelationshipKind.OPENING_CLEAR
                ),
                coverage_manifest=replace(
                    original_assembly.coverage_manifest,
                    obligations=tuple(
                        replace(
                            obligation,
                            disposition=(
                                AssemblyObligationDisposition.NOT_APPLICABLE
                            ),
                            relationship_kind=None,
                            endpoint_index=None,
                            requirement_id=None,
                        )
                        if obligation.relationship_kind
                        is RelationshipKind.OPENING_CLEAR
                        else obligation
                        for obligation in (
                            original_assembly.coverage_manifest.obligations
                        )
                    ),
                ),
            )
            reduced_sources = replace(
                sources,
                assembly=(reduced_assembly,),
            )
            reduced_requirements = (
                component_lineage_stage_requirement(
                    reduced_sources.component_lineage[0].profile
                ),
                spatial_layout_stage_requirement(
                    reduced_sources.spatial_layout[0].profile
                ),
                assembly_stage_requirement(reduced_assembly),
            )
            reduced_profile = replace(
                profile,
                requirements=reduced_requirements,
            )
            original_receipts = tuple(
                CheckReceiptEnvelope.from_dict(repository.load_json(ref))
                for ref in bundle.check_receipt_refs
            )
            reduced_assembly_receipt = check_assembly(
                reduced_assembly,
                branch=profile.branch,
                scope_digest=profile.scope_digest,
                stage_subject_digest=(
                    previous.maturity.operational_state_digest
                ),
            )
            reduced_receipts = tuple(
                reduced_assembly_receipt
                if receipt.checker_id == "assembly-relationship-checker"
                else receipt
                for receipt in original_receipts
            )
            reduced_closure = compile_composite_stage_closure(
                reduced_profile,
                subject_digest=previous.maturity.operational_state_digest,
                check_receipts=reduced_receipts,
            )
            reduced_baseline = compile_stage_baseline_coverage(
                reduced_profile,
                level=baseline_level_for_design_phase(
                    previous.maturity.phase
                ),
                sources=reduced_sources,
                subject_digest=previous.maturity.operational_state_digest,
                subject_inventory=inventory,
                check_receipts=reduced_receipts,
            )
            self.assertIs(reduced_assembly_receipt.status, CheckStatus.PASS)
            self.assertEqual(reduced_closure.status.value, "SATISFIED")
            self.assertIs(reduced_baseline.status, StageBaselineStatus.OPEN)
            reduced_profile_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="reduced-stage-requirement-profile",
                payload=reduced_profile.to_dict(),
            )
            original_binding = StageRequirementProfileBinding.from_dict(
                repository.load_json(bundle.profile_binding_ref)
            )
            reduced_binding = replace(
                original_binding,
                profile_digest=reduced_profile.profile_digest,
                profile_ref=ProjectRecordRef(
                    project_id=reduced_profile_record.project_id,
                    relative_path=reduced_profile_record.relative_path,
                    sha256=reduced_profile.profile_digest,
                    media_type=reduced_profile_record.media_type,
                ),
            )
            reduced_binding_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="reduced-stage-profile-binding",
                payload=reduced_binding.to_dict(),
            )
            reduced_closure_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="reduced-stage-closure",
                payload=reduced_closure.to_dict(),
            )
            reduced_source_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="reduced-stage-baseline-sources",
                payload=reduced_sources.to_dict(),
            )
            reduced_baseline_record = repository.put_json(
                run=previous.tree.branch.run,
                destination=destination,
                record_kind="reduced-stage-baseline-coverage",
                payload=reduced_baseline.to_dict(),
            )
            reduced_check_records = tuple(
                repository.put_json(
                    run=previous.tree.branch.run,
                    destination=destination,
                    record_kind=f"reduced-stage-check-{index:03d}",
                    payload=receipt.to_dict(),
                )
                for index, receipt in enumerate(reduced_receipts)
            )
            reduced_bundle = replace(
                bundle,
                profile_binding_ref=reduced_binding_record,
                profile_ref=reduced_profile_record,
                closure_ref=reduced_closure_record,
                baseline_sources_ref=reduced_source_record,
                baseline_coverage_ref=reduced_baseline_record,
                check_receipt_refs=reduced_check_records,
            )

            with self.assertRaisesRegex(
                DesignControllerError,
                "baseline is not satisfied",
            ):
                adapter.save_checkpoint(
                    advanced,
                    stage_exit_bundle=reduced_bundle,
                )

    def test_claim_bound_basis_requires_all_five_exact_p036_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, _previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-claim-bound-basis",
                    project_id="controller-claim-bound-basis",
                    claim_bound=True,
                )
            )
            basis_by_kind = {
                repository.load_json(ref)["kind"]: ref
                for ref in bundle.requirement_basis_refs
            }
            self.assertEqual(
                set(basis_by_kind),
                {
                    "claim",
                    "applicability",
                    "adoption",
                    "source",
                    "authority",
                },
            )

            with self.assertRaisesRegex(
                DesignControllerError,
                "requirement basis refs are not exact P036 records",
            ):
                adapter.save_checkpoint(
                    advanced,
                    stage_exit_bundle=replace(
                        bundle,
                        requirement_basis_refs=tuple(
                            ref
                            for ref in bundle.requirement_basis_refs
                            if ref != basis_by_kind["claim"]
                        ),
                    ),
                )

            with self.assertRaisesRegex(
                DesignControllerError,
                "requirement basis_ref P036 readback failed",
            ):
                adapter.save_checkpoint(
                    advanced,
                    stage_exit_bundle=replace(
                        bundle,
                        requirement_basis_refs=tuple(
                            replace(ref, sha256="f" * 64)
                            if ref == basis_by_kind["adoption"]
                            else ref
                            for ref in bundle.requirement_basis_refs
                        ),
                    ),
                )

            record = adapter.save_checkpoint(
                advanced,
                stage_exit_bundle=bundle,
            )
            self.assertIsNotNone(
                repository.load_json(record)["stage_exit_proof"]
            )

    def test_same_epoch_checkpoint_retains_exact_stage_exit_anchor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, _previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-checkpoint-anchor",
                    project_id="controller-checkpoint-anchor",
                )
            )
            anchor_record = adapter.save_checkpoint(
                advanced,
                stage_exit_bundle=bundle,
            )
            prior_event = adapter.event_log.records()[-1]
            followup_event = DesignEvent.create(
                sequence=2,
                project_id=advanced.tree.branch.run.project_id,
                event_type="controller.same-phase-progress",
                decision=EventDecision.ACCEPTED,
                actor_id="controller",
                authority_id="controller",
                prior_event_sha256=prior_event.event_sha256,
                prior_state=advanced.tree.branch.run.base,
                proposed_delta={"iteration": advanced.iteration + 1},
                evidence_refs=(),
                validation_receipt_refs=(),
                commit_receipt_ref=None,
                artifact_refs=(),
                reducer_version="controller-test@1",
                resulting_state=advanced.tree.branch.run.base,
            )
            adapter.event_log.append(followup_event)
            followup = replace(
                advanced,
                iteration=advanced.iteration + 1,
                history_event_refs=(
                    *advanced.history_event_refs,
                    followup_event.event_id,
                ),
            )
            followup_record = adapter.save_checkpoint(followup)
            followup_payload = repository.load_json(followup_record)

            self.assertIsNone(followup_payload["stage_exit_proof"])
            self.assertEqual(
                followup_payload["stage_exit_anchor_ref"],
                {
                    "project_id": anchor_record.project_id,
                    "relative_path": anchor_record.relative_path,
                    "sha256": anchor_record.sha256,
                    "media_type": anchor_record.media_type,
                },
            )
            self.assertEqual(
                followup_payload["previous_checkpoint_ref"],
                followup_payload["stage_exit_anchor_ref"],
            )
            self.assertEqual(
                followup_payload["previous_checkpoint_digest"],
                advanced.checkpoint_digest,
            )
            self.assertEqual(
                adapter.load_latest_checkpoint().checkpoint,
                followup,
            )

            invalid_payload = {
                **followup_payload,
                "stage_exit_anchor_ref": None,
                "previous_checkpoint_ref": None,
                "previous_checkpoint_digest": None,
            }
            invalid_record = repository.put_json(
                run=advanced.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=advanced.tree.branch.run.run_id,
                    branch_id=advanced.tree.branch.branch_id,
                ),
                record_kind="proofless-high-stage-checkpoint",
                payload=invalid_payload,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "high-stage checkpoint has no stage-exit proof anchor",
            ):
                adapter.load_checkpoint(invalid_record)
            with self.assertRaisesRegex(
                DesignControllerError,
                "high-stage checkpoint has no stage-exit proof anchor",
            ):
                adapter.load_latest_checkpoint()

            wrong_digest_payload = {
                **followup_payload,
                "previous_checkpoint_digest": "0" * 64,
            }
            wrong_digest_record = repository.put_json(
                run=advanced.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=advanced.tree.branch.run.run_id,
                    branch_id=advanced.tree.branch.branch_id,
                ),
                record_kind="wrong-previous-checkpoint-digest",
                payload=wrong_digest_payload,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "previous checkpoint digest disagrees",
            ):
                adapter.load_checkpoint(wrong_digest_record)

            anchor_payload = repository.load_json(anchor_record)
            proof_schema_payload = {
                key: value
                for key, value in anchor_payload.items()
                if key
                not in {
                    "stage_exit_anchor_ref",
                    "previous_checkpoint_ref",
                    "previous_checkpoint_digest",
                }
            }
            proof_schema_payload["schema"] = (
                adapter.PROOF_CHECKPOINT_SCHEMA
            )
            proof_schema_record = repository.put_json(
                run=advanced.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=advanced.tree.branch.run.run_id,
                    branch_id=advanced.tree.branch.branch_id,
                ),
                record_kind="read-only-proof-checkpoint-v2",
                payload=proof_schema_payload,
            )
            self.assertEqual(
                adapter.load_checkpoint(proof_schema_record).checkpoint,
                advanced,
            )

    def test_true_legacy_stage_exit_proof_loads_read_only_and_tamper_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-true-legacy-proof",
                    project_id="controller-true-legacy-proof",
                )
            )
            head_before = repository.read_head()
            legacy_record, legacy_payload, legacy_bundle = (
                _persist_true_legacy_stage_exit_checkpoint(
                    repository,
                    adapter,
                    previous,
                    advanced,
                    bundle,
                )
            )

            resumed = adapter.load_checkpoint(legacy_record)
            self.assertEqual(resumed.checkpoint, advanced)
            self.assertEqual(
                legacy_payload["stage_exit_proof"]["schema"],
                adapter.LEGACY_STAGE_EXIT_PROOF_SCHEMA,
            )
            self.assertEqual(
                legacy_bundle["schema"],
                StageExitArchiveBundle.LEGACY_SCHEMA,
            )
            legacy_binding_ref = _project_record_from_payload(
                legacy_bundle["profile_binding_ref"]
            )
            legacy_baseline_ref = _project_record_from_payload(
                legacy_bundle["baseline_coverage_ref"]
            )
            legacy_binding = StageRequirementProfileBinding.from_dict(
                repository.load_json(legacy_binding_ref)
            )
            legacy_baseline = StageBaselineCoverageReceipt.from_dict(
                repository.load_json(legacy_baseline_ref)
            )
            self.assertTrue(legacy_binding.is_legacy_read_only)
            self.assertIsNone(
                legacy_baseline.stage_subject_inventory_digest
            )

            tampered_proof_payload = copy.deepcopy(legacy_payload)
            tampered_proof_payload["stage_exit_proof"]["proof_digest"] = (
                "0" * 64
            )
            tampered_proof_record = repository.put_json(
                run=advanced.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=advanced.tree.branch.run.run_id,
                    branch_id=advanced.tree.branch.branch_id,
                ),
                record_kind="tampered-legacy-stage-exit-proof",
                payload=tampered_proof_payload,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "proof digest|digest changed",
            ):
                adapter.load_checkpoint(tampered_proof_record)

            tampered_bundle_payload = copy.deepcopy(legacy_payload)
            tampered_bundle_payload["stage_exit_proof"]["bundle"][
                "bundle_digest"
            ] = "0" * 64
            tampered_bundle_record = repository.put_json(
                run=advanced.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=advanced.tree.branch.run.run_id,
                    branch_id=advanced.tree.branch.branch_id,
                ),
                record_kind="tampered-legacy-stage-exit-bundle",
                payload=tampered_bundle_payload,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "bundle digest|digest changed",
            ):
                adapter.load_checkpoint(tampered_bundle_record)
            self.assertEqual(repository.read_head(), head_before)

    def test_checkpoint_v2_with_current_proof_is_read_only_for_followup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-v2-current-proof",
                    project_id="controller-v2-current-proof",
                )
            )
            proof = adapter._admit_stage_exit(  # noqa: SLF001
                previous,
                advanced,
                bundle,
            )
            payload = _proof_checkpoint_payload(
                adapter,
                advanced,
                checkpoint_schema=adapter.PROOF_CHECKPOINT_SCHEMA,
                proof=proof,
            )
            record = repository.put_json(
                run=advanced.tree.branch.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=advanced.tree.branch.run.run_id,
                    branch_id=advanced.tree.branch.branch_id,
                ),
                record_kind="checkpoint-v2-with-current-proof",
                payload=payload,
            )
            self.assertEqual(
                adapter.load_checkpoint(record).checkpoint,
                advanced,
            )

            prior_event = adapter.event_log.records()[-1]
            followup_event = DesignEvent.create(
                sequence=2,
                project_id=advanced.tree.branch.run.project_id,
                event_type="controller.v2-read-only-followup",
                decision=EventDecision.ACCEPTED,
                actor_id="controller",
                authority_id="controller",
                prior_event_sha256=prior_event.event_sha256,
                prior_state=advanced.tree.branch.run.base,
                proposed_delta={"iteration": advanced.iteration + 1},
                evidence_refs=(),
                validation_receipt_refs=(),
                commit_receipt_ref=None,
                artifact_refs=(),
                reducer_version="controller-test@1",
                resulting_state=advanced.tree.branch.run.base,
            )
            adapter.event_log.append(followup_event)
            followup = replace(
                advanced,
                iteration=advanced.iteration + 1,
                history_event_refs=(
                    *advanced.history_event_refs,
                    followup_event.event_id,
                ),
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "read-only|current stage-exit proof|proof anchor",
            ):
                adapter.save_checkpoint(followup)

    def test_true_legacy_stage_exit_proof_cannot_anchor_same_epoch_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-legacy-same-epoch",
                    project_id="controller-legacy-same-epoch",
                )
            )
            legacy_record, _payload, _legacy_bundle = (
                _persist_true_legacy_stage_exit_checkpoint(
                    repository,
                    adapter,
                    previous,
                    advanced,
                    bundle,
                )
            )
            self.assertEqual(
                adapter.load_checkpoint(legacy_record).checkpoint,
                advanced,
            )

            prior_event = adapter.event_log.records()[-1]
            followup_event = DesignEvent.create(
                sequence=2,
                project_id=advanced.tree.branch.run.project_id,
                event_type="controller.legacy-proof-followup",
                decision=EventDecision.ACCEPTED,
                actor_id="controller",
                authority_id="controller",
                prior_event_sha256=prior_event.event_sha256,
                prior_state=advanced.tree.branch.run.base,
                proposed_delta={"iteration": advanced.iteration + 1},
                evidence_refs=(),
                validation_receipt_refs=(),
                commit_receipt_ref=None,
                artifact_refs=(),
                reducer_version="controller-test@1",
                resulting_state=advanced.tree.branch.run.base,
            )
            adapter.event_log.append(followup_event)
            followup = replace(
                advanced,
                iteration=advanced.iteration + 1,
                history_event_refs=(
                    *advanced.history_event_refs,
                    followup_event.event_id,
                ),
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "read-only|current stage-exit proof|proof anchor",
            ):
                adapter.save_checkpoint(followup)

    def test_true_legacy_stage_exit_proof_cannot_anchor_next_epoch_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-legacy-next-epoch",
                    project_id="controller-legacy-next-epoch",
                )
            )
            legacy_record, _payload, _legacy_bundle = (
                _persist_true_legacy_stage_exit_checkpoint(
                    repository,
                    adapter,
                    previous,
                    advanced,
                    bundle,
                )
            )
            prior_event = adapter.event_log.records()[-1]
            phase_event = DesignEvent.create(
                sequence=2,
                project_id=advanced.tree.branch.run.project_id,
                event_type="controller.legacy-proof-next-phase",
                decision=EventDecision.ACCEPTED,
                actor_id="controller",
                authority_id="controller",
                prior_event_sha256=prior_event.event_sha256,
                prior_state=advanced.tree.branch.run.base,
                proposed_delta={
                    "from_phase": advanced.maturity.phase.value,
                    "to_phase": DesignPhase.CANDIDATE_COORDINATION.value,
                },
                evidence_refs=(),
                validation_receipt_refs=("validation://stage-exit/closure",),
                commit_receipt_ref=None,
                artifact_refs=(),
                reducer_version="controller-test@1",
                resulting_state=advanced.tree.branch.run.base,
            )
            next_checkpoint = _advance_checkpoint(
                advanced,
                event_ref=phase_event.event_id,
                next_phase=DesignPhase.CANDIDATE_COORDINATION,
            )
            next_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=next_checkpoint.tree.branch,
            )
            next_adapter.event_log.append(phase_event)
            legacy_predecessor_bundle = replace(
                bundle,
                predecessor_checkpoint_ref=legacy_record,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "read-only|current stage-exit proof|authority anchor",
            ):
                next_adapter.save_checkpoint(
                    next_checkpoint,
                    stage_exit_bundle=legacy_predecessor_bundle,
                )

    def test_first_checkpoint_cannot_start_after_schematic_design(
        self,
    ) -> None:
        template = _phase_ready_checkpoint()
        advanced = _advance_checkpoint(
            template,
            event_ref="design-event:unarchived-first-advance",
        )

        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-first-advanced"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            repository = FilesystemProjectRepository.initialize(
                Path(temporary) / "controller-first-advanced",
                project_id="controller-first-advanced",
                initial_state=canonical_state_to_dict(sealed),
            )
            run = repository.create_run("run-001")
            checkpoint = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                template=advanced,
            )
            adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint.tree.branch,
            )
            adapter.event_log.append(initial_event)
            with self.assertRaisesRegex(
                DesignControllerError,
                "exceeds bootstrap maturity",
            ):
                adapter.save_checkpoint(checkpoint)

    def test_legacy_checkpoint_cannot_bypass_new_stage_exit_proof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, adapter, previous, advanced, bundle = (
                _prepare_stage_exit(
                    Path(temporary) / "controller-stage-exit-legacy",
                    project_id="controller-stage-exit-legacy",
                    legacy_previous=True,
                )
            )
            legacy_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=previous.tree.branch,
            )
            self.assertEqual(
                legacy_adapter.load_latest_checkpoint().checkpoint,
                previous,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "requires a P036 stage-exit bundle",
            ):
                adapter.save_checkpoint(advanced)
            record = adapter.save_checkpoint(
                advanced,
                stage_exit_bundle=bundle,
            )
            payload = repository.load_json(record)
            self.assertEqual(payload["schema"], adapter.CHECKPOINT_SCHEMA)
            self.assertIsNotNone(payload["stage_exit_proof"])

    def test_restart_selects_only_the_current_branch_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-epoch-resume"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-epoch-resume"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-epoch-resume",
                initial_state=canonical_state_to_dict(sealed),
            )
            run = repository.create_run("run-001")
            head_before = repository.read_head()

            epoch_three = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=3,
            )
            epoch_three_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=epoch_three.tree.branch,
            )
            epoch_three_adapter.event_log.append(initial_event)
            epoch_three_record = epoch_three_adapter.save_checkpoint(
                epoch_three
            )

            epoch_four = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=4,
            )
            epoch_four_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=epoch_four.tree.branch,
            )
            epoch_four_record = epoch_four_adapter.save_checkpoint(
                epoch_four
            )

            del epoch_three_adapter
            del epoch_four_adapter
            del repository
            reopened = FilesystemProjectRepository.open(root)
            durable_run = reopened.load_run("run-001")
            current_adapter = ProjectControllerArchiveAdapter(
                reopened,
                branch=BranchRef(
                    run=durable_run,
                    branch_id=epoch_four.tree.branch.branch_id,
                    epoch=4,
                ),
            )

            historical_payload = reopened.load_json(epoch_three_record)
            historical_payload["run_base"] = {
                **historical_payload["run_base"],
                "state_sha256": "0" * 64,
            }
            reopened.put_json(
                run=durable_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=durable_run.run_id,
                    branch_id=epoch_four.tree.branch.branch_id,
                ),
                record_kind="design-controller-corrupt-historical-epoch",
                payload=historical_payload,
            )

            resumed = current_adapter.load_latest_checkpoint()

            self.assertNotEqual(epoch_three_record, epoch_four_record)
            self.assertEqual(resumed.record_ref, epoch_four_record)
            self.assertEqual(resumed.checkpoint, epoch_four)
            self.assertEqual(resumed.event_chain, (initial_event,))
            self.assertEqual(reopened.read_head(), head_before)

            tampered_payload = reopened.load_json(epoch_four_record)
            tampered_payload["event_head_sha256"] = "0" * 64
            reopened.put_json(
                run=durable_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=durable_run.run_id,
                    branch_id=epoch_four.tree.branch.branch_id,
                ),
                record_kind="design-controller-tampered-current-epoch",
                payload=tampered_payload,
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "event binding disagrees",
            ):
                current_adapter.load_latest_checkpoint()
            self.assertEqual(reopened.read_head(), head_before)

    def test_restart_fails_on_same_epoch_latest_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-epoch-ambiguity"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-epoch-ambiguity"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-epoch-ambiguity",
                initial_state=canonical_state_to_dict(sealed),
            )
            run = repository.create_run("run-001")
            checkpoint = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=4,
            )
            adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint.tree.branch,
            )
            adapter.event_log.append(initial_event)
            record = adapter.save_checkpoint(checkpoint)
            duplicate_payload = repository.load_json(record)
            repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=run.run_id,
                    branch_id=checkpoint.tree.branch.branch_id,
                ),
                record_kind="design-controller-duplicate-current-epoch",
                payload=duplicate_payload,
            )

            with self.assertRaisesRegex(
                DesignControllerError,
                "ambiguous latest checkpoint lineage",
            ):
                adapter.load_latest_checkpoint()

    def test_checkpoint_requires_exact_event_prefix_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-guard"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-guard"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-guard",
                initial_state=canonical_state_to_dict(sealed),
            )
            run = repository.create_run("run-001")
            checkpoint = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
            )
            adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint.tree.branch,
            )
            adapter.event_log.append(initial_event)
            record = adapter.save_checkpoint(checkpoint)

            with self.assertRaisesRegex(
                DesignControllerError,
                "checkpoint history does not extend the exact predecessor",
            ):
                adapter.save_checkpoint(
                    replace(
                        checkpoint,
                        history_event_refs=("design-event:invented",),
                    )
                )

            other_branch = BranchRef(
                run=run,
                branch_id="other-option",
                epoch=0,
            )
            other_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=other_branch,
            )
            self.assertEqual(other_adapter.event_log.records(), ())
            with self.assertRaisesRegex(
                DesignControllerError,
                "another branch",
            ):
                other_adapter.save_checkpoint(checkpoint)
            with self.assertRaisesRegex(
                DesignControllerError,
                "another branch",
            ):
                other_adapter.load_checkpoint(record)

            other_run = repository.create_run("run-002")
            other_run_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=BranchRef(
                    run=other_run,
                    branch_id=checkpoint.tree.branch.branch_id,
                    epoch=checkpoint.tree.branch.epoch,
                ),
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "another branch",
            ):
                other_run_adapter.load_checkpoint(record)

    def test_latest_checkpoint_filters_historical_branch_epochs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-epoch-resume"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-epoch-resume"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-epoch-resume",
                initial_state=canonical_state_to_dict(sealed),
            )
            head_before = repository.read_head()
            run = repository.create_run("run-001")

            checkpoint_3 = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=3,
            )
            adapter_3 = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint_3.tree.branch,
            )
            adapter_3.event_log.append(initial_event)
            record_3 = adapter_3.save_checkpoint(checkpoint_3)
            historical_payload = repository.load_json(record_3)
            historical_payload["branch_epoch"] = 2
            historical_payload["legacy_extension"] = (
                "retained historical checkpoint field"
            )
            repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=run.run_id,
                    branch_id=checkpoint_3.tree.branch.branch_id,
                ),
                record_kind="design-controller-historical-legacy",
                payload=historical_payload,
            )

            checkpoint_4 = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=4,
            )
            adapter_4 = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint_4.tree.branch,
            )
            record_4 = adapter_4.save_checkpoint(checkpoint_4)
            self.assertNotEqual(record_3, record_4)
            with self.assertRaisesRegex(
                DesignControllerError,
                "record identity changed",
            ):
                adapter_4.load_checkpoint(record_3)

            del adapter_3
            del adapter_4
            del repository
            reopened = FilesystemProjectRepository.open(root)
            durable_run = reopened.load_run("run-001")
            resumed_4 = ProjectControllerArchiveAdapter(
                reopened,
                branch=BranchRef(
                    run=durable_run,
                    branch_id=checkpoint_4.tree.branch.branch_id,
                    epoch=4,
                ),
            ).load_latest_checkpoint()
            resumed_3 = ProjectControllerArchiveAdapter(
                reopened,
                branch=BranchRef(
                    run=durable_run,
                    branch_id=checkpoint_3.tree.branch.branch_id,
                    epoch=3,
                ),
            ).load_latest_checkpoint()

            self.assertEqual(record_4, resumed_4.record_ref)
            self.assertEqual(checkpoint_4, resumed_4.checkpoint)
            self.assertEqual(record_3, resumed_3.record_ref)
            self.assertEqual(checkpoint_3, resumed_3.checkpoint)
            self.assertEqual(head_before, reopened.read_head())

if __name__ == "__main__":
    unittest.main()
