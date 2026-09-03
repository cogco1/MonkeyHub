"""Pure Stage 3 realization and stage-artifact claim compilers.

This module joins already typed control evidence.  It performs no filesystem
I/O, chooses no project path, materializes no CAD object, and has no stage
acceptance or canonical-write authority.
"""

from __future__ import annotations

from archive.archflow.control.baseline import (
    RelationRealizationBaselineSource,
    RelationTopologyBaselineSource,
    StageBaselineCoverageReceipt,
    StageBaselineError,
    StageBaselineRole,
    StageBaselineSourceSet,
    StageBaselineStatus,
    baseline_level_for_design_phase,
    compile_stage_baseline_coverage,
    derive_stage_requirement_profile,
)
from archive.archflow.control.component_functions import (
    ComponentFunctionLedger,
    FunctionStatus,
)
from archive.archflow.control.function_relations import FunctionRelationRequirementSet
from archive.archflow.control.stage_artifacts import (
    ArtifactShaBinding,
    RecordDigestBinding,
    StageArtifactClaim,
    StageArtifactStatus,
    StageArtifactVerificationDenominator,
)
from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureError,
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.control.requirements import StageRequirementProfile
from archive.archflow.control.stage_subjects import StageSubjectInventory
from archflow.project.refs import BranchRef
from archive.archflow.relations.realization import RelationRealizationManifest
from archflow.compilers.geometry import CompiledGeometryProgram
from archflow.state.design_maturity import StageEntryProof, require_stage_entry_proof
from archive.archflow.validation.cad_readback import CadReadbackSnapshot
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.relation_realization import check_relation_realization


class StageArtifactChainError(ValueError):
    """Stage 3 inputs are incomplete, stale, or cross-lineage."""


def _exact_branch(value: BranchRef, expected: BranchRef, field: str) -> None:
    if value != expected:
        raise StageArtifactChainError(f"{field} crossed the exact branch or epoch")


def _sorted_bindings(
    values: tuple[RecordDigestBinding, ...],
    field: str,
) -> tuple[RecordDigestBinding, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(item, RecordDigestBinding) for item in values
    ):
        raise TypeError(f"{field} must contain RecordDigestBinding values")
    ordered = tuple(sorted(values, key=lambda item: item.record_ref.uri))
    uris = tuple(item.record_ref.uri for item in ordered)
    if len(uris) != len(set(uris)):
        raise StageArtifactChainError(f"{field} repeats a P036 record")
    return ordered


def _require_exact_bindings(
    bindings: tuple[RecordDigestBinding, ...],
    expected_digests: tuple[str, ...],
    field: str,
) -> tuple[RecordDigestBinding, ...]:
    ordered = _sorted_bindings(bindings, field)
    actual = tuple(sorted(item.content_digest for item in ordered))
    expected = tuple(sorted(expected_digests))
    if actual != expected:
        raise StageArtifactChainError(
            f"{field} differ from the exact typed denominator"
        )
    return ordered


def compile_relation_realization_baseline_source(
    topology_source: RelationTopologyBaselineSource,
    *,
    program: CompiledGeometryProgram,
    readback: CadReadbackSnapshot,
    manifest: RelationRealizationManifest,
    verification_receipts: tuple[CheckReceiptEnvelope, ...] = (),
) -> RelationRealizationBaselineSource:
    """Validate and bind one promoted Stage 2 graph to its Stage 3 realization."""

    if not isinstance(topology_source, RelationTopologyBaselineSource):
        raise TypeError("topology_source must be RelationTopologyBaselineSource")
    if not isinstance(program, CompiledGeometryProgram):
        raise TypeError("program must be CompiledGeometryProgram")
    if not isinstance(readback, CadReadbackSnapshot):
        raise TypeError("readback must be CadReadbackSnapshot")
    if not isinstance(manifest, RelationRealizationManifest):
        raise TypeError("manifest must be RelationRealizationManifest")
    if not isinstance(verification_receipts, tuple) or any(
        not isinstance(item, CheckReceiptEnvelope)
        for item in verification_receipts
    ):
        raise TypeError(
            "verification_receipts must contain CheckReceiptEnvelope values"
        )

    graph = topology_source.promotion.graph
    if graph.branch != topology_source.branch:
        raise StageArtifactChainError("promoted topology source changed branch")
    receipt = check_relation_realization(
        graph,
        manifest,
        program,
        readback,
        verification_receipts=verification_receipts,
    )
    if receipt.status is not CheckStatus.PASS:
        raise StageArtifactChainError(
            "promoted topology does not have an exact passing realization"
        )

    bound_receipt_digests = {
        owner.verification.receipt_digest
        for owner in (*manifest.pairings, *manifest.paths)
        if owner.verification is not None
    }
    supplied_receipt_digests = {
        item.receipt_digest for item in verification_receipts
    }
    if len(supplied_receipt_digests) != len(verification_receipts):
        raise StageArtifactChainError("realization repeats a verification receipt")
    if bound_receipt_digests != supplied_receipt_digests:
        raise StageArtifactChainError(
            "realization receipts differ from the manifest denominator"
        )

    return RelationRealizationBaselineSource(
        graph=graph,
        manifest=manifest,
        program=program,
        readback=readback,
        verification_receipts=verification_receipts,
    )


