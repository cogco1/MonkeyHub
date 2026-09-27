"""The project index and the keeper that is its only writer (GH-365, ADR-008 phase 1b).

The index is a SQLite file of rows one projector derives from a project, kept
outside it. It is never migrated: a different stamp rebuilds it under a new
epoch, and a file SQLite cannot read is moved aside and rebuilt. A kept file
reopened after a restart is used as it is when nothing moved, and only what
moved is projected again. A commit that changed a row moves the revision once.

The keeper applies every change on a thread of its own, fed by the layout
watch and this process's writes: a reader never projects, and a snapshot is
one commit however many applies run beside it.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from archflow.project.index import (
    INDEX_FILE,
    IndexKeeper,
    IndexUnavailable,
    ProjectIndex,
    RecordRow,
    RunRows,
    TreeRows,
    line_places,
    manifest_stamp,
    place_area,
)
from archflow.project.layout import FINGERPRINT_SETTLED_NS, layout_fingerprint
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.watch import _Tree, watch_layout

PROJECT_ID = "project-a"


def settle(root: Path) -> None:
    """Age every time in the project, as if its last write were long ago."""

    old = time.time_ns() - 10 * FINGERPRINT_SETTLED_NS
    for folder, _, names in os.walk(root):
        for name in names:
            os.utime(os.path.join(folder, name), ns=(old, old))
    for folder, _, _ in os.walk(root, topdown=False):
        os.utime(folder, ns=(old, old))


def sight(root: Path) -> tuple[tuple[str, ...], int]:
    """The layout lines of the project now and when they were read, as a watch's walk gives them."""

    tree = _Tree(os.fspath(root))
    scanned_at = time.time_ns()
    tree.walk(lambda: False)
    _, _, lines = tree.fingerprint(scanned_at)
    return lines, scanned_at


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class RecordingProjector:
    """A projector over the repository's own listings that counts what it projects."""

    def __init__(self, repository: FilesystemProjectRepository, version: str = "test-projector@1") -> None:
        self.repository = repository
        self.version = version
        self.projected: list[str] = []
        self.trees = 0
        self.threads: set[str] = set()
        self.delay = 0.0

    def run_ids(self) -> tuple[str, ...]:
        runs = self.repository.layout.runs
        return tuple(sorted(path.name for path in runs.iterdir() if path.is_dir())) if runs.is_dir() else ()

    def project_run(self, run_id: str) -> RunRows:
        self.projected.append(run_id)
        self.threads.add(threading.current_thread().name)
        try:
            run = self.repository.load_run(run_id)
            refs = self.repository.list_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
            )
        except Exception:  # noqa: BLE001 - a run another process has only begun
            return RunRows(run_id, {"unreadable": True})
        if self.delay:
            time.sleep(self.delay)
        return RunRows(run_id, {"records": len(refs)},
                       tuple(RecordRow(ref.uri, ref.record_kind, ref.sha256) for ref in refs))

    def project_tree(self) -> TreeRows:
        self.trees += 1
        try:
            return TreeRows({"branches": sorted(self.repository.read_design_branches())})
        except Exception:  # noqa: BLE001 - a pointer written by someone else
            return TreeRows({"branches_unreadable": True})


class _IndexCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        self.root = temporary / PROJECT_ID
        self.cache = temporary / "cache" / "projects" / "runtime-a"
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"phase": "request"},
        )
        self.repository.create_run("run-001")
        self.put("run-001", {"record": 1})

    def put(self, run_id: str, payload: dict) -> Path:
        ref = self.repository.put_json(
            run=self.repository.load_run(run_id),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
            record_kind=STATE_RECORD, payload=payload,
        )
        return self.repository.layout.resolve_record(ref)

    def index(self, projector: RecordingProjector | None = None) -> tuple[ProjectIndex, RecordingProjector]:
        projector = projector or RecordingProjector(self.repository)
        index = ProjectIndex(
            self.cache, projector=projector,
            stamp=lambda: manifest_stamp(self.root, projector_version=projector.version, project_id=PROJECT_ID),
        )
        self.addCleanup(index.close)
        return index, projector

    def load(self, index: ProjectIndex):
        return index.load(*sight(self.root))

    def records(self, index: ProjectIndex, run_id: str) -> int:
        return len(index.query("record", {"run_id": run_id}))


