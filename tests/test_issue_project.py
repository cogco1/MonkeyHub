"""ADR-007 rule 5: the act that publishes a run, and everything it refuses.

An issue cites the closure of the stage the run closed. There is no issue
without a satisfied closure, none from a run developed against a version that
is no longer published, and none from a run that did not finish. The closure
and the exit binding are built here the way the runner will write them.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from archflow.project.containers import published
from archflow.project.issue import (
    NoSatisfiedClosure,
    RunNotComplete,
    StaleBase,
    issue_run,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    DEVELOPED_DESIGN_STATE,
    RUNNER_RUN_RECEIPT,
    STAGE_CLOSURE,
    STAGE_EXIT_BINDING,
    STATE_RECORD,
)
from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.stage_workflow import (
    CompositeStageClosureReceipt,
    StageClosureFinding,
    StageClosureFindingCode,
    StageClosureStatus,
    StageExitBinding,
)
from tools.issue_project import main


PROJECT_ID = "demo"
STAGE_ID = "stage-1-geometry"
SUBJECT_REF = "state:developed-design-state"
STATE_DIGEST = "a" * 64
RECORD_DIGEST = "b" * 64
INITIAL_STATE = {
    "schema": "CanonicalProjectState@1",
    "authoritative_record_refs": [],
    "derived_record_refs": [],
    "phase": "project_initialized",
}


class IssueProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id=PROJECT_ID,
            initial_state=INITIAL_STATE,
        )

    # ---- helpers: the records a finished, closed run retains

    def put(
        self,
        run: RunRef,
        kind: str,
        payload: dict[str, object],
    ) -> ProjectRecordRef:
        return self.repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
            record_kind=kind,
            payload=payload,
        )

    def closure_of(
        self,
        run: RunRef,
        *,
        findings: tuple[StageClosureFinding, ...] = (),
        stage_id: str = STAGE_ID,
    ) -> CompositeStageClosureReceipt:
        return CompositeStageClosureReceipt(
            profile_id="stage-1-profile",
            profile_digest="1" * 64,
            stage_id=stage_id,
            branch=BranchRef(run, "main", 1),
            stage_subject_ref=SUBJECT_REF,
            subject_digest=STATE_DIGEST,
            check_receipt_digests=(),
            findings=findings,
            status=(
                StageClosureStatus.OPEN
                if findings
                else StageClosureStatus.SATISFIED
            ),
        )

    def exit_binding_of(
        self,
        run: RunRef,
        closure: CompositeStageClosureReceipt,
        closure_ref: str,
    ) -> StageExitBinding:
        return StageExitBinding(
            project_id=run.project_id,
            run_id=run.run_id,
            base_version=run.base.version,
            base_state_sha256=run.base.require_digest(),
            branch_id="main",
            branch_epoch=1,
            stage_id=closure.stage_id,
            stage_index=1,
            workflow_ref="project:workflow/building-stages-v1",
            workflow_digest="2" * 64,
            envelope_ref="project:runs/envelope",
            envelope_digest="3" * 64,
            close_obligation_id="close-stage-1-geometry",
            subject_ref=SUBJECT_REF,
            state_digest=STATE_DIGEST,
            closure_ref=closure_ref,
            closure_digest=closure.receipt_digest,
        )

    def closed_run(
        self,
        run_id: str,
        *,
        with_closure: bool = True,
        with_binding: bool = True,
        findings: tuple[StageClosureFinding, ...] = (),
        seats_complete: bool = True,
    ) -> tuple[RunRef, dict[str, str]]:
        """One run with the records the runner writes, and their URIs."""

        run = self.repository.create_run(run_id)
        record_ref = self.put(
            run,
            STATE_RECORD,
            {"schema": "StateRecord@1", "record_id": run_id},
        )
        state_ref = self.put(
            run,
            DEVELOPED_DESIGN_STATE,
            {"schema": "DevelopedDesignState@1", "state_digest": STATE_DIGEST},
        )
        refs = {"state_record": record_ref.uri, "design_state": state_ref.uri}
        if with_closure:
            closure = self.closure_of(run, findings=findings)
            closure_ref = self.put(run, STAGE_CLOSURE, closure.to_dict())
            refs["closure"] = closure_ref.uri
            if with_binding:
                self.put(
                    run,
                    STAGE_EXIT_BINDING,
                    self.exit_binding_of(
                        run,
                        closure,
                        closure_ref.uri,
                    ).to_dict(),
                )
        self.put(
            run,
            RUNNER_RUN_RECEIPT,
            {
                "schema": "RunnerRunReceipt@3",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "state_record_ref": record_ref.uri,
                "state_record_digest": RECORD_DIGEST,
                "design_state_ref": state_ref.uri,
                "design_state_digest": STATE_DIGEST,
                "seat_execution_complete": seats_complete,
            },
        )
        return run, refs

    # ---- the issue

    def test_a_closed_run_becomes_issue_one_and_the_state_names_its_records(
        self,
    ) -> None:
        run, refs = self.closed_run("runner-001")

        receipt = issue_run(
            self.repository,
            run_id=run.run_id,
            decided_by="kaiwen",
            note="stage 1 sign-off",
        )

        self.assertEqual(receipt.issue, 1)
        self.assertEqual(receipt.previous, 0)
        self.assertEqual(receipt.run_id, "runner-001")
        self.assertEqual(receipt.stage_id, STAGE_ID)
        self.assertEqual(receipt.closure_ref, refs["closure"])
        self.assertIn("promotion-decision", receipt.decision_ref)
        head = self.repository.read_head()
        self.assertEqual(head.version, 1)
        self.assertEqual(receipt.state_sha256, head.require_digest())
        # The replacement state is the CanonicalProjectState@1 the snapshot
        # has always held: the authored record it was issued from, the
        # developed state and the closure it stood on, and the stage.
        self.assertEqual(
            self.repository.load_current_state(),
            {
                "schema": "CanonicalProjectState@1",
                "authoritative_record_refs": [refs["state_record"]],
                "derived_record_refs": sorted(
                    (refs["design_state"], refs["closure"])
                ),
                "phase": STAGE_ID,
            },
        )
        # The published container is the one HEAD names, and it says so in
        # the words a person reads.
        self.assertEqual(published(self.repository).note, "issue 1")

    def test_the_same_run_cannot_be_issued_twice(self) -> None:
        run, _ = self.closed_run("runner-001")
        issue_run(self.repository, run_id=run.run_id, decided_by="kaiwen")

        with self.assertRaises(StaleBase) as refused:
            issue_run(self.repository, run_id=run.run_id, decided_by="kaiwen")

        self.assertIn("version 0", str(refused.exception))
        self.assertEqual(self.repository.read_head().version, 1)

    def test_a_run_with_no_closure_is_refused(self) -> None:
        run, _ = self.closed_run("runner-001", with_closure=False)

        with self.assertRaises(NoSatisfiedClosure) as refused:
            issue_run(self.repository, run_id=run.run_id, decided_by="kaiwen")

        self.assertIn("retains no stage closure", str(refused.exception))
        self.assertEqual(self.repository.read_head().version, 0)

    def test_a_closure_with_findings_is_refused(self) -> None:
        run, _ = self.closed_run(
            "runner-001",
            findings=(
                StageClosureFinding(
                    code=StageClosureFindingCode.CHECK_FAILED,
                    requirement_id="support-contact",
                ),
            ),
            with_binding=False,
        )

        with self.assertRaises(NoSatisfiedClosure) as refused:
            issue_run(self.repository, run_id=run.run_id, decided_by="kaiwen")

        self.assertIn("check_failed", str(refused.exception))
        self.assertEqual(self.repository.read_head().version, 0)
        self.assertEqual(self.repository.load_current_state(), INITIAL_STATE)

    def test_a_satisfied_closure_with_no_exit_binding_is_refused(self) -> None:
        run, _ = self.closed_run("runner-001", with_binding=False)

        with self.assertRaises(NoSatisfiedClosure) as refused:
            issue_run(self.repository, run_id=run.run_id, decided_by="kaiwen")

        self.assertIn("no stage exit binding", str(refused.exception))
        self.assertEqual(self.repository.read_head().version, 0)

    def test_a_run_whose_seats_did_not_all_execute_is_refused(self) -> None:
        run, _ = self.closed_run("runner-001", seats_complete=False)

        with self.assertRaises(RunNotComplete) as refused:
            issue_run(self.repository, run_id=run.run_id, decided_by="kaiwen")

        self.assertIn("every seat executed", str(refused.exception))
        self.assertEqual(self.repository.read_head().version, 0)

    # ---- the command line

    def test_the_command_prints_the_issue_and_its_refs(self) -> None:
        run, refs = self.closed_run("runner-001")
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            code = main(
                [
                    "--project",
                    str(self.root),
                    "--run",
                    run.run_id,
                    "--decided-by",
                    "kaiwen",
                    "--note",
                    "stage 1 sign-off",
                ]
            )

        printed = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("published: issue 1 (was issue 0)", printed)
        self.assertIn(refs["closure"], printed)
        self.assertIn("kaiwen", printed)
        self.assertIn("stage 1 sign-off", printed)

    def test_the_command_exits_one_with_the_typed_reason(self) -> None:
        run, _ = self.closed_run("runner-001", with_closure=False)
        err = io.StringIO()

        with contextlib.redirect_stderr(err):
            code = main(
                [
                    "--project",
                    str(self.root),
                    "--run",
                    run.run_id,
                    "--decided-by",
                    "kaiwen",
                ]
            )

        self.assertEqual(code, 1)
        self.assertIn("NoSatisfiedClosure", err.getvalue())
        self.assertEqual(self.repository.read_head().version, 0)


if __name__ == "__main__":
    unittest.main()
