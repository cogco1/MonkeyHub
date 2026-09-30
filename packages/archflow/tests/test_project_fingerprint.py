"""The layout fingerprint and the process write serial (GH-363, ADR-008 phase 0a).

A reader may keep an answer only while nothing it was derived from has moved.
Two signals say so: ``layout_fingerprint`` sees any process's writes through
directory and pointer-file metadata, and ``write_serial`` counts this
process's own writes the moment they land.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from archflow.project.layout import FINGERPRINT_SETTLED_NS, layout_fingerprint
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import PROMOTION_DECISION, STATE_RECORD
from archflow.project.repository import (
    FilesystemProjectRepository,
    add_write_observer,
    write_serial,
)

PROJECT_ID = "project-a"


def _working(label: str) -> dict:
    return {
        "schema": "ProjectWorkingDraft@1",
        "projectId": PROJECT_ID,
        "current": None,
        "runs": {"run-001": {"updatedAt": "2026-09-26T00:00:00+00:00", "sourceStageRef": None,
                             "branchId": None, "label": label, "automatic": False}},
        "active": {},
        "localDraftRef": None,
    }


class _ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"phase": "request"},
        )
        self.run = self.repository.create_run("run-001")
        self.records = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="run-001")

    def settle(self) -> None:
        """Age every time in the project, so the next write cannot share a clock tick."""

        old = time.time_ns() - 10 * FINGERPRINT_SETTLED_NS
        for folder, _, names in os.walk(self.root):
            for name in names:
                os.utime(os.path.join(folder, name), ns=(old, old))
        for folder, _, _ in os.walk(self.root, topdown=False):
            os.utime(folder, ns=(old, old))

    def put(self, payload: dict) -> Path:
        ref = self.repository.put_json(
            run=self.run, destination=self.records, record_kind=STATE_RECORD, payload=payload,
        )
        return self.repository.layout.resolve_record(ref)


class LayoutFingerprintTests(_ProjectCase):
    def test_nothing_written_keeps_the_fingerprint(self) -> None:
        self.settle()
        first, second = layout_fingerprint(self.root), layout_fingerprint(self.root)

        self.assertEqual(first.digest, second.digest)
        self.assertTrue(first.stable and second.stable)
        self.assertEqual(first.newest_mtime_ns, second.newest_mtime_ns)
        self.assertGreater(second.scanned_at_ns, second.newest_mtime_ns)

    def test_reading_writes_nothing_the_fingerprint_sees(self) -> None:
        self.settle()
        before = layout_fingerprint(self.root)
        reopened = FilesystemProjectRepository.open(self.root)
        reopened.read_head()
        reopened.read_working_draft()
        reopened.list_json(run=self.run, destination=self.records)
        with reopened.working_draft_guard():
            pass

        self.assertEqual(layout_fingerprint(self.root).digest, before.digest)

    def test_a_new_file_in_an_existing_record_directory_changes_it(self) -> None:
        self.settle()
        before = layout_fingerprint(self.root)
        self.put({"record": 1})
        after = layout_fingerprint(self.root)

        self.assertNotEqual(after.digest, before.digest)
        self.assertFalse(after.stable)

    def test_a_file_another_process_adds_to_a_record_directory_changes_it(self) -> None:
        self.settle()
        before = layout_fingerprint(self.root)
        records = self.repository.layout.run("run-001").records
        (records / "state-record-external.json").write_bytes(b"{}\n")

        self.assertNotEqual(layout_fingerprint(self.root).digest, before.digest)

    def test_a_new_run_directory_changes_it(self) -> None:
        self.settle()
        before = layout_fingerprint(self.root)
        self.repository.create_run("run-002")

        self.assertNotEqual(layout_fingerprint(self.root).digest, before.digest)

    def test_an_atomic_replace_of_the_working_position_changes_it(self) -> None:
        _, revision = self.repository.read_working_draft()
        _, revision = self.repository.compare_and_swap_working_draft(
            expected_revision=revision, value=_working("first"),
        )
        self.settle()
        before = layout_fingerprint(self.root)
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=_working("second"))
        through_repository = layout_fingerprint(self.root)

        self.assertNotEqual(through_repository.digest, before.digest)

        # The same replacement made by another process, which counts nothing here.
        self.settle()
        settled = layout_fingerprint(self.root)
        working = self.repository.layout.working_draft
        temporary = working.with_name(".working.json.external.tmp")
        temporary.write_bytes(working.read_bytes().replace(b"second", b"third!"))
        os.replace(temporary, working)

        self.assertNotEqual(layout_fingerprint(self.root).digest, settled.digest)

    def test_it_is_not_stable_right_after_a_write(self) -> None:
        self.settle()
        self.assertTrue(layout_fingerprint(self.root).stable)
        self.put({"record": 2})
        fingerprint = layout_fingerprint(self.root)

        self.assertFalse(fingerprint.stable)
        self.assertLessEqual(fingerprint.scanned_at_ns - fingerprint.newest_mtime_ns, FINGERPRINT_SETTLED_NS)

    def test_a_missing_project_is_never_stable(self) -> None:
        fingerprint = layout_fingerprint(self.root.parent / "absent")

        self.assertFalse(fingerprint.stable)


class WriteSerialTests(_ProjectCase):
    def test_put_json_counts(self) -> None:
        before = write_serial(self.root)
        self.put({"record": 3})

        self.assertGreater(write_serial(self.root), before)

    def test_compare_and_swap_counts(self) -> None:
        base = self.repository.read_head()
        decision = self.repository.put_json(
            run=self.run,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id="run-001"),
            record_kind=PROMOTION_DECISION,
            payload={"schema": "PromotionDecision@1", "status": "accepted", "project_id": PROJECT_ID,
                     "run_id": "run-001", "checked_state": base.to_dict(),
                     "candidate_ref": f"project://{PROJECT_ID}/runs/run-001"},
        )
        prepared = self.repository.prepare_transition(
            run=self.run, expected=base, replacement_state={"phase": "candidate"}, decision_receipt=decision,
        )
        before = write_serial(self.root)
        self.repository.compare_and_swap(expected=base, event=prepared.event, replacement=prepared.replacement)

        self.assertGreater(write_serial(self.root), before)

    def test_working_position_compare_and_swap_counts(self) -> None:
        _, revision = self.repository.read_working_draft()
        before = write_serial(self.root)
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=_working("first"))

        self.assertGreater(write_serial(self.root), before)

    def test_reads_do_not_count(self) -> None:
        before = write_serial(self.root)
        reopened = FilesystemProjectRepository.open(self.root)
        reopened.verify()
        reopened.list_json(run=self.run, destination=self.records)
        with reopened.working_draft_guard():
            pass

        self.assertEqual(write_serial(self.root), before)

    def test_every_repository_on_one_root_shares_its_serial(self) -> None:
        other = FilesystemProjectRepository.open(self.root)
        before = write_serial(self.root)
        other.put_json(run=self.run, destination=self.records, record_kind=STATE_RECORD, payload={"record": 4})

        self.assertGreater(write_serial(self.root), before)
        self.assertEqual(write_serial(str(self.root)), write_serial(self.repository.layout.root))

    def test_an_observer_receives_the_written_path(self) -> None:
        seen: list[Path] = []
        remove = add_write_observer(self.root, seen.append)
        self.addCleanup(remove)
        path = self.put({"record": 5})

        self.assertIn(os.path.normcase(str(path)), [os.path.normcase(str(item)) for item in seen])
        remove()
        seen.clear()
        self.put({"record": 6})
        self.assertEqual(seen, [])

    def test_a_raising_observer_does_not_fail_the_write(self) -> None:
        def broken(path: Path) -> None:
            raise RuntimeError(f"observer broke on {path.name}")

        remove = add_write_observer(self.root, broken)
        self.addCleanup(remove)
        before = write_serial(self.root)
        with self.assertLogs("archflow.project.repository", level="ERROR") as logged:
            path = self.put({"record": 7})

        self.assertTrue(path.is_file())
        self.assertGreater(write_serial(self.root), before)
        self.assertIn("observer", "\n".join(logged.output))


if __name__ == "__main__":
    unittest.main()
