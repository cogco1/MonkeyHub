"""Exact architectural relation to geometry/readback realization tests."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archive.archflow.control.check_requirements import (
    relation_realization_stage_requirement,
)
from archive.archflow.control.requirements import StageRequirementProfile
from archflow.state.stage_workflow import StageClosureStatus
from archive.archflow.control.stage_closure import compile_composite_stage_closure
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
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
    RelationEndpointObjectBinding,
    RelationEndpointPairing,
    RelationObjectPath,
    RelationRealizationManifest,
    RelationRealizationPurpose,
    RelationVerificationBinding,
)
from archflow.compilers.geometry import (
    CompiledGeometryObject,
    CompiledGeometryProgram,
)
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
from archive.archflow.validation.cad_readback import (
    CadObjectReadback,
    CadReadbackSnapshot,
)
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.relation_realization import (
    check_relation_realization,
)


SHA_BASE = "a" * 64
SHA_STATE = "b" * 64
SHA_SCOPE = "c" * 64
SHA_STAGE = "d" * 64


def branch(branch_id: str = "selected") -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="relation-realization-fixture",
            run_id="run-001",
            base=ProjectVersionRef(
                "relation-realization-fixture",
                3,
                SHA_BASE,
            ),
        ),
        branch_id=branch_id,
        epoch=2,
    )


def node(component: str) -> ArchitecturalNode:
    return ArchitecturalNode(
        node_ref=f"component:{component}",
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind="generic_component",
        stage_id="stage-a",
        source_refs=(f"stage-entry:{component}",),
    )


def relation(
    relation_id: str,
    kind: ArchitecturalRelationKind,
    roles: tuple[tuple[str, str], ...],
) -> ArchitecturalRelation:
    return ArchitecturalRelation(
        relation_id=relation_id,
        kind=kind,
        participants=tuple(
            RelationParticipant(role=role, node_ref=f"component:{component}")
            for role, component in roles
        ),
        scenario_ref="scenario:universal",
        epistemic_status=RelationEpistemicStatus.OBSERVED,
        source_refs=(f"record:{relation_id}",),
        evidence_refs=(f"evidence:{relation_id}",),
        authority_refs=("authority:relation-policy",),
        propagation_rules=(),
        source_stage_id="stage-a",
    )


def graph(*, include_access: bool = False) -> ArchitecturalRelationGraph:
    relations = [
        relation(
            "support",
            ArchitecturalRelationKind.SUPPORT,
            (("supported", "a"), ("supporter", "b")),
        )
    ]
    if include_access:
        relations.append(
            relation(
                "walking",
                ArchitecturalRelationKind.ACCESS,
                (("from", "a"), ("to", "b")),
            )
        )
    return ArchitecturalRelationGraph(
        graph_id="realization-graph",
        branch=branch(),
        stage_id="stage-a",
        state_digest=SHA_STATE,
        scope_digest=SHA_SCOPE,
        stage_subject_digest=SHA_STAGE,
        subject_inventory_digest="e" * 64,
        nodes=(node("a"), node("b")),
        relations=tuple(relations),
    )


def single_relation_graph(
    kind: ArchitecturalRelationKind,
    roles: tuple[str, str],
) -> ArchitecturalRelationGraph:
    selected_relation = relation(
        kind.value,
        kind,
        ((roles[0], "a"), (roles[1], "b")),
    )
    return ArchitecturalRelationGraph(
        graph_id=f"{kind.value}-realization-graph",
        branch=branch(),
        stage_id="stage-a",
        state_digest=SHA_STATE,
        scope_digest=SHA_SCOPE,
        stage_subject_digest=SHA_STAGE,
        subject_inventory_digest="e" * 64,
        nodes=(node("a"), node("b")),
        relations=(selected_relation,),
    )


def compiled_program(
    *,
    objects_per_endpoint: int = 1,
) -> CompiledGeometryProgram:
    object_specs = tuple(
        (component, f"object-{component}-{index}")
        for component in ("a", "b")
        for index in range(objects_per_endpoint)
    )
    operations = tuple(
        sorted(
            (
                GeometryOperation(
                    op_id=f"op-{object_id}",
                    kind=GeometryOperationKind.SOLID,
                    output_object_ids=(object_id,),
                    input_object_ids=(),
                    frame_id="world",
                    parameters=(),
                    semantic_binding_ids=(f"binding-{component}",),
                )
                for component, object_id in object_specs
            ),
            key=lambda item: item.op_id,
        )
    )
    semantics = tuple(
        SemanticBinding(
            binding_id=f"binding-{component}",
            component_id=component,
            object_ids=tuple(
                object_id
                for selected, object_id in object_specs
                if selected == component
            ),
            commitment_refs=(),
            evidence_refs=(f"evidence:component-{component}",),
        )
        for component in ("a", "b")
    )
    proposal = GeometryProgramProposal(
        proposal_id="relation-realization-program",
        project_id=branch().run.project_id,
        run_id=branch().run.run_id,
        base=branch().run.base,
        design_state_digest=SHA_STATE,
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
        semantic_bindings=semantics,
        operations=operations,
        assemblies=(),
    )
    return CompiledGeometryProgram(
        proposal=proposal,
        operation_order=tuple(item.op_id for item in operations),
        frame_digests=(("world", "1" * 64),),
        component_digests=(),
        semantic_binding_digests=(
            ("binding-a", "2" * 64),
            ("binding-b", "3" * 64),
        ),
        objects=tuple(
            CompiledGeometryObject(
                object_id=object_id,
                producer_op_id=f"op-{object_id}",
                object_digest=("4" if component == "a" else "5") * 64,
            )
            for component, object_id in sorted(
                object_specs,
                key=lambda item: item[1],
            )
        ),
        asset_substitutions=(),
    )


def readback(
    program: CompiledGeometryProgram,
    *,
    selected_branch: BranchRef | None = None,
    duplicate_first: bool = False,
) -> CadReadbackSnapshot:
    objects = tuple(
        CadObjectReadback(
            object_ref=f"cad-object:{item.object_id}",
            operation_ref=f"cad-operation:{item.producer_op_id}",
            layer_ref=None,
            attributes=(),
        )
        for item in program.objects
    )
    if duplicate_first:
        objects = (*objects, objects[0])
    return CadReadbackSnapshot(
        project_id=branch().run.project_id,
        branch=selected_branch or branch(),
        stage_id="stage-a",
        profile_digest="6" * 64,
        program_digest=program.program_digest,
        length_unit="meter",
        up_axis=None,
        declared_layer_refs=(),
        operation_refs=tuple(
            f"cad-operation:{item.producer_op_id}" for item in program.objects
        ),
        objects=objects,
    )


def endpoint_bindings(
    selected_graph: ArchitecturalRelationGraph,
    program: CompiledGeometryProgram,
) -> tuple[RelationEndpointObjectBinding, ...]:
    rows = []
    for selected_relation in selected_graph.relations:
        for participant in selected_relation.participants:
            component = participant.node_ref.split(":", 1)[1]
            for compiled_object in program.objects:
                if not compiled_object.object_id.startswith(f"object-{component}-"):
                    continue
                rows.append(
                    RelationEndpointObjectBinding(
                        binding_id=(
                            f"{selected_relation.relation_id}-"
                            f"{compiled_object.object_id}"
                        ),
                        relation_ref=selected_relation.ref,
                        participant_digest=participant.participant_digest,
                        role=participant.role,
                        node_ref=participant.node_ref,
                        ordinal=participant.ordinal,
                        semantic_binding_id=f"binding-{component}",
                        program_object_id=compiled_object.object_id,
                        program_object_digest=compiled_object.object_digest,
                        producer_operation_id=compiled_object.producer_op_id,
                        readback_object_ref=(
                            f"cad-object:{compiled_object.object_id}"
                        ),
                        readback_operation_ref=(
                            f"cad-operation:{compiled_object.producer_op_id}"
                        ),
                    )
                )
    return tuple(rows)


def verification_receipt(
    selected_graph: ArchitecturalRelationGraph,
    selected_relation: ArchitecturalRelation,
    *,
    checker_id: str,
    owner: RelationEndpointPairing | RelationObjectPath,
) -> CheckReceiptEnvelope:
    subject_refs = verification_subject_refs(owner)
    return CheckReceiptEnvelope(
        check_id=f"verify-{selected_relation.relation_id}",
        checker_id=checker_id,
        checker_version="1.0.0",
        branch=selected_graph.branch,
        scope_digest=selected_graph.scope_digest,
        subject_refs=subject_refs,
        subject_digest=selected_graph.stage_subject_digest,
        status=CheckStatus.PASS,
        coverage_denominator=subject_refs,
        covered_refs=subject_refs,
    )


def verification_subject_refs(
    owner: RelationEndpointPairing | RelationObjectPath,
) -> tuple[str, ...]:
    topology_owner = replace(owner, verification=None)
    if isinstance(owner, RelationEndpointPairing):
        refs = (
            owner.relation_ref,
            topology_owner.ref,
            owner.first_binding_ref,
            owner.second_binding_ref,
        )
    else:
        refs = (
            owner.relation_ref,
            topology_owner.ref,
            *owner.endpoint_binding_refs,
            *owner.pairing_refs,
        )
    return tuple(sorted(set(refs)))


def verification_binding(
    receipt: CheckReceiptEnvelope,
    purpose: RelationRealizationPurpose,
) -> RelationVerificationBinding:
    return RelationVerificationBinding(
        purpose=purpose,
        checker_id=receipt.checker_id,
        receipt_digest=receipt.receipt_digest,
        subject_refs=receipt.subject_refs,
    )


def manifest(
    selected_graph: ArchitecturalRelationGraph,
    program: CompiledGeometryProgram,
    snapshot: CadReadbackSnapshot,
    bindings: tuple[RelationEndpointObjectBinding, ...],
    *,
    pairings: tuple[RelationEndpointPairing, ...],
    paths: tuple[RelationObjectPath, ...] = (),
) -> RelationRealizationManifest:
    return RelationRealizationManifest(
        manifest_id="stage-a-relations",
        branch=selected_graph.branch,
        stage_id=selected_graph.stage_id,
        scope_digest=selected_graph.scope_digest,
        relation_graph_digest=selected_graph.graph_digest,
        program_digest=program.program_digest,
        readback_digest=snapshot.snapshot_digest,
        stage_subject_digest=selected_graph.stage_subject_digest,
        endpoint_bindings=bindings,
        pairings=pairings,
        paths=paths,
    )


def relation_bindings(
    bindings: tuple[RelationEndpointObjectBinding, ...],
    relation_ref: str,
) -> tuple[RelationEndpointObjectBinding, ...]:
    return tuple(item for item in bindings if item.relation_ref == relation_ref)


class RelationRealizationTests(unittest.TestCase):
    def test_every_special_relation_kind_requires_its_exact_purpose(self) -> None:
        cases = (
            (
                ArchitecturalRelationKind.INTERFACE,
                ("first", "second"),
                RelationRealizationPurpose.CONTACT_INTERFACE,
            ),
            (
                ArchitecturalRelationKind.INTERSECTS,
                ("first", "second"),
                RelationRealizationPurpose.CONTACT_INTERFACE,
            ),
            (
                ArchitecturalRelationKind.HOST,
                ("hosted", "host"),
                RelationRealizationPurpose.HOST_INTERFACE,
            ),
            (
                ArchitecturalRelationKind.HOSTS_VOID,
                ("host", "void"),
                RelationRealizationPurpose.HOST_INTERFACE,
            ),
            (
                ArchitecturalRelationKind.FILLS_VOID,
                ("void", "fill"),
                RelationRealizationPurpose.HOST_INTERFACE,
            ),
            (
                ArchitecturalRelationKind.SUPPORT,
                ("supported", "supporter"),
                RelationRealizationPurpose.SUPPORT_CHAIN,
            ),
            (
                ArchitecturalRelationKind.LOAD_TRANSFER,
                ("sender", "receiver"),
                RelationRealizationPurpose.LOAD_PATH,
            ),
            (
                ArchitecturalRelationKind.ACCESS,
                ("from", "to"),
                RelationRealizationPurpose.WALKING_PATH,
            ),
            (
                ArchitecturalRelationKind.ALLOWS_PASSAGE,
                ("from", "to"),
                RelationRealizationPurpose.WALKING_PATH,
            ),
            (
                ArchitecturalRelationKind.ADJACENT,
                ("first", "second"),
                RelationRealizationPurpose.GENERIC_PAIR,
            ),
        )
        program = compiled_program()
        snapshot = readback(program)

        for kind, roles, purpose in cases:
            with self.subTest(kind=kind.value):
                selected_graph = single_relation_graph(kind, roles)
                selected_relation = selected_graph.relations[0]
                bindings = endpoint_bindings(selected_graph, program)
                unsigned_pairing = RelationEndpointPairing(
                    pairing_id=kind.value,
                    relation_ref=selected_relation.ref,
                    first_binding_ref=bindings[0].ref,
                    second_binding_ref=bindings[1].ref,
                )
                receipt = verification_receipt(
                    selected_graph,
                    selected_relation,
                    checker_id=f"{kind.value}-narrow-phase-checker",
                    owner=unsigned_pairing,
                )
                pairing = replace(
                    unsigned_pairing,
                    verification=verification_binding(receipt, purpose),
                )
                selected_manifest = manifest(
                    selected_graph,
                    program,
                    snapshot,
                    bindings,
                    pairings=(pairing,),
                )

                check = check_relation_realization(
                    selected_graph,
                    selected_manifest,
                    program,
                    snapshot,
                    verification_receipts=(receipt,),
                )

                self.assertIs(check.status, CheckStatus.PASS)

                wrong_purpose = (
                    RelationRealizationPurpose.CONTACT_INTERFACE
                    if purpose is RelationRealizationPurpose.GENERIC_PAIR
                    else RelationRealizationPurpose.GENERIC_PAIR
                )
                wrong_manifest = replace(
                    selected_manifest,
                    pairings=(
                        replace(
                            unsigned_pairing,
                            verification=verification_binding(
                                receipt,
                                wrong_purpose,
                            ),
                        ),
                    ),
                )
                wrong = check_relation_realization(
                    selected_graph,
                    wrong_manifest,
                    program,
                    snapshot,
                    verification_receipts=(receipt,),
                )
                self.assertIs(wrong.status, CheckStatus.FAIL)
                self.assertIn(
                    "relation-verification-purpose-incompatible",
                    {item.code for item in wrong.findings},
                )

    def test_verification_for_one_pair_cannot_cover_another_pair(self) -> None:
        selected_graph = graph()
        program = compiled_program(objects_per_endpoint=2)
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        support = selected_graph.relations[0]
        support_bindings = relation_bindings(bindings, support.ref)
        by_component = {
            component: tuple(
                item
                for item in support_bindings
                if item.node_ref == f"component:{component}"
            )
            for component in ("a", "b")
        }
        unsigned_pairings = tuple(
            RelationEndpointPairing(
                pairing_id=f"support-{index}",
                relation_ref=support.ref,
                first_binding_ref=by_component["a"][index].ref,
                second_binding_ref=by_component["b"][index].ref,
            )
            for index in range(2)
        )
        first_receipt = verification_receipt(
            selected_graph,
            support,
            checker_id="support-narrow-phase-checker",
            owner=unsigned_pairings[0],
        )
        reused_verification = verification_binding(
            first_receipt,
            RelationRealizationPurpose.SUPPORT_CHAIN,
        )
        reused_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=tuple(
                replace(pairing, verification=reused_verification)
                for pairing in unsigned_pairings
            ),
        )

        reused = check_relation_realization(
            selected_graph,
            reused_manifest,
            program,
            snapshot,
            verification_receipts=(first_receipt,),
        )

        self.assertIs(reused.status, CheckStatus.FAIL)
        self.assertIn(
            "relation-verification-subject-unbound",
            {item.code for item in reused.findings},
        )

        second_receipt = verification_receipt(
            selected_graph,
            support,
            checker_id="support-narrow-phase-checker",
            owner=unsigned_pairings[1],
        )
        exact_manifest = replace(
            reused_manifest,
            pairings=(
                replace(
                    unsigned_pairings[0],
                    verification=verification_binding(
                        first_receipt,
                        RelationRealizationPurpose.SUPPORT_CHAIN,
                    ),
                ),
                replace(
                    unsigned_pairings[1],
                    verification=verification_binding(
                        second_receipt,
                        RelationRealizationPurpose.SUPPORT_CHAIN,
                    ),
                ),
            ),
        )
        exact = check_relation_realization(
            selected_graph,
            exact_manifest,
            program,
            snapshot,
            verification_receipts=(first_receipt, second_receipt),
        )
        self.assertIs(exact.status, CheckStatus.PASS)

    def test_path_verification_requires_owner_bindings_and_pairings(self) -> None:
        selected_graph = single_relation_graph(
            ArchitecturalRelationKind.ACCESS,
            ("from", "to"),
        )
        program = compiled_program()
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        walking = selected_graph.relations[0]
        pairing = RelationEndpointPairing(
            pairing_id="walking-hop",
            relation_ref=walking.ref,
            first_binding_ref=bindings[0].ref,
            second_binding_ref=bindings[1].ref,
        )
        unsigned_path = RelationObjectPath(
            path_id="walking-path",
            relation_ref=walking.ref,
            endpoint_binding_refs=(
                pairing.first_binding_ref,
                pairing.second_binding_ref,
            ),
            pairing_refs=(pairing.ref,),
            purpose=RelationRealizationPurpose.WALKING_PATH,
        )
        relation_only_receipt = CheckReceiptEnvelope(
            check_id="verify-walking-relation-only",
            checker_id="walking-surface-continuity-checker",
            checker_version="1.0.0",
            branch=selected_graph.branch,
            scope_digest=selected_graph.scope_digest,
            subject_refs=(walking.ref,),
            subject_digest=selected_graph.stage_subject_digest,
            status=CheckStatus.PASS,
            coverage_denominator=(walking.ref,),
            covered_refs=(walking.ref,),
        )
        relation_only_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=(pairing,),
            paths=(
                replace(
                    unsigned_path,
                    verification=verification_binding(
                        relation_only_receipt,
                        RelationRealizationPurpose.WALKING_PATH,
                    ),
                ),
            ),
        )

        relation_only = check_relation_realization(
            selected_graph,
            relation_only_manifest,
            program,
            snapshot,
            verification_receipts=(relation_only_receipt,),
        )

        self.assertIs(relation_only.status, CheckStatus.FAIL)
        self.assertIn(
            "relation-verification-subject-unbound",
            {item.code for item in relation_only.findings},
        )

        exact_receipt = verification_receipt(
            selected_graph,
            walking,
            checker_id="walking-surface-continuity-checker",
            owner=unsigned_path,
        )
        exact_manifest = replace(
            relation_only_manifest,
            paths=(
                replace(
                    unsigned_path,
                    verification=verification_binding(
                        exact_receipt,
                        RelationRealizationPurpose.WALKING_PATH,
                    ),
                ),
            ),
        )
        exact = check_relation_realization(
            selected_graph,
            exact_manifest,
            program,
            snapshot,
            verification_receipts=(exact_receipt,),
        )
        self.assertIs(exact.status, CheckStatus.PASS)

    def test_direct_interface_cannot_be_satisfied_by_multihop_path(self) -> None:
        selected_graph = single_relation_graph(
            ArchitecturalRelationKind.INTERFACE,
            ("first", "second"),
        )
        program = compiled_program(objects_per_endpoint=2)
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        selected_relation = selected_graph.relations[0]
        by_component = {
            component: tuple(
                item
                for item in bindings
                if item.node_ref == f"component:{component}"
            )
            for component in ("a", "b")
        }
        a0, a1 = by_component["a"]
        b0, b1 = by_component["b"]
        pairings = (
            RelationEndpointPairing(
                pairing_id="a0-b0",
                relation_ref=selected_relation.ref,
                first_binding_ref=a0.ref,
                second_binding_ref=b0.ref,
            ),
            RelationEndpointPairing(
                pairing_id="b0-a1",
                relation_ref=selected_relation.ref,
                first_binding_ref=b0.ref,
                second_binding_ref=a1.ref,
            ),
            RelationEndpointPairing(
                pairing_id="a1-b1",
                relation_ref=selected_relation.ref,
                first_binding_ref=a1.ref,
                second_binding_ref=b1.ref,
            ),
        )
        unsigned_path = RelationObjectPath(
            path_id="a0-b0-a1-b1",
            relation_ref=selected_relation.ref,
            endpoint_binding_refs=(a0.ref, b0.ref, a1.ref, b1.ref),
            pairing_refs=tuple(item.ref for item in pairings),
            purpose=RelationRealizationPurpose.CONTACT_INTERFACE,
        )
        path_receipt = verification_receipt(
            selected_graph,
            selected_relation,
            checker_id="interface-path-checker",
            owner=unsigned_path,
        )
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=pairings,
            paths=(
                replace(
                    unsigned_path,
                    verification=verification_binding(
                        path_receipt,
                        RelationRealizationPurpose.CONTACT_INTERFACE,
                    ),
                ),
            ),
        )

        check = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
            verification_receipts=(path_receipt,),
        )

        self.assertIs(check.status, CheckStatus.UNKNOWN)
        missing_pairing_refs = {
            ref
            for item in check.findings
            if item.code == "relation-narrow-phase-verification-missing"
            for ref in item.subject_refs
            if ref.startswith("relation-endpoint-pairing:")
        }
        self.assertEqual(
            {item.ref for item in pairings},
            missing_pairing_refs,
        )

    def test_exact_non_cartesian_pairings_and_stage_closure_pass(self) -> None:
        selected_graph = graph()
        program = compiled_program(objects_per_endpoint=2)
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        support = selected_graph.relations[0]
        support_bindings = relation_bindings(bindings, support.ref)
        by_component = {
            component: tuple(
                item
                for item in support_bindings
                if item.node_ref == f"component:{component}"
            )
            for component in ("a", "b")
        }
        unsigned_pairings = tuple(
            RelationEndpointPairing(
                pairing_id=f"support-{index}",
                relation_ref=support.ref,
                first_binding_ref=by_component["a"][index].ref,
                second_binding_ref=by_component["b"][index].ref,
            )
            for index in range(2)
        )
        receipts = tuple(
            verification_receipt(
                selected_graph,
                support,
                checker_id="support-narrow-phase-checker",
                owner=pairing,
            )
            for pairing in unsigned_pairings
        )
        pairings = tuple(
            replace(
                pairing,
                verification=verification_binding(
                    receipt,
                    RelationRealizationPurpose.SUPPORT_CHAIN,
                ),
            )
            for pairing, receipt in zip(
                unsigned_pairings,
                receipts,
                strict=True,
            )
        )
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=pairings,
        )

        check = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
            verification_receipts=receipts,
        )

        self.assertIs(check.status, CheckStatus.PASS)
        self.assertEqual(2, len(pairings))
        self.assertEqual(check.subject_refs, check.coverage_denominator)
        self.assertEqual(check.coverage_denominator, check.covered_refs)
        self.assertFalse(check.findings)
        self.assertTrue(
            all(not item.input_object_ids for item in program.proposal.operations)
        )

        requirement = relation_realization_stage_requirement(
            selected_graph,
            selected_manifest,
        )
        profile = StageRequirementProfile(
            profile_id="stage-a-profile",
            typology_id="generic-building",
            stage_id="stage-a",
            branch=selected_graph.branch,
            predecessor_state_digest=selected_graph.state_digest,
            scope_digest=selected_graph.scope_digest,
            stage_subject_ref="stage-subject:stage-a",
            requirements=(requirement,),
        )
        closure = compile_composite_stage_closure(
            profile,
            subject_digest=selected_graph.stage_subject_digest,
            check_receipts=(check,),
        )
        self.assertIs(closure.status, StageClosureStatus.SATISFIED)
        self.assertFalse(
            selected_manifest.to_dict()["stage_acceptance_authority"]
        )
        self.assertFalse(selected_manifest.to_dict()["commit_authority"])
        self.assertFalse(check.to_dict()["canonical_write_authority"])

        self.assertEqual(
            selected_manifest,
            RelationRealizationManifest.from_dict(
                selected_manifest.to_dict()
            ),
        )

    def test_missing_binding_and_missing_pair_manifest_fail_closed(self) -> None:
        selected_graph = graph()
        program = compiled_program()
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings[:1],
            pairings=(),
        )

        check = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
        )

        self.assertIs(check.status, CheckStatus.FAIL)
        codes = {item.code for item in check.findings}
        self.assertIn("relation-endpoint-binding-missing", codes)
        self.assertIn("relation-pairing-manifest-missing", codes)
        self.assertFalse(check.covered_refs)

    def test_duplicate_readback_object_is_ambiguous(self) -> None:
        selected_graph = graph()
        program = compiled_program()
        snapshot = readback(program, duplicate_first=True)
        bindings = endpoint_bindings(selected_graph, program)
        support = selected_graph.relations[0]
        unsigned_pairing = RelationEndpointPairing(
            pairing_id="support",
            relation_ref=support.ref,
            first_binding_ref=bindings[0].ref,
            second_binding_ref=bindings[1].ref,
        )
        receipt = verification_receipt(
            selected_graph,
            support,
            checker_id="support-narrow-phase-checker",
            owner=unsigned_pairing,
        )
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=(
                replace(
                    unsigned_pairing,
                    verification=verification_binding(
                        receipt,
                        RelationRealizationPurpose.SUPPORT_CHAIN,
                    ),
                ),
            ),
        )

        check = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
            verification_receipts=(receipt,),
        )

        self.assertIs(check.status, CheckStatus.FAIL)
        self.assertIn(
            "relation-readback-object-cardinality",
            {item.code for item in check.findings},
        )

    def test_cross_branch_readback_fails(self) -> None:
        selected_graph = graph()
        program = compiled_program()
        snapshot = readback(program, selected_branch=branch("other"))
        bindings = endpoint_bindings(selected_graph, program)
        support = selected_graph.relations[0]
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=(
                RelationEndpointPairing(
                    pairing_id="support",
                    relation_ref=support.ref,
                    first_binding_ref=bindings[0].ref,
                    second_binding_ref=bindings[1].ref,
                ),
            ),
        )

        check = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
        )

        self.assertIs(check.status, CheckStatus.FAIL)
        self.assertIn(
            "relation-readback-branch-mismatch",
            {item.code for item in check.findings},
        )

    def test_object_pair_without_narrow_phase_witness_is_unknown(self) -> None:
        selected_graph = graph()
        program = compiled_program()
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        support = selected_graph.relations[0]
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=(
                RelationEndpointPairing(
                    pairing_id="support",
                    relation_ref=support.ref,
                    first_binding_ref=bindings[0].ref,
                    second_binding_ref=bindings[1].ref,
                ),
            ),
        )

        check = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
        )

        self.assertIs(check.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "relation-narrow-phase-verification-missing",
            {item.code for item in check.findings},
        )

    def test_walking_path_pass_cannot_satisfy_support_chain(self) -> None:
        selected_graph = graph(include_access=True)
        program = compiled_program()
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        relations = {item.relation_id: item for item in selected_graph.relations}
        support = relations["support"]
        walking = relations["walking"]
        support_bindings = relation_bindings(bindings, support.ref)
        walking_bindings = relation_bindings(bindings, walking.ref)
        support_pair = RelationEndpointPairing(
            pairing_id="support",
            relation_ref=support.ref,
            first_binding_ref=support_bindings[0].ref,
            second_binding_ref=support_bindings[1].ref,
            verification=None,
        )
        walking_pair = RelationEndpointPairing(
            pairing_id="walking-hop",
            relation_ref=walking.ref,
            first_binding_ref=walking_bindings[0].ref,
            second_binding_ref=walking_bindings[1].ref,
        )
        unsigned_walking_path = RelationObjectPath(
            path_id="walking-path",
            relation_ref=walking.ref,
            endpoint_binding_refs=(
                walking_pair.first_binding_ref,
                walking_pair.second_binding_ref,
            ),
            pairing_refs=(walking_pair.ref,),
            purpose=RelationRealizationPurpose.WALKING_PATH,
        )
        support_receipt = verification_receipt(
            selected_graph,
            support,
            checker_id="support-narrow-phase-checker",
            owner=support_pair,
        )
        walking_receipt = verification_receipt(
            selected_graph,
            walking,
            checker_id="walking-surface-continuity-checker",
            owner=unsigned_walking_path,
        )
        walking_path = replace(
            unsigned_walking_path,
            verification=verification_binding(
                walking_receipt,
                RelationRealizationPurpose.WALKING_PATH,
            ),
        )
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=(support_pair, walking_pair),
            paths=(walking_path,),
        )

        walking_only = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
            verification_receipts=(walking_receipt,),
        )

        self.assertIs(walking_only.status, CheckStatus.UNKNOWN)
        unknown_subjects = {
            ref
            for item in walking_only.findings
            if item.code == "relation-narrow-phase-verification-missing"
            for ref in item.subject_refs
        }
        self.assertIn(support.ref, unknown_subjects)

        closed_manifest = replace(
            selected_manifest,
            pairings=(
                replace(
                    support_pair,
                    verification=verification_binding(
                        support_receipt,
                        RelationRealizationPurpose.SUPPORT_CHAIN,
                    ),
                ),
                walking_pair,
            ),
        )
        closed = check_relation_realization(
            selected_graph,
            closed_manifest,
            program,
            snapshot,
            verification_receipts=(walking_receipt, support_receipt),
        )
        self.assertIs(closed.status, CheckStatus.PASS)

    def test_walking_verification_purpose_is_rejected_for_support(self) -> None:
        selected_graph = graph()
        program = compiled_program()
        snapshot = readback(program)
        bindings = endpoint_bindings(selected_graph, program)
        support = selected_graph.relations[0]
        unsigned_pairing = RelationEndpointPairing(
            pairing_id="misclassified",
            relation_ref=support.ref,
            first_binding_ref=bindings[0].ref,
            second_binding_ref=bindings[1].ref,
        )
        receipt = verification_receipt(
            selected_graph,
            support,
            checker_id="walking-surface-continuity-checker",
            owner=unsigned_pairing,
        )
        selected_manifest = manifest(
            selected_graph,
            program,
            snapshot,
            bindings,
            pairings=(
                replace(
                    unsigned_pairing,
                    verification=verification_binding(
                        receipt,
                        RelationRealizationPurpose.WALKING_PATH,
                    ),
                ),
            ),
        )

        check = check_relation_realization(
            selected_graph,
            selected_manifest,
            program,
            snapshot,
            verification_receipts=(receipt,),
        )

        self.assertIs(check.status, CheckStatus.FAIL)
        self.assertIn(
            "relation-verification-purpose-incompatible",
            {item.code for item in check.findings},
        )


if __name__ == "__main__":
    unittest.main()
