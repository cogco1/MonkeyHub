"""Exact cross-stage architectural-relation inheritance tests."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archive.archflow.control.baseline import (
    RelationRealizationBaselineSource,
    StageBaselineError,
    StageBaselineSourceSet,
    StageRelationInheritanceBaselineSource,
)
from archive.archflow.control.stage_relation_inheritance import (
    AcceptedRelationTopologyIdentity,
    AcceptedStageRelationPredecessor,
    RelationInheritanceDisposition,
    StageRelationInheritanceReceipt,
    compile_stage_relation_inheritance,
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
    ArchitecturalRelation,
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
    RelationEpistemicStatus,
    RelationParticipant,
)
from archive.archflow.relations.realization import (
    RelationEndpointPairing,
    RelationRealizationManifest,
    RelationRealizationPurpose,
)
from archive.archflow.validation.contracts import CheckFinding, CheckStatus, FindingSeverity
from archive.archflow.validation.relation_realization import (
    check_relation_realization,
)
from archive.tests.test_relation_realization import (
    SHA_SCOPE,
    SHA_STATE,
    branch,
    compiled_program,
    endpoint_bindings,
    readback,
    relation_bindings,
    verification_binding,
    verification_receipt,
)


PREDECESSOR_SUBJECT = "7" * 64
CURRENT_SUBJECT = "8" * 64


def node(component: str, stage_id: str) -> ArchitecturalNode:
    return ArchitecturalNode(
        node_ref=f"component:{component}",
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind="generic_component",
        stage_id=stage_id,
        source_refs=(f"stage-entry:{stage_id}/{component}",),
    )


def relation(
    relation_id: str,
    stage_id: str,
    *,
    kind: ArchitecturalRelationKind = ArchitecturalRelationKind.SUPPORT,
    scenario_ref: str = "scenario:universal",
    predecessor_relation_ref: str | None = None,
    evidence_refs: tuple[str, ...] | None = None,
    authority_refs: tuple[str, ...] | None = None,
) -> ArchitecturalRelation:
    roles = {
        ArchitecturalRelationKind.SUPPORT: ("supported", "supporter"),
        ArchitecturalRelationKind.HOST: ("hosted", "host"),
    }[kind]
    return ArchitecturalRelation(
        relation_id=relation_id,
        kind=kind,
        participants=(
            RelationParticipant(roles[0], "component:a"),
            RelationParticipant(roles[1], "component:b"),
        ),
        scenario_ref=scenario_ref,
        epistemic_status=RelationEpistemicStatus.DERIVED,
        source_refs=(f"record:{relation_id}",),
        evidence_refs=(
            (f"evidence:{relation_id}",)
            if evidence_refs is None
            else evidence_refs
        ),
        authority_refs=(
            ("authority:relation-policy",)
            if authority_refs is None
            else authority_refs
        ),
        propagation_rules=(),
        source_stage_id=stage_id,
        predecessor_relation_ref=predecessor_relation_ref,
    )


def graph(
    stage_id: str,
    relations: tuple[ArchitecturalRelation, ...],
    *,
    selected_branch: BranchRef | None = None,
) -> ArchitecturalRelationGraph:
    return ArchitecturalRelationGraph(
        graph_id=f"{stage_id}-relation-graph",
        branch=selected_branch or branch(),
        stage_id=stage_id,
        state_digest=SHA_STATE,
        scope_digest=SHA_SCOPE,
        stage_subject_digest=(
            PREDECESSOR_SUBJECT
            if stage_id == "stage-3"
            else CURRENT_SUBJECT
        ),
        subject_inventory_digest=("9" if stage_id == "stage-3" else "a")
        * 64,
        nodes=(node("a", stage_id), node("b", stage_id)),
        relations=relations,
    )


def predecessor_graph() -> ArchitecturalRelationGraph:
    return graph(
        "stage-3",
        (relation("support-stage-3", "stage-3"),),
    )


def current_graph(
    predecessor: ArchitecturalRelationGraph,
    *,
    scenario_ref: str = "scenario:universal",
    extra_new: bool = False,
) -> ArchitecturalRelationGraph:
    rows = [
        relation(
            "support-stage-4",
            "stage-4",
            scenario_ref=scenario_ref,
            predecessor_relation_ref=predecessor.relations[0].ref,
        )
    ]
    if extra_new:
        rows.append(
            relation(
                "host-stage-4",
                "stage-4",
                kind=ArchitecturalRelationKind.HOST,
            )
        )
    return graph("stage-4", tuple(rows))


def purpose(kind: ArchitecturalRelationKind) -> RelationRealizationPurpose:
    return {
        ArchitecturalRelationKind.SUPPORT: (
            RelationRealizationPurpose.SUPPORT_CHAIN
        ),
        ArchitecturalRelationKind.HOST: (
            RelationRealizationPurpose.HOST_INTERFACE
        ),
    }[kind]


def realize(selected_graph: ArchitecturalRelationGraph):
    program = compiled_program()
    snapshot = replace(
        readback(program, selected_branch=selected_graph.branch),
        stage_id=selected_graph.stage_id,
    )
    bindings = endpoint_bindings(selected_graph, program)
    pairings = []
    narrow_receipts = []
    for selected_relation in selected_graph.relations:
        selected_bindings = relation_bindings(
            bindings,
            selected_relation.ref,
        )
        topology_pair = RelationEndpointPairing(
            pairing_id=f"pair-{selected_relation.relation_id}",
            relation_ref=selected_relation.ref,
            first_binding_ref=selected_bindings[0].ref,
            second_binding_ref=selected_bindings[1].ref,
        )
        narrow = verification_receipt(
            selected_graph,
            selected_relation,
            checker_id=f"{selected_relation.kind.value}-narrow-phase",
            owner=topology_pair,
        )
        narrow_receipts.append(narrow)
        pairings.append(
            replace(
                topology_pair,
                verification=verification_binding(
                    narrow,
                    purpose(selected_relation.kind),
                ),
            )
        )
    manifest = RelationRealizationManifest(
        manifest_id=f"{selected_graph.stage_id}-relations",
        branch=selected_graph.branch,
        stage_id=selected_graph.stage_id,
        scope_digest=selected_graph.scope_digest,
        relation_graph_digest=selected_graph.graph_digest,
        program_digest=program.program_digest,
        readback_digest=snapshot.snapshot_digest,
        stage_subject_digest=selected_graph.stage_subject_digest,
        endpoint_bindings=bindings,
        pairings=tuple(pairings),
    )
    receipt = check_relation_realization(
        selected_graph,
        manifest,
        program,
        snapshot,
        verification_receipts=tuple(narrow_receipts),
    )
    if receipt.status is not CheckStatus.PASS:
        raise AssertionError(receipt.to_dict())
    return manifest, receipt


class StageRelationInheritanceTests(unittest.TestCase):
    def test_accepted_predecessor_binds_exact_p036_stage_records(self) -> None:
        predecessor = predecessor_graph()
        prefix = "runs/run-001/branches/selected/records"
        binding = AcceptedStageRelationPredecessor(
            predecessor_checkpoint_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/controller-checkpoint.json",
                "1" * 64,
            ),
            predecessor_checkpoint_digest="2" * 64,
            stage_exit_anchor_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/stage-exit-anchor.json",
                "3" * 64,
            ),
            stage_exit_proof_digest="4" * 64,
            baseline_sources_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/baseline-sources.json",
                "5" * 64,
            ),
            baseline_sources_digest="6" * 64,
            baseline_coverage_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/baseline-coverage.json",
                "8" * 64,
            ),
            baseline_coverage_digest="9" * 64,
            accepted_topologies=(
                AcceptedRelationTopologyIdentity(
                    topology_source_digest="7" * 64,
                    graph_digest=predecessor.graph_digest,
                ),
            ),
            topology_source_digest="7" * 64,
            graph=predecessor,
        )

        self.assertEqual(
            AcceptedStageRelationPredecessor.from_dict(binding.to_dict()),
            binding,
        )
        self.assertEqual(
            binding.ref,
            f"accepted-stage-relation-predecessor:{binding.binding_digest}",
        )
        self.assertFalse(binding.to_dict()["stage_acceptance_authority"])
        with self.assertRaisesRegex(
            ValueError,
            "crossed predecessor project, run, or branch",
        ):
            replace(
                binding,
                baseline_sources_ref=replace(
                    binding.baseline_sources_ref,
                    relative_path=(
                        "runs/run-001/branches/foreign/records/"
                        "baseline-sources.json"
                    ),
                ),
            )

    def test_source_set_rejects_partial_accepted_predecessor_topology_set(
        self,
    ) -> None:
        predecessor = predecessor_graph()
        second_graph = graph(
            "stage-3",
            (relation("secondary-support-stage-3", "stage-3"),),
        )
        prefix = "runs/run-001/branches/selected/records"
        binding = AcceptedStageRelationPredecessor(
            predecessor_checkpoint_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/controller-checkpoint.json",
                "1" * 64,
            ),
            predecessor_checkpoint_digest="2" * 64,
            stage_exit_anchor_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/stage-exit-anchor.json",
                "3" * 64,
            ),
            stage_exit_proof_digest="4" * 64,
            baseline_sources_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/baseline-sources.json",
                "5" * 64,
            ),
            baseline_sources_digest="6" * 64,
            baseline_coverage_ref=ProjectRecordRef(
                predecessor.branch.run.project_id,
                f"{prefix}/baseline-coverage.json",
                "8" * 64,
            ),
            baseline_coverage_digest="9" * 64,
            accepted_topologies=(
                AcceptedRelationTopologyIdentity(
                    topology_source_digest="7" * 64,
                    graph_digest=predecessor.graph_digest,
                ),
                AcceptedRelationTopologyIdentity(
                    topology_source_digest="a" * 64,
                    graph_digest=second_graph.graph_digest,
                ),
            ),
            topology_source_digest="7" * 64,
            graph=predecessor,
        )
        current = current_graph(predecessor)
        manifest, _receipt = realize(current)
        program = compiled_program()
        snapshot = replace(
            readback(program, selected_branch=current.branch),
            stage_id=current.stage_id,
        )
        realization_source = RelationRealizationBaselineSource(
            graph=current,
            manifest=manifest,
            program=program,
            readback=snapshot,
        )
        second_current = current_graph(second_graph)
        second_manifest, _second_receipt = realize(second_current)
        second_program = compiled_program()
        second_snapshot = replace(
            readback(
                second_program,
                selected_branch=second_current.branch,
            ),
            stage_id=second_current.stage_id,
        )
        second_realization_source = RelationRealizationBaselineSource(
            graph=second_current,
            manifest=second_manifest,
            program=second_program,
            readback=second_snapshot,
        )
        complete = StageBaselineSourceSet(
            relation_inheritance=(
                StageRelationInheritanceBaselineSource(
                    predecessor=binding,
                    current_realization=realization_source,
                ),
                StageRelationInheritanceBaselineSource(
                    predecessor=replace(
                        binding,
                        topology_source_digest="a" * 64,
                        graph=second_graph,
                    ),
                    current_realization=second_realization_source,
                ),
            ),
        )
        self.assertEqual(
            StageBaselineSourceSet.from_dict(complete.to_dict()),
            complete,
        )
        with self.assertRaisesRegex(
            StageBaselineError,
            "full accepted predecessor topology set",
        ):
            StageBaselineSourceSet(
                relation_inheritance=(
                    StageRelationInheritanceBaselineSource(
                        predecessor=binding,
                        current_realization=realization_source,
                    ),
                ),
            )

    def test_retained_and_refined_relations_are_classified_mechanically(
        self,
    ) -> None:
        predecessor = predecessor_graph()

        retained = current_graph(predecessor)
        retained_manifest, retained_check = realize(retained)
        retained_receipt = compile_stage_relation_inheritance(
            predecessor,
            retained,
            retained_manifest,
            retained_check,
        )

        refined = current_graph(
            predecessor,
            scenario_ref="scenario:wind",
        )
        refined_manifest, refined_check = realize(refined)
        refined_receipt = compile_stage_relation_inheritance(
            predecessor,
            refined,
            refined_manifest,
            refined_check,
        )

        self.assertIs(CheckStatus.PASS, retained_receipt.status)
        self.assertIs(
            RelationInheritanceDisposition.RETAINED,
            retained_receipt.coverage[0].disposition,
        )
        self.assertIs(CheckStatus.PASS, refined_receipt.status)
        self.assertIs(
            RelationInheritanceDisposition.REFINED,
            refined_receipt.coverage[0].disposition,
        )

    def test_missing_predecessor_relation_fails_exact_denominator(self) -> None:
        predecessor = predecessor_graph()
        current = graph(
            "stage-4",
            (relation("new-support", "stage-4"),),
        )
        manifest, realization = realize(current)

        receipt = compile_stage_relation_inheritance(
            predecessor,
            current,
            manifest,
            realization,
        )

        self.assertIs(CheckStatus.FAIL, receipt.status)
        self.assertIs(
            RelationInheritanceDisposition.MISSING,
            receipt.coverage[0].disposition,
        )
        self.assertIn(
            "stage-relation-predecessor-missing",
            {item.code for item in receipt.findings},
        )
        self.assertFalse(receipt.closure_ready)
        self.assertFalse(receipt.covered_predecessor_relation_refs)

    def test_duplicate_successors_fail_exactly_once_rule(self) -> None:
        predecessor = predecessor_graph()
        predecessor_ref = predecessor.relations[0].ref
        current = graph(
            "stage-4",
            (
                relation(
                    "support-stage-4-a",
                    "stage-4",
                    predecessor_relation_ref=predecessor_ref,
                ),
                relation(
                    "support-stage-4-b",
                    "stage-4",
                    predecessor_relation_ref=predecessor_ref,
                ),
            ),
        )
        manifest, realization = realize(current)

        receipt = compile_stage_relation_inheritance(
            predecessor,
            current,
            manifest,
            realization,
        )

        self.assertIs(CheckStatus.FAIL, receipt.status)
        self.assertIs(
            RelationInheritanceDisposition.DUPLICATE,
            receipt.coverage[0].disposition,
        )
        self.assertIn(
            "stage-relation-predecessor-duplicate",
            {item.code for item in receipt.findings},
        )

    def test_stage_three_receipt_cannot_revalidate_stage_four(self) -> None:
        predecessor = predecessor_graph()
        current = current_graph(predecessor)
        current_manifest, _ = realize(current)
        _, stale_stage_three_receipt = realize(predecessor)

        receipt = compile_stage_relation_inheritance(
            predecessor,
            current,
            current_manifest,
            stale_stage_three_receipt,
        )

        self.assertIs(CheckStatus.FAIL, receipt.status)
        self.assertIn(
            "stage-relation-current-receipt-stale",
            {item.code for item in receipt.findings},
        )
        self.assertFalse(receipt.closure_ready)

    def test_unknown_predecessor_ref_is_stale_not_a_new_relation(self) -> None:
        predecessor = predecessor_graph()
        current = graph(
            "stage-4",
            (
                relation(
                    "support-stage-4",
                    "stage-4",
                    predecessor_relation_ref=(
                        "architectural-relation:support-stage-2"
                    ),
                ),
            ),
        )
        manifest, realization = realize(current)

        receipt = compile_stage_relation_inheritance(
            predecessor,
            current,
            manifest,
            realization,
        )

        codes = {item.code for item in receipt.findings}
        self.assertIs(CheckStatus.FAIL, receipt.status)
        self.assertIn("stage-relation-predecessor-unknown", codes)
        self.assertIn("stage-relation-predecessor-missing", codes)
        self.assertFalse(receipt.new_current_relation_refs)

    def test_fresh_current_pass_closes_and_allows_authorized_new_relation(
        self,
    ) -> None:
        predecessor = predecessor_graph()
        current = current_graph(predecessor, extra_new=True)
        manifest, realization = realize(current)

        receipt = compile_stage_relation_inheritance(
            predecessor,
            current,
            manifest,
            realization,
        )

        self.assertIs(CheckStatus.PASS, receipt.status)
        self.assertTrue(receipt.closure_ready)
        self.assertEqual(
            receipt.predecessor_relation_refs,
            receipt.covered_predecessor_relation_refs,
        )
        self.assertEqual(
            ("architectural-relation:host-stage-4",),
            receipt.new_current_relation_refs,
        )
        self.assertEqual(
            receipt,
            StageRelationInheritanceReceipt.from_dict(receipt.to_dict()),
        )
        self.assertFalse(receipt.to_dict()["stage_acceptance_authority"])
        self.assertFalse(receipt.to_dict()["canonical_write_authority"])

    def test_new_relation_requires_evidence_and_authority_at_typed_boundary(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            relation(
                "unsupported-new-host",
                "stage-4",
                kind=ArchitecturalRelationKind.HOST,
                evidence_refs=(),
                authority_refs=(),
            )

    def test_cross_branch_source_stage_and_graph_digest_fail_closed(self) -> None:
        predecessor = predecessor_graph()
        current = current_graph(predecessor)
        manifest, realization = realize(current)
        foreign_branch = replace(current.branch, branch_id="foreign")
        foreign_current = replace(current, branch=foreign_branch)

        cross_branch = compile_stage_relation_inheritance(
            predecessor,
            foreign_current,
            manifest,
            realization,
        )
        stale_manifest = replace(
            manifest,
            relation_graph_digest="f" * 64,
        )
        wrong_digest = compile_stage_relation_inheritance(
            predecessor,
            current,
            stale_manifest,
            realization,
        )
        wrong_stage_relation = replace(
            current.relations[0],
            source_stage_id="stage-3",
        )
        wrong_stage_graph = replace(
            current,
            relations=(wrong_stage_relation,),
        )
        wrong_stage_manifest, wrong_stage_check = realize(wrong_stage_graph)
        wrong_stage = compile_stage_relation_inheritance(
            predecessor,
            wrong_stage_graph,
            wrong_stage_manifest,
            wrong_stage_check,
        )

        self.assertIn(
            "stage-relation-cross-branch-lineage",
            {item.code for item in cross_branch.findings},
        )
        self.assertIn(
            "stage-relation-manifest-graph-digest-mismatch",
            {item.code for item in wrong_digest.findings},
        )
        self.assertIn(
            "stage-relation-current-source-stage-mismatch",
            {item.code for item in wrong_stage.findings},
        )
        self.assertTrue(
            all(
                item.status is CheckStatus.FAIL
                for item in (cross_branch, wrong_digest, wrong_stage)
            )
        )

    def test_unknown_or_failed_current_realization_never_closes(self) -> None:
        predecessor = predecessor_graph()
        current = current_graph(predecessor)
        manifest, realization = realize(current)
        unknown = replace(
            realization,
            status=CheckStatus.UNKNOWN,
            covered_refs=(),
            findings=(
                CheckFinding(
                    code="synthetic-open",
                    severity=FindingSeverity.UNKNOWN,
                    message="Current relation realization remains open.",
                    subject_refs=(current.ref,),
                ),
            ),
        )
        failed = replace(
            realization,
            status=CheckStatus.FAIL,
            covered_refs=(),
            findings=(
                CheckFinding(
                    code="synthetic-failure",
                    severity=FindingSeverity.ERROR,
                    message="Current relation realization failed.",
                    subject_refs=(current.ref,),
                ),
            ),
        )

        open_receipt = compile_stage_relation_inheritance(
            predecessor,
            current,
            manifest,
            unknown,
        )
        failed_receipt = compile_stage_relation_inheritance(
            predecessor,
            current,
            manifest,
            failed,
        )

        self.assertIs(CheckStatus.UNKNOWN, open_receipt.status)
        self.assertIs(CheckStatus.FAIL, failed_receipt.status)
        self.assertFalse(open_receipt.closure_ready)
        self.assertFalse(failed_receipt.closure_ready)
        self.assertFalse(open_receipt.covered_predecessor_relation_refs)
        self.assertFalse(failed_receipt.covered_predecessor_relation_refs)


if __name__ == "__main__":
    unittest.main()
