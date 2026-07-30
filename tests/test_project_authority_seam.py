from __future__ import annotations

import unittest

from archflow.capabilities.experts import ExpertSnapshot
from archflow.project import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.runtime import initial_state
from archflow.state import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    CriterionRef,
    RevisionPolicy,
    initialize_canonical_project,
)
from archflow.submission import CandidateDelta, CandidateSubmission, Claim
from archflow.validation import AuthorizedCommitmentClaimsValidator


ZERO_DIGEST = "0" * 64


def _hard_commitment(
    *,
    status: CommitmentStatus,
    authorized_by: str | None,
) -> Commitment:
    return Commitment(
        commitment_id=f"commitment-{status.value}",
        kind=CommitmentKind.ACHIEVEMENT,
        strength=CommitmentStrength.HARD,
        status=status,
        authority_id="authority.user",
        authorized_by=authorized_by,
        source_event_ref="project://project-a/events/request.json",
        satisfaction_criterion=CriterionRef(
            criterion_id="capacity.minimum",
            provider_id="validator.capacity",
        ),
        revision_policy=RevisionPolicy.OWNER_ONLY,
    )


def _submission(base: ProjectVersionRef, *, with_claim: bool) -> CandidateSubmission:
    return CandidateSubmission(
        submission_id="candidate-001",
        base=base,
        workspace_id="workspace-001",
        intent="Test the authority boundary.",
        delta=CandidateDelta(),
        claims=(
            (
                Claim(
                    key="capacity.minimum",
                    value="satisfied",
                    evidence_refs=("evidence://capacity",),
                ),
            )
            if with_claim
            else ()
        ),
        evidence_refs=("evidence://capacity",) if with_claim else (),
    )


class ProjectAuthoritySeamTests(unittest.TestCase):
    def test_canonical_project_run_and_branch_identities_do_not_collapse(self) -> None:
        base = ProjectVersionRef("project-a", 2, ZERO_DIGEST)
        run = RunRef("project-a", "run-001", base)
        branch = BranchRef(run, "option-a", 0)

        self.assertEqual(base.project_id, "project-a")
        self.assertEqual(run.run_id, "run-001")
        self.assertEqual(branch.branch_id, "option-a")
        with self.assertRaises(ValueError):
            RunRef(
                "project-b",
                "run-001",
                ProjectVersionRef("project-a", 3, ZERO_DIGEST),
            )

    def test_production_initializer_contains_no_fixture_goal_or_program(self) -> None:
        state = initialize_canonical_project("project-a")

        self.assertEqual(state.ref.project_id, "project-a")
        self.assertIsNone(state.goal)
        self.assertIsNone(state.design_program_ref)
        self.assertIsNone(state.legacy_program_view)
        self.assertNotEqual(initial_state.__doc__, None)
        self.assertIn("never production", initial_state.__doc__ or "")

    def test_only_authorized_typed_commitment_gates_production_claims(self) -> None:
        proposed = _hard_commitment(
            status=CommitmentStatus.PROPOSED,
            authorized_by=None,
        )
        proposed_state = initialize_canonical_project(
            "project-a",
            commitments=(proposed,),
        )
        self.assertEqual(
            AuthorizedCommitmentClaimsValidator().validate(
                proposed_state,
                _submission(proposed_state.ref, with_claim=False),
            ),
            (),
        )

        active = _hard_commitment(
            status=CommitmentStatus.ACTIVE,
            authorized_by="authority.user",
        )
        active_state = initialize_canonical_project(
            "project-a",
            commitments=(active,),
        )
        missing = AuthorizedCommitmentClaimsValidator().validate(
            active_state,
            _submission(active_state.ref, with_claim=False),
        )
        self.assertEqual(missing[0].code, "commitment.required_claim_missing")
        self.assertEqual(
            AuthorizedCommitmentClaimsValidator().validate(
                active_state,
                _submission(active_state.ref, with_claim=True),
            ),
            (),
        )

    def test_legacy_program_view_has_no_automatic_expert_authority(self) -> None:
        program_ref = ProjectRecordRef(
            project_id="project-a",
            relative_path="canonical/design-program.json",
            sha256=ZERO_DIGEST,
        )
        state = initialize_canonical_project(
            "project-a",
            design_program_ref=program_ref,
        )
        snapshot = ExpertSnapshot.detach(state, obligation_topics={})

        self.assertEqual(snapshot.design_program_ref, program_ref)
        self.assertIsNone(snapshot.program_json)


if __name__ == "__main__":
    unittest.main()