def compile_stage_artifact_claim(
    *,
    claim_id: str,
    stage_entry_proof: StageEntryProof,
    stage_entry_proof_record: RecordDigestBinding,
    artifact: ArtifactShaBinding,
    geometry_program_record: RecordDigestBinding,
    component_index_record: RecordDigestBinding,
    stage_subject_inventory_record: RecordDigestBinding,
    stage_subject_inventory: StageSubjectInventory,
    function_ledger_record: RecordDigestBinding,
    function_ledger: ComponentFunctionLedger,
    function_requirement_record: RecordDigestBinding,
    function_requirements: FunctionRelationRequirementSet,
    stage_requirement_profile_record: RecordDigestBinding,
    stage_requirement_profile: StageRequirementProfile,
    stage_check_receipt_records: tuple[RecordDigestBinding, ...],
    stage_check_receipts: tuple[CheckReceiptEnvelope, ...],
    baseline_sources_record: RecordDigestBinding,
    baseline_sources: StageBaselineSourceSet,
    baseline_coverage_record: RecordDigestBinding,
    baseline_coverage: StageBaselineCoverageReceipt,
    stage_closure_record: RecordDigestBinding,
    stage_closure: CompositeStageClosureReceipt,
    cad_readback_record: RecordDigestBinding,
    topology_source_records: tuple[RecordDigestBinding, ...],
    realization_source_records: tuple[RecordDigestBinding, ...],
    realization_receipt_records: tuple[RecordDigestBinding, ...],
    viewer_refs: tuple[str, ...] = (),
    diagnostic_refs: tuple[str, ...] = (),
) -> StageArtifactClaim:
    """Mechanically derive a verified claim from one exact Stage 3 denominator."""

    if not isinstance(stage_entry_proof, StageEntryProof):
        raise TypeError("stage_entry_proof must be StageEntryProof")
    for field, value, expected_type in (
        ("stage_entry_proof_record", stage_entry_proof_record, RecordDigestBinding),
        ("artifact", artifact, ArtifactShaBinding),
        ("geometry_program_record", geometry_program_record, RecordDigestBinding),
        ("component_index_record", component_index_record, RecordDigestBinding),
        (
            "stage_subject_inventory_record",
            stage_subject_inventory_record,
            RecordDigestBinding,
        ),
        (
            "stage_subject_inventory",
            stage_subject_inventory,
            StageSubjectInventory,
        ),
        ("function_ledger_record", function_ledger_record, RecordDigestBinding),
        ("function_ledger", function_ledger, ComponentFunctionLedger),
        (
            "function_requirement_record",
            function_requirement_record,
            RecordDigestBinding,
        ),
        (
            "function_requirements",
            function_requirements,
            FunctionRelationRequirementSet,
        ),
        (
            "stage_requirement_profile_record",
            stage_requirement_profile_record,
            RecordDigestBinding,
        ),
        (
            "stage_requirement_profile",
            stage_requirement_profile,
            StageRequirementProfile,
        ),
        ("baseline_sources_record", baseline_sources_record, RecordDigestBinding),
        ("baseline_sources", baseline_sources, StageBaselineSourceSet),
        (
            "baseline_coverage_record",
            baseline_coverage_record,
            RecordDigestBinding,
        ),
        ("baseline_coverage", baseline_coverage, StageBaselineCoverageReceipt),
        ("stage_closure_record", stage_closure_record, RecordDigestBinding),
        ("stage_closure", stage_closure, CompositeStageClosureReceipt),
        ("cad_readback_record", cad_readback_record, RecordDigestBinding),
    ):
        if not isinstance(value, expected_type):
            raise TypeError(f"{field} must be {expected_type.__name__}")
    if not isinstance(stage_check_receipts, tuple) or any(
        not isinstance(item, CheckReceiptEnvelope)
        for item in stage_check_receipts
    ):
        raise TypeError(
            "stage_check_receipts must contain CheckReceiptEnvelope values"
        )

    branch = stage_entry_proof.successor_branch
    stage_id = stage_entry_proof.phase_gate.to_phase.value
    require_stage_entry_proof(
        stage_entry_proof,
        successor_branch=branch,
        from_phase=stage_entry_proof.phase_gate.from_phase,
        to_phase=stage_entry_proof.phase_gate.to_phase,
    )

    if baseline_sources.is_legacy_read_only:
        raise StageArtifactChainError(
            "legacy baseline source sets are replay-only and cannot verify artifacts"
        )
    if baseline_sources.relation_inheritance:
        raise StageArtifactChainError(
            "Stage 3 source sets cannot consume coordinated-stage inheritance"
        )
    if not baseline_sources.relation_topology:
        raise StageArtifactChainError("Stage 3 is missing its topology denominator")
    if not baseline_sources.relation_realization:
        raise StageArtifactChainError("Stage 3 is missing its realization denominator")

    _exact_branch(function_ledger.branch, branch, "function ledger")
    _exact_branch(
        stage_subject_inventory.branch,
        branch,
        "stage subject inventory",
    )
    _exact_branch(function_requirements.branch, branch, "function requirements")
    _exact_branch(
        stage_requirement_profile.branch,
        branch,
        "stage requirement profile",
    )
    _exact_branch(baseline_coverage.branch, branch, "baseline coverage")
    _exact_branch(stage_closure.branch, branch, "stage closure")
    if any(source.branch != branch for source in baseline_sources.relation_topology):
        raise StageArtifactChainError("topology sources crossed the exact branch")
    if any(source.branch != branch for source in baseline_sources.relation_realization):
        raise StageArtifactChainError("realization sources crossed the exact branch")

    if (
        stage_subject_inventory.stage_id != stage_id
        or
        function_ledger.stage_id != stage_id
        or function_requirements.stage_id != stage_id
        or stage_requirement_profile.stage_id != stage_id
        or baseline_coverage.stage_id != stage_id
        or stage_closure.stage_id != stage_id
    ):
        raise StageArtifactChainError("Stage 3 evidence crossed the entered stage")
    if baseline_coverage.level is not baseline_level_for_design_phase(
        stage_entry_proof.phase_gate.to_phase
    ):
        raise StageArtifactChainError("baseline level differs from the entered stage")

    if (
        stage_requirement_profile.predecessor_state_digest
        != stage_subject_inventory.stage_subject_digest
        or stage_requirement_profile.stage_subject_ref
        != stage_subject_inventory.stage_subject_ref
    ):
        raise StageArtifactChainError(
            "stage requirement profile crossed the exact stage subject"
        )
    try:
        exact_profile = derive_stage_requirement_profile(
            stage_requirement_profile,
            level=baseline_coverage.level,
            sources=baseline_sources,
            subject_digest=stage_subject_inventory.stage_subject_digest,
            subject_inventory=stage_subject_inventory,
        )
        recomputed_coverage = compile_stage_baseline_coverage(
            exact_profile,
            level=baseline_coverage.level,
            sources=baseline_sources,
            subject_digest=stage_subject_inventory.stage_subject_digest,
            subject_inventory=stage_subject_inventory,
            check_receipts=stage_check_receipts,
        )
        recomputed_closure = compile_composite_stage_closure(
            exact_profile,
            subject_digest=stage_subject_inventory.stage_subject_digest,
            check_receipts=stage_check_receipts,
        )
    except (StageBaselineError, StageClosureError) as exc:
        raise StageArtifactChainError(str(exc)) from exc
    if exact_profile != stage_requirement_profile:
        raise StageArtifactChainError(
            "stage requirement profile omitted a framework baseline requirement"
        )
    if recomputed_coverage != baseline_coverage:
        raise StageArtifactChainError(
            "baseline coverage was not recomputed from the exact source and check denominator"
        )
    if recomputed_closure != stage_closure:
        raise StageArtifactChainError(
            "stage closure was not recomputed from the exact profile and check denominator"
        )
    if baseline_coverage.status is not StageBaselineStatus.SATISFIED:
        raise StageArtifactChainError("baseline coverage is not satisfied")
    if baseline_coverage.stage_subject_inventory_digest is None:
        raise StageArtifactChainError("baseline coverage has no current inventory denominator")
    if stage_closure.status is not StageClosureStatus.SATISFIED:
        raise StageArtifactChainError("stage closure is not satisfied")
    if (
        baseline_coverage.profile_id != stage_closure.profile_id
        or baseline_coverage.profile_digest != stage_closure.profile_digest
    ):
        raise StageArtifactChainError("baseline coverage and closure crossed profiles")

    inventory_digest = function_ledger.subject_inventory_digest
    if (
        stage_subject_inventory.inventory_digest != inventory_digest
        or function_requirements.subject_inventory_digest != inventory_digest
        or baseline_coverage.stage_subject_inventory_digest != inventory_digest
    ):
        raise StageArtifactChainError("Stage 3 inventory denominator is stale")
    if (
        function_requirements.function_ledger_ref != function_ledger.ledger_ref
        or function_requirements.function_ledger_digest
        != function_ledger.ledger_digest
    ):
        raise StageArtifactChainError(
            "function requirements are stale against the exact ledger"
        )
    if any(row.status is not FunctionStatus.SATISFIED for row in function_ledger.rows):
        raise StageArtifactChainError("function ledger is not fully satisfied")
    expected_function_requirements = {
        (row.component_ref, evaluation.obligation_ref)
        for row in function_ledger.rows
        for evaluation in row.evaluations
    }
    actual_function_requirements = {
        (item.component_ref, item.functional_obligation_ref)
        for item in function_requirements.requirements
    }
    if (
        not expected_function_requirements
        or actual_function_requirements != expected_function_requirements
    ):
        raise StageArtifactChainError(
            "function relation requirement denominator differs from the ledger"
        )

    if len(baseline_sources.component_functions) != 1:
        raise StageArtifactChainError(
            "Stage 3 requires one exact component-function baseline source"
        )
    function_source = baseline_sources.component_functions[0]
    if (
        function_source.ledger != function_ledger
        or function_source.relation_requirements != function_requirements
        or function_source.ledger_ref != function_ledger_record.record_ref
        or function_source.relation_requirements_ref
        != function_requirement_record.record_ref
    ):
        raise StageArtifactChainError(
            "function records differ from the baseline source set"
        )

    topology_by_graph: dict[str, RelationTopologyBaselineSource] = {}
    topology_questions = {}
    for source in baseline_sources.relation_topology:
        graph = source.promotion.graph
        if graph.graph_digest in topology_by_graph:
            raise StageArtifactChainError("topology denominator repeats a graph")
        if (
            graph.stage_id != stage_id
            or graph.subject_inventory_digest != inventory_digest
        ):
            raise StageArtifactChainError("topology graph is stale against Stage 3")
        topology_by_graph[graph.graph_digest] = source
        for question in source.context.questions:
            prior = topology_questions.setdefault(question.ref, question)
            if prior != question:
                raise StageArtifactChainError(
                    "topology sources disagree on a function question"
                )
    for requirement in function_requirements.requirements:
        if topology_questions.get(requirement.question.ref) != requirement.question:
            raise StageArtifactChainError(
                "topology omitted an exact function-derived question"
            )
        expected_slot = requirement.slot
        matching_slots = tuple(
            slot
            for source in baseline_sources.relation_topology
            for slot in source.compilation.slots
            if (
                slot.stage_id == expected_slot.stage_id
                and slot.rule_id == expected_slot.rule_id
                and slot.node_ref == expected_slot.node_ref
                and slot.node_kind == expected_slot.node_kind
                and slot.semantic_kind == expected_slot.semantic_kind
                and slot.relation_kind == expected_slot.relation_kind
                and slot.subject_role == expected_slot.subject_role
                and slot.counted_role == expected_slot.counted_role
                and slot.minimum_count == expected_slot.minimum_count
                and slot.maximum_count == expected_slot.maximum_count
                and slot.scenario_ref == expected_slot.scenario_ref
                and slot.evidence_refs == expected_slot.evidence_refs
                and slot.authority_refs == expected_slot.authority_refs
                and slot.allow_not_applicable
                == expected_slot.allow_not_applicable
            )
        )
        if len(matching_slots) != 1:
            raise StageArtifactChainError(
                "topology omitted an exact function-derived relation slot"
            )

    realization_by_graph: dict[str, RelationRealizationBaselineSource] = {}
    realization_checks: list[CheckReceiptEnvelope] = []
    independent_receipts: list[CheckReceiptEnvelope] = []
    program_digests: set[str] = set()
    readback_digests: set[str] = set()
    subject_digests: set[str] = set()
    for source in baseline_sources.relation_realization:
        graph_digest = source.graph.graph_digest
        if graph_digest in realization_by_graph:
            raise StageArtifactChainError("realization denominator repeats a graph")
        if graph_digest not in topology_by_graph:
            raise StageArtifactChainError(
                "realization does not derive from a promoted topology source"
            )
        if source.graph != topology_by_graph[graph_digest].promotion.graph:
            raise StageArtifactChainError("realization topology content changed")
        if (
            source.graph.stage_id != stage_id
            or source.graph.subject_inventory_digest != inventory_digest
            or source.manifest.stage_id != stage_id
        ):
            raise StageArtifactChainError("realization is stale against Stage 3")
        receipt = check_relation_realization(
            source.graph,
            source.manifest,
            source.program,
            source.readback,
            verification_receipts=source.verification_receipts,
        )
        if receipt.status is not CheckStatus.PASS:
            raise StageArtifactChainError("relation realization no longer passes")
        bound_receipt_digests = {
            owner.verification.receipt_digest
            for owner in (*source.manifest.pairings, *source.manifest.paths)
            if owner.verification is not None
        }
        supplied_receipt_digests = {
            item.receipt_digest for item in source.verification_receipts
        }
        if (
            len(supplied_receipt_digests) != len(source.verification_receipts)
            or bound_receipt_digests != supplied_receipt_digests
        ):
            raise StageArtifactChainError(
                "realization verification receipts changed denominator"
            )
        realization_by_graph[graph_digest] = source
        realization_checks.append(receipt)
        independent_receipts.extend(source.verification_receipts)
        program_digests.add(source.program.program_digest)
        readback_digests.add(source.readback.snapshot_digest)
        subject_digests.add(source.graph.stage_subject_digest)

    if set(realization_by_graph) != set(topology_by_graph):
        raise StageArtifactChainError(
            "realization does not exactly cover the topology denominator"
        )
    if len(program_digests) != 1 or len(readback_digests) != 1:
        raise StageArtifactChainError(
            "Stage 3 sources do not share one exact program and readback"
        )
    if len(subject_digests) != 1 or stage_closure.subject_digest not in subject_digests:
        raise StageArtifactChainError("stage closure crossed the realized subject")

    assembly_coverage = next(
        (
            item
            for item in baseline_coverage.coverage
            if item.role is StageBaselineRole.ASSEMBLY_RELATIONSHIPS
        ),
        None,
    )
    if assembly_coverage is None:
        raise StageArtifactChainError("baseline omitted assembly relationships")
    relation_source_digests = {
        *(item.source_digest for item in baseline_sources.relation_topology),
        *(item.source_digest for item in baseline_sources.relation_realization),
    }
    if not relation_source_digests.issubset(assembly_coverage.source_digests):
        raise StageArtifactChainError(
            "baseline coverage omitted a topology or realization source"
        )
    realization_check_digests = {
        item.receipt_digest for item in realization_checks
    }
    if not realization_check_digests.issubset(
        assembly_coverage.check_receipt_digests
    ):
        raise StageArtifactChainError(
            "baseline coverage omitted a realization check"
        )
    coverage_check_digests = {
        digest
        for item in baseline_coverage.coverage
        for digest in item.check_receipt_digests
    }
    if not coverage_check_digests.issubset(stage_closure.check_receipt_digests):
        raise StageArtifactChainError("stage closure omitted a baseline check")

    program_digest = next(iter(program_digests))
    readback_digest = next(iter(readback_digests))
    exact_scalar_bindings = (
        (stage_entry_proof_record.content_digest, stage_entry_proof.proof_digest),
        (geometry_program_record.content_digest, program_digest),
        (
            component_index_record.content_digest,
            stage_subject_inventory.component_index_digest,
        ),
        (stage_subject_inventory_record.content_digest, inventory_digest),
        (function_ledger_record.content_digest, function_ledger.ledger_digest),
        (function_requirement_record.content_digest, function_requirements.set_digest),
        (
            stage_requirement_profile_record.content_digest,
            stage_requirement_profile.profile_digest,
        ),
        (baseline_sources_record.content_digest, baseline_sources.source_set_digest),
        (baseline_coverage_record.content_digest, baseline_coverage.receipt_digest),
        (stage_closure_record.content_digest, stage_closure.receipt_digest),
        (cad_readback_record.content_digest, readback_digest),
    )
    if any(actual != expected for actual, expected in exact_scalar_bindings):
        raise StageArtifactChainError("a P036 record binding is stale")

    stage_check_records = _require_exact_bindings(
        stage_check_receipt_records,
        tuple(item.receipt_digest for item in stage_check_receipts),
        "stage check receipt records",
    )

    topology_records = _require_exact_bindings(
        topology_source_records,
        tuple(item.source_digest for item in baseline_sources.relation_topology),
        "topology source records",
    )
    realization_records = _require_exact_bindings(
        realization_source_records,
        tuple(item.source_digest for item in baseline_sources.relation_realization),
        "realization source records",
    )
    all_realization_receipts = (*realization_checks, *independent_receipts)
    receipt_digests = tuple(
        sorted(item.receipt_digest for item in all_realization_receipts)
    )
    if len(receipt_digests) != len(set(receipt_digests)):
        raise StageArtifactChainError("realization receipt denominator repeats a digest")
    realization_records_bound = _require_exact_bindings(
        realization_receipt_records,
        receipt_digests,
        "realization receipt records",
    )

    denominator = StageArtifactVerificationDenominator(
        stage_subject_inventory_digest=inventory_digest,
        component_index_digest=stage_subject_inventory.component_index_digest,
        stage_requirement_profile_digest=(
            stage_requirement_profile.profile_digest
        ),
        baseline_source_set_digest=baseline_sources.source_set_digest,
        baseline_coverage_digest=baseline_coverage.receipt_digest,
        stage_closure_digest=stage_closure.receipt_digest,
        function_ledger_digest=function_ledger.ledger_digest,
        function_relation_requirement_digest=function_requirements.set_digest,
        program_digest=program_digest,
        readback_digest=readback_digest,
        topology_source_digests=tuple(
            item.source_digest for item in baseline_sources.relation_topology
        ),
        topology_graph_digests=tuple(topology_by_graph),
        realization_source_digests=tuple(
            item.source_digest for item in baseline_sources.relation_realization
        ),
        realization_receipt_digests=receipt_digests,
        stage_check_receipt_digests=tuple(
            item.receipt_digest for item in stage_check_receipts
        ),
    )
    return StageArtifactClaim(
        claim_id=claim_id,
        status=StageArtifactStatus.STAGE3_VERIFIED_CANDIDATE,
        branch=branch,
        stage_id=stage_id,
        stage_entry_proof=stage_entry_proof,
        stage_entry_proof_record=stage_entry_proof_record,
        artifact=artifact,
        geometry_program=geometry_program_record,
        component_index=component_index_record,
        stage_subject_inventory=stage_subject_inventory_record,
        function_ledger=function_ledger_record,
        function_relation_requirements=function_requirement_record,
        stage_requirement_profile=stage_requirement_profile_record,
        baseline_sources=baseline_sources_record,
        baseline_coverage=baseline_coverage_record,
        stage_closure=stage_closure_record,
        stage_checks=stage_check_records,
        relation_topology=topology_records,
        cad_readback=cad_readback_record,
        relation_realization=realization_records,
        functional_verification=realization_records_bound,
        verification_denominator=denominator,
        viewer_refs=viewer_refs,
        diagnostic_refs=diagnostic_refs,
    )


__all__ = [
    "StageArtifactChainError",
    "compile_relation_realization_baseline_source",
    "compile_stage_artifact_claim",
]
