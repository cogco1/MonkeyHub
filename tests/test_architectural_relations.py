"""Contracts, coverage, projection, and traversal tests for building relations."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archive.archflow.control.baseline import StageBaselineLevel
from archive.archflow.control.check_requirements import (
    relation_coverage_stage_requirement,
)
from archive.archflow.control.relation_checks import check_relation_coverage
from archflow.control.requirements import StageRequirementProfile
from archflow.control.stage_closure import (
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archive.archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectInventoryEntry,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archive.archflow.relations.adapters import (
    compile_dependency_edges,
    compile_support_requirements,
)
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
    ArchitecturalRelation,
    ArchitecturalRelationContractError,
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
    ImpactEffect,
    RelationEpistemicStatus,
    RelationParticipant,
    RelationProjection,
    RelationPropagationRule,
)
from archive.archflow.relations.coverage import (
    GraphCoverageManifest,
    RelationCoverageError,
    RelationCoverageStatus,
    RelationNotApplicable,
    SemanticKindRelationPolicy,
    SemanticRelationRule,
    compile_graph_coverage,
    compile_requirement_slots,
)
from archive.archflow.relations.traversal import (
    GraphTraversalReceipt,
    RelationArc,
    RelationCheckerRequirement,
    RelationCheckStatus,
    RelationStatusBinding,
    RelationTraversalError,
    RelationTraversalPolicy,
    RelationView,
    TraversalStatus,
    check_reachability,
    compile_relation_view,
    find_cycle_node_groups,
    propagate_impacts,
    topological_order,
)
from archflow.state.operational_state import DependencyEffect
from archive.archflow.validation.assembly import RelationshipKind
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
UNIVERSAL = "scenario:universal"
OPEN_DOOR = "scenario:door-open"


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="relation-fixture",
            run_id="run-001",
            base=ProjectVersionRef("relation-fixture", 2, SHA_A),
        ),
        branch_id="selected",
        epoch=4,
    )


def node(
    ref: str,
    semantic_kind: str,
    *,
    kind: ArchitecturalNodeKind = ArchitecturalNodeKind.COMPONENT,
    stage_id: str = "stage-4",
    predecessor_ref: str | None = None,
) -> ArchitecturalNode:
    return ArchitecturalNode(
        node_ref=ref,
        node_kind=kind,
        semantic_kind=semantic_kind,
        stage_id=stage_id,
        source_refs=(f"record:{ref.split(':', 1)[1]}",),
        predecessor_ref=predecessor_ref,
    )


def relation(
    relation_id: str,
    kind: ArchitecturalRelationKind,
    participants: tuple[tuple[str, str], ...],
    *,
    scenario_ref: str = UNIVERSAL,
    rules: tuple[tuple[str, str, ImpactEffect], ...] = (),
    predecessor_relation_ref: str | None = None,
    epistemic_status: RelationEpistemicStatus = RelationEpistemicStatus.DERIVED,
) -> ArchitecturalRelation:
    counts: dict[str, int] = {}
    for role, _ in participants:
        counts[role] = counts.get(role, 0) + 1
    ordinal: dict[str, int] = {}
    values = []
    for role, node_ref in participants:
        index = ordinal.get(role, 0)
        ordinal[role] = index + 1
        values.append(
            RelationParticipant(
                role=role,
                node_ref=node_ref,
                ordinal=index if counts[role] > 1 else None,
            )
        )
    return ArchitecturalRelation(
        relation_id=relation_id,
        kind=kind,
        participants=tuple(values),
        scenario_ref=scenario_ref,
        epistemic_status=epistemic_status,
        source_refs=(f"record:{relation_id}",),
        evidence_refs=(f"evidence:{relation_id}",),
        authority_refs=("authority:relation-policy",),
        propagation_rules=tuple(
            RelationPropagationRule(
                trigger_role=trigger,
                affected_role=affected,
                effect=effect,
            )
            for trigger, affected, effect in rules
        ),
        source_stage_id="stage-4",
        predecessor_relation_ref=predecessor_relation_ref,
    )


def graph(
    nodes: tuple[ArchitecturalNode, ...],
    relations: tuple[ArchitecturalRelation, ...],
) -> ArchitecturalRelationGraph:
    return ArchitecturalRelationGraph(
        graph_id="fixture-graph",
        branch=branch(),
        stage_id="stage-4",
        state_digest=SHA_A,
        scope_digest=SHA_A,
        stage_subject_digest=SHA_B,
        subject_inventory_digest=SHA_B,
        nodes=nodes,
        relations=relations,
    )


def stage_inventory(
    components: tuple[tuple[str, str, str | None], ...],
) -> StageSubjectInventory:
    selected_branch = branch()
    record_prefix = (
        f"runs/{selected_branch.run.run_id}/branches/"
        f"{selected_branch.branch_id}/records"
    )
    return StageSubjectInventory(
        inventory_id="stage-4-relation-subjects",
        branch=selected_branch,
        stage_id="stage-4",
        stage_subject_ref="stage-subject:stage-4",
        stage_subject_digest=SHA_B,
        baseline_level=StageBaselineLevel.PRE_GEOMETRY,
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
        entries=tuple(
            StageSubjectInventoryEntry(
                component_id=component_id,
                identity_ref=f"design-component:{component_id}",
                parent_component_id=parent_component_id,
                semantic_kind=semantic_kind,
                component_digest=SHA_A,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=(),
            )
            for component_id, semantic_kind, parent_component_id in components
        ),
    )


def inventory_node(entry: StageSubjectInventoryEntry) -> ArchitecturalNode:
    return ArchitecturalNode(
        node_ref=entry.identity_ref,
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind=entry.semantic_kind,
        stage_id="stage-4",
        source_refs=(f"stage-subject-entry:{entry.entry_digest}",),
    )


def status_evidence(
    view,
    overrides: dict[str, RelationCheckStatus] | None = None,
) -> tuple[
    tuple[RelationStatusBinding, ...],
    tuple[CheckReceiptEnvelope, ...],
]:
    overrides = overrides or {}
    receipts = []
    for index, relation_ref in enumerate(view.relation_refs):
        relation_status = overrides.get(
            relation_ref,
            RelationCheckStatus.PASS,
        )
        check_status = {
            RelationCheckStatus.PASS: CheckStatus.PASS,
            RelationCheckStatus.FAIL: CheckStatus.FAIL,
            RelationCheckStatus.UNKNOWN: CheckStatus.UNKNOWN,
        }[relation_status]
        if check_status is CheckStatus.FAIL:
            findings = (
                CheckFinding(
                    code="relation-check-failed",
                    severity=FindingSeverity.ERROR,
                    message="Relation-specific validation failed.",
                    subject_refs=(relation_ref,),
                ),
            )
        elif check_status is CheckStatus.UNKNOWN:
            findings = (
                CheckFinding(
                    code="relation-check-unknown",
                    severity=FindingSeverity.UNKNOWN,
                    message="Relation-specific validation is unresolved.",
                    subject_refs=(relation_ref,),
                ),
            )
        else:
            findings = ()
        receipts.append(
            CheckReceiptEnvelope(
                check_id=f"relation-check-{index:04d}",
                checker_id="relation-geometry-checker",
                checker_version="1.0.0",
                branch=branch(),
                scope_digest=SHA_A,
                subject_refs=(relation_ref,),
                subject_digest=SHA_B,
                status=check_status,
                findings=findings,
                coverage_denominator=(relation_ref,),
                covered_refs=(relation_ref,),
            )
        )
    compiled_receipts = tuple(receipts)
    bindings = tuple(
        RelationStatusBinding.bind(relation_ref, receipt)
        for relation_ref, receipt in zip(
            view.relation_refs,
            compiled_receipts,
            strict=True,
        )
    )
    return bindings, compiled_receipts


def traversal_policy(
    view: RelationView,
    *,
    start_refs: tuple[str, ...],
    target_refs: tuple[str, ...] = (),
    receipts: tuple[CheckReceiptEnvelope, ...] = (),
    minimum_hops: int | None = None,
) -> RelationTraversalPolicy:
    impact = view.projection is RelationProjection.IMPACT
    requirements = (
        ()
        if impact
        else tuple(
            RelationCheckerRequirement(
                relation_ref=relation_ref,
                check_id=receipt.check_id,
                checker_id=receipt.checker_id,
                checker_version=receipt.checker_version,
            )
            for relation_ref, receipt in zip(
                view.relation_refs,
                receipts,
                strict=True,
            )
        )
    )
    return RelationTraversalPolicy(
        policy_id=f"{view.projection.value}-relation-traversal",
        projection=view.projection,
        start_refs=start_refs,
        target_refs=target_refs,
        minimum_hops=(0 if impact else 1) if minimum_hops is None else minimum_hops,
        allowed_checker_ids=(
            ()
            if impact
            else ("relation-geometry-checker",)
        ),
        check_requirements=requirements,
        evidence_refs=("evidence:relation-traversal-policy",),
        authority_refs=("authority:relation-traversal-policy",),
    )


class ArchitecturalRelationContractTests(unittest.TestCase):
    def test_graph_roundtrip_is_exact_and_authority_free(self) -> None:
        wall = node("component:wall", "wall")
        opening = node(
            "opening:door",
            "door-opening",
            kind=ArchitecturalNodeKind.OPENING,
        )
        hosted = relation(
            "wall-door-opening",
            ArchitecturalRelationKind.HOSTS_VOID,
            (("host", wall.node_ref), ("void", opening.node_ref)),
        )
        original = graph((opening, wall), (hosted,))

        payload = original.to_dict()
        restored = ArchitecturalRelationGraph.from_dict(payload)

        self.assertEqual(restored, original)
        self.assertEqual(restored.graph_digest, original.graph_digest)
        self.assertFalse(payload["design_authority"])
        self.assertFalse(payload["canonical_write_authority"])

    def test_graph_rejects_unknown_relation_endpoint(self) -> None:
        wall = node("component:wall", "wall")
        hosted = relation(
            "wall-missing-opening",
            ArchitecturalRelationKind.HOSTS_VOID,
            (("host", wall.node_ref), ("void", "opening:missing")),
        )

        with self.assertRaises(ArchitecturalRelationContractError):
            graph((wall,), (hosted,))

    def test_relation_kind_rejects_incomplete_role_schema(self) -> None:
        with self.assertRaises(ArchitecturalRelationContractError):
            relation(
                "fake-support",
                ArchitecturalRelationKind.SUPPORT,
                (
                    ("supported", "component:beam"),
                    ("decorator", "component:column"),
                ),
            )

    def test_relation_kind_rejects_undeclared_extra_role(self) -> None:
        with self.assertRaises(ArchitecturalRelationContractError):
            relation(
                "support-with-hidden-policy-role",
                ArchitecturalRelationKind.SUPPORT,
                (
                    ("supported", "component:beam"),
                    ("supporter", "component:foundation"),
                    ("foo", "component:column"),
                ),
            )


class ArchitecturalProjectionTests(unittest.TestCase):
    def hosted_graph(self) -> ArchitecturalRelationGraph:
        wall = node("component:wall", "wall")
        opening = node(
            "opening:door",
            "door-opening",
            kind=ArchitecturalNodeKind.OPENING,
        )
        leaf = node("component:door-leaf", "door-leaf")
        host = relation(
            "wall-opening",
            ArchitecturalRelationKind.HOSTS_VOID,
            (("host", wall.node_ref), ("void", opening.node_ref)),
            rules=(
                ("host", "void", ImpactEffect.INVALIDATE),
                ("void", "host", ImpactEffect.REVALIDATE),
            ),
        )
        fill = relation(
            "opening-leaf",
            ArchitecturalRelationKind.FILLS_VOID,
            (("void", opening.node_ref), ("fill", leaf.node_ref)),
            rules=(
                ("void", "fill", ImpactEffect.INVALIDATE),
                ("fill", "void", ImpactEffect.REVALIDATE),
            ),
        )
        return graph((wall, opening, leaf), (host, fill))

    def test_one_relation_compiles_different_host_and_impact_directions(self) -> None:
        source = self.hosted_graph()
        host_view = compile_relation_view(
            source,
            projection=RelationProjection.HOST,
            scenario_ref=UNIVERSAL,
        )
        impact_view = compile_relation_view(
            source,
            projection=RelationProjection.IMPACT,
            scenario_ref=UNIVERSAL,
        )

        self.assertEqual(
            {(item.source_ref, item.target_ref) for item in host_view.arcs},
            {
                ("component:door-leaf", "opening:door"),
                ("opening:door", "component:wall"),
            },
        )
        self.assertEqual(len(impact_view.arcs), 4)
        self.assertEqual(
            find_cycle_node_groups(impact_view),
            (("component:door-leaf", "component:wall", "opening:door"),),
        )
        self.assertEqual(
            topological_order(host_view),
            ("component:door-leaf", "opening:door", "component:wall"),
        )
        with self.assertRaises(RelationTraversalError):
            topological_order(impact_view)

    def test_impact_fixed_point_propagates_and_preserves_witnesses(self) -> None:
        source = self.hosted_graph()
        impact_view = compile_relation_view(
            source,
            projection=RelationProjection.IMPACT,
            scenario_ref=UNIVERSAL,
        )

        receipt = propagate_impacts(
            impact_view,
            policy=traversal_policy(
                impact_view,
                start_refs=("component:wall",),
            ),
            traversal_id="wall-change",
            start_refs=("component:wall",),
        )

        self.assertEqual(receipt.status, TraversalStatus.PASS)
        self.assertEqual(
            {item.node_ref: item.effect for item in receipt.impacts},
            {
                "component:wall": ImpactEffect.INVALIDATE,
                "opening:door": ImpactEffect.INVALIDATE,
                "component:door-leaf": ImpactEffect.INVALIDATE,
            },
        )
        self.assertEqual(len(receipt.witnesses), 2)
        self.assertEqual(
            GraphTraversalReceipt.from_dict(receipt.to_dict()),
            receipt,
        )

    def test_revalidation_edge_does_not_escalate_to_invalidation(self) -> None:
        upstream = node("component:upstream", "upstream-component")
        downstream = node("component:downstream", "downstream-component")
        source = graph(
            (upstream, downstream),
            (
                relation(
                    "downstream-dependency",
                    ArchitecturalRelationKind.DEPENDENCY,
                    (
                        ("upstream", upstream.node_ref),
                        ("downstream", downstream.node_ref),
                    ),
                    rules=(
                        (
                            "upstream",
                            "downstream",
                            ImpactEffect.REVALIDATE,
                        ),
                    ),
                ),
            ),
        )
        view = compile_relation_view(
            source,
            projection=RelationProjection.IMPACT,
            scenario_ref=UNIVERSAL,
        )

        receipt = propagate_impacts(
            view,
            policy=traversal_policy(
                view,
                start_refs=(upstream.node_ref,),
            ),
            traversal_id="revalidation-impact",
            start_refs=(upstream.node_ref,),
        )

        self.assertEqual(
            {item.node_ref: item.effect for item in receipt.impacts},
            {
                upstream.node_ref: ImpactEffect.INVALIDATE,
                downstream.node_ref: ImpactEffect.REVALIDATE,
            },
        )

    def test_impact_projection_compiles_to_existing_dependency_edges(self) -> None:
        edges = compile_dependency_edges(
            self.hosted_graph(),
            scenario_ref=UNIVERSAL,
        )

        self.assertEqual(len(edges), 4)
        self.assertIn(
            (
                "component:wall",
                "opening:door",
                "architectural.hosts_void",
                DependencyEffect.INVALIDATES.value,
            ),
            {item.identity for item in edges},
        )

    def test_long_acyclic_view_uses_bounded_iterative_traversal(self) -> None:
        node_refs = tuple(f"component:n-{index:04d}" for index in range(1_500))
        view = RelationView(
            graph_digest=SHA_A,
            branch=branch(),
            scope_digest=SHA_A,
            stage_subject_digest=SHA_B,
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
            node_refs=node_refs,
            arcs=tuple(
                RelationArc(
                    relation_ref=f"architectural-relation:r-{index:04d}",
                    source_ref=node_refs[index],
                    target_ref=node_refs[index + 1],
                    projection=RelationProjection.SUPPORT,
                    epistemic_status=RelationEpistemicStatus.DERIVED,
                )
                for index in range(len(node_refs) - 1)
            ),
        )

        self.assertEqual(find_cycle_node_groups(view), ())
        self.assertEqual(topological_order(view), node_refs)

    def test_hyperrelation_pair_expansion_fails_before_unbounded_allocation(
        self,
    ) -> None:
        supported = tuple(
            node(f"component:supported-{index:02d}", "supported-member")
            for index in range(65)
        )
        supporters = tuple(
            node(f"component:supporter-{index:02d}", "supporter-member")
            for index in range(65)
        )
        source = graph(
            supported + supporters,
            (
                relation(
                    "bounded-support-hyperedge",
                    ArchitecturalRelationKind.SUPPORT,
                    tuple(
                        ("supported", item.node_ref) for item in supported
                    )
                    + tuple(
                        ("supporter", item.node_ref) for item in supporters
                    ),
                ),
            ),
        )

        with self.assertRaises(RelationTraversalError):
            compile_relation_view(
                source,
                projection=RelationProjection.SUPPORT,
                scenario_ref="loadcase:gravity",
            )
        with self.assertRaises(RelationTraversalError):
            compile_support_requirements(
                source,
                scenario_ref="loadcase:gravity",
            )


class ArchitecturalSupportTraversalTests(unittest.TestCase):
    def support_graph(
        self,
        *,
        cycle: bool = False,
        open_middle_relation: bool = False,
    ) -> ArchitecturalRelationGraph:
        nodes = (
            node("component:foundation", "foundation"),
            node("component:column", "structural-column"),
            node("component:beam", "beam"),
            node("component:roof", "roof"),
        )
        relations = [
            relation(
                "column-foundation",
                ArchitecturalRelationKind.SUPPORT,
                (("supported", "component:column"), ("supporter", "component:foundation")),
            ),
            relation(
                "beam-column",
                ArchitecturalRelationKind.SUPPORT,
                (("supported", "component:beam"), ("supporter", "component:column")),
                epistemic_status=(
                    RelationEpistemicStatus.UNKNOWN
                    if open_middle_relation
                    else RelationEpistemicStatus.DERIVED
                ),
            ),
            relation(
                "roof-beam",
                ArchitecturalRelationKind.SUPPORT,
                (("supported", "component:roof"), ("supporter", "component:beam")),
            ),
        ]
        if cycle:
            relations.append(
                relation(
                    "foundation-roof",
                    ArchitecturalRelationKind.SUPPORT,
                    (("supported", "component:foundation"), ("supporter", "component:roof")),
                )
            )
        return graph(nodes, tuple(relations))

    def test_every_load_origin_reaches_foundation_through_pass_edges(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )

        bindings, check_receipts = status_evidence(view)
        receipt = check_reachability(
            view,
            policy=traversal_policy(
                view,
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                receipts=check_receipts,
            ),
            traversal_id="gravity-path",
            start_refs=("component:roof",),
            target_refs=("component:foundation",),
            status_bindings=bindings,
            check_receipts=check_receipts,
        )

        self.assertEqual(receipt.status, TraversalStatus.PASS)
        self.assertEqual(
            receipt.witnesses[0].node_refs,
            (
                "component:roof",
                "component:beam",
                "component:column",
                "component:foundation",
            ),
        )
        receipt.require_view(view)
        receipt.require_check_receipts(
            view,
            traversal_policy(
                view,
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                receipts=check_receipts,
            ),
            check_receipts,
        )
        self.assertEqual(
            GraphTraversalReceipt.from_dict(receipt.to_dict()),
            receipt,
        )
        with self.assertRaises(RelationTraversalError):
            replace(
                receipt,
                status_bindings=receipt.status_bindings[:-1],
            )

    def test_unknown_support_stays_unknown_and_fail_blocks(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        uncertain_ref = "architectural-relation:beam-column"
        unknown_bindings, unknown_receipts = status_evidence(
            view,
            {uncertain_ref: RelationCheckStatus.UNKNOWN},
        )
        unknown = check_reachability(
            view,
            policy=traversal_policy(
                view,
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                receipts=unknown_receipts,
            ),
            traversal_id="gravity-unknown",
            start_refs=("component:roof",),
            target_refs=("component:foundation",),
            status_bindings=unknown_bindings,
            check_receipts=unknown_receipts,
        )
        failed_bindings, failed_receipts = status_evidence(
            view,
            {uncertain_ref: RelationCheckStatus.FAIL},
        )
        failed = check_reachability(
            view,
            policy=traversal_policy(
                view,
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                receipts=failed_receipts,
            ),
            traversal_id="gravity-fail",
            start_refs=("component:roof",),
            target_refs=("component:foundation",),
            status_bindings=failed_bindings,
            check_receipts=failed_receipts,
        )

        self.assertEqual(unknown.status, TraversalStatus.UNKNOWN)
        self.assertEqual(unknown.unresolved_relation_refs, (uncertain_ref,))
        self.assertEqual(failed.status, TraversalStatus.FAIL)
        self.assertIn(uncertain_ref, failed.blocker_relation_refs)

    def test_open_epistemic_relation_cannot_self_report_pass(self) -> None:
        view = compile_relation_view(
            self.support_graph(open_middle_relation=True),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        bindings, receipts = status_evidence(view)

        receipt = check_reachability(
            view,
            policy=traversal_policy(
                view,
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                receipts=receipts,
            ),
            traversal_id="gravity-open-relation",
            start_refs=("component:roof",),
            target_refs=("component:foundation",),
            status_bindings=bindings,
            check_receipts=receipts,
        )

        self.assertEqual(receipt.status, TraversalStatus.UNKNOWN)
        self.assertEqual(
            receipt.unresolved_relation_refs,
            ("architectural-relation:beam-column",),
        )

    def test_relation_check_receipts_cannot_cross_branch(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        _, receipts = status_evidence(view)
        wrong_receipts = tuple(
            replace(
                item,
                branch=replace(item.branch, epoch=item.branch.epoch + 1),
            )
            for item in receipts
        )
        wrong_bindings = tuple(
            RelationStatusBinding.bind(relation_ref, receipt)
            for relation_ref, receipt in zip(
                view.relation_refs,
                wrong_receipts,
                strict=True,
            )
        )

        with self.assertRaises(RelationTraversalError):
            check_reachability(
                view,
                policy=traversal_policy(
                    view,
                    start_refs=("component:roof",),
                    target_refs=("component:foundation",),
                    receipts=receipts,
                ),
                traversal_id="cross-branch-checks",
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                status_bindings=wrong_bindings,
                check_receipts=wrong_receipts,
            )

    def test_traversal_rejects_checker_outside_bound_policy(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        _, receipts = status_evidence(view)
        unapproved_receipts = tuple(
            replace(item, checker_id="unapproved-agent-checker")
            for item in receipts
        )
        bindings = tuple(
            RelationStatusBinding.bind(relation_ref, receipt)
            for relation_ref, receipt in zip(
                view.relation_refs,
                unapproved_receipts,
                strict=True,
            )
        )

        with self.assertRaises(RelationTraversalError):
            check_reachability(
                view,
                policy=traversal_policy(
                    view,
                    start_refs=("component:roof",),
                    target_refs=("component:foundation",),
                    receipts=receipts,
                ),
                traversal_id="unapproved-checker",
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                status_bindings=bindings,
                check_receipts=unapproved_receipts,
            )

    def test_passing_check_with_revalidation_debt_stays_unknown(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        stale_ref = "architectural-relation:beam-column"
        _, receipts = status_evidence(view)
        stale_receipts = tuple(
            replace(item, revalidation_refs=(stale_ref,))
            if relation_ref == stale_ref
            else item
            for relation_ref, item in zip(
                view.relation_refs,
                receipts,
                strict=True,
            )
        )
        bindings = tuple(
            RelationStatusBinding.bind(relation_ref, receipt)
            for relation_ref, receipt in zip(
                view.relation_refs,
                stale_receipts,
                strict=True,
            )
        )

        receipt = check_reachability(
            view,
            policy=traversal_policy(
                view,
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                receipts=stale_receipts,
            ),
            traversal_id="stale-pass",
            start_refs=("component:roof",),
            target_refs=("component:foundation",),
            status_bindings=bindings,
            check_receipts=stale_receipts,
        )

        self.assertEqual(
            next(
                item.status
                for item in bindings
                if item.relation_ref == stale_ref
            ),
            RelationCheckStatus.UNKNOWN,
        )
        self.assertEqual(receipt.status, TraversalStatus.UNKNOWN)
        self.assertEqual(receipt.unresolved_relation_refs, (stale_ref,))

    def test_traversal_binds_exact_check_family_and_version(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        _, expected_receipts = status_evidence(view)
        unrelated_receipts = tuple(
            replace(
                item,
                check_id=f"unrelated-check-{index:04d}",
                checker_version="0.0.1",
            )
            for index, item in enumerate(expected_receipts)
        )
        bindings = tuple(
            RelationStatusBinding.bind(relation_ref, receipt)
            for relation_ref, receipt in zip(
                view.relation_refs,
                unrelated_receipts,
                strict=True,
            )
        )

        with self.assertRaises(RelationTraversalError):
            check_reachability(
                view,
                policy=traversal_policy(
                    view,
                    start_refs=("component:roof",),
                    target_refs=("component:foundation",),
                    receipts=expected_receipts,
                ),
                traversal_id="unrelated-check-family",
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                status_bindings=bindings,
                check_receipts=unrelated_receipts,
            )

    def test_zero_hop_and_endpoints_require_exact_policy_authority(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        failed_bindings, failed_receipts = status_evidence(
            view,
            {
                relation_ref: RelationCheckStatus.FAIL
                for relation_ref in view.relation_refs
            },
        )
        edge_required = traversal_policy(
            view,
            start_refs=("component:roof",),
            target_refs=("component:roof",),
            receipts=failed_receipts,
            minimum_hops=1,
        )
        failed = check_reachability(
            view,
            policy=edge_required,
            traversal_id="zero-hop-forbidden",
            start_refs=("component:roof",),
            target_refs=("component:roof",),
            status_bindings=failed_bindings,
            check_receipts=failed_receipts,
        )
        explicitly_allowed = check_reachability(
            view,
            policy=traversal_policy(
                view,
                start_refs=("component:roof",),
                target_refs=("component:roof",),
                receipts=failed_receipts,
                minimum_hops=0,
            ),
            traversal_id="zero-hop-authorized",
            start_refs=("component:roof",),
            target_refs=("component:roof",),
            status_bindings=failed_bindings,
            check_receipts=failed_receipts,
        )

        self.assertEqual(failed.status, TraversalStatus.FAIL)
        self.assertEqual(explicitly_allowed.status, TraversalStatus.PASS)
        self.assertFalse(explicitly_allowed.witnesses[0].relation_refs)
        with self.assertRaises(RelationTraversalError):
            check_reachability(
                view,
                policy=edge_required,
                traversal_id="endpoint-denominator-shrunk",
                start_refs=("component:beam",),
                target_refs=("component:roof",),
                status_bindings=failed_bindings,
                check_receipts=failed_receipts,
            )

    def test_status_denominator_is_exact_and_cycles_fail_closed(self) -> None:
        view = compile_relation_view(
            self.support_graph(),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        bindings, check_receipts = status_evidence(view)
        with self.assertRaises(RelationTraversalError):
            check_reachability(
                view,
                policy=traversal_policy(
                    view,
                    start_refs=("component:roof",),
                    target_refs=("component:foundation",),
                    receipts=check_receipts,
                ),
                traversal_id="missing-status",
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                status_bindings=bindings[:-1],
                check_receipts=check_receipts,
            )

        cyclic = compile_relation_view(
            self.support_graph(cycle=True),
            projection=RelationProjection.SUPPORT,
            scenario_ref="loadcase:gravity",
        )
        cyclic_bindings, cyclic_receipts = status_evidence(cyclic)
        receipt = check_reachability(
            cyclic,
            policy=traversal_policy(
                cyclic,
                start_refs=("component:roof",),
                target_refs=("component:foundation",),
                receipts=cyclic_receipts,
            ),
            traversal_id="cyclic-support",
            start_refs=("component:roof",),
            target_refs=("component:foundation",),
            status_bindings=cyclic_bindings,
            check_receipts=cyclic_receipts,
        )
        self.assertEqual(receipt.status, TraversalStatus.FAIL)
        self.assertTrue(receipt.reject_cycles)
        self.assertTrue(receipt.cycle_node_groups)

    def test_support_adapter_preserves_supported_supporter_order(self) -> None:
        requirements = compile_support_requirements(
            self.support_graph(),
            scenario_ref="loadcase:gravity",
        )

        self.assertEqual(len(requirements), 3)
        self.assertTrue(
            all(item.kind is RelationshipKind.SUPPORT for item in requirements)
        )
        by_id = {item.requirement_id: item for item in requirements}
        self.assertEqual(
            by_id["beam-column-0000"].subject_refs,
            ("component:beam", "component:column"),
        )


class ArchitecturalCoverageTests(unittest.TestCase):
    def policy(self) -> SemanticKindRelationPolicy:
        return SemanticKindRelationPolicy(
            policy_id="column-relations",
            stage_id="stage-4",
            rules=(
                SemanticRelationRule(
                    rule_id="supported-below",
                    node_kind=ArchitecturalNodeKind.COMPONENT,
                    semantic_kind="structural-column",
                    relation_kind=ArchitecturalRelationKind.SUPPORT,
                    subject_role="supported",
                    counted_role="supporter",
                    minimum_count=1,
                    maximum_count=1,
                    scenario_ref=UNIVERSAL,
                    evidence_refs=("evidence:column-policy",),
                    authority_refs=("authority:building-rule",),
                ),
                SemanticRelationRule(
                    rule_id="supports-above",
                    node_kind=ArchitecturalNodeKind.COMPONENT,
                    semantic_kind="structural-column",
                    relation_kind=ArchitecturalRelationKind.SUPPORT,
                    subject_role="supporter",
                    counted_role="supported",
                    minimum_count=1,
                    maximum_count=None,
                    scenario_ref=UNIVERSAL,
                    evidence_refs=("evidence:column-policy",),
                    authority_refs=("authority:building-rule",),
                    allow_not_applicable=True,
                ),
            ),
            source_refs=("policy-source:structural-role",),
        )

    def test_policy_cannot_create_a_relation_role_outside_kind_schema(
        self,
    ) -> None:
        with self.assertRaises(RelationCoverageError):
            SemanticRelationRule(
                rule_id="hidden-support-role",
                node_kind=ArchitecturalNodeKind.COMPONENT,
                semantic_kind="structural-column",
                relation_kind=ArchitecturalRelationKind.SUPPORT,
                subject_role="foo",
                counted_role="supporter",
                minimum_count=1,
                maximum_count=1,
                scenario_ref=UNIVERSAL,
                evidence_refs=("evidence:hidden-role",),
                authority_refs=("authority:hidden-role",),
            )

    def column_graph(
        self,
        *,
        include_above: bool,
        duplicate_below: bool = False,
    ) -> ArchitecturalRelationGraph:
        nodes = [
            node("component:foundation", "foundation"),
            node("component:column", "structural-column"),
            node("component:beam", "beam"),
        ]
        relations = [
            relation(
                "column-foundation",
                ArchitecturalRelationKind.SUPPORT,
                (("supported", "component:column"), ("supporter", "component:foundation")),
            )
        ]
        if include_above:
            relations.append(
                relation(
                    "beam-column",
                    ArchitecturalRelationKind.SUPPORT,
                    (("supported", "component:beam"), ("supporter", "component:column")),
                )
            )
        if duplicate_below:
            nodes.append(node("component:foundation-b", "foundation"))
            relations.append(
                relation(
                    "column-foundation-b",
                    ArchitecturalRelationKind.SUPPORT,
                    (("supported", "component:column"), ("supporter", "component:foundation-b")),
                )
            )
        return graph(tuple(nodes), tuple(relations))

    def bound_column_context(
        self,
        *,
        include_above: bool,
    ) -> tuple[ArchitecturalRelationGraph, StageSubjectInventory]:
        inventory = stage_inventory(
            (
                ("building", "building", None),
                ("foundation", "foundation", "building"),
                ("column", "structural-column", "building"),
                ("beam", "beam", "building"),
            )
        )
        entries = {item.component_id: item for item in inventory.entries}
        relations = [
            relation(
                "column-foundation-bound",
                ArchitecturalRelationKind.SUPPORT,
                (
                    ("supported", entries["column"].identity_ref),
                    ("supporter", entries["foundation"].identity_ref),
                ),
            )
        ]
        if include_above:
            relations.append(
                relation(
                    "beam-column-bound",
                    ArchitecturalRelationKind.SUPPORT,
                    (
                        ("supported", entries["beam"].identity_ref),
                        ("supporter", entries["column"].identity_ref),
                    ),
                )
            )
        return (
            ArchitecturalRelationGraph(
                graph_id="bound-column-graph",
                branch=inventory.branch,
                stage_id=inventory.stage_id,
                state_digest=SHA_A,
                scope_digest=SHA_A,
                stage_subject_digest=inventory.stage_subject_digest,
                subject_inventory_digest=inventory.inventory_digest,
                nodes=tuple(inventory_node(item) for item in inventory.entries),
                relations=tuple(relations),
            ),
            inventory,
        )

    def test_semantic_policy_mechanically_closes_complete_column_roles(self) -> None:
        source = self.column_graph(include_above=True)
        policy = self.policy()
        slots = compile_requirement_slots(source, policy)

        manifest = compile_graph_coverage(source, policy, slots)

        self.assertEqual(len(slots), 2)
        self.assertTrue(manifest.closure_ready)
        self.assertEqual(
            {item.status for item in manifest.dispositions},
            {RelationCoverageStatus.SATISFIED},
        )
        self.assertEqual(
            GraphCoverageManifest.from_dict(manifest.to_dict()),
            manifest,
        )

    def test_missing_relation_is_unknown_until_independently_waived(self) -> None:
        source = self.column_graph(include_above=False)
        policy = self.policy()
        slots = compile_requirement_slots(source, policy)
        open_manifest = compile_graph_coverage(source, policy, slots)
        missing = next(item for item in slots if item.rule_id == "supports-above")

        waived = compile_graph_coverage(
            source,
            policy,
            slots,
            (
                RelationNotApplicable(
                    slot_ref=missing.slot_ref,
                    reason="Terminal element is explicitly outside this support scenario.",
                    evidence_refs=("evidence:terminal-role",),
                    authority_refs=("authority:structural-review",),
                ),
            ),
        )

        self.assertFalse(open_manifest.closure_ready)
        self.assertIn(
            RelationCoverageStatus.UNKNOWN,
            {item.status for item in open_manifest.dispositions},
        )
        self.assertTrue(waived.closure_ready)
        self.assertIn(
            RelationCoverageStatus.NOT_APPLICABLE,
            {item.status for item in waived.dispositions},
        )

    def test_not_applicable_requires_explicit_slot_policy(self) -> None:
        source = self.column_graph(include_above=False)
        policy = self.policy()
        forbidden = replace(
            policy,
            rules=tuple(
                replace(item, allow_not_applicable=False)
                for item in policy.rules
            ),
        )
        slots = compile_requirement_slots(source, forbidden)
        missing = next(
            item for item in slots if item.rule_id == "supports-above"
        )

        with self.assertRaises(RelationCoverageError):
            compile_graph_coverage(
                source,
                forbidden,
                slots,
                (
                    RelationNotApplicable(
                        slot_ref=missing.slot_ref,
                        reason="Unauthorized attempt to waive a required edge.",
                        evidence_refs=("evidence:terminal-role",),
                        authority_refs=("authority:structural-review",),
                    ),
                ),
            )

    def test_excess_cardinality_is_violated_and_cannot_be_waived(self) -> None:
        source = self.column_graph(include_above=True, duplicate_below=True)
        policy = self.policy()
        slots = compile_requirement_slots(source, policy)
        manifest = compile_graph_coverage(source, policy, slots)
        violated = next(item for item in manifest.dispositions if item.status is RelationCoverageStatus.VIOLATED)

        self.assertFalse(manifest.closure_ready)
        self.assertEqual(violated.matched_count, 2)
        with self.assertRaises(RelationCoverageError):
            compile_graph_coverage(
                source,
                policy,
                slots,
                (
                    RelationNotApplicable(
                        slot_ref=violated.slot_ref,
                        reason="Invalid attempt to waive an existing over-count.",
                        evidence_refs=("evidence:waiver",),
                        authority_refs=("authority:review",),
                    ),
                ),
            )

    def test_hyperrelation_counts_exact_counterpart_incidences(self) -> None:
        source = graph(
            (
                node("component:column", "structural-column"),
                node("component:foundation-a", "foundation"),
                node("component:foundation-b", "foundation"),
            ),
            (
                relation(
                    "column-two-foundations",
                    ArchitecturalRelationKind.SUPPORT,
                    (
                        ("supported", "component:column"),
                        ("supporter", "component:foundation-a"),
                        ("supporter", "component:foundation-b"),
                    ),
                ),
            ),
        )
        policy = SemanticKindRelationPolicy(
            policy_id="single-support-incidence",
            stage_id="stage-4",
            rules=(
                SemanticRelationRule(
                    rule_id="one-supporter",
                    node_kind=ArchitecturalNodeKind.COMPONENT,
                    semantic_kind="structural-column",
                    relation_kind=ArchitecturalRelationKind.SUPPORT,
                    subject_role="supported",
                    counted_role="supporter",
                    minimum_count=1,
                    maximum_count=1,
                    scenario_ref=UNIVERSAL,
                    evidence_refs=("evidence:single-support",),
                    authority_refs=("authority:single-support",),
                ),
            ),
            source_refs=("policy-source:single-support",),
        )

        manifest = compile_graph_coverage(
            source,
            policy,
            compile_requirement_slots(source, policy),
        )
        disposition = manifest.dispositions[0]

        self.assertEqual(disposition.status, RelationCoverageStatus.VIOLATED)
        self.assertEqual(disposition.matched_count, 2)
        self.assertEqual(len(disposition.relation_refs), 1)
        self.assertEqual(len(disposition.match_refs), 2)
        self.assertEqual(
            len(
                compile_support_requirements(
                    source,
                    scenario_ref="loadcase:gravity",
                )
            ),
            2,
        )

    def test_open_epistemic_relation_does_not_close_required_slot(self) -> None:
        source = self.column_graph(include_above=True)
        below = next(
            item
            for item in source.relations
            if item.relation_id == "column-foundation"
        )
        hypothesis = replace(
            below,
            epistemic_status=RelationEpistemicStatus.HYPOTHESIS,
        )
        source = replace(
            source,
            relations=tuple(
                hypothesis if item.relation_id == below.relation_id else item
                for item in source.relations
            ),
        )
        policy = self.policy()
        slots = compile_requirement_slots(source, policy)

        manifest = compile_graph_coverage(source, policy, slots)

        self.assertFalse(manifest.closure_ready)
        self.assertIn(
            RelationCoverageStatus.UNKNOWN,
            {item.status for item in manifest.dispositions},
        )

    def test_universal_relation_satisfies_specific_scenario_slot(self) -> None:
        source = self.column_graph(include_above=True)
        policy = SemanticKindRelationPolicy(
            policy_id="gravity-column-relations",
            stage_id="stage-4",
            rules=(
                SemanticRelationRule(
                    rule_id="gravity-supported-below",
                    node_kind=ArchitecturalNodeKind.COMPONENT,
                    semantic_kind="structural-column",
                    relation_kind=ArchitecturalRelationKind.SUPPORT,
                    subject_role="supported",
                    counted_role="supporter",
                    minimum_count=1,
                    maximum_count=1,
                    scenario_ref="loadcase:gravity",
                    evidence_refs=("evidence:gravity-policy",),
                    authority_refs=("authority:building-rule",),
                ),
            ),
            source_refs=("policy-source:structural-role",),
        )
        slots = compile_requirement_slots(source, policy)

        manifest = compile_graph_coverage(source, policy, slots)

        self.assertTrue(manifest.closure_ready)

    def test_complete_relation_denominator_satisfies_generic_stage_closure(
        self,
    ) -> None:
        source, inventory = self.bound_column_context(include_above=True)
        policy = self.policy()
        slots = compile_requirement_slots(source, policy)
        requirement = relation_coverage_stage_requirement(
            source,
            policy,
            inventory,
        )

        manifest, receipt = check_relation_coverage(
            source,
            policy,
            inventory,
            slots,
        )
        profile = StageRequirementProfile(
            profile_id="relation-stage-closure",
            typology_id="generic-building",
            stage_id=source.stage_id,
            branch=source.branch,
            predecessor_state_digest=source.state_digest,
            scope_digest=SHA_A,
            stage_subject_ref=inventory.stage_subject_ref,
            requirements=(requirement,),
        )
        closure = compile_composite_stage_closure(
            profile,
            subject_digest=inventory.stage_subject_digest,
            check_receipts=(receipt,),
        )

        self.assertTrue(manifest.closure_ready)
        self.assertEqual(receipt.status, CheckStatus.PASS)
        self.assertIn(source.ref, receipt.source_refs)
        self.assertIn(policy.ref, receipt.source_refs)
        self.assertIn(manifest.ref, receipt.source_refs)
        self.assertFalse(requirement.allow_not_applicable)
        self.assertNotEqual(
            inventory.inventory_digest,
            inventory.stage_subject_digest,
        )
        self.assertEqual(
            receipt.subject_digest,
            inventory.stage_subject_digest,
        )
        self.assertEqual(closure.status, StageClosureStatus.SATISFIED)

    def test_stage_closure_rejects_graph_that_shrinks_exact_inventory(self) -> None:
        source, inventory = self.bound_column_context(include_above=False)
        shrunk = replace(
            source,
            nodes=tuple(
                item
                for item in source.nodes
                if item.node_ref != "design-component:beam"
            ),
        )

        with self.assertRaises(RelationCoverageError):
            relation_coverage_stage_requirement(
                shrunk,
                self.policy(),
                inventory,
            )

    def test_stage_closure_rejects_inventory_node_kind_masquerade(self) -> None:
        source, inventory = self.bound_column_context(include_above=True)
        masqueraded = replace(
            source,
            nodes=tuple(
                replace(item, node_kind=ArchitecturalNodeKind.SPACE)
                if item.node_ref == "design-component:column"
                else item
                for item in source.nodes
            ),
        )

        with self.assertRaises(RelationCoverageError):
            relation_coverage_stage_requirement(
                masqueraded,
                self.policy(),
                inventory,
            )

    def test_stage_policy_cannot_name_an_uninventoried_node_kind(self) -> None:
        source, inventory = self.bound_column_context(include_above=True)
        unbound_policy = SemanticKindRelationPolicy(
            policy_id="space-relations-without-space-inventory",
            stage_id="stage-4",
            rules=(
                SemanticRelationRule(
                    rule_id="space-access",
                    node_kind=ArchitecturalNodeKind.SPACE,
                    semantic_kind="principal-space",
                    relation_kind=ArchitecturalRelationKind.ACCESS,
                    subject_role="from",
                    counted_role="to",
                    minimum_count=1,
                    maximum_count=None,
                    scenario_ref=UNIVERSAL,
                    evidence_refs=("evidence:space-policy",),
                    authority_refs=("authority:space-policy",),
                ),
            ),
            source_refs=("policy-source:space-relations",),
        )

        with self.assertRaises(RelationCoverageError):
            relation_coverage_stage_requirement(
                source,
                unbound_policy,
                inventory,
            )

    def test_missing_required_relation_keeps_stage_open(self) -> None:
        source, inventory = self.bound_column_context(include_above=False)
        policy = self.policy()
        slots = compile_requirement_slots(source, policy)
        requirement = relation_coverage_stage_requirement(
            source,
            policy,
            inventory,
        )
        _, receipt = check_relation_coverage(
            source,
            policy,
            inventory,
            slots,
        )
        profile = StageRequirementProfile(
            profile_id="relation-stage-open",
            typology_id="generic-building",
            stage_id=source.stage_id,
            branch=source.branch,
            predecessor_state_digest=source.state_digest,
            scope_digest=SHA_A,
            stage_subject_ref=inventory.stage_subject_ref,
            requirements=(requirement,),
        )

        closure = compile_composite_stage_closure(
            profile,
            subject_digest=inventory.stage_subject_digest,
            check_receipts=(receipt,),
        )

        self.assertEqual(receipt.status, CheckStatus.UNKNOWN)
        self.assertEqual(closure.status, StageClosureStatus.OPEN)


class ArchitecturalAccessAndLineageTests(unittest.TestCase):
    def test_access_projection_is_scenario_bound(self) -> None:
        exterior = node(
            "space:exterior",
            "exterior",
            kind=ArchitecturalNodeKind.SPACE,
        )
        interior = node(
            "space:interior",
            "interior",
            kind=ArchitecturalNodeKind.SPACE,
        )
        opening = node(
            "opening:entry",
            "entry-opening",
            kind=ArchitecturalNodeKind.OPENING,
        )
        passage = relation(
            "entry-passage-open",
            ArchitecturalRelationKind.ALLOWS_PASSAGE,
            (("from", exterior.node_ref), ("via", opening.node_ref), ("to", interior.node_ref)),
            scenario_ref=OPEN_DOOR,
        )
        source = graph((exterior, interior, opening), (passage,))

        open_view = compile_relation_view(
            source,
            projection=RelationProjection.ACCESS,
            scenario_ref=OPEN_DOOR,
        )
        closed_view = compile_relation_view(
            source,
            projection=RelationProjection.ACCESS,
            scenario_ref="scenario:door-closed",
        )

        self.assertEqual(len(open_view.arcs), 2)
        self.assertFalse(closed_view.arcs)

    def test_access_cycle_is_allowed_by_projection_policy(self) -> None:
        exterior = node(
            "space:exterior",
            "exterior",
            kind=ArchitecturalNodeKind.SPACE,
        )
        interior = node(
            "space:interior",
            "interior",
            kind=ArchitecturalNodeKind.SPACE,
        )
        source = graph(
            (exterior, interior),
            (
                relation(
                    "entry-in",
                    ArchitecturalRelationKind.ALLOWS_PASSAGE,
                    (("from", exterior.node_ref), ("to", interior.node_ref)),
                    scenario_ref=OPEN_DOOR,
                ),
                relation(
                    "entry-out",
                    ArchitecturalRelationKind.ALLOWS_PASSAGE,
                    (("from", interior.node_ref), ("to", exterior.node_ref)),
                    scenario_ref=OPEN_DOOR,
                ),
            ),
        )
        view = compile_relation_view(
            source,
            projection=RelationProjection.ACCESS,
            scenario_ref=OPEN_DOOR,
        )
        bindings, receipts = status_evidence(view)

        traversal = check_reachability(
            view,
            policy=traversal_policy(
                view,
                start_refs=(exterior.node_ref,),
                target_refs=(interior.node_ref,),
                receipts=receipts,
            ),
            traversal_id="round-trip-access",
            start_refs=(exterior.node_ref,),
            target_refs=(interior.node_ref,),
            status_bindings=bindings,
            check_receipts=receipts,
        )

        self.assertEqual(traversal.status, TraversalStatus.PASS)
        self.assertFalse(traversal.reject_cycles)
        self.assertTrue(traversal.cycle_node_groups)

    def test_lineage_projection_is_current_to_exact_predecessor(self) -> None:
        predecessor = node(
            "component:column-stage-3",
            "column",
            stage_id="stage-3",
        )
        current = node(
            "component:column-stage-4",
            "column",
            predecessor_ref=predecessor.node_ref,
        )
        refinement = relation(
            "column-refinement",
            ArchitecturalRelationKind.REFINES,
            (("current", current.node_ref), ("predecessor", predecessor.node_ref)),
            predecessor_relation_ref="architectural-relation:column-stage-3",
        )
        view = compile_relation_view(
            graph((predecessor, current), (refinement,)),
            projection=RelationProjection.LINEAGE,
            scenario_ref=UNIVERSAL,
        )

        self.assertEqual(
            [(item.source_ref, item.target_ref) for item in view.arcs],
            [(current.node_ref, predecessor.node_ref)],
        )


if __name__ == "__main__":
    unittest.main()
