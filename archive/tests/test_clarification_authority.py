from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from archive.archflow.interaction.clarification import AuthorityDecisionReceipt, ClarificationAlternative, ClarificationDisposition, ClarificationEffect, ClarificationRequest, ClarifiedFactValue, CommitmentClarificationAction
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.runtime.clarification import (
    ClarificationDuplicateError,
    ClarificationExpiredError,
    ClarificationResumeStatus,
    ClarificationStaleError,
    ClarificationUnauthorizedError,
    create_clarification_request,
    issue_authority_decision,
    resume_from_clarification,
    validate_authority_decision,
)
from archflow.state.commitments import Commitment, CommitmentKind, CommitmentStatus, CommitmentStrength, CriterionRef, RevisionPolicy
from archflow.state.operational_state import DesignObligation, FactEpistemicStatus, ObligationStatus, OperationalMarkovState, StateDomain, StateFact


CREATED = "2026-07-25T10:00:00Z"
ISSUED = "2026-07-25T10:05:00Z"
VALID = "2026-07-25T11:00:00Z"
EXPIRES = "2026-07-26T10:00:00Z"


def _branch(
    project_id: str = "case-a",
    *,
    branch_id: str = "option-a",
    epoch: int = 0,
) -> BranchRef:
    base = ProjectVersionRef(
        project_id=project_id,
        version=0,
        state_sha256=("a" if project_id == "case-a" else "b") * 64,
    )
    return BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id="run-001",
            base=base,
        ),
        branch_id=branch_id,
        epoch=epoch,
    )


def _state(
    project_id: str = "case-a",
    *,
    branch_id: str = "option-a",
    commitment: Commitment | None = None,
    obligation_id: str = "resolve-design-mode",
) -> OperationalMarkovState:
    unresolved = StateFact(
        domain=StateDomain.BRIEF,
        key="design-mode",
        value="unknown",
        source_ref="evidence://brief/design-mode",
        epistemic_status=FactEpistemicStatus.UNKNOWN,
    )
    obligation = DesignObligation(
        obligation_id=obligation_id,
        statement="A named authority must resolve the design mode.",
        source_ref="evidence://brief/design-mode",
        subject_refs=(
            f"commitment:{commitment.commitment_id}"
            if commitment is not None
            else unresolved.ref,
        ),
        validator_ref="validator:authority-clarification",
    )
    return OperationalMarkovState(
        branch=_branch(project_id, branch_id=branch_id),
        compiler_version="compiler-1",
        phase="brief",
        facts=(unresolved,),
        commitments=((commitment,) if commitment is not None else ()),
        obligations=(obligation,),
        evidence_refs=("evidence://brief/design-mode",),
    )


def _alternative(
    alternative_id: str = "mode-a",
    value: str = "mode-a",
) -> ClarificationAlternative:
    return ClarificationAlternative(
        alternative_id=alternative_id,
        label=f"Select {alternative_id}",
        effect=ClarificationEffect(
            fact_updates=(
                ClarifiedFactValue(
                    domain=StateDomain.BRIEF,
                    key="design-mode",
                    value=value,
                ),
            ),
        ),
    )


def _request(
    state: OperationalMarkovState,
    *,
    alternatives: tuple[ClarificationAlternative, ...] | None = None,
) -> ClarificationRequest:
    return create_clarification_request(
        state,
        obligation_id=state.obligations[0].obligation_id,
        requesting_agent_id="agent-primary",
        authority_ids=("authority-user",),
        question="Which project-scoped interpretation should be used?",
        blocked_reason="The current brief has more than one valid meaning.",
        alternatives=alternatives or (_alternative(),),
        created_at_utc=CREATED,
        expires_at_utc=EXPIRES,
    )


