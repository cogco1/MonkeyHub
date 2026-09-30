"""Remembered reads below a project (GH-365, ADR-008 phase 1a).

A reader keeps what it derived from the P036 files only under the key it was
derived from: a record's digest, or the settled stat stamp of the place it was
read out of. What it keeps is never handed out to be changed, and a write this
process makes is seen by the very next read.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.project.layout import FINGERPRINT_SETTLED_NS
from archflow.project.memo import ContentMemo, PathStamps, settled
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, STATE_RECORD
from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import FilesystemProjectRepository, ProjectIntegrityError

PROJECT_ID = "project-a"
OLD_NS = 10 * FINGERPRINT_SETTLED_NS


def _age(path: Path, when_ns: int) -> None:
    os.utime(path, ns=(when_ns, when_ns))


class ContentMemoTests(unittest.TestCase):
    def test_entries_are_bounded_least_recently_used_first(self) -> None:
        memo = ContentMemo("test", max_entries=2)
        memo.put(("a",), 1)
        memo.put(("b",), 2)
        self.assertEqual(memo.get(("a",)), 1)  # now the most recently used
        memo.put(("c",), 3)

        self.assertNotIn(("b",), memo)
        self.assertEqual((memo.get(("a",)), memo.get(("c",))), (1, 3))
        self.assertEqual(len(memo), 2)

    def test_size_is_bounded_and_a_value_above_the_entry_bound_is_not_kept(self) -> None:
        memo = ContentMemo("test", max_entries=10, max_size=10, max_entry_size=6)
        self.assertTrue(memo.put(("a",), "a", size=4))
        self.assertTrue(memo.put(("b",), "b", size=4))
        self.assertTrue(memo.put(("c",), "c", size=4))  # 12 > 10: the oldest leaves

        self.assertNotIn(("a",), memo)
        self.assertEqual(memo.size, 8)
        self.assertFalse(memo.put(("b",), "too large", size=7))
        self.assertNotIn(("b",), memo, "a refused value also forgets what its key held")
        self.assertEqual(memo.size, 4)

    def test_discard_where_forgets_only_the_matching_keys(self) -> None:
        memo = ContentMemo("test", max_entries=10)
        for key in (("root-1", "x"), ("root-1", "y"), ("root-2", "x")):
            memo.put(key, key[1], size=1)

        self.assertEqual(memo.discard_where(lambda key: key[0] == "root-1"), 2)
        self.assertEqual([key for key in (("root-1", "x"), ("root-2", "x")) if key in memo], [("root-2", "x")])
        self.assertEqual(memo.size, 1)

    def test_a_stamp_is_settled_only_once_it_is_older_than_the_racy_window(self) -> None:
        now = time.time_ns()
        self.assertFalse(settled(now - FINGERPRINT_SETTLED_NS // 2, now))
        self.assertTrue(settled(now - 2 * FINGERPRINT_SETTLED_NS, now))

    def test_path_stamps_state_absence_and_refuse_a_fresh_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "tree"
            (folder / "inner").mkdir(parents=True)
            (folder / "inner" / "file.bin").write_bytes(b"one")
            fresh = PathStamps()
            fresh.tree(folder)
            self.assertIsNone(fresh.value(), "a time inside the racy window is no key")

            old = time.time_ns() - OLD_NS
            for path in (folder / "inner" / "file.bin", folder / "inner", folder):
                _age(path, old)
            first, again = PathStamps(), PathStamps()
            for stamps in (first, again):
                stamps.tree(folder)
                stamps.file(folder / "missing.bin")
            self.assertIsNotNone(first.value())
            self.assertEqual(first.value(), again.value())

            (folder / "inner" / "file.bin").write_bytes(b"two")
            _age(folder / "inner" / "file.bin", old + 1_000_000)
            changed = PathStamps()
            changed.tree(folder)
            changed.file(folder / "missing.bin")
            self.assertNotEqual(changed.value(), first.value())


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

    def settle(self) -> int:
        """Age every time in the project past the racy window; the time it was aged to."""

        old = time.time_ns() - OLD_NS
        for folder, _, names in os.walk(self.root):
            for name in names:
                _age(Path(folder, name), old)
        for folder, _, _ in os.walk(self.root, topdown=False):
            _age(Path(folder), old)
        return old

    def put(self, payload: dict, *, kind: str = STATE_RECORD) -> ProjectRecordRef:
        return self.repository.put_json(run=self.run, destination=self.records, record_kind=kind, payload=payload)

    def reads(self):
        """Count the record files read from disk while the context is open."""

        read_bytes, names = Path.read_bytes, []

        def counted(path: Path) -> bytes:
            names.append(path.name)
            return read_bytes(path)

        return names, patch.object(Path, "read_bytes", autospec=True, side_effect=counted)


class RecordBytesTests(_ProjectCase):
    def test_every_load_gets_a_payload_of_its_own_from_bytes_read_once(self) -> None:
        ref = self.put({"schema": "StateRecord@1", "value": [1, 2]})
        self.settle()
        first = self.repository.load_json(ref)
        first["value"].append(3)
        first["added"] = True

        names, counting = self.reads()
        with counting:
            second = self.repository.load_json(ref)
            third = self.repository.load_json(ref)
        self.assertEqual(second, {"schema": "StateRecord@1", "value": [1, 2]})
        self.assertIsNot(second, third)
        self.assertIsNot(second["value"], third["value"])
        self.assertNotIn(Path(ref.relative_path).name, names, "verified bytes are not read again")

    def test_a_replaced_or_missing_record_is_read_and_refused_again(self) -> None:
        ref = self.put({"schema": "StateRecord@1", "value": 1})
        old = self.settle()
        self.repository.load_json(ref)
        path = self.repository.layout.resolve_record(ref)

        path.write_bytes(b'{"schema":"StateRecord@1","value":2}\n')
        _age(path, old + 1_000_000)
        with self.assertRaisesRegex(ProjectIntegrityError, "record digest mismatch"):
            self.repository.load_json(ref)
        path.unlink()
        with self.assertRaisesRegex(ProjectIntegrityError, "cannot read project record"):
            self.repository.load_json(ref)

    def test_a_record_inside_the_racy_window_is_not_kept(self) -> None:
        ref = self.put({"schema": "StateRecord@1", "value": 1})
        self.repository.load_json(ref)
        names, counting = self.reads()
        with counting:
            self.repository.load_json(ref)
        self.assertIn(Path(ref.relative_path).name, names)

    def test_verify_reads_every_file_itself(self) -> None:
        self.put({"schema": "StateRecord@1", "value": 1})
        self.settle()
        self.repository.verify()
        snapshot = self.repository._read_head_document()[1]
        names, counting = self.reads()
        with counting:
            self.repository.verify()
        self.assertIn(Path(snapshot.relative_path).name, names)


class ListingTests(_ProjectCase):
    def test_a_listing_is_kept_until_its_directory_moves(self) -> None:
        first = self.put({"schema": "StateRecord@1", "value": 1})
        self.settle()
        self.assertEqual(self.repository.list_json(run=self.run, destination=self.records), (first,))
        names, counting = self.reads()
        with counting:
            self.assertEqual(self.repository.list_json(run=self.run, destination=self.records), (first,))
            self.assertEqual(
                self.repository.list_json(run=self.run, destination=self.records, record_kind=STATE_RECORD), (first,),
            )
        self.assertEqual(names, [])

    def test_a_record_this_process_writes_is_listed_at_once(self) -> None:
        first = self.put({"schema": "StateRecord@1", "value": 1})
        old = self.settle()
        directory = self.repository.layout.run("run-001").records
        self.assertEqual(self.repository.list_json(run=self.run, destination=self.records), (first,))

        second = self.put({"schema": "StateRecord@1", "value": 2})
        # Put the directory's time back, so only the write itself can say that
        # the listing moved: the repository forgets it when the write lands.
        _age(directory, old)
        listed = self.repository.list_json(run=self.run, destination=self.records)

        self.assertEqual(sorted(ref.relative_path for ref in listed),
                         sorted(ref.relative_path for ref in (first, second)))

    def test_another_process_s_record_is_listed_once_the_directory_moves(self) -> None:
        first = self.put({"schema": "StateRecord@1", "value": 1})
        self.settle()
        self.assertEqual(self.repository.list_json(run=self.run, destination=self.records), (first,))

        other = FilesystemProjectRepository.open(self.root)
        with patch("archflow.project.repository._forget_listings"):
            second = other.put_json(run=self.run, destination=self.records, record_kind=STATE_RECORD,
                                    payload={"schema": "StateRecord@1", "value": 2})
        listed = self.repository.list_json(run=self.run, destination=self.records)

        self.assertIn(second, listed)

    def test_a_run_manifest_change_is_seen(self) -> None:
        self.settle()
        run = self.repository.load_run("run-001")
        manifest = self.repository.layout.run("run-001").manifest
        manifest.write_text('{"schema":"ProjectRun@1"}', encoding="utf-8")
        with self.assertRaises(ProjectIntegrityError):
            self.repository.load_run("run-001")
        with self.assertRaises(ProjectIntegrityError):
            self.repository.list_json(run=run, destination=self.records)


class DesignBranchTests(_ProjectCase):
    def stage(self, name: str) -> ProjectRecordRef:
        run = self.repository.create_run(name)
        return self.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=name),
            record_kind=DESIGN_STAGE, payload={"schema": "DesignStage@1", "candidate_id": name, "parent_stage": None},
        )

    def branch(self, head: ProjectRecordRef, fork: ProjectRecordRef) -> dict:
        return {"branch_id": "main", "parent_branch": None, "fork_stage": fork.to_dict(), "head_stage": head.to_dict()}

    def test_every_caller_gets_its_own_table(self) -> None:
        s0 = self.stage("initial")
        self.repository.compare_and_swap_design_branch(branch_id="main", expected_head=None, branch=self.branch(s0, s0))
        self.settle()
        first = self.repository.read_design_branches()
        first["main"]["head_stage"]["sha256"] = "0" * 64
        first["other"] = {}

        self.assertEqual(self.repository.read_design_branches(), {"main": self.branch(s0, s0)})

    def test_an_atomic_replace_is_read_in_process_and_from_another_process(self) -> None:
        s0, s1, s2 = self.stage("initial"), self.stage("next"), self.stage("later")
        self.repository.compare_and_swap_design_branch(branch_id="main", expected_head=None, branch=self.branch(s0, s0))
        old = self.settle()
        self.assertEqual(self.repository.read_design_branches()["main"]["head_stage"], s0.to_dict())

        self.repository.compare_and_swap_design_branch(branch_id="main", expected_head=s0, branch=self.branch(s1, s0))
        self.assertEqual(self.repository.read_design_branches()["main"]["head_stage"], s1.to_dict())

        # Another process replaces the file; its time is settled and differs.
        other = FilesystemProjectRepository.open(self.root)
        _age(self.repository.layout.design_branches, old)
        self.assertEqual(self.repository.read_design_branches()["main"]["head_stage"], s1.to_dict())
        with patch("archflow.project.repository._forget_listings"):
            other.compare_and_swap_design_branch(branch_id="main", expected_head=s1, branch=self.branch(s2, s0))
        _age(self.repository.layout.design_branches, old + 1_000_000)
        self.assertEqual(self.repository.read_design_branches()["main"]["head_stage"], s2.to_dict())


if __name__ == "__main__":
    unittest.main()
