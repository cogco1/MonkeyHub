from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    RelationTopologyBaselineSource,
    StageBaselineCoverageReceipt,
    StageBaselineLevel,
    StageBaselineSourceSet,
    StageBaselineStatus,
)
from archflow.control.component_functions import compile_component_function_ledger
from archflow.control.function_relations import FunctionRelationRequirementSet
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.stage_artifacts import (
    ArtifactShaBinding,
    RecordDigestBinding,
)
from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureStatus,
)
from archflow.control.stage_control_sources import ComponentFunctionBaselineSource
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.relations.contracts import ArchitecturalRelationKind
from archflow.relations.realization import (
    RelationEndpointObjectBinding,
    RelationEndpointPairing,
    RelationRealizationManifest,
    RelationRealizationPurpose,
    RelationVerificationBinding,
)
from archflow.runtime.geometry_compiler import (
    CompiledGeometryObject,
    CompiledGeometryProgram,
)
from archflow.runtime.stage_artifact_chain import (
    StageArtifactChainError,
    compile_relation_realization_baseline_source,
    compile_stage_artifact_claim,
)
from archflow.state.design_maturity import (
    DesignPhase,
    DeliverableRole,
    PhaseGateReceipt,
    StageEntryProof,
)
from archflow.state.model import ArtifactRef
from archflow.state.geometry_program import (
    AffineTransform,
    CoordinateFrame,
    GeometryOperation,
    GeometryOperationKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    SemanticBinding,
)
from archflow.validation.cad_readback import (
    CadObjectReadback,
    CadReadbackSnapshot,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus

from tests.test_relation_authoring import (
    _bound_context_and_inventory,
    _proposal,
)
from archflow.control.check_requirements import (
    relation_authoring_stage_requirements,
)
from archflow.control.relation_promotion import promote_verified_relation_graph
from archflow.relations.authoring import compile_relation_authoring


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _topology_source() -> RelationTopologyBaselineSource:
    context, inventory = _bound_context_and_inventory()
    compilation = compile_relation_authoring(context, _proposal(context))
    requirements = relation_authoring_stage_requirements(
        context,
        compilation,
        inventory,
    )
    verification = next(
        item
        for item in requirements
        if item.requirement_id.startswith("relation-verification-")
    )
    receipt = CheckReceiptEnvelope(
        check_id=verification.requirement_id,
        checker_id=verification.checker_id,
        checker_version="1.0.0",
        branch=context.branch,
        scope_digest=context.scope_digest,
        subject_refs=verification.denominator_refs,
        subject_digest=inventory.stage_subject_digest,
        status=CheckStatus.PASS,
        source_refs=verification.required_source_refs,
        authority_refs=verification.required_authority_refs,
        coverage_denominator=verification.denominator_refs,
        covered_refs=verification.denominator_refs,
    )
    promotion = promote_verified_relation_graph(
        context,
        compilation,
        inventory,
        (receipt,),
    )
    return RelationTopologyBaselineSource(
        context=context,
        compilation=compilation,
        promotion=promotion,
    )


def _program(source: RelationTopologyBaselineSource) -> CompiledGeometryProgram:
    graph = source.promotion.graph
    component_ids = tuple(
        item.node_ref.split(":", 1)[1] for item in graph.nodes
    )
    operations = tuple(
        GeometryOperation(
            op_id=f"op-{component_id}",
            kind=GeometryOperationKind.SOLID,
            output_object_ids=(f"object-{component_id}",),
            input_object_ids=(),
            frame_id="world",
            parameters=(),
            semantic_binding_ids=(f"binding-{component_id}",),
        )
        for component_id in component_ids
    )
    proposal = GeometryProgramProposal(
        proposal_id="stage3-relation-program",
        project_id=graph.branch.run.project_id,
        run_id=graph.branch.run.run_id,
        base=graph.branch.run.base,
        design_state_digest=graph.state_digest,
        predecessor_program_digest=None,
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=("evidence:world-frame",),
            ),
        ),
        assets=(),
        semantic_bindings=tuple(
            SemanticBinding(
                binding_id=f"binding-{component_id}",
                component_id=component_id,
                object_ids=(f"object-{component_id}",),
                commitment_refs=(),
                evidence_refs=(f"evidence:{component_id}",),
            )
            for component_id in component_ids
        ),
        operations=operations,
        assemblies=(),
    )
    return CompiledGeometryProgram(
        proposal=proposal,
        operation_order=tuple(item.op_id for item in operations),
        frame_digests=(("world", _sha("world")),),
        component_digests=(),
        semantic_binding_digests=tuple(
            (f"binding-{item}", _sha(f"binding-{item}"))
            for item in component_ids
        ),
        objects=tuple(
            CompiledGeometryObject(
                object_id=f"object-{component_id}",
                producer_op_id=f"op-{component_id}",
                object_digest=_sha(f"object-{component_id}"),
            )
            for component_id in component_ids
        ),
        asset_substitutions=(),
    )