def _receipt(
    request: ClarificationRequest,
    *,
    disposition: ClarificationDisposition = (
        ClarificationDisposition.SELECTED
    ),
    selected_alternative_id: str | None = "mode-a",
    revised_effect: ClarificationEffect | None = None,
) -> AuthorityDecisionReceipt:
    return issue_authority_decision(
        request,
        authority_id="authority-user",
        disposition=disposition,
        selected_alternative_id=selected_alternative_id,
        revised_effect=revised_effect,
        authority_event_ref="event://authority/decision-001",
        issued_at_utc=ISSUED,
        valid_until_utc=VALID,
    )


def _commitment(
    commitment_id: str = "commitment-mode",
    *,
    predecessor_id: str | None = None,
) -> Commitment:
    return Commitment(
        commitment_id=commitment_id,
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.PROPOSED,
        authority_id="authority-user",
        source_event_ref="event://brief/commitment",
        satisfaction_criterion=CriterionRef(
            criterion_id=f"criterion-{commitment_id}",
            provider_id="validator-brief",
            subject_refs=("fact:brief:design-mode",),
        ),
        evidence_refs=("evidence://brief/design-mode",),
        scope_refs=("fact:brief:design-mode",),
        revision_policy=RevisionPolicy.OWNER_ONLY,
        predecessor_id=predecessor_id,
    )


