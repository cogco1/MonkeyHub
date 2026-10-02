"""One process writes a project at a time: the writer lease on ``writer.lock`` (ADR-012, #599).

The lease is an OS lock that ends with the process holding it. While another
process holds it, every write is refused with ``PROJECT_WRITER_BUSY`` and the
project is left exactly as it was; reads are never refused. A process that
does not hold it takes it for the length of one write.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from archflow.project import writer_lease
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD, STUDIO_LOCAL_DRAFT
from archflow.project.repository import FilesystemProjectRepository, ProjectWriterBusy, add_write_observer
from archflow.project.writer_lease import WRITER_LOCK, hold_writer_lease, holds_writer_lease

NOW = "2026-10-02T12:00:00+00:00"
OLD = "2026-09-01T00:00:00+00:00"
# A second process imports the kernel this suite belongs to, not an installed one.
CHILD_ENV = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
# Holds the lease of the project at argv[1] until told to release it, then until told to exit.
HOLDER = """import sys
from archflow.project.writer_lease import hold_writer_lease
lease = hold_writer_lease(sys.argv[1], wait_s=float(sys.argv[2]))
print('held', flush=True)
sys.stdin.readline()
lease.release()
print('released', flush=True)
sys.stdin.readline()
"""
# Creates a project at argv[1].
CREATE = """import sys
from archflow.project.repository import FilesystemProjectRepository
FilesystemProjectRepository.initialize(sys.argv[1], project_id='fresh', initial_state={})
"""
# Tries one write of the project at argv[1] and says how it ended.
WRITER = """import sys
from archflow.project import writer_lease
from archflow.project.repository import FilesystemProjectRepository, ProjectWriterBusy
writer_lease.WRITE_WAIT_S = float(sys.argv[2])
repository = FilesystemProjectRepository.open(sys.argv[1])
try:
    repository.create_run('from-another-process')
except ProjectWriterBusy as exc:
    print(exc.code, flush=True)
else:
    print('written', flush=True)
