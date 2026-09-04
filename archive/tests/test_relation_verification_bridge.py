"""Independent checker to architectural relation verification bridge tests."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
)
from archive.archflow.relations.authoring import (
    RelationAnswerStatus,
    RelationAuthoringContext,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisKind,
    RelationBasisUse,
    RelationDerivationAnswer,
    RelationDerivationQuestion,
    RelationProposalSpec,
    RelationRuleEnvelope,
    RelationRuleProposalSpec,
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
from archive.archflow.validation.assembly import check_assembly
from archive.archflow.validation.contracts import CheckStatus
from archive.archflow.validation.relation_verification import (
    RelationQuestionVerificationProfile,
    RelationVerificationBinding,
    RelationVerificationError,
    compile_relation_question_verification,
)
from archive.tests.test_assembly_validation import passing_profile


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
QUESTION_REF = "relation-question:beam-foundation-path"


def _branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="relation-verification-fixture",
            run_id="run-001",
            base=ProjectVersionRef(
                "relation-verification-fixture",
                0,
                SHA_A,
            ),
        ),
        branch_id="candidate",
        epoch=1,
    )


def _context() -> RelationAuthoringContext:
    question = RelationDerivationQuestion(
        question_id="beam-foundation-path",
        projection=RelationProjection.SUPPORT,
        scenario_ref="scenario:gravity",
        subject_refs=("component:beam",),
        target_refs=("component:foundation",),
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
        basis_ids=("support-policy", "support-topology"),
        prompt="Verify the beam-to-foundation support path.",
    )
    bases = (
        RelationBasisBinding(
            basis_id="support-policy",
            basis_kind=RelationBasisKind.HUMAN,
            basis_use=RelationBasisUse.POLICY,
            question_refs=(question.ref,),
            allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
            epistemic_status=RelationEpistemicStatus.DERIVED,
            evidence_refs=("evidence:support-policy",),
            authority_refs=("authority:structural-review",),
            summary="Every supported beam needs an explicit support path.",
        ),
        RelationBasisBinding(
            basis_id="support-topology",
            basis_kind=RelationBasisKind.RAG,
            basis_use=RelationBasisUse.TOPOLOGY,
            question_refs=(question.ref,),
            allowed_relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
            epistemic_status=RelationEpistemicStatus.DERIVED,
            evidence_refs=("evidence:support-topology",),
            authority_refs=("authority:adopted-topology",),
            summary="The adopted topology uses beam, column, and foundation.",
        ),
    )
    return RelationAuthoringContext(
        context_id="beam-support-context",
        branch=_branch(),
        stage_id="stage-1",
        state_digest=SHA_A,
        scope_digest=SHA_B,
        stage_subject_digest=SHA_C,
        subject_inventory_ref=f"stage-subject-inventory:{SHA_C}",
        subject_inventory_digest=SHA_C,
        nodes=tuple(
            ArchitecturalNode(
                node_ref=ref,
                node_kind=ArchitecturalNodeKind.COMPONENT,
                semantic_kind=kind,
                stage_id="stage-1",
                source_refs=(f"stage-subject-entry:{kind}",),
            )
            for ref, kind in (
                ("component:beam", "beam"),
                ("component:column", "column"),
                ("component:foundation", "foundation"),
            )
        ),
        questions=(question,),
        bases=bases,
    )


def _proposal(context: RelationAuthoringContext) -> RelationAuthoringProposal:
    relations = (
        RelationProposalSpec(
            relation_id="beam-on-column",
            question_refs=(QUESTION_REF,),
            kind=ArchitecturalRelationKind.SUPPORT,
            participants=(
                RelationParticipant("supported", "component:beam"),
                RelationParticipant("supporter", "component:column"),
            ),
            scenario_ref="scenario:gravity",
            basis_ids=("support-topology",),
        ),
        RelationProposalSpec(
            relation_id="column-on-foundation",
            question_refs=(QUESTION_REF,),
            kind=ArchitecturalRelationKind.SUPPORT,
            participants=(
                RelationParticipant("supported", "component:column"),
                RelationParticipant("supporter", "component:foundation"),
            ),
            scenario_ref="scenario:gravity",
            basis_ids=("support-topology",),
        ),
    )
    rule = RelationRuleProposalSpec(
        rule_id="beam-support-required",
        question_refs=(QUESTION_REF,),
        node_kind=ArchitecturalNodeKind.COMPONENT,
        semantic_kind="beam",
        relation_kind=ArchitecturalRelationKind.SUPPORT,
        subject_role="supported",
        counted_role="supporter",
        minimum_count=1,
        maximum_count=None,
        scenario_ref="scenario:gravity",
        basis_ids=("support-policy",),
    )
    return RelationAuthoringProposal(
        context_digest=context.context_digest,
        answers=(
            RelationDerivationAnswer(
                question_ref=QUESTION_REF,
                status=RelationAnswerStatus.PROPOSED,
                relation_ids=tuple(item.relation_id for item in relations),
                rule_ids=(rule.rule_id,),
                rationale="The explicit chain transfers the beam to foundation.",
            ),
        ),
        relations=relations,
        rules=(rule,),
    )


def _profile(context: RelationAuthoringContext):
    compilation = compile_relation_authoring(context, _proposal(context))
    assembly = passing_profile(
        stage_subject_source_digest=context.stage_subject_digest
    )
    requirements = {
        item.requirement_id: item for item in assembly.requirements
    }
    relation_refs = {
        item.relation_id: item.ref for item in compilation.graph.relations
    }
    profile = RelationQuestionVerificationProfile(
        profile_id="beam-support-verification",
        question=context.questions[0],
        proposal_graph=compilation.graph,
        subject_inventory_ref=context.subject_inventory_ref,
        output_checker_id="architectural-support-verifier",
        base_checker_id="assembly-relationship-checker",
        bindings=(
            RelationVerificationBinding(
                relation_ref=relation_refs["beam-on-column"],
                checker_requirement_refs=(
                    requirements["beam-on-column"].ref,
                ),
                checker_subject_refs=("component:beam", "component:column"),
            ),
            RelationVerificationBinding(
                relation_ref=relation_refs["column-on-foundation"],
                checker_requirement_refs=(
                    requirements["column-on-foundation"].ref,
                ),
                checker_subject_refs=(
                    "component:column",
                    "component:foundation",
                ),
            ),
        ),
    )
    return compilation, assembly, profile


class RelationVerificationBridgeTests(unittest.TestCase):
    def test_independent_assembly_pass_compiles_exact_question_receipt(self) -> None:
        context = _context()
        _, assembly, profile = _profile(context)
        base_receipt = check_assembly(
            assembly,
            branch=context.branch,
            scope_digest=context.scope_digest,
            stage_subject_digest=context.stage_subject_digest,
        )

        receipt = compile_relation_question_verification(
            profile,
            base_receipt,
        )

        self.assertIs(CheckStatus.PASS, base_receipt.status)
        self.assertIs(CheckStatus.PASS, receipt.status)
        self.assertEqual(profile.relation_refs, receipt.subject_refs)
        self.assertEqual(
            profile,
            RelationQuestionVerificationProfile.from_dict(profile.to_dict()),
        )

    def test_unrelated_checker_subjects_cannot_verify_relation(self) -> None:
        context = _context()
        compilation, assembly, _ = _profile(context)
        relations = {
            item.relation_id: item for item in compilation.graph.relations
        }
        wrong = RelationVerificationBinding(
            relation_ref=relations["beam-on-column"].ref,
            checker_requirement_refs=(assembly.requirements[0].ref,),
            checker_subject_refs=("component:panel-a", "component:panel-b"),
        )

        with self.assertRaisesRegex(
            RelationVerificationError,
            "omit a hypothesis relation endpoint",
        ):
            RelationQuestionVerificationProfile(
                profile_id="misbound-support-verification",
                question=context.questions[0],
                proposal_graph=compilation.graph,
                subject_inventory_ref=context.subject_inventory_ref,
                output_checker_id="architectural-support-verifier",
                base_checker_id="assembly-relationship-checker",
                bindings=(
                    wrong,
                    RelationVerificationBinding(
                        relation_ref=relations["column-on-foundation"].ref,
                        checker_requirement_refs=(assembly.requirements[0].ref,),
                        checker_subject_refs=(
                            "component:column",
                            "component:foundation",
                        ),
                    ),
                ),
            )

    def test_unknown_independent_geometry_remains_unknown(self) -> None:
        context = _context()
        _, assembly, profile = _profile(context)
        assembly = replace(
            assembly,
            subjects=tuple(
                replace(item, bounds=None, bounds_basis=None)
                if item.subject_ref == "component:beam"
                else item
                for item in assembly.subjects
            ),
        )
        base_receipt = check_assembly(
            assembly,
            branch=context.branch,
            scope_digest=context.scope_digest,
            stage_subject_digest=context.stage_subject_digest,
        )

        receipt = compile_relation_question_verification(
            profile,
            base_receipt,
        )

        self.assertIs(CheckStatus.UNKNOWN, base_receipt.status)
        self.assertIs(CheckStatus.UNKNOWN, receipt.status)

    def test_cross_branch_base_receipt_is_rejected(self) -> None:
        context = _context()
        _, assembly, profile = _profile(context)
        foreign = replace(
            context.branch,
            branch_id="foreign",
        )
        base_receipt = check_assembly(
            assembly,
            branch=foreign,
            scope_digest=context.scope_digest,
            stage_subject_digest=context.stage_subject_digest,
        )

        with self.assertRaisesRegex(
            RelationVerificationError,
            "crossed checker, branch",
        ):
            compile_relation_question_verification(profile, base_receipt)


if __name__ == "__main__":
    unittest.main()
