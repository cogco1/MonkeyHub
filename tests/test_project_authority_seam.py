from __future__ import annotations

import unittest

from archflow.project import ProjectVersionRef
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


if __name__ == "__main__":
    unittest.main()