def _realization_inputs(source: RelationTopologyBaselineSource):  # type: ignore[no-untyped-def]
    graph = source.promotion.graph
    program = _program(source)
    snapshot = CadReadbackSnapshot(
        project_id=graph.branch.run.project_id,
        branch=graph.branch,
        stage_id=graph.stage_id,
        profile_digest=_sha("profile"),
        program_digest=program.program_digest,
        length_unit="meter",
        up_axis=None,
        declared_layer_refs=(),
        operation_refs=tuple(
            f"cad-operation:{item.producer_op_id}" for item in program.objects
        ),
        objects=tuple(
            CadObjectReadback(
                object_ref=f"cad-object:{item.object_id}",
                operation_ref=f"cad-operation:{item.producer_op_id}",
                layer_ref=None,
                attributes=(),
            )
            for item in program.objects
        ),
    )
    object_by_component = {
        item.object_id.removeprefix("object-"): item
        for item in program.objects
    }
    bindings = []
    by_relation = {}
    for relation in graph.relations:
        relation_bindings = []
        for participant in relation.participants:
            component_id = participant.node_ref.split(":", 1)[1]
            compiled_object = object_by_component[component_id]
            binding = RelationEndpointObjectBinding(
                binding_id=f"{relation.relation_id}-{participant.role}",
                relation_ref=relation.ref,
                participant_digest=participant.participant_digest,
                role=participant.role,
                node_ref=participant.node_ref,
                ordinal=participant.ordinal,
                semantic_binding_id=f"binding-{component_id}",
                program_object_id=compiled_object.object_id,
                program_object_digest=compiled_object.object_digest,
                producer_operation_id=compiled_object.producer_op_id,
                readback_object_ref=f"cad-object:{compiled_object.object_id}",
                readback_operation_ref=(
                    f"cad-operation:{compiled_object.producer_op_id}"
                ),
            )
            bindings.append(binding)
            relation_bindings.append(binding)
        by_relation[relation.ref] = tuple(relation_bindings)

    unsigned = tuple(
        RelationEndpointPairing(
            pairing_id=relation.relation_id,
            relation_ref=relation.ref,
            first_binding_ref=by_relation[relation.ref][0].ref,
            second_binding_ref=by_relation[relation.ref][1].ref,
        )
        for relation in graph.relations
    )
    receipts = []
    pairings = []
    purpose_by_kind = {
        ArchitecturalRelationKind.INTERFACE: (
            RelationRealizationPurpose.CONTACT_INTERFACE
        ),
        ArchitecturalRelationKind.INTERSECTS: (
            RelationRealizationPurpose.CONTACT_INTERFACE
        ),
        ArchitecturalRelationKind.HOST: RelationRealizationPurpose.HOST_INTERFACE,
        ArchitecturalRelationKind.HOSTS_VOID: (
            RelationRealizationPurpose.HOST_INTERFACE
        ),
        ArchitecturalRelationKind.FILLS_VOID: (
            RelationRealizationPurpose.HOST_INTERFACE
        ),
        ArchitecturalRelationKind.SUPPORT: (
            RelationRealizationPurpose.SUPPORT_CHAIN
        ),
        ArchitecturalRelationKind.LOAD_TRANSFER: (
            RelationRealizationPurpose.LOAD_PATH
        ),
        ArchitecturalRelationKind.ACCESS: (
            RelationRealizationPurpose.WALKING_PATH
        ),
        ArchitecturalRelationKind.ALLOWS_PASSAGE: (
            RelationRealizationPurpose.WALKING_PATH
        ),
    }
    for relation, pairing in zip(graph.relations, unsigned, strict=True):
        subject_refs = tuple(
            sorted(
                {
                    relation.ref,
                    pairing.ref,
                    pairing.first_binding_ref,
                    pairing.second_binding_ref,
                }
            )
        )
        receipt = CheckReceiptEnvelope(
            check_id=f"verify-{relation.relation_id}",
            checker_id="support-narrow-phase-checker",
            checker_version="1.0.0",
            branch=graph.branch,
            scope_digest=graph.scope_digest,
            subject_refs=subject_refs,
            subject_digest=graph.stage_subject_digest,
            status=CheckStatus.PASS,
            coverage_denominator=subject_refs,
            covered_refs=subject_refs,
        )
        receipts.append(receipt)
        pairings.append(
            replace(
                pairing,
                verification=RelationVerificationBinding(
                    purpose=purpose_by_kind.get(
                        relation.kind,
                        RelationRealizationPurpose.GENERIC_PAIR,
                    ),
                    checker_id=receipt.checker_id,
                    receipt_digest=receipt.receipt_digest,
                    subject_refs=receipt.subject_refs,
                ),
            )
        )
    manifest = RelationRealizationManifest(
        manifest_id="stage3-relations",
        branch=graph.branch,
        stage_id=graph.stage_id,
        scope_digest=graph.scope_digest,
        relation_graph_digest=graph.graph_digest,
        program_digest=program.program_digest,
        readback_digest=snapshot.snapshot_digest,
        stage_subject_digest=graph.stage_subject_digest,
        endpoint_bindings=tuple(bindings),
        pairings=tuple(pairings),
        paths=(),
    )
    return program, snapshot, manifest, tuple(receipts)


