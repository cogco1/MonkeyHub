from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archive.archflow.interaction.clarification import AuthorityDecisionReceipt, ClarificationAlternative, ClarificationDisposition, ClarificationEffect, ClarificationRequest, ClarifiedFactValue
from archflow.project.refs import BranchRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.project.bootstrap import bootstrap_raw_request_project
from archive.archflow.runtime.clarification import (
    ClarificationDuplicateError,
    ClarificationError,
    ClarificationResumeStatus,
    create_clarification_request,
    issue_authority_decision,
    persist_authority_decision,
    persist_clarification_request,
    resume_from_clarification,
)
from archflow.state.operational_state import DesignObligation, FactEpistemicStatus, OperationalMarkovState, StateDomain, StateFact


_RETIRED_LANE_KINDS = (
    "a retired lane writes the record kinds this needs; put_json writes only "
    "kinds registered in archflow.project.record_kinds, and a kind no spine "
    "module writes, reads or names is not registered"
)


@unittest.skip(_RETIRED_LANE_KINDS)
class ClarificationResumeIntegrationTests(unittest.TestCase):
    def test_pause_reload_and_resume_need_no_chat_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case-a"
            bootstrap = bootstrap_raw_request_project(
                root,
                project_id="case-a",
                prompt="Create a building for a named use.",
            )
            repository = FilesystemProjectRepository.open(root)
            unresolved = StateFact(
                domain=StateDomain.BRIEF,
                key="design-mode",
                value="unknown",
                source_ref=bootstrap.request.uri,
                epistemic_status=FactEpistemicStatus.UNKNOWN,
            )
            obligation = DesignObligation(
                obligation_id="resolve-design-mode",
                statement="A named authority must resolve design mode.",
                source_ref=bootstrap.request.uri,
                subject_refs=(unresolved.ref,),
            )
            state = OperationalMarkovState(
                branch=BranchRef(
                    run=bootstrap.run,
                    branch_id="option-a",
                    epoch=0,
                ),
                compiler_version="compiler-1",
                phase="brief",
                facts=(unresolved,),
                obligations=(obligation,),
                evidence_refs=(bootstrap.request.uri,),
            )
            state_ref = repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=bootstrap.run.run_id,
                ),
                record_kind="operational-state",
                payload=state.to_dict(),
            )
            alternative = ClarificationAlternative(
                alternative_id="mode-a",
                label="Use the project-scoped first interpretation",
                effect=ClarificationEffect(
                    fact_updates=(
                        ClarifiedFactValue(
                            domain=StateDomain.BRIEF,
                            key="design-mode",
                            value="mode-a",
                        ),
                    ),
                ),
            )
            request = create_clarification_request(
                state,
                obligation_id=obligation.obligation_id,
                requesting_agent_id="agent-primary",
                authority_ids=("authority-user",),
                question="Which interpretation should control this project?",
                blocked_reason="The current request is ambiguous.",
                alternatives=(alternative,),
                created_at_utc="2026-07-25T10:00:00Z",
                expires_at_utc="2026-07-26T10:00:00Z",
            )
            persisted = persist_clarification_request(
                repository,
                run=bootstrap.run,
                request=request,
            )
            receipt = issue_authority_decision(
                request,
                authority_id="authority-user",
                disposition=ClarificationDisposition.SELECTED,
                selected_alternative_id="mode-a",
                authority_event_ref="event://authority/decision-001",
                issued_at_utc="2026-07-25T10:05:00Z",
                valid_until_utc="2026-07-25T11:00:00Z",
            )
            persisted = persist_authority_decision(
                repository,
                run=bootstrap.run,
                request_ref=persisted.request,
                request=request,
                receipt=receipt,
            )
            with self.assertRaises(ClarificationDuplicateError):
                persist_authority_decision(
                    repository,
                    run=bootstrap.run,
                    request_ref=persisted.request,
                    request=request,
                    receipt=receipt,
                )

            reopened = FilesystemProjectRepository.open(root)
            loaded_state = OperationalMarkovState.from_dict(
                reopened.load_json(state_ref)
            )
            loaded_request = ClarificationRequest.from_dict(
                reopened.load_json(persisted.request)
            )
            loaded_receipt = AuthorityDecisionReceipt.from_dict(
                reopened.load_json(persisted.receipt)
            )
            result = resume_from_clarification(
                loaded_request,
                loaded_state,
                receipt=loaded_receipt,
                now_utc="2026-07-25T10:10:00Z",
            )

            self.assertIs(
                result.status,
                ClarificationResumeStatus.RESUMED,
            )
            self.assertEqual(
                result.state.value_for_ref("fact:brief:design-mode"),
                "mode-a",
            )
            all_records = str(
                [
                    reopened.load_json(state_ref),
                    reopened.load_json(persisted.request),
                    reopened.load_json(persisted.receipt),
                ]
            ).lower()
            self.assertNotIn("transcript", all_records)
            self.assertNotIn("chat_history", all_records)

            foreign = bootstrap_raw_request_project(
                Path(temporary) / "case-b",
                project_id="case-b",
                prompt="Create another project.",
            )
            with self.assertRaisesRegex(
                ClarificationError,
                "project run or canonical base",
            ):
                persist_clarification_request(
                    FilesystemProjectRepository.open(
                        Path(temporary) / "case-b"
                    ),
                    run=foreign.run,
                    request=request,
                )


if __name__ == "__main__":
    unittest.main()