"""


class WriterLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)
        self.repo = FilesystemProjectRepository.initialize(
            self.temporary / "project", project_id="building", initial_state={})
        self.root = self.repo.layout.root
        self.run = self.repo.create_run("source")
        value, revision = self.repo.read_working_draft()
        value["runs"]["source"] = {"automatic": True, "label": None, "updatedAt": OLD,
                                   "sourceStageRef": None, "branchId": None}
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)

    def hold_elsewhere(self, *, wait_s: float = 10.0) -> subprocess.Popen:
        """Another process that holds the lease until this test says otherwise."""

        child = subprocess.Popen([sys.executable, "-u", "-c", HOLDER, str(self.root), str(wait_s)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=CHILD_ENV)

        def stop() -> None:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

        self.addCleanup(stop)
        self.assertEqual(child.stdout.readline().strip(), "held", child.stderr.read() if child.poll() is not None else "")
        return child

    def write_elsewhere(self, *, wait_s: float) -> str:
        """One write from another process: ``written`` or the code it was refused with."""

        done = subprocess.run([sys.executable, "-c", WRITER, str(self.root), str(wait_s)],
                              capture_output=True, text=True, env=CHILD_ENV, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def state(self) -> dict[str, tuple[int, int]]:
        """Every file and folder of the project with its size and modification time."""

        found = {".": (0, os.stat(self.root).st_mtime_ns)}
        for path in self.root.rglob("*"):
            stat = path.stat()
            found[path.relative_to(self.root).as_posix()] = (0 if path.is_dir() else stat.st_size, stat.st_mtime_ns)
        return found

    def test_a_second_writer_is_refused_while_another_process_holds_the_lease_and_nothing_is_written(self) -> None:
        self.hold_elsewhere()
        records = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="source")
        value, revision = self.repo.read_working_draft()
        writes = {
            "a run": lambda: self.repo.create_run("refused"),
            "a record": lambda: self.repo.put_json(run=self.run, destination=records, record_kind=STATE_RECORD,
                                                   payload={"refused": True}),
            "an object": lambda: self.repo.ingest(run=self.run, destination=PersistenceDestination(PersistenceArea.OBJECT),
                                                  artifact_id="model", media_type="model/3dm",
                                                  source=io.BytesIO(b"refused")),
            "the working position": lambda: self.repo.compare_and_swap_working_draft(
                expected_revision=revision, value={**value, "current": "source"}),
            "a design branch": lambda: self.repo.compare_and_swap_design_branch(
                branch_id="main", expected_head=None, branch={}),
            "the recovery expiry": lambda: self.repo.prune_working_draft(now=NOW),
            "the trash": lambda: self.repo.trash_run("source", now=NOW, rule="superseded", reason="refused"),
            "the source guard": lambda: self.repo.working_draft_guard().__enter__(),
            "a new project here": lambda: FilesystemProjectRepository.initialize(
                self.root, project_id="building", initial_state={}),
        }
        before = self.state()
        with patch.object(writer_lease, "WRITE_WAIT_S", 0.2):
            for name, write in writes.items():
                with self.subTest(write=name), self.assertRaises(ProjectWriterBusy) as refused:
                    write()
                self.assertEqual(refused.exception.code, "PROJECT_WRITER_BUSY")
        self.assertEqual(self.state(), before, "a refused write touched the project")
        # Reads are never refused, and read without the lease.
        reopened = FilesystemProjectRepository.open(self.root)
        self.assertEqual(reopened.read_head(), self.repo.read_head())
        self.assertEqual(reopened.run_ids(), ("source",))
        self.assertEqual(reopened.read_working_draft(), (value, revision))
        reopened.verify()
        self.assertFalse(holds_writer_lease(self.root))
        self.assertEqual(self.state(), before, "a read touched the project")

    def test_the_lease_ends_when_its_holder_releases_it_and_survives_no_crash(self) -> None:
        holder = self.hold_elsewhere()
        self.assertEqual(self.write_elsewhere(wait_s=0.2), "PROJECT_WRITER_BUSY")
        holder.stdin.write("\n")
        holder.stdin.flush()
        self.assertEqual(holder.stdout.readline().strip(), "released")
        self.repo.create_run("after-release")

        crashed = self.hold_elsewhere()
        with patch.object(writer_lease, "WRITE_WAIT_S", 0.2), self.assertRaises(ProjectWriterBusy):
            self.repo.create_run("while-held")
        crashed.kill()
        crashed.wait(timeout=10)
        # The operating system ended the lock with its process; the next write waits out a slow release.
        self.repo.create_run("after-crash")
        self.assertEqual(self.repo.run_ids(), ("after-crash", "after-release", "source"))
        self.assertTrue((self.root / WRITER_LOCK).is_file(), "the file stays: it never was the lock")

    def test_a_write_in_a_process_that_does_not_hold_the_lease_takes_it_for_its_length(self) -> None:
        seen: list[bool] = []
        self.addCleanup(add_write_observer(self.root, lambda path: seen.append(holds_writer_lease(self.root))))
        self.assertFalse(holds_writer_lease(self.root))
        self.repo.create_run("lone")
        self.assertTrue(seen and all(seen), "the write ran under the lease")
        self.assertFalse(holds_writer_lease(self.root), "and gave it back")
        # A file left behind holds nothing: another process takes the lease at once.
        self.assertTrue((self.root / WRITER_LOCK).is_file())
        holder = self.hold_elsewhere(wait_s=0)
        holder.stdin.write("\n")
        holder.stdin.flush()
        self.assertEqual(holder.stdout.readline().strip(), "released")

    def test_the_holding_process_writes_as_before_and_its_holds_are_counted(self) -> None:
        first = hold_writer_lease(self.root, wait_s=0)
        second = hold_writer_lease(self.root, wait_s=0)
        self.addCleanup(second.release)
        self.repo.create_run("held")
        drafts = self.repo.create_run("studio-working-draft")
        snapshot = self.repo.put_json(
            run=drafts, destination=PersistenceDestination(PersistenceArea.RUN_RECOVERY, run_id=drafts.run_id),
            record_kind=STUDIO_LOCAL_DRAFT,
            payload={"schema": "StudioLocalDraft@1", "projectId": "building", "updatedAt": OLD,
                     "draft": {"source": {"projectId": "building", "sourceRunId": "source", "sourceStageRef": None,
                                          "stateDigest": "a" * 64}, "commands": [], "attempt": None}})
        # The HEAD and design locks keep working inside the lease: the expiry takes both.
        self.assertEqual(self.repo.prune_working_draft(now=NOW), (snapshot.relative_path,))
        first.release()
        first.release()
        self.assertTrue(holds_writer_lease(self.root), "one hold is still held")
        self.assertEqual(self.write_elsewhere(wait_s=0.2), "PROJECT_WRITER_BUSY")
        second.release()
        self.assertFalse(holds_writer_lease(self.root))
        self.assertEqual(self.write_elsewhere(wait_s=0.2), "written")

    def test_a_write_waits_for_a_writer_that_finishes(self) -> None:
        holder = self.hold_elsewhere()
        # Released while this write is already waiting for it.
        holder.stdin.write("\n")
        holder.stdin.flush()
        self.repo.create_run("waited")
        self.assertIn("waited", self.repo.run_ids())

    def test_a_lease_first_named_by_a_relative_path_stays_on_its_project(self) -> None:
        # A project this process has never written, so its first lease here is the relative one.
        fresh = self.temporary / "fresh"
        subprocess.run([sys.executable, "-c", CREATE, str(fresh)], env=CHILD_ENV, check=True, timeout=60)
        elsewhere = self.temporary / "elsewhere"
        elsewhere.mkdir()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(fresh.parent)
        hold_writer_lease(Path(fresh.name), wait_s=0).release()
        os.chdir(elsewhere)
        repository = FilesystemProjectRepository.open(fresh)
        repository.create_run("after-a-move")
        self.assertIn("after-a-move", repository.run_ids())
        self.assertEqual(list(elsewhere.iterdir()), [], "the lease was taken again in the working directory")

    def test_a_held_lease_leaves_the_project_folder_readable_and_copyable(self) -> None:
        self.hold_elsewhere()
        self.assertEqual((self.root / WRITER_LOCK).read_bytes(), b"")
        copy = shutil.copytree(self.root, self.temporary / "copy")
        self.assertEqual(FilesystemProjectRepository.open(copy).run_ids(), ("source",))


if __name__ == "__main__":
    unittest.main()
