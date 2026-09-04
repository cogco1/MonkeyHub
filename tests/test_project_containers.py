"""ADR-007: the four container states, read off one project.

Work in progress is a loose authored file; shared is a run directory; the
published container is HEAD; the archive is every snapshot HEAD left behind.
Nothing here writes, and a run that cannot be read is still reported as the
container it is.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from archflow.project.containers import (
    Container,
    ContainerError,
    ContainerState,
    StatusCode,
    archived,
    published,
    shared,
    work_in_progress,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import RunRef
from archflow.project.repository import FilesystemProjectRepository


PROJECT_ID = "demo"


class ProjectContainersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id=PROJECT_ID,
            initial_state={"schema": "TestState@1"},
        )
        self.layout = self.repository.layout

    # ---- helpers
    def make_run(self, run_id: str) -> RunRef:
        return self.repository.create_run(run_id)

    def put(self, run: RunRef, kind: str, payload: dict[str, object]) -> None:
        self.repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=run.run_id
            ),
            record_kind=kind,
            payload=payload,
        )

    def receipt(self, run: RunRef, **extra: object) -> None:
        payload: dict[str, object] = {
            "schema": "RunnerRunReceipt@3",
            "project_id": PROJECT_ID,
            "run_id": run.run_id,
            "seat_execution_complete": True,
        }
        payload.update(extra)
        self.put(run, "runner-run-receipt", payload)

    def closure(self, run: RunRef, status: str = "SATISFIED") -> None:
        self.put(
            run,
            "stage-closure",
            {
                "schema": "CompositeStageClosureReceipt@1",
                "stage_id": "stage-0",
                "status": status,
            },
        )

    def author_record(self, path: Path, payload: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    # ---- work in progress
    def test_an_authored_slot_is_one_work_in_progress_container(self) -> None:
        self.author_record(self.layout.authored_record, {"schema": "x"})
        containers = work_in_progress(self.repository)
        self.assertEqual(len(containers), 1)
        container = containers[0]
        self.assertEqual(container.state, ContainerState.WORK_IN_PROGRESS)
        self.assertEqual(container.status_code, StatusCode.WORK_IN_PROGRESS)
        self.assertEqual(container.status_code, "S0")
        self.assertEqual(container.project_id, PROJECT_ID)
        self.assertIsNone(container.run_id)
        self.assertIsNone(container.author)
        self.assertEqual(container.ref, str(self.layout.authored_record))
        self.assertIn("runner", container.note)
        self.assertIn("no seat pack", container.note)

    def test_the_seat_pack_beside_it_shows_in_the_note(self) -> None:
        self.author_record(self.layout.authored_record, {"schema": "x"})
        self.author_record(self.layout.seat_pack, {"schema": "RunnerSeats@1"})
        self.assertIn("seats beside it", work_in_progress(self.repository)[0].note)

    def test_an_absent_slot_is_empty_not_a_refusal(self) -> None:
        self.assertEqual(work_in_progress(self.repository), ())
        self.assertEqual(work_in_progress(self.repository, author="kaiwen"), ())

    def test_a_named_author_reads_that_author_s_slot(self) -> None:
        self.author_record(
            self.layout.resolve_relative("input/kaiwen/state-record.json"),
            {"schema": "x"},
        )
        self.assertEqual(work_in_progress(self.repository), ())
        containers = work_in_progress(self.repository, author="kaiwen")
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0].author, "kaiwen")
        self.assertIn("kaiwen", containers[0].note)

    def test_the_note_carries_the_digest_of_the_authored_bytes(self) -> None:
        import hashlib

        path = self.author_record(self.layout.authored_record, {"schema": "x"})
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertIn(digest[:12], work_in_progress(self.repository)[0].note)

    # ---- shared
    def test_a_run_with_a_complete_receipt_and_no_closure_is_s1(self) -> None:
        run = self.make_run("runner-002")
        self.receipt(run)
        containers = shared(self.repository)
        self.assertEqual(len(containers), 1)
        container = containers[0]
        self.assertEqual(container.state, ContainerState.SHARED)
        self.assertEqual(container.status_code, StatusCode.SHARED_FOR_COORDINATION)
        self.assertEqual(container.status_code, "S1")
        self.assertEqual(container.run_id, "runner-002")
        self.assertEqual(container.ref, f"project://{PROJECT_ID}/runs/runner-002")
        self.assertEqual(
            container.note, "runner-002 · seats complete · no closure retained"
        )

    def test_a_satisfied_closure_beside_it_makes_the_run_s4(self) -> None:
        run = self.make_run("runner-003")
        self.receipt(run)
        self.closure(run)
        container = shared(self.repository)[0]
        self.assertEqual(container.status_code, StatusCode.SHARED_FOR_STAGE_APPROVAL)
        self.assertEqual(container.status_code, "S4")
        self.assertEqual(
            container.note, "runner-003 · seats complete · closure satisfied"
        )

    def test_an_open_closure_does_not_award_stage_approval(self) -> None:
        run = self.make_run("runner-004")
        self.receipt(run)
        self.closure(run, status="OPEN")
        container = shared(self.repository)[0]
        self.assertEqual(container.status_code, "S1")
        self.assertIn("closure open", container.note)

    def test_incomplete_seats_do_not_award_stage_approval(self) -> None:
        run = self.make_run("runner-005")
        self.receipt(run, seat_execution_complete=False)
        self.closure(run)
        container = shared(self.repository)[0]
        self.assertEqual(container.status_code, "S1")
        self.assertIn("seats incomplete", container.note)

    def test_a_run_with_no_receipt_says_so(self) -> None:
        self.make_run("runner-006")
        container = shared(self.repository)[0]
        self.assertEqual(
            container.note, "runner-006 · no run receipt · no closure retained"
        )

    def test_runs_come_back_in_run_id_order(self) -> None:
        for run_id in ("runner-003", "runner-001", "runner-002"):
            self.make_run(run_id)
        self.assertEqual(
            tuple(item.run_id for item in shared(self.repository)),
            ("runner-001", "runner-002", "runner-003"),
        )

    def test_a_branch_filter_keeps_only_the_runs_that_claim_it(self) -> None:
        claimed = self.make_run("runner-007")
        self.receipt(claimed, branch_id="baseline")
        silent = self.make_run("runner-008")
        self.receipt(silent)
        containers = shared(self.repository, branch_id="baseline")
        self.assertEqual(
            tuple(item.run_id for item in containers), ("runner-007",)
        )
        self.assertEqual(containers[0].branch_id, "baseline")
        self.assertEqual(shared(self.repository, branch_id="other"), ())

    def test_an_unreadable_run_is_reported_not_skipped(self) -> None:
        good = self.make_run("runner-009")
        self.receipt(good)
        broken = self.layout.runs / "runner-010"
        broken.mkdir(parents=True, exist_ok=True)
        containers = shared(self.repository)
        self.assertEqual(
            tuple(item.run_id for item in containers),
            ("runner-009", "runner-010"),
        )
        self.assertEqual(containers[1].note, "run manifest unreadable")
        self.assertEqual(containers[1].state, ContainerState.SHARED)
        self.assertEqual(containers[1].status_code, "S1")

    def test_a_project_with_no_runs_shares_nothing(self) -> None:
        self.assertEqual(shared(self.repository), ())

    # ---- published and archived
    def test_head_is_the_one_published_container(self) -> None:
        container = published(self.repository)
        self.assertIsInstance(container, Container)
        self.assertEqual(container.state, ContainerState.PUBLISHED)
        self.assertEqual(container.status_code, StatusCode.PUBLISHED)
        self.assertEqual(container.status_code, "A")
        self.assertEqual(container.note, "issue 0")
        self.assertTrue(
            container.ref.startswith(f"project://{PROJECT_ID}/canonical/state-v000000-")
        )
        self.assertIsNone(container.run_id)

    def test_a_fresh_project_has_an_empty_archive(self) -> None:
        self.assertEqual(archived(self.repository), ())

    def test_an_issue_moves_the_old_snapshot_into_the_archive(self) -> None:
        base = self.repository.read_head()
        run = self.make_run("runner-011")
        decision = self.repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_REVIEW, run_id=run.run_id
            ),
            record_kind="decision-accepted",
            payload={
                "schema": "PromotionDecision@1",
                "status": "accepted",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "checked_state": base.to_dict(),
                "candidate_ref": f"project://{PROJECT_ID}/runs/{run.run_id}",
            },
        )
        prepared = self.repository.prepare_transition(
            run=run,
            expected=base,
            replacement_state={"schema": "TestState@2"},
            decision_receipt=decision,
        )
        self.repository.compare_and_swap(
            expected=prepared.expected,
            event=prepared.event,
            replacement=prepared.replacement,
        )
        self.assertEqual(published(self.repository).note, "issue 1")
        superseded = archived(self.repository)
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0].state, ContainerState.ARCHIVED)
        self.assertEqual(superseded[0].status_code, "A")
        self.assertEqual(superseded[0].note, "issue 0 · superseded")
        self.assertTrue(
            superseded[0].ref.startswith(
                f"project://{PROJECT_ID}/canonical/state-v000000-"
            )
        )

    def test_a_project_whose_head_cannot_be_read_refuses(self) -> None:
        self.layout.head.write_text("{}", encoding="utf-8")
        with self.assertRaises(ContainerError):
            published(self.repository)
        with self.assertRaises(ContainerError):
            archived(self.repository)


if __name__ == "__main__":
    unittest.main()