class RebuildTests(_IndexCase):
    def test_a_first_load_builds_the_file_outside_the_project(self) -> None:
        index, projector = self.index()
        token = self.load(index)

        self.assertEqual(index.loaded, "rebuilt")
        self.assertEqual(token.revision, 1)
        self.assertTrue((self.cache / INDEX_FILE).is_file())
        self.assertFalse(any(name.startswith("index") for name in os.listdir(self.root)))
        self.assertEqual(projector.projected, ["run-001"])
        self.assertEqual(self.records(index, "run-001"), 1)
        meta = index.meta()
        self.assertEqual((meta["project_id"], meta["schema_version"]), (PROJECT_ID, "2"))
        self.assertNotIn(os.fspath(self.root), repr(meta), "no machine path is kept as identity")

    def test_a_different_projector_version_rebuilds_under_a_new_epoch(self) -> None:
        settle(self.root)
        first, _ = self.index()
        epoch = self.load(first).epoch
        first.close()

        again, projector = self.index(RecordingProjector(self.repository, version="test-projector@2"))
        token = self.load(again)

        self.assertEqual(again.loaded, "rebuilt")
        self.assertNotEqual(token.epoch, epoch)
        self.assertEqual(token.revision, 1)
        self.assertEqual(projector.projected, ["run-001"])
        self.assertEqual(again.meta()["projector_version"], "test-projector@2")

    def test_a_changed_manifest_rebuilds_on_open(self) -> None:
        settle(self.root)
        first, _ = self.index()
        epoch = self.load(first).epoch
        first.close()
        manifest = self.root / "project.json"
        manifest.write_bytes(manifest.read_bytes().replace(b"\n", b" \n", 1))

        again, _ = self.index()
        self.assertNotEqual(self.load(again).epoch, epoch)
        self.assertEqual(again.loaded, "rebuilt")

    def test_a_deleted_index_is_rebuilt_on_the_next_load(self) -> None:
        settle(self.root)
        first, _ = self.index()
        epoch = self.load(first).epoch
        first.close()
        for name in os.listdir(self.cache):
            if name.startswith(INDEX_FILE):
                os.unlink(self.cache / name)

        again, projector = self.index()
        token = self.load(again)
        self.assertEqual(again.loaded, "rebuilt")
        self.assertNotEqual(token.epoch, epoch)
        self.assertEqual(projector.projected, ["run-001"])
        self.assertEqual(self.records(again, "run-001"), 1)

    def test_a_corrupt_file_is_moved_aside_rebuilt_once_and_warned_about(self) -> None:
        first, _ = self.index()
        self.load(first)
        first.close()
        (self.cache / INDEX_FILE).write_bytes(b"this is not a database" * 64)

        again, projector = self.index()
        with self.assertLogs("archflow.project.index.store", level="WARNING") as logged:
            token = self.load(again)

        self.assertEqual(again.loaded, "rebuilt")
        self.assertEqual(token.revision, 1)
        self.assertEqual(projector.projected, ["run-001"])
        aside = [name for name in os.listdir(self.cache) if name.startswith(INDEX_FILE + ".corrupt-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual((self.cache / aside[0]).read_bytes()[:22], b"this is not a database")
        self.assertIn("moved aside", "\n".join(logged.output))
        self.assertEqual(self.records(again, "run-001"), 1)

        again.close()
        (self.cache / INDEX_FILE).write_bytes(b"broken again" * 64)
        third, _ = self.index()
        with self.assertLogs("archflow.project.index.store", level="WARNING"):
            self.load(third)
        aside = [name for name in os.listdir(self.cache) if ".corrupt-" in name]
        self.assertEqual(len(aside), 1, "only the latest failure is kept to look at")
        self.assertEqual((self.cache / aside[0]).read_bytes()[:12], b"broken again")

    def test_a_sqlite_file_of_another_shape_is_rebuilt(self) -> None:
        self.cache.mkdir(parents=True)
        connection = sqlite3.connect(self.cache / INDEX_FILE)
        connection.execute("CREATE TABLE unrelated (x)")
        connection.commit()
        connection.close()
        index, _ = self.index()
        with self.assertLogs("archflow.project.index.store", level="WARNING"):
            self.load(index)

        self.assertEqual(index.loaded, "rebuilt")
        self.assertEqual(self.records(index, "run-001"), 1)

    def test_a_second_writer_is_refused(self) -> None:
        first, _ = self.index()
        self.load(first)
        second, _ = self.index()

        with self.assertRaises(IndexUnavailable):
            self.load(second)


class RestartTests(_IndexCase):
    def test_a_kept_index_is_reused_when_nothing_moved(self) -> None:
        settle(self.root)
        first, _ = self.index()
        token = self.load(first)
        first.close()

        again, projector = self.index()
        self.assertEqual(self.load(again), token)
        self.assertEqual(again.loaded, "reused")
        self.assertEqual(projector.projected, [])
        self.assertEqual(projector.trees, 0)
        self.assertEqual(self.records(again, "run-001"), 1)

    def test_what_moved_while_closed_is_projected_again_and_nothing_else(self) -> None:
        self.repository.create_run("run-002")
        settle(self.root)
        first, _ = self.index()
        token = self.load(first)
        first.close()
        written = self.put("run-002", {"record": 2})
        # Aged on its own: everything nobody wrote keeps the times it was indexed with.
        old = time.time_ns() - 5 * FINGERPRINT_SETTLED_NS
        for path in (written, written.parent):
            os.utime(path, ns=(old, old))

        again, projector = self.index()
        reopened = self.load(again)

        self.assertEqual(again.loaded, "reconciled")
        self.assertEqual((reopened.epoch, reopened.revision), (token.epoch, token.revision + 1))
        self.assertEqual(projector.projected, ["run-002"])
        self.assertEqual(self.records(again, "run-002"), 1)

    def test_a_kept_index_written_under_racy_times_is_checked_again(self) -> None:
        first, _ = self.index()
        self.load(first)
        first.close()
        settle(self.root)

        again, projector = self.index()
        self.load(again)

        self.assertEqual(again.loaded, "reconciled", "times too new at the last scan are not trusted")
        self.assertEqual(projector.projected, ["run-001"])


class ApplyTests(_IndexCase):
    def setUp(self) -> None:
        super().setUp()
        settle(self.root)
        self.index_, self.projector = self.index()
        self.load(self.index_)
        self.projector.projected.clear()
        self.trees = self.projector.trees

    def sync(self):
        return self.index_.apply(*sight(self.root))

    def test_nothing_changed_commits_nothing(self) -> None:
        token = self.sync()
        self.assertEqual(self.sync(), token)
        self.assertEqual(self.projector.projected, [])

    def test_changes_move_the_revision_once_and_project_only_their_run(self) -> None:
        self.repository.create_run("run-002")
        token = self.sync()
        self.assertEqual(token.revision, 2)
        self.put("run-001", {"record": 2})
        self.put("run-001", {"record": 3})

        after = self.sync()
        self.assertEqual(after.revision, token.revision + 1, "one commit for the changes one apply found")
        self.assertEqual(self.projector.projected, ["run-002", "run-001"])
        self.assertEqual(self.records(self.index_, "run-001"), 3)

    def test_a_written_area_is_projected_without_a_new_sighting(self) -> None:
        self.put("run-001", {"record": 2})
        token = self.index_.apply(None, 0, areas={"run:run-001"})

        self.assertEqual(token.revision, 2)
        self.assertEqual(self.projector.projected, ["run-001"])
        self.assertEqual(self.records(self.index_, "run-001"), 2)

    def test_projecting_rows_that_did_not_change_does_not_move_the_revision(self) -> None:
        token = self.index_.apply(None, 0, areas={"run:run-001"})

        self.assertEqual(self.projector.projected, ["run-001"])
        self.assertEqual(token.revision, 1)

    def test_a_removed_run_leaves_the_index(self) -> None:
        self.repository.create_run("run-002")
        self.sync()
        shutil.rmtree(self.root / "runs" / "run-002")
        self.sync()

        self.assertEqual([row["run_id"] for row in self.index_.query("run")], ["run-001"])
        self.assertEqual(self.index_.query("record", {"run_id": "run-002"}), [])

    def test_the_design_branches_project_the_tree_and_not_the_runs(self) -> None:
        branches = self.root / "design" / "branches.json"
        branches.parent.mkdir(exist_ok=True)
        branches.write_text("{}", encoding="utf-8")
        self.sync()

        self.assertEqual(self.projector.trees, self.trees + 1)
        self.assertEqual(self.projector.projected, [])

    def test_a_saved_draft_or_head_projects_nothing(self) -> None:
        token = self.index_.apply(None, 0, areas={"working", "head", "area:design"})

        self.assertEqual(self.projector.trees, self.trees)
        self.assertEqual(self.projector.projected, [])
        self.assertEqual(token.revision, 1)

    def test_the_query_refuses_what_it_does_not_list(self) -> None:
        with self.assertRaisesRegex(ValueError, "no table"):
            self.index_.query("secret")
        with self.assertRaisesRegex(ValueError, "filtered by"):
            self.index_.query("record", {"body": "x"})
        self.assertEqual(len(self.index_.query("record", limit=0)), 0)


class PlaceTests(unittest.TestCase):
    def test_lines_are_keyed_by_what_they_name(self) -> None:
        places = line_places([
            "d . 5", "d runs/a b 7", "d runs/x unreadable 13", "l design unreadable 2",
            "f HEAD 41 9", "f design/working.json missing", "f project.json unreadable 5",
        ])
        self.assertEqual(places, {
            "d .": ("d . 5", 5), "d runs/a b": ("d runs/a b 7", 7), "d runs/x": ("d runs/x unreadable 13", 0),
            "l design": ("l design unreadable 2", 0), "f HEAD": ("f HEAD 41 9", 9),
            "f design/working.json": ("f design/working.json missing", 0),
            "f project.json": ("f project.json unreadable 5", 0),
        })

    def test_places_name_the_part_of_the_project_they_belong_to(self) -> None:
        self.assertEqual(place_area("d runs/a/records"), "run:a")
        self.assertEqual(place_area("d runs"), "runs")
        self.assertEqual(place_area("f design/branches.json"), "branches")
        self.assertEqual(place_area("f design/working.json"), "working")
        self.assertEqual(place_area("f HEAD"), "head")
        self.assertEqual(place_area("f project.json"), "manifest")
        self.assertEqual(place_area("d objects/sha256"), "area:objects")
        self.assertEqual(place_area("d ."), "root")


class KeeperTests(_IndexCase):
    """The keeper is the index's only writer, on a thread of its own."""

    def keeper(self, projector: RecordingProjector | None = None) -> tuple[IndexKeeper, RecordingProjector]:
        index, projector = self.index(projector)
        keeper = IndexKeeper(index, watch_layout(self.root, notify=False), name="test")
        keeper.start()
        self.addCleanup(keeper.stop)
        self.assertIsNotNone(keeper.wait_loaded(30), keeper.failure)
        return keeper, projector

    def test_it_loads_in_the_background_and_applies_this_process_writes(self) -> None:
        keeper, projector = self.keeper()
        self.assertEqual(keeper.index.loaded, "rebuilt")
        self.repository.create_run("run-002")
        self.put("run-002", {"record": 2})

        state = keeper.wait_readable(10)
        self.assertIsNotNone(state)
        self.assertEqual(self.records(keeper.index, "run-002"), 1)
        self.assertEqual(projector.threads, {"project-index:test"}, "only the keeper's thread projects")

    def test_another_process_write_is_applied_once_the_watch_sees_it(self) -> None:
        keeper, _ = self.keeper()
        before = keeper.state
        (self.root / "runs" / "run-outside" / "records").mkdir(parents=True)
        watched = keeper._lease.sync()

        self.assertTrue(wait_until(lambda: keeper.state.digest == watched.fingerprint.digest))
        self.assertGreater(keeper.state.token.revision, before.token.revision)
        self.assertEqual([row["run_id"] for row in keeper.index.query("run")], ["run-001", "run-outside"])

    def test_a_restart_reuses_the_kept_file(self) -> None:
        settle(self.root)
        keeper, _ = self.keeper()
        token = keeper.state.token
        keeper.stop()

        again, projector = self.keeper()
        self.assertEqual(again.index.loaded, "reused")
        self.assertEqual(again.state.token, token)
        self.assertEqual(projector.projected, [])

    def test_a_deleted_index_is_rebuilt_by_the_next_keeper(self) -> None:
        settle(self.root)
        keeper, _ = self.keeper()
        epoch = keeper.state.token.epoch
        keeper.stop()
        shutil.rmtree(self.cache)

        again, _ = self.keeper()
        self.assertEqual(again.index.loaded, "rebuilt")
        self.assertNotEqual(again.state.token.epoch, epoch)
        self.assertEqual(self.records(again.index, "run-001"), 1)

    def test_a_second_keeper_on_the_same_directory_is_refused_and_answers_nothing(self) -> None:
        self.keeper()
        index, _ = self.index()
        second = IndexKeeper(index, watch_layout(self.root, notify=False), name="second")
        second.start()
        self.addCleanup(second.stop)

        self.assertIsNone(second.wait_loaded(30))
        self.assertIn("another process", second.failure)
        self.assertIsNone(second.readable())

    def test_reads_during_applies_are_consistent_snapshots_and_never_fail(self) -> None:
        projector = RecordingProjector(self.repository)
        projector.delay = 0.002
        keeper, _ = self.keeper(projector)
        stop = threading.Event()
        seen: list[tuple[int, int, int]] = []
        failures: list[BaseException] = []

        def read() -> None:
            while not stop.is_set():
                try:
                    with keeper.index.snapshot() as snapshot:
                        runs = {row["run_id"]: row["body"].get("records", 0) for row in snapshot.rows("run", limit=None)}
                        records = snapshot.rows("record", limit=None)
                        revision = snapshot.token.revision
                except IndexUnavailable:
                    continue
                except BaseException as exc:  # noqa: BLE001 - recorded for the assertion below
                    failures.append(exc)
                    return
                counted: dict[str, int] = {}
                for row in records:
                    counted[row["run_id"]] = counted.get(row["run_id"], 0) + 1
                # One commit: every run row states exactly the records beside it.
                if counted != {run_id: count for run_id, count in runs.items() if count}:
                    failures.append(AssertionError(f"torn snapshot at {revision}: {runs} vs {counted}"))
                    return
                seen.append((revision, len(runs), len(records)))

        readers = [threading.Thread(target=read) for _ in range(4)]
        for reader in readers:
            reader.start()
        try:
            for number in range(2, 22):
                run_id = f"run-{number:03d}"
                self.repository.create_run(run_id)
                self.put(run_id, {"record": number})
                self.put("run-001", {"record": number})
        finally:
            self.assertIsNotNone(keeper.wait_readable(30))
            stop.set()
            for reader in readers:
                reader.join(30)

        self.assertEqual(failures, [])
        self.assertTrue(seen)
        revisions = sorted({revision for revision, _, _ in seen})
        self.assertGreater(len(revisions), 1, "the readers saw applies land while they read")
        self.assertEqual(len(keeper.index.query("run", limit=None)), 21)
        self.assertEqual(self.records(keeper.index, "run-001"), 21)


if __name__ == "__main__":
    unittest.main()
