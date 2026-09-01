"""Project-specific Agent relation proposal and deterministic compiler tests."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from dataclasses import replace

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
)
from archflow.capabilities.relation_authoring import (
    RelationAuthoringProviderIdentity,
    RelationAuthoringProviderReceipt,
    RelationAuthoringProviderStatus,
    author_project_relations,
)
from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    StageBaselineLevel,
    StageBaselineRole,
)
from archflow.control.check_requirements import (
    relation_authoring_stage_requirements,
)
from archflow.control.relation_checks import check_relation_coverage
from archflow.control.relation_promotion import (
    RelationPromotionResult,
    promote_verified_relation_graph,
)
from archflow.control.requirements import StageRequirementProfile
from archflow.control.stage_closure import (
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.control.stage_subjects import (
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
from archflow.relations.authoring import (
    RelationAnswerStatus,
    RelationAuthoringCompilation,
    RelationAuthoringCompilationStatus,
    RelationAuthoringContext,
    RelationAuthoringError,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisKind,
    RelationBasisUse,
    RelationDerivationAnswer,
    RelationDerivationQuestion,
    RelationProposalSpec,
    RelationRuleProposalSpec,
    RelationRuleEnvelope,
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
from archflow.relations.coverage import (
    RelationCoverageError,
    RelationCoverageStatus,
    compile_graph_coverage,
    compile_requirement_slots,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus


SHA_A = "a" * 64
SHA_B = "b" * 64
PROVIDER_FINGERPRINT = hashlib.sha256(b"relation-provider").hexdigest()
SCENARIO = "scenario:gravity"
QUESTION_REF = "relation-question:gravity-path"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="relation-agent-fixture",
            run_id="run-004",
            base=ProjectVersionRef("relation-agent-fixture", 0, SHA_A),
        ),
        branch_id="candidate",
        epoch=4,
    )


def _node(ref: str, semantic_kind: str) -> ArchitecturalNode:
    return ArchitecturalNode(
        node_ref=ref,
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind=semantic_kind,
        stage_id="stage-4",
        source_refs=(f"inventory-entry:{semantic_kind}",),
    )


def _context(
    *,
    two_questions: bool = False,
    allow_not_applicable: bool = False,
) -> RelationAuthoringContext:
    questions = [
        RelationDerivationQuestion(
            question_id="gravity-path",
            projection=RelationProjection.SUPPORT,
            scenario_ref=SCENARIO,
            subject_refs=("design-component:roof",),
            target_refs=("design-component:foundation",),
            allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
            rule_envelopes=(
                RelationRuleEnvelope(
                    relation_kind=ArchitecturalRelationKind.SUPPORT,
                    subject_role="supported",
                    counted_role="supporter",
                    minimum_count=1,
                    maximum_count=None,
                ),
            ),
            basis_ids=("gravity-policy", "gravity-topology"),
            prompt="How does every roof load reach the foundation?",
            allow_not_applicable=allow_not_applicable,
        )
    ]
    if two_questions:
        questions.append(
            RelationDerivationQuestion(
                question_id="beam-support",
                projection=RelationProjection.SUPPORT,
                scenario_ref=SCENARIO,
                subject_refs=("design-component:beam",),
                target_refs=("design-component:foundation",),
                allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                rule_envelopes=(
                    RelationRuleEnvelope(
                        relation_kind=ArchitecturalRelationKind.SUPPORT,
                        subject_role="supported",
                        counted_role="supporter",
                        minimum_count=1,
                        maximum_count=None,
                    ),
                ),
                basis_ids=("beam-policy", "beam-topology"),
                prompt="How does the beam load reach the foundation?",
            )
        )
    question_refs = tuple(sorted(item.ref for item in questions))
    bases = [
        RelationBasisBinding(
            basis_id="gravity-policy",
            basis_kind=RelationBasisKind.HUMAN,
            basis_use=RelationBasisUse.POLICY,
            question_refs=(QUESTION_REF,),
            allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
            epistemic_status=RelationEpistemicStatus.DERIVED,
            evidence_refs=("evidence:gravity-policy",),
            authority_refs=("authority:structural-policy",),
            summary="Every roof system requires a directed support denominator.",
        ),
        RelationBasisBinding(
            basis_id="gravity-topology",
            basis_kind=RelationBasisKind.RAG,
            basis_use=RelationBasisUse.TOPOLOGY,
            question_refs=(QUESTION_REF,),
            allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
            epistemic_status=RelationEpistemicStatus.DERIVED,
            evidence_refs=("evidence:gravity-topology",),
            authority_refs=("authority:adopted-topology",),
            summary="The adopted structural basis authorizes this topology question.",
        ),
    ]
    if two_questions:
        beam_ref = "relation-question:beam-support"
        bases.extend(
            (
                RelationBasisBinding(
                    basis_id="beam-policy",
                    basis_kind=RelationBasisKind.HUMAN,
                    basis_use=RelationBasisUse.POLICY,
                    question_refs=(beam_ref,),
                    allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                    epistemic_status=RelationEpistemicStatus.DERIVED,
                    evidence_refs=("evidence:beam-policy",),
                    authority_refs=("authority:structural-policy",),
                    summary="The beam also requires a support denominator.",
                ),
                RelationBasisBinding(
                    basis_id="beam-topology",
                    basis_kind=RelationBasisKind.RAG,
                    basis_use=RelationBasisUse.TOPOLOGY,
                    question_refs=(beam_ref,),
                    allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                    epistemic_status=RelationEpistemicStatus.DERIVED,
                    evidence_refs=("evidence:beam-topology",),
                    authority_refs=("authority:adopted-topology",),
                    summary="The adopted basis covers the beam topology.",
                ),
            )
        )
    assert question_refs
    return RelationAuthoringContext(
        context_id="stage-4-structural-relations",
        branch=_branch(),
        stage_id="stage-4",
        state_digest=SHA_A,
        scope_digest=SHA_A,
        stage_subject_digest=SHA_B,
        subject_inventory_ref=f"stage-subject-inventory:{SHA_B}",
        subject_inventory_digest=SHA_B,
        nodes=(
            _node("design-component:beam", "beam"),
            _node("design-component:column", "column"),
            _node("design-component:foundation", "foundation"),
            _node("design-component:roof", "roof"),
        ),
        questions=tuple(questions),
        bases=tuple(sorted(bases, key=lambda item: item.basis_id)),
    )


def _relation(
    relation_id: str,
    supported: str,
    supporter: str,
    *,
    basis_id: str = "gravity-topology",
    question_ref: str = QUESTION_REF,
) -> RelationProposalSpec:
    return RelationProposalSpec(
        relation_id=relation_id,
        question_refs=(question_ref,),
        kind=ArchitecturalRelationKind.SUPPORT,
        participants=(
            RelationParticipant(role="supported", node_ref=supported),
            RelationParticipant(role="supporter", node_ref=supporter),
        ),
        scenario_ref=SCENARIO,
        basis_ids=(basis_id,),
    )


def _rule(
    rule_id: str = "roof-support-required",
    *,
    semantic_kind: str = "roof",
    basis_id: str = "gravity-policy",
    question_ref: str = QUESTION_REF,
) -> RelationRuleProposalSpec:
    return RelationRuleProposalSpec(
        rule_id=rule_id,
        question_refs=(question_ref,),
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind=semantic_kind,
        relation_kind=ArchitecturalRelationKind.SUPPORT,
        subject_role="supported",
        counted_role="supporter",
        minimum_count=1,
        maximum_count=None,
        scenario_ref=SCENARIO,
        basis_ids=(basis_id,),
    )


def _proposal(context: RelationAuthoringContext) -> RelationAuthoringProposal:
    relations = (
        _relation(
            "beam-on-column",
            "design-component:beam",
            "design-component:column",
        ),
        _relation(
            "column-on-foundation",
            "design-component:column",
            "design-component:foundation",
        ),
        _relation(
            "roof-on-beam",
            "design-component:roof",
            "design-component:beam",
        ),
    )
    rule = _rule()
    answer = RelationDerivationAnswer(
        question_ref=QUESTION_REF,
        status=RelationAnswerStatus.PROPOSED,
        relation_ids=tuple(sorted(item.relation_id for item in relations)),
        rule_ids=(rule.rule_id,),
        rationale="The adopted topology forms a directed roof-to-foundation chain.",
    )
    return RelationAuthoringProposal(
        context_digest=context.context_digest,
        answers=(answer,),
        relations=relations,
        rules=(rule,),
    )


def _open_proposal(
    context: RelationAuthoringContext,
    *,
    status: RelationAnswerStatus = RelationAnswerStatus.UNKNOWN,
) -> RelationAuthoringProposal:
    return RelationAuthoringProposal(
        context_digest=context.context_digest,
        answers=(
            RelationDerivationAnswer(
                question_ref=QUESTION_REF,
                status=status,
                relation_ids=(),
                rule_ids=(),
                rationale="The supplied evidence does not identify the intermediate system.",
                human_question="Which authorized roof support topology should be used?",
            ),
        ),
        relations=(),
        rules=(),
    )


def _two_question_proposal(
    context: RelationAuthoringContext,
) -> RelationAuthoringProposal:
    gravity = _proposal(context)
    beam_question_ref = "relation-question:beam-support"
    beam_relations = (
        _relation(
            "beam-question-column",
            "design-component:beam",
            "design-component:column",
            basis_id="beam-topology",
            question_ref=beam_question_ref,
        ),
        _relation(
            "beam-question-foundation",
            "design-component:column",
            "design-component:foundation",
            basis_id="beam-topology",
            question_ref=beam_question_ref,
        ),
    )
    beam_rule = _rule(
        "beam-support-required",
        semantic_kind="beam",
        basis_id="beam-policy",
        question_ref=beam_question_ref,
    )
    return RelationAuthoringProposal(
        context_digest=context.context_digest,
        answers=(
            gravity.answers[0],
            RelationDerivationAnswer(
                question_ref=beam_question_ref,
                status=RelationAnswerStatus.PROPOSED,
                relation_ids=tuple(
                    sorted(item.relation_id for item in beam_relations)
                ),
                rule_ids=(beam_rule.rule_id,),
                rationale="The beam has its own exact support question.",
            ),
        ),
        relations=tuple(
            sorted(
                (*gravity.relations, *beam_relations),
                key=lambda item: item.relation_id,
            )
        ),
        rules=tuple(
            sorted(
                (*gravity.rules, beam_rule),
                key=lambda item: item.rule_id,
            )
        ),
    )


def _role_obligations(
    *required: StageBaselineRole,
) -> tuple[StageSubjectRoleObligation, ...]:
    required_roles = set(required)
    return tuple(
        StageSubjectRoleObligation(
            role=role,
            disposition=(
                StageSubjectDisposition.REQUIRED
                if role in required_roles
                else StageSubjectDisposition.NOT_APPLICABLE
            ),
            target_refs=(
                ("design-component:foundation",)
                if role in required_roles
                else ()
            ),
            evidence_refs=(f"evidence:inventory-{role.value}",),
            authority_refs=(f"authority:inventory-{role.value}",),
        )
        for role in sorted(
            BASELINE_LEVEL_ROLES[StageBaselineLevel.SPATIAL],
            key=lambda item: item.value,
        )
    )


def _bound_context_and_inventory(
    *,
    extra_required_roof: bool = False,
) -> tuple[RelationAuthoringContext, StageSubjectInventory]:
    selected_branch = _branch()
    component_specs = [
        ("foundation", None, "foundation", ()),
        ("column", "foundation", "column", ()),
        ("beam", "column", "beam", ()),
        (
            "roof",
            "beam",
            "roof",
            (
                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                StageBaselineRole.LOAD_PATH,
            ),
        ),
    ]
    if extra_required_roof:
        component_specs.append(
            (
                "roof-secondary",
                "beam",
                "roof",
                (StageBaselineRole.LOAD_PATH,),
            )
        )
    entries = tuple(
        StageSubjectInventoryEntry(
            component_id=component_id,
            identity_ref=f"design-component:{component_id}",
            parent_component_id=parent_id,
            semantic_kind=semantic_kind,
            component_digest=SHA_A,
            geometry_object_ids=(),
            binding_ids=(),
            role_obligations=_role_obligations(*required_roles),
        )
        for component_id, parent_id, semantic_kind, required_roles in component_specs
    )
    record_prefix = (
        f"runs/{selected_branch.run.run_id}/branches/"
        f"{selected_branch.branch_id}/records"
    )
    inventory = StageSubjectInventory(
        inventory_id="stage-4-relation-subjects",
        branch=selected_branch,
        stage_id="stage-4",
        stage_subject_ref="stage-subject:stage-4",
        stage_subject_digest=SHA_B,
        baseline_level=StageBaselineLevel.SPATIAL,
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
        entries=entries,
    )
    base = _context()
    context = replace(
        base,
        subject_inventory_ref=f"stage-subject-inventory:{inventory.inventory_digest}",
        subject_inventory_digest=inventory.inventory_digest,
        nodes=tuple(
            ArchitecturalNode(
                node_ref=entry.identity_ref,
                node_kind=ArchitecturalNodeKind.COMPONENT,
                semantic_kind=entry.semantic_kind,
                stage_id="stage-4",
                source_refs=(f"stage-subject-entry:{entry.entry_digest}",),
            )
            for entry in entries
        ),
    )
    return context, inventory


class RelationAuthoringContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.proposal = _proposal(self.context)

    def test_context_and_proposal_roundtrip_without_authority(self) -> None:
        context_payload = self.context.to_dict()
        proposal_payload = self.proposal.to_dict()

        self.assertEqual(
            self.context,
            RelationAuthoringContext.from_dict(context_payload),
        )
        self.assertEqual(
            self.proposal,
            RelationAuthoringProposal.from_dict(proposal_payload),
        )
        for payload in (context_payload, proposal_payload):
            self.assertFalse(payload["design_authority"])
            self.assertFalse(payload["stage_acceptance_authority"])
            self.assertFalse(payload["persistence_authority"])
            self.assertFalse(payload["canonical_write_authority"])

    def test_compiler_builds_directed_topology_but_requires_verification(self) -> None:
        result = compile_relation_authoring(self.context, self.proposal)

        self.assertIs(
            RelationAuthoringCompilationStatus.PROPOSAL_COMPILED,
            result.receipt.status,
        )
        self.assertFalse(result.coverage_manifest.closure_ready)
        self.assertEqual(
            {RelationCoverageStatus.UNKNOWN},
            {item.status for item in result.coverage_manifest.dispositions},
        )
        self.assertEqual(1, len(result.receipt.topology_witnesses))
        witness = result.receipt.topology_witnesses[0]
        self.assertEqual(
            (
                "design-component:roof",
                "design-component:beam",
                "design-component:column",
                "design-component:foundation",
            ),
            witness.node_refs,
        )
        self.assertTrue(result.receipt.to_dict()["requires_independent_verification"])
        self.assertFalse(result.receipt.to_dict()["stage_acceptance_authority"])

    def test_compilation_roundtrip_reexecutes_proposal_only_invariants(self) -> None:
        result = compile_relation_authoring(self.context, self.proposal)

        self.assertEqual(
            result,
            RelationAuthoringCompilation.from_dict(result.to_dict()),
        )

    def test_reload_rejects_promoted_agent_graph_even_with_matching_digests(self) -> None:
        result = compile_relation_authoring(self.context, self.proposal)
        promoted_graph = replace(
            result.graph,
            relations=tuple(
                replace(
                    item,
                    epistemic_status=RelationEpistemicStatus.DERIVED,
                )
                for item in result.graph.relations
            ),
        )
        slots = compile_requirement_slots(promoted_graph, result.policy)
        manifest = compile_graph_coverage(promoted_graph, result.policy, slots)
        forged_receipt = replace(
            result.receipt,
            graph_digest=promoted_graph.graph_digest,
            coverage_manifest_digest=manifest.manifest_digest,
        )

        with self.assertRaisesRegex(RelationAuthoringError, "verification"):
            RelationAuthoringCompilation(
                receipt=forged_receipt,
                graph=promoted_graph,
                policy=result.policy,
                slots=slots,
                coverage_manifest=manifest,
            )

    def test_unknown_answer_stays_open_and_carries_no_graph(self) -> None:
        result = compile_relation_authoring(
            self.context,
            _open_proposal(self.context),
        )

        self.assertIs(RelationAuthoringCompilationStatus.OPEN, result.receipt.status)
        self.assertEqual((QUESTION_REF,), result.receipt.unresolved_question_refs)
        self.assertIsNone(result.graph)
        self.assertIsNone(result.coverage_manifest)

    def test_agent_not_applicable_request_cannot_close(self) -> None:
        context = _context(allow_not_applicable=True)
        result = compile_relation_authoring(
            context,
            _open_proposal(
                context,
                status=RelationAnswerStatus.NOT_APPLICABLE_REQUESTED,
            ),
        )

        self.assertIs(RelationAuthoringCompilationStatus.OPEN, result.receipt.status)
        self.assertEqual((QUESTION_REF,), result.receipt.unresolved_question_refs)

    def test_not_applicable_request_is_rejected_when_controller_forbids_it(self) -> None:
        with self.assertRaisesRegex(RelationAuthoringError, "forbidden"):
            compile_relation_authoring(
                self.context,
                _open_proposal(
                    self.context,
                    status=RelationAnswerStatus.NOT_APPLICABLE_REQUESTED,
                ),
            )

    def test_agent_cannot_shrink_the_question_denominator(self) -> None:
        context = _context(two_questions=True)
        proposal = replace(self.proposal, context_digest=context.context_digest)

        with self.assertRaisesRegex(RelationAuthoringError, "question denominator"):
            compile_relation_authoring(context, proposal)

    def test_answer_and_specs_require_bidirectional_question_ownership(self) -> None:
        context = _context(two_questions=True)
        proposal = _two_question_proposal(context)
        foreign_relation_id = "beam-question-column"
        tampered_answers = tuple(
            replace(
                answer,
                relation_ids=tuple(
                    sorted((*answer.relation_ids, foreign_relation_id))
                ),
            )
            if answer.question_ref == QUESTION_REF
            else answer
            for answer in proposal.answers
        )
        tampered = replace(proposal, answers=tampered_answers)

        with self.assertRaisesRegex(RelationAuthoringError, "bidirectional"):
            compile_relation_authoring(context, tampered)

    def test_one_relation_can_use_question_scoped_bases_as_a_complete_union(
        self,
    ) -> None:
        context = _context(two_questions=True)
        proposal = _two_question_proposal(context)
        gravity_ref = QUESTION_REF
        beam_ref = "relation-question:beam-support"
        merged_ids = {"beam-on-column", "column-on-foundation"}
        merged_relations = []
        for relation in proposal.relations:
            if relation.relation_id in {
                "beam-question-column",
                "beam-question-foundation",
            }:
                continue
            if relation.relation_id in merged_ids:
                relation = replace(
                    relation,
                    question_refs=tuple(sorted((gravity_ref, beam_ref))),
                    basis_ids=("beam-topology", "gravity-topology"),
                )
            merged_relations.append(relation)
        answers = tuple(
            replace(
                answer,
                relation_ids=(
                    tuple(sorted(merged_ids))
                    if answer.question_ref == beam_ref
                    else tuple(
                        sorted({*answer.relation_ids} - {
                            "beam-question-column",
                            "beam-question-foundation",
                        })
                    )
                ),
            )
            for answer in proposal.answers
        )

        result = compile_relation_authoring(
            context,
            replace(
                proposal,
                answers=answers,
                relations=tuple(
                    sorted(merged_relations, key=lambda item: item.relation_id)
                ),
            ),
        )

        self.assertEqual(
            RelationAuthoringCompilationStatus.PROPOSAL_COMPILED,
            result.receipt.status,
        )
        self.assertEqual(3, len(result.graph.relations))

    def test_unknown_question_cannot_hide_partial_relation_artifacts(self) -> None:
        context = _context(two_questions=True)
        gravity = _proposal(context)
        partial = RelationAuthoringProposal(
            context_digest=context.context_digest,
            answers=(
                gravity.answers[0],
                RelationDerivationAnswer(
                    question_ref="relation-question:beam-support",
                    status=RelationAnswerStatus.UNKNOWN,
                    relation_ids=(),
                    rule_ids=(),
                    rationale="Beam topology remains unresolved.",
                    human_question="Which beam support topology is authorized?",
                ),
            ),
            relations=gravity.relations,
            rules=gravity.rules,
        )

        with self.assertRaisesRegex(RelationAuthoringError, "partial"):
            compile_relation_authoring(context, partial)

    def test_agent_cannot_name_an_unknown_endpoint(self) -> None:
        bad = replace(
            self.proposal.relations[2],
            participants=(
                RelationParticipant(
                    role="supported",
                    node_ref="design-component:invented-roof",
                ),
                RelationParticipant(
                    role="supporter",
                    node_ref="design-component:beam",
                ),
            ),
        )
        proposal = replace(
            self.proposal,
            relations=(self.proposal.relations[0], self.proposal.relations[1], bad),
        )

        with self.assertRaisesRegex(RelationAuthoringError, "unknown endpoint"):
            compile_relation_authoring(self.context, proposal)

    def test_agent_cannot_invent_or_cross_a_basis(self) -> None:
        bad = replace(self.proposal.relations[2], basis_ids=("agent-invented",))
        proposal = replace(
            self.proposal,
            relations=(self.proposal.relations[0], self.proposal.relations[1], bad),
        )

        with self.assertRaisesRegex(RelationAuthoringError, "unknown basis_id"):
            compile_relation_authoring(self.context, proposal)

    def test_agent_cannot_use_policy_basis_as_topology_authority(self) -> None:
        bad = replace(self.proposal.relations[2], basis_ids=("gravity-policy",))
        proposal = replace(
            self.proposal,
            relations=(self.proposal.relations[0], self.proposal.relations[1], bad),
        )

        with self.assertRaisesRegex(RelationAuthoringError, "verified scope"):
            compile_relation_authoring(self.context, proposal)

    def test_cad_readback_cannot_author_policy(self) -> None:
        with self.assertRaisesRegex(RelationAuthoringError, "cannot author policy"):
            RelationBasisBinding(
                basis_id="cad-policy",
                basis_kind=RelationBasisKind.CAD_READBACK,
                basis_use=RelationBasisUse.POLICY,
                question_refs=(QUESTION_REF,),
                allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                epistemic_status=RelationEpistemicStatus.OBSERVED,
                evidence_refs=("evidence:cad",),
                authority_refs=("authority:cad",),
                summary="Invalid CAD-authored policy.",
            )

    def test_proposed_answer_must_reach_its_exact_target(self) -> None:
        only_roof = self.proposal.relations[2]
        proposal = RelationAuthoringProposal(
            context_digest=self.context.context_digest,
            answers=(
                replace(
                    self.proposal.answers[0],
                    relation_ids=(only_roof.relation_id,),
                ),
            ),
            relations=(only_roof,),
            rules=self.proposal.rules,
        )

        with self.assertRaisesRegex(RelationAuthoringError, "no proposed path"):
            compile_relation_authoring(self.context, proposal)

    def test_agent_cannot_reduce_the_controller_cardinality(self) -> None:
        weakened = replace(self.proposal.rules[0], minimum_count=0)
        proposal = replace(self.proposal, rules=(weakened,))

        with self.assertRaisesRegex(RelationAuthoringError, "weaken"):
            compile_relation_authoring(self.context, proposal)

    def test_agent_cannot_self_count_the_subject_as_its_supporter(self) -> None:
        self_counting = replace(
            self.proposal.rules[0],
            counted_role="supported",
        )
        proposal = replace(self.proposal, rules=(self_counting,))

        with self.assertRaisesRegex(RelationAuthoringError, "weaken"):
            compile_relation_authoring(self.context, proposal)

    def test_authority_tampering_is_rejected(self) -> None:
        payload = copy.deepcopy(self.proposal.to_dict())
        payload["stage_acceptance_authority"] = True

        with self.assertRaisesRegex(RelationAuthoringError, "authority"):
            RelationAuthoringProposal.from_dict(payload)

    def test_agent_cannot_inject_predecessor_or_propagation_authority(self) -> None:
        payload = copy.deepcopy(self.proposal.to_dict())
        payload["relations"][0]["predecessor_binding_authority"] = True

        with self.assertRaisesRegex(RelationAuthoringError, "schema|authority"):
            RelationAuthoringProposal.from_dict(payload)

    def test_inventory_ref_must_bind_the_exact_digest(self) -> None:
        with self.assertRaisesRegex(RelationAuthoringError, "exact digest"):
            replace(
                self.context,
                subject_inventory_ref="stage-subject-inventory:foreign",
            )

    def test_stage_requirements_force_independent_relation_verification(self) -> None:
        context, inventory = _bound_context_and_inventory()
        compilation = compile_relation_authoring(context, _proposal(context))
        requirements = relation_authoring_stage_requirements(
            context,
            compilation,
            inventory,
        )
        _, coverage_receipt = check_relation_coverage(
            compilation.graph,
            compilation.policy,
            inventory,
            compilation.slots,
        )
        profile = StageRequirementProfile(
            profile_id="relation-authoring-stage",
            typology_id="project-specific-building",
            stage_id="stage-4",
            branch=context.branch,
            predecessor_state_digest=context.state_digest,
            scope_digest=context.scope_digest,
            stage_subject_ref=inventory.stage_subject_ref,
            requirements=requirements,
        )

        closure = compile_composite_stage_closure(
            profile,
            subject_digest=inventory.stage_subject_digest,
            check_receipts=(coverage_receipt,),
        )

        self.assertEqual(2, len(requirements))
        self.assertEqual("architectural-support-verifier", requirements[1].checker_id)
        self.assertIs(RelationCoverageStatus.UNKNOWN, compilation.coverage_manifest.dispositions[0].status)
        self.assertIs(StageClosureStatus.OPEN, closure.status)

    def test_verified_proposal_promotes_and_closes_the_exact_stage(self) -> None:
        context, inventory = _bound_context_and_inventory()
        compilation = compile_relation_authoring(context, _proposal(context))
        open_requirements = relation_authoring_stage_requirements(
            context,
            compilation,
            inventory,
        )
        verification_requirement = next(
            item
            for item in open_requirements
            if item.requirement_id.startswith("relation-verification-")
        )
        verification_receipt = CheckReceiptEnvelope(
            check_id=verification_requirement.requirement_id,
            checker_id=verification_requirement.checker_id,
            checker_version="1.0.0",
            branch=context.branch,
            scope_digest=context.scope_digest,
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
        requirements = relation_authoring_stage_requirements(
            context,
            compilation,
            inventory,
            promotion=promotion,
        )
        _, coverage_receipt = check_relation_coverage(
            promotion.graph,
            compilation.policy,
            inventory,
            compilation.slots,
            promotion=promotion.receipt,
        )
        profile = StageRequirementProfile(
            profile_id="verified-relation-authoring-stage",
            typology_id="project-specific-building",
            stage_id="stage-4",
            branch=context.branch,
            predecessor_state_digest=context.state_digest,
            scope_digest=context.scope_digest,
            stage_subject_ref=inventory.stage_subject_ref,
            requirements=requirements,
        )

        closure = compile_composite_stage_closure(
            profile,
            subject_digest=inventory.stage_subject_digest,
            check_receipts=(verification_receipt, coverage_receipt),
        )

        self.assertIs(StageClosureStatus.SATISFIED, closure.status)
        self.assertTrue(
            all(
                item.epistemic_status is RelationEpistemicStatus.DERIVED
                for item in promotion.graph.relations
            )
        )
        self.assertIn(
            promotion.receipt.proposal_graph_ref,
            coverage_receipt.source_refs,
        )
        self.assertEqual(
            promotion,
            RelationPromotionResult.from_dict(promotion.to_dict()),
        )

    def test_promotion_rejects_incomplete_verification_denominator(self) -> None:
        context, inventory = _bound_context_and_inventory()
        compilation = compile_relation_authoring(context, _proposal(context))

        with self.assertRaisesRegex(
            ValueError,
            "verification receipts do not equal",
        ):
            promote_verified_relation_graph(
                context,
                compilation,
                inventory,
                (),
            )

    def test_inventory_component_cannot_disappear_from_question_denominator(self) -> None:
        context, inventory = _bound_context_and_inventory(
            extra_required_roof=True
        )
        compilation = compile_relation_authoring(context, _proposal(context))

        with self.assertRaisesRegex(
            RelationCoverageError,
            "omit required inventory obligations",
        ):
            relation_authoring_stage_requirements(
                context,
                compilation,
                inventory,
            )

    def test_question_kind_must_match_projection(self) -> None:
        with self.assertRaisesRegex(RelationAuthoringError, "incompatible"):
            RelationDerivationQuestion(
                question_id="bad-support",
                projection=RelationProjection.SUPPORT,
                scenario_ref=SCENARIO,
                subject_refs=("design-component:roof",),
                target_refs=("design-component:foundation",),
                allowed_relation_kinds=(ArchitecturalRelationKind.HOST,),
                rule_envelopes=(
                    RelationRuleEnvelope(
                        relation_kind=ArchitecturalRelationKind.HOST,
                        subject_role="hosted",
                        counted_role="host",
                        minimum_count=1,
                        maximum_count=1,
                    ),
                ),
                basis_ids=("gravity-topology",),
                prompt="Bad relation kind.",
            )


def _provider_identity() -> RelationAuthoringProviderIdentity:
    return RelationAuthoringProviderIdentity(
        provider_id="scripted-relation-provider",
        model_id="scripted-relation-model",
        provider_version="1",
        provider_fingerprint=PROVIDER_FINGERPRINT,
    )


def _success(
    request: ModelInvocationRequest,
    output: dict[str, object],
    *,
    bound_request: ModelInvocationRequest | None = None,
    provider_id: str = "scripted-relation-provider",
) -> ModelInvocationReceipt:
    encoded = _canonical_json(output)
    return ModelInvocationReceipt(
        receipt_id=f"scripted-{request.request_id}",
        status=ModelInvocationStatus.SUCCESS,
        request=request if bound_request is None else bound_request,
        provider_id=provider_id,
        model_id="scripted-relation-model",
        provider_version="1",
        provider_fingerprint=PROVIDER_FINGERPRINT,
        input_bytes=len(request.payload_json.encode("utf-8")),
        output_bytes=len(encoded.encode("utf-8")),
        output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        output_json=encoded,
    )


class _ScriptedProvider:
    def __init__(self, output_factory) -> None:
        self.output_factory = output_factory
        self.requests: list[ModelInvocationRequest] = []

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationReceipt:
        self.requests.append(request)
        output = self.output_factory(request)
        if isinstance(output, ModelInvocationReceipt):
            return output
        return _success(request, output)


def _output(
    request: ModelInvocationRequest,
    context: RelationAuthoringContext,
    proposal: RelationAuthoringProposal,
) -> dict[str, object]:
    return {
        "schema": "RelationAuthoringOutput@1",
        "request_context_digest": request.context_digest,
        "relation_context_digest": context.context_digest,
        "proposal": proposal.to_dict(),
    }


class RelationAuthoringProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.proposal = _proposal(self.context)
        self.identity = _provider_identity()

    async def _author(self, provider: _ScriptedProvider):
        return await author_project_relations(
            provider,
            provider_identity=self.identity,
            request_id="derive-stage-4-relations",
            context=self.context,
        )

    async def test_provider_is_called_once_and_compiled_without_authority(self) -> None:
        provider = _ScriptedProvider(
            lambda request: _output(request, self.context, self.proposal)
        )

        result = await self._author(provider)

        self.assertEqual(1, len(provider.requests))
        self.assertIs(
            RelationAuthoringProviderStatus.PROPOSAL_COMPILED,
            result.receipt.status,
        )
        self.assertTrue(result.receipt.to_dict()["single_provider_call"])
        self.assertFalse(result.receipt.to_dict()["validation_authority"])
        self.assertEqual(
            result.receipt,
            RelationAuthoringProviderReceipt.from_dict(
                result.receipt.to_dict()
            ),
        )

    async def test_success_receipt_reload_replays_compiler_status(self) -> None:
        provider = _ScriptedProvider(
            lambda request: _output(request, self.context, self.proposal)
        )
        result = await self._author(provider)
        payload = copy.deepcopy(result.receipt.to_dict())
        payload["status"] = RelationAuthoringProviderStatus.OPEN.value

        with self.assertRaisesRegex(
            RelationAuthoringError,
            "incomplete",
        ):
            RelationAuthoringProviderReceipt.from_dict(payload)

    async def test_valid_unknown_output_stays_open(self) -> None:
        proposal = _open_proposal(self.context)
        provider = _ScriptedProvider(
            lambda request: _output(request, self.context, proposal)
        )

        result = await self._author(provider)

        self.assertIs(RelationAuthoringProviderStatus.OPEN, result.receipt.status)
        self.assertEqual((QUESTION_REF,), result.compilation.receipt.unresolved_question_refs)

    async def test_provider_request_mismatch_is_rejected_without_retry(self) -> None:
        def output_factory(request: ModelInvocationRequest) -> ModelInvocationReceipt:
            other = ModelInvocationRequest.create(
                request_id="foreign-request",
                phase=request.phase,
                checkpoint_digest=request.checkpoint_digest,
                context_digest=request.context_digest,
                payload=request.payload,
            )
            return _success(
                request,
                _output(request, self.context, self.proposal),
                bound_request=other,
            )

        provider = _ScriptedProvider(output_factory)

        result = await self._author(provider)

        self.assertEqual(1, len(provider.requests))
        self.assertIs(RelationAuthoringProviderStatus.REJECTED, result.receipt.status)
        self.assertEqual(
            "relation_authoring.provider_request_mismatch",
            result.receipt.error_code,
        )

    async def test_provider_identity_mismatch_is_rejected(self) -> None:
        provider = _ScriptedProvider(
            lambda request: _success(
                request,
                _output(request, self.context, self.proposal),
                provider_id="substituted-provider",
            )
        )

        result = await self._author(provider)

        self.assertIs(RelationAuthoringProviderStatus.REJECTED, result.receipt.status)
        self.assertEqual(
            "relation_authoring.provider_identity_mismatch",
            result.receipt.error_code,
        )

    async def test_stale_context_echo_is_rejected(self) -> None:
        def output_factory(request: ModelInvocationRequest) -> dict[str, object]:
            output = _output(request, self.context, self.proposal)
            output["relation_context_digest"] = SHA_A
            return output

        provider = _ScriptedProvider(output_factory)

        result = await self._author(provider)

        self.assertIs(RelationAuthoringProviderStatus.REJECTED, result.receipt.status)
        self.assertEqual("relation_authoring.proposal_rejected", result.receipt.error_code)

    async def test_provider_failure_is_retained_without_retry(self) -> None:
        def output_factory(request: ModelInvocationRequest) -> ModelInvocationReceipt:
            return ModelInvocationReceipt(
                receipt_id="scripted-timeout",
                status=ModelInvocationStatus.TIMEOUT,
                request=request,
                provider_id=self.identity.provider_id,
                model_id=self.identity.model_id,
                provider_version=self.identity.provider_version,
                provider_fingerprint=self.identity.provider_fingerprint,
                input_bytes=len(request.payload_json.encode("utf-8")),
                output_bytes=0,
                output_sha256=None,
                error_code="model.timeout",
                message="timed out",
            )

        provider = _ScriptedProvider(output_factory)

        result = await self._author(provider)

        self.assertEqual(1, len(provider.requests))
        self.assertIs(
            RelationAuthoringProviderStatus.PROVIDER_FAILED,
            result.receipt.status,
        )
        self.assertEqual("model.timeout", result.receipt.error_code)

    async def test_provider_exception_is_translated_once_into_failure_receipt(self) -> None:
        class RaisingProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def invoke(self, request: ModelInvocationRequest):
                self.calls += 1
                raise RuntimeError("provider transport failed")

        provider = RaisingProvider()

        result = await self._author(provider)

        self.assertEqual(1, provider.calls)
        self.assertIs(
            RelationAuthoringProviderStatus.PROVIDER_FAILED,
            result.receipt.status,
        )
        self.assertEqual(
            "relation_authoring.provider_exception",
            result.receipt.error_code,
        )

    async def test_untyped_provider_return_is_retained_without_retry(self) -> None:
        class UntypedProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def invoke(self, request: ModelInvocationRequest):
                self.calls += 1
                return {"untyped": "provider result"}

        provider = UntypedProvider()

        result = await self._author(provider)

        self.assertEqual(1, provider.calls)
        self.assertIs(
            RelationAuthoringProviderStatus.REJECTED,
            result.receipt.status,
        )
        self.assertIs(
            ModelInvocationStatus.MALFORMED,
            result.receipt.model_receipt.status,
        )
        self.assertEqual(
            "relation_authoring.provider_contract_violation",
            result.receipt.error_code,
        )


if __name__ == "__main__":
    unittest.main()