def test_promoted_topology_compiles_one_exact_realization_source() -> None:
    topology = _topology_source()
    program, snapshot, manifest, receipts = _realization_inputs(topology)

    source = compile_relation_realization_baseline_source(
        topology,
        program=program,
        readback=snapshot,
        manifest=manifest,
        verification_receipts=receipts,
    )

    assert source.graph == topology.promotion.graph
    assert source.program == program
    assert source.readback == snapshot
    assert source.manifest == manifest
    assert source.verification_receipts == tuple(
        sorted(receipts, key=lambda item: item.receipt_digest)
    )


def test_realization_compiler_rejects_stale_cross_branch_and_missing_receipts() -> None:
    topology = _topology_source()
    program, snapshot, manifest, receipts = _realization_inputs(topology)

    stale_snapshot = replace(snapshot, program_digest=_sha("stale-program"))
    with pytest.raises(StageArtifactChainError, match="passing realization"):
        compile_relation_realization_baseline_source(
            topology,
            program=program,
            readback=stale_snapshot,
            manifest=manifest,
            verification_receipts=receipts,
        )

    cross_branch = BranchRef(
        run=RunRef(
            project_id=snapshot.branch.run.project_id,
            run_id=snapshot.branch.run.run_id,
            base=ProjectVersionRef(
                snapshot.branch.run.project_id,
                snapshot.branch.run.base.version,
                snapshot.branch.run.base.state_sha256,
            ),
        ),
        branch_id="other-candidate",
        epoch=snapshot.branch.epoch,
    )
    with pytest.raises(StageArtifactChainError, match="passing realization"):
        compile_relation_realization_baseline_source(
            topology,
            program=program,
            readback=replace(snapshot, branch=cross_branch),
            manifest=manifest,
            verification_receipts=receipts,
        )

    with pytest.raises(StageArtifactChainError, match="passing realization"):
        compile_relation_realization_baseline_source(
            topology,
            program=program,
            readback=snapshot,
            manifest=manifest,
            verification_receipts=receipts[:-1],
        )


def _record(branch: BranchRef, name: str) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/"
            f"records/{name}.json"
        ),
        sha256=_sha(f"record-{name}"),
    )


def _binding(branch: BranchRef, name: str, digest: str) -> RecordDigestBinding:
    return RecordDigestBinding(_record(branch, name), digest)