class ClarificationAuthorityTests(unittest.TestCase):
    def test_request_and_receipt_round_trip_bind_exact_state(self) -> None:
        state = _state()
        request = _request(state)
        receipt = _receipt(request)

        self.assertEqual(
            ClarificationRequest.from_dict(request.to_dict()),
            request,
        )
        self.assertEqual(
            AuthorityDecisionReceipt.from_dict(receipt.to_dict()),
            receipt,
        )
        self.assertEqual(request.branch, state.branch)
        self.assertEqual(
            request.operational_state_digest,
            state.state_digest,
        )
        self.assertIn(
            f"obligation:{state.obligations[0].obligation_id}",
            request.target_refs,
        )
        self.assertFalse(
            request.to_dict()["candidate_approval_authority"]
        )
        self.assertFalse(
            receipt.to_dict()["hard_gate_waiver_authority"]
        )

    def test_named_selection_resumes_only_through_decision_compiler(self) -> None:
        state = _state()
        request = _request(state)
        receipt = _receipt(request)

        result = resume_from_clarification(
            request,
            state,
            receipt=receipt,
            now_utc="2026-07-25T10:10:00Z",
        )

        self.assertIs(
            result.status,
            ClarificationResumeStatus.RESUMED,
        )
        self.assertEqual(state.epoch, 0)
        self.assertEqual(result.state.epoch, 1)
        self.assertEqual(
            result.state.value_for_ref("fact:brief:design-mode"),
            "mode-a",
        )
        self.assertIs(
            result.state.obligations[0].status,
            ObligationStatus.SATISFIED,
        )
        self.assertEqual(
            result.operator.base_state_digest,
            state.state_digest,
        )
        self.assertIn(receipt.ref, result.operator.evidence_refs)

    def test_open_revision_is_typed_and_contains_no_transcript(self) -> None:
        state = _state()
        request = _request(state)
        revised = ClarificationEffect(
            fact_updates=(
                ClarifiedFactValue(
                    domain=StateDomain.BRIEF,
                    key="design-mode",
                    value={"mode": "custom", "certainty": "declared"},
                ),
            ),
        )
        receipt = _receipt(
            request,
            disposition=ClarificationDisposition.REVISED,
            selected_alternative_id=None,
            revised_effect=revised,
        )
        result = resume_from_clarification(
            request,
            state,
            receipt=receipt,
            now_utc="2026-07-25T10:10:00Z",
        )

        self.assertEqual(
            result.state.value_for_ref("fact:brief:design-mode"),
            {"mode": "custom", "certainty": "declared"},
        )
        encoded = str(receipt.to_dict()).lower()
        self.assertNotIn("transcript", encoded)
        self.assertNotIn("chat_history", encoded)

    def test_self_unauthorized_stale_duplicate_and_expired_fail_closed(
        self,
    ) -> None:
        state = _state()
        with self.assertRaisesRegex(ValueError, "answer its own"):
            create_clarification_request(
                state,
                obligation_id=state.obligations[0].obligation_id,
                requesting_agent_id="agent-primary",
                authority_ids=("agent-primary",),
                question="Choose.",
                blocked_reason="Authority is unresolved.",
                alternatives=(_alternative(),),
                created_at_utc=CREATED,
                expires_at_utc=EXPIRES,
            )
        request = _request(state)
        with self.assertRaises(ClarificationUnauthorizedError):
            issue_authority_decision(
                request,
                authority_id="authority-other",
                disposition=ClarificationDisposition.SELECTED,
                selected_alternative_id="mode-a",
                authority_event_ref="event://authority/other",
                issued_at_utc=ISSUED,
                valid_until_utc=VALID,
            )
        receipt = _receipt(request)
        with self.assertRaises(ClarificationDuplicateError):
            validate_authority_decision(
                request,
                receipt,
                state,
                now_utc="2026-07-25T10:10:00Z",
                consumed_request_ids=(request.request_id,),
            )
        with self.assertRaises(ClarificationExpiredError):
            validate_authority_decision(
                request,
                receipt,
                state,
                now_utc="2026-07-25T12:00:00Z",
            )
        foreign = replace(receipt, branch=_branch("case-b"))
        with self.assertRaises(ClarificationStaleError):
            validate_authority_decision(
                request,
                foreign,
                state,
                now_utc="2026-07-25T10:10:00Z",
            )
        stale_state = replace(
            state,
            branch=replace(state.branch, epoch=1),
        )
        with self.assertRaises(ClarificationStaleError):
            validate_authority_decision(
                request,
                receipt,
                stale_state,
                now_utc="2026-07-25T10:10:00Z",
            )

    def test_decline_unanswered_and_timeout_preserve_blocked_state(self) -> None:
        state = _state()
        request = _request(state)
        declined = _receipt(
            request,
            disposition=ClarificationDisposition.DECLINED,
            selected_alternative_id=None,
        )
        declined_result = resume_from_clarification(
            request,
            state,
            receipt=declined,
            now_utc="2026-07-25T10:10:00Z",
        )
        unresolved = _receipt(
            request,
            disposition=ClarificationDisposition.UNRESOLVED,
            selected_alternative_id=None,
        )
        unresolved_result = resume_from_clarification(
            request,
            state,
            receipt=unresolved,
            now_utc="2026-07-25T10:10:00Z",
        )
        unanswered = resume_from_clarification(
            request,
            state,
            now_utc="2026-07-25T10:10:00Z",
        )
        timed_out = resume_from_clarification(
            request,
            state,
            now_utc="2026-07-27T10:00:00Z",
        )

        for result in (
            declined_result,
            unresolved_result,
            unanswered,
            timed_out,
        ):
            self.assertIs(
                result.status,
                ClarificationResumeStatus.BLOCKED,
            )
            self.assertEqual(result.state, state)
            self.assertIs(
                result.state.obligations[0].status,
                ObligationStatus.OPEN,
            )

    def test_commitment_authorize_release_and_revision_use_lifecycle(self) -> None:
        proposed = _commitment()
        state = _state(commitment=proposed)
        authorize = ClarificationAlternative(
            alternative_id="authorize",
            label="Authorize the proposed commitment",
            effect=ClarificationEffect(
                commitment_action=(
                    CommitmentClarificationAction.AUTHORIZE
                ),
                commitment_id=proposed.commitment_id,
            ),
        )
        request = _request(state, alternatives=(authorize,))
        receipt = _receipt(
            request,
            selected_alternative_id="authorize",
        )
        authorized = resume_from_clarification(
            request,
            state,
            receipt=receipt,
            now_utc="2026-07-25T10:10:00Z",
        )
        active = authorized.state.commitments[0]
        self.assertIs(active.status, CommitmentStatus.ACTIVE)

        revision_obligation = DesignObligation(
            obligation_id="resolve-commitment-revision",
            statement="A named authority must resolve the revision.",
            source_ref="event://revision/request",
            subject_refs=(f"commitment:{active.commitment_id}",),
        )
        revision_state = replace(
            authorized.state,
            obligations=(revision_obligation,),
        )
        replacement = _commitment(
            "commitment-mode-v2",
            predecessor_id=active.commitment_id,
        )
        revise = ClarificationAlternative(
            alternative_id="revise",
            label="Authorize the named replacement",
            effect=ClarificationEffect(
                commitment_action=CommitmentClarificationAction.REVISE,
                commitment_id=active.commitment_id,
                replacement_commitment_id=replacement.commitment_id,
            ),
        )
        revision_request = _request(
            revision_state,
            alternatives=(revise,),
        )
        revision_receipt = _receipt(
            revision_request,
            selected_alternative_id="revise",
        )
        revised = resume_from_clarification(
            revision_request,
            revision_state,
            receipt=revision_receipt,
            now_utc="2026-07-25T10:10:00Z",
            commitment_catalog=(replacement,),
        )
        by_id = {
            item.commitment_id: item
            for item in revised.state.commitments
        }
        self.assertIs(
            by_id[active.commitment_id].status,
            CommitmentStatus.REVISED,
        )
        self.assertIs(
            by_id[replacement.commitment_id].status,
            CommitmentStatus.ACTIVE,
        )

        release_obligation = replace(
            revision_obligation,
            obligation_id="resolve-commitment-release",
            subject_refs=(f"commitment:{replacement.commitment_id}",),
        )
        release_state = replace(
            revised.state,
            obligations=(release_obligation,),
        )
        release = ClarificationAlternative(
            alternative_id="release",
            label="Release the replacement commitment",
            effect=ClarificationEffect(
                commitment_action=CommitmentClarificationAction.RELEASE,
                commitment_id=replacement.commitment_id,
            ),
        )
        release_request = _request(
            release_state,
            alternatives=(release,),
        )
        release_receipt = _receipt(
            release_request,
            selected_alternative_id="release",
        )
        released = resume_from_clarification(
            release_request,
            release_state,
            receipt=release_receipt,
            now_utc="2026-07-25T10:10:00Z",
        )
        released_by_id = {
            item.commitment_id: item
            for item in released.state.commitments
        }
        self.assertIs(
            released_by_id[replacement.commitment_id].status,
            CommitmentStatus.RELEASED,
        )

    def test_no_downstream_authority_or_instance_defaults(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "cannot write geometry evaluation",
        ):
            ClarifiedFactValue(
                domain=StateDomain.EVALUATION,
                key="hard-gate-result",
                value="pass",
            )
        sufficient = replace(_state(), obligations=())
        with self.assertRaisesRegex(
            ValueError,
            "obligation is missing",
        ):
            create_clarification_request(
                sufficient,
                obligation_id="invented-clarification",
                requesting_agent_id="agent-primary",
                authority_ids=("authority-user",),
                question="Should not be asked.",
                blocked_reason="No real obligation exists.",
                alternatives=(_alternative(),),
                created_at_utc=CREATED,
                expires_at_utc=EXPIRES,
            )

        first = _request(
            _state("case-a"),
            alternatives=(_alternative("alpha", "alpha-value"),),
        )
        second = _request(
            _state("case-b"),
            alternatives=(_alternative("beta", "beta-value"),),
        )
        self.assertNotEqual(first.request_digest, second.request_digest)
        source = (
            Path(__file__).parents[2]
            / "archive" / "archflow"
            / "interaction"
            / "clarification.py"
        ).read_text(encoding="utf-8") + (
            Path(__file__).parents[2]
            / "archive" / "archflow"
            / "runtime"
            / "clarification.py"
        ).read_text(encoding="utf-8")
        lowered = source.lower()
        for forbidden in (
            "pantheon",
            "rotunda",
            "library",
            "16x12",
            "default alternative",
            "candidate approval receipt",
            "hard gate waiver",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