def test_artifact_compiler_rejects_a_missing_relation_denominator() -> None:
    _context, base_inventory = _bound_context_and_inventory()
    predecessor = base_inventory.branch
    successor = replace(predecessor, epoch=predecessor.epoch + 1)
    inventory = replace(
        base_inventory,
        branch=successor,
        stage_id=DesignPhase.DESIGN_DEVELOPMENT.value,
        component_proposal_ref=_record(successor, "component-proposal"),
        component_index_ref=_record(successor, "component-index"),
    )
    ledger = compile_component_function_ledger(
        ledger_id="missing-denominator-ledger",
        inventory=inventory,
        contracts=(),
    )
    requirements = FunctionRelationRequirementSet(
        set_id="missing-denominator-requirements",
        branch=successor,
        stage_id=inventory.stage_id,
        subject_inventory_digest=inventory.inventory_digest,
        function_ledger_ref=ledger.ledger_ref,
        function_ledger_digest=ledger.ledger_digest,
        requirements=(),
    )
    ledger_ref = _record(successor, "function-ledger")
    requirements_ref = _record(successor, "function-requirements")
    sources = StageBaselineSourceSet(
        component_functions=(
            ComponentFunctionBaselineSource(
                ledger_ref=ledger_ref,
                ledger=ledger,
                relation_requirements_ref=requirements_ref,
                relation_requirements=requirements,
            ),
        ),
    )
    required_roles = tuple(
        sorted(BASELINE_LEVEL_ROLES[StageBaselineLevel.DEVELOPED])
    )
    coverage = StageBaselineCoverageReceipt(
        profile_id="missing-denominator-profile",
        profile_digest=_sha("profile"),
        branch=successor,
        stage_id=inventory.stage_id,
        level=StageBaselineLevel.DEVELOPED,
        stage_subject_inventory_digest=inventory.inventory_digest,
        required_roles=required_roles,
        coverage=(),
        missing_roles=required_roles,
        status=StageBaselineStatus.OPEN,
    )
    closure = CompositeStageClosureReceipt(
        profile_id=coverage.profile_id,
        profile_digest=coverage.profile_digest,
        stage_id=inventory.stage_id,
        branch=successor,
        stage_subject_ref=inventory.stage_subject_ref,
        subject_digest=inventory.stage_subject_digest,
        check_receipt_digests=(),
        findings=(),
        status=StageClosureStatus.SATISFIED,
    )
    gate = PhaseGateReceipt(
        receipt_id="schematic-exit",
        request_id="enter-development",
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
        stage_exit_checkpoint_ref=_record(successor, "stage-exit-checkpoint"),
        stage_exit_proof_digest=_sha("stage-exit-proof"),
        predecessor_checkpoint_digest=_sha("predecessor-checkpoint"),
        successor_checkpoint_digest=_sha("successor-checkpoint"),
        successor_branch=successor,
    )
    artifact_digest = _sha("artifact")
    artifact = ArtifactShaBinding(
        ArtifactRef(
            artifact_id="candidate",
            uri=(
                f"project://{successor.run.project_id}/runs/"
                f"{successor.run.run_id}/branches/{successor.branch_id}/"
                "artifacts/candidate.3dm"
            ),
            media_type="model/vnd.rhino",
            sha256=artifact_digest,
        ),
        artifact_digest,
    )
    stage_profile = StageRequirementProfile(
        profile_id="missing-denominator-profile",
        typology_id="generic",
        stage_id=inventory.stage_id,
        branch=successor,
        predecessor_state_digest=inventory.stage_subject_digest,
        scope_digest=_sha("scope"),
        stage_subject_ref=inventory.stage_subject_ref,
        requirements=(
            StageCheckRequirement(
                requirement_id="project-stage-check",
                checker_id="project-stage-checker",
                target_kind=RequirementTargetKind.STAGE,
                basis_mode=RequirementBasisMode.UNIVERSAL,
                denominator_refs=(inventory.stage_subject_ref,),
            ),
        ),
    )

    with pytest.raises(StageArtifactChainError, match="topology denominator"):
        compile_stage_artifact_claim(
            claim_id="missing-relation-denominator",
            stage_entry_proof=proof,
            stage_entry_proof_record=_binding(
                successor,
                "stage-entry-proof",
                proof.proof_digest,
            ),
            artifact=artifact,
            geometry_program_record=_binding(
                successor,
                "geometry-program",
                _sha("program"),
            ),
            component_index_record=_binding(
                successor,
                "component-index",
                inventory.component_index_digest,
            ),
            stage_subject_inventory_record=_binding(
                successor,
                "stage-subject-inventory",
                inventory.inventory_digest,
            ),
            stage_subject_inventory=inventory,
            function_ledger_record=RecordDigestBinding(
                ledger_ref,
                ledger.ledger_digest,
            ),
            function_ledger=ledger,
            function_requirement_record=RecordDigestBinding(
                requirements_ref,
                requirements.set_digest,
            ),
            function_requirements=requirements,
            stage_requirement_profile_record=_binding(
                successor,
                "stage-requirement-profile",
                stage_profile.profile_digest,
            ),
            stage_requirement_profile=stage_profile,
            stage_check_receipt_records=(),
            stage_check_receipts=(),
            baseline_sources_record=_binding(
                successor,
                "baseline-sources",
                sources.source_set_digest,
            ),
            baseline_sources=sources,
            baseline_coverage_record=_binding(
                successor,
                "baseline-coverage",
                coverage.receipt_digest,
            ),
            baseline_coverage=coverage,
            stage_closure_record=_binding(
                successor,
                "stage-closure",
                closure.receipt_digest,
            ),
            stage_closure=closure,
            cad_readback_record=_binding(
                successor,
                "cad-readback",
                _sha("readback"),
            ),
            topology_source_records=(),
            realization_source_records=(),
            realization_receipt_records=(),
        )
