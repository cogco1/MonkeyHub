"""A project's layout as this process last read it: no watch, three readings (ADR-012, #599).

A ``KnownLayout`` must always come back to what ``layout_fingerprint`` says of
the same tree, and it reads the project at exactly three points: once at open,
once after this process's writes have settled (one re-check, which reads only
what was written or too young to trust) and when someone asks to read it again
(a refresh, which names what moved outside this process). Nothing runs in
between: no thread, no handle on the folder, no poll.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from archflow.project import watch
from archflow.project.layout import FINGERPRINT_SETTLED_NS, layout_fingerprint
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.repository import FilesystemProjectRepository, write_serial

PROJECT_ID = "project-w"
WINDOWS = sys.platform == "win32"
# Long enough for a re-check due two seconds after a write to have run.
SETTLE_S = FINGERPRINT_SETTLED_NS / 1e9 + 1.5


def until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def settle(root: Path) -> None:
    """Age every time in the project, as if its last write were long ago."""

    old = time.time_ns() - 10 * FINGERPRINT_SETTLED_NS
    for folder, _, names in os.walk(root):
        for name in names:
            os.utime(os.path.join(folder, name), ns=(old, old))
    for folder, _, _ in os.walk(root, topdown=False):
        os.utime(folder, ns=(old, old))


def layout_threads() -> list[str]:
    return [thread.name for thread in threading.enumerate() if thread.name.startswith(("layout-", "project-index"))]


class _LayoutCase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="known-layout-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"phase": "request"},
        )
        self.run = self.repository.create_run("run-001")
        self.records = self.repository.layout.run("run-001").records

    def layout(self, root: Path | str | None = None) -> watch.KnownLayout:
        layout = watch.KnownLayout(self.root if root is None else root)
        self.addCleanup(layout.close)
        return layout

    def opened(self) -> watch.KnownLayout:
        settle(self.root)
        layout = self.layout()
        self.assertTrue(layout.latest().fingerprint.stable)
        return layout

    def put(self, payload: dict) -> None:
        self.repository.put_json(
            run=self.run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="run-001"),
            record_kind=STATE_RECORD, payload=payload,
        )

    def agrees(self, layout: watch.KnownLayout) -> bool:
        return layout.latest().fingerprint.digest == layout_fingerprint(self.root).digest

    @staticmethod
    def counting(below: Path):
        """Count every ``stat`` and listing below ``below``, each named relative to it, however it is spelled.

        A temporary folder may be named by its 8.3 short name (``RUNNER~1``)
        where the repository resolves it to the long one: the layout reads
        below the spelling it was given, the repository below the resolved
        one, and both count as the same place.
        """

        roots = sorted({os.path.normcase(os.fspath(below)), os.path.normcase(os.path.realpath(below))},
                       key=len, reverse=True)
        seen: list[tuple[str, str]] = []
        real_stat, real_scandir = os.stat, os.scandir

        def relative(path) -> str | None:
            try:
                spelled = os.path.normcase(os.fsdecode(path))
            except TypeError:
                return None
            for root in roots:
                if spelled == root:
                    return "."
                if spelled.startswith(root + os.sep):
                    return spelled[len(root) + len(os.sep):]
            return None

        def stat(path, *args, **kwargs):
            if (name := relative(path)) is not None:
                seen.append(("stat", name))
            return real_stat(path, *args, **kwargs)

        def scandir(path=".", *args, **kwargs):
            if (name := relative(path)) is not None:
                seen.append(("list", name))
            return real_scandir(path, *args, **kwargs)

        return seen, mock.patch("os.stat", stat), mock.patch("os.scandir", scandir)


class OpenTests(_LayoutCase):
    def test_the_open_reading_is_the_walk_of_the_same_tree_and_nothing_runs_after_it(self) -> None:
        before = set(layout_threads())
        layout = self.layout()
        self.assertIsNone(layout.latest(wait=False), "nobody has read it yet")
        seen = layout.latest()

        self.assertEqual(seen.fingerprint.digest, layout_fingerprint(self.root).digest)
        self.assertEqual(layout.readings, {"open": 1, "recheck": 0, "refresh": 0})
        self.assertIs(layout.latest(wait=False), seen)
        # Just written: a re-check is due once its times have settled, and
        # nothing else runs - no watch, no poll.
        self.assertFalse(seen.fingerprint.stable)
        self.assertEqual(set(layout_threads()) - before, {"layout-recheck:project-w"})

    def test_a_reader_after_the_open_reads_nothing(self) -> None:
        layout = self.opened()
        seen, *patches = self.counting(self.root)
        with patches[0], patches[1]:
            for _ in range(200):
                layout.latest()
        self.assertEqual(seen, [])
        self.assertTrue(until(lambda: layout_threads() == [], 2.0), "a settled project runs nothing")

    def test_another_process_s_change_is_read_on_refresh_and_not_before(self) -> None:
        layout = self.opened()
        before = layout.latest()
        (self.records / "external" / "deeper").mkdir(parents=True)

        # Nothing watches: time passing reads nothing.
        time.sleep(1.5)
        self.assertIs(layout.latest(), before)
        self.assertFalse(layout.pending)
        self.assertNotEqual(before.fingerprint.digest, layout_fingerprint(self.root).digest)

        refreshed = layout.refresh()
        self.assertTrue(refreshed.changed)
        self.assertEqual(set(refreshed.moved), {"runs/run-001/records", "runs/run-001/records/external",
                                                "runs/run-001/records/external/deeper"})
        self.assertIs(refreshed.layout, layout.latest())
        self.assertTrue(self.agrees(layout))
        self.assertEqual(layout.readings["refresh"], 1)

    def test_a_pointer_file_replaced_in_place_is_read_on_refresh(self) -> None:
        layout = self.opened()
        working = self.repository.layout.working_draft
        working.parent.mkdir(parents=True, exist_ok=True)
        temporary = working.with_name(".working.tmp")
        temporary.write_bytes(b"{}\n")
        os.replace(temporary, working)

        refreshed = layout.refresh()
        self.assertIn("design/working.json", refreshed.moved)
        self.assertTrue(self.agrees(layout))

    def test_a_missing_root_is_never_stable_and_is_read_coming_back(self) -> None:
        layout = self.opened()
        aside = self.base / "aside"
        os.rename(self.root, aside)
        gone = layout.refresh()
        self.assertFalse(gone.layout.fingerprint.stable)
        self.assertIn(".", gone.moved)
        self.assertEqual(gone.layout.fingerprint.digest, layout_fingerprint(self.root).digest)
        self.assertFalse(layout.pending, "a reading that cannot settle schedules nothing")

        os.rename(aside, self.root)
        back = layout.refresh()
        self.assertTrue(back.changed)
        self.assertTrue(self.agrees(layout))

    def test_a_time_in_the_future_schedules_no_re_check(self) -> None:
        settle(self.root)
        later = time.time_ns() + 3600 * 1_000_000_000
        os.utime(self.records, ns=(later, later))
        with self.assertLogs("archflow.project.watch", level="INFO"):
            layout = self.layout()
            seen = layout.latest()
        self.assertFalse(seen.fingerprint.stable)
        self.assertFalse(layout.pending, "no reading is repeated on a clock")

    @unittest.skipUnless(WINDOWS, "an extended-length prefix is a Windows spelling")
    def test_an_extended_length_root_is_read_and_hears_its_writes(self) -> None:
        settle(self.root)
        layout = self.layout("\\\\?\\" + str(self.root))
        self.assertEqual(layout.latest().fingerprint.digest, layout_fingerprint(self.root).digest)
        self.put({"record": "through another spelling"})
        self.assertTrue(layout.pending, "a write below the root, however spelled, asks for its re-check")


class OwnWriteTests(_LayoutCase):
    def test_this_process_s_write_is_read_once_it_has_settled_and_only_where_it_wrote(self) -> None:
        layout = self.opened()
        before = layout.latest()
        seen, *patches = self.counting(self.root)
        with patches[0], patches[1]:
            self.put({"record": 1})
            written = write_serial(self.root)
            # Nothing is read on the writer's thread: the reading still says
            # what it said, filed under the serial before the write.
            self.assertIs(layout.latest(), before)
            self.assertLess(before.serial, written)
            self.assertTrue(layout.pending)
            self.assertEqual(layout.readings, {"open": 1, "recheck": 0, "refresh": 0})
            seen.clear()
            self.assertTrue(until(lambda: layout.readings["recheck"] == 1, SETTLE_S))
        records = os.path.normcase(os.path.join("runs", "run-001", "records"))
        self.assertEqual(seen, [("stat", records), ("list", records)], "the re-check read only what was written")
        after = layout.latest()
        self.assertEqual(after.serial, written)
        self.assertTrue(after.fingerprint.stable, "read after its time settled")
        self.assertTrue(self.agrees(layout))
        self.assertFalse(layout.pending)

    def test_a_racy_reading_settles_after_exactly_one_re_check(self) -> None:
        # Just written by the fixture: the open reading is too young to trust.
        layout = self.layout()
        self.assertFalse(layout.latest().fingerprint.stable)
        self.assertTrue(layout.pending)
        self.assertTrue(until(lambda: layout.latest().fingerprint.stable, SETTLE_S))
        self.assertEqual(layout.readings, {"open": 1, "recheck": 1, "refresh": 0})
        # Settled, it is never read again by itself.
        time.sleep(FINGERPRINT_SETTLED_NS / 1e9 + 0.5)
        self.assertEqual(layout.readings, {"open": 1, "recheck": 1, "refresh": 0})
        self.assertFalse(layout.pending)
        self.assertTrue(until(lambda: layout_threads() == [], 2.0))

    def test_a_burst_of_writes_is_read_once_after_the_last_has_settled(self) -> None:
        layout = self.opened()
        for number in range(4):
            self.put({"record": number})
            time.sleep(0.3)
        self.assertTrue(until(lambda: layout.readings["recheck"] >= 1, SETTLE_S + 1))
        time.sleep(0.5)
        self.assertEqual(layout.readings["recheck"], 1, "each write moved the one re-check later")
        self.assertTrue(layout.latest().fingerprint.stable)
        self.assertTrue(self.agrees(layout))

    def test_new_and_removed_directories_written_here_are_read(self) -> None:
        layout = self.opened()
        self.repository.create_run("run-002")
        self.assertTrue(until(lambda: layout.readings["recheck"] == 1, SETTLE_S))
        self.assertTrue(self.agrees(layout))

        # A whole run moved into the project trash: gone from runs, new in trash.
        self.repository.trash_run("run-002", now="2026-10-02T12:00:00+00:00", rule="superseded",
                                  reason="Superseded by the next run.")
        self.assertTrue(until(lambda: layout.readings["recheck"] == 2, SETTLE_S))
        self.assertTrue(self.agrees(layout))
        self.assertEqual(layout.readings["open"], 1, "never walked whole again")

    def test_a_refresh_names_what_moved_outside_and_never_this_process_s_writes(self) -> None:
        layout = self.opened()
        self.repository.create_run("run-002")
        self.put({"record": 2})
        refreshed = layout.refresh()
        self.assertEqual(refreshed.moved, (), "this process's own writes did not change the folder outside it")

        (self.base / PROJECT_ID / "outside").mkdir()
        self.repository.create_run("run-003")
        refreshed = layout.refresh()
        self.assertEqual(refreshed.moved, (".", "outside"))
        self.assertTrue(self.agrees(layout))

    def test_a_write_landing_while_a_refresh_walks_is_never_said_to_be_from_outside(self) -> None:
        layout = self.opened()
        real_walk = watch._Tree.walk

        def walk(tree, stopping):
            real_walk(tree, stopping)
            if tree.root == layout.root and not written:
                written.append(True)
                self.repository.create_run("run-during")

        written: list[bool] = []
        with mock.patch.object(watch._Tree, "walk", walk):
            refreshed = layout.refresh()
        self.assertTrue(written)
        self.assertEqual(refreshed.moved, ())
        self.assertTrue(self.agrees(layout))

    def test_closing_cancels_the_re_check_and_stops_hearing_writes(self) -> None:
        layout = self.opened()
        self.put({"record": 1})
        self.assertTrue(layout.pending)
        layout.close()
        self.assertFalse(layout.pending)
        self.put({"record": 2})
        self.assertFalse(layout.pending, "a closed layout hears no write")
        time.sleep(FINGERPRINT_SETTLED_NS / 1e9 + 0.5)
        self.assertEqual(layout.readings["recheck"], 0)
        self.assertTrue(until(lambda: layout_threads() == [], 2.0))
        with self.assertRaises(RuntimeError):
            layout.refresh()

    def test_a_failed_reading_distrusts_the_last_and_the_next_reads_everything(self) -> None:
        layout = self.opened()
        real_refresh = watch._Tree.refresh

        def refresh(tree, *args, **kwargs):
            raise RuntimeError("simulated: a re-check failed")

        with mock.patch.object(watch._Tree, "refresh", refresh), \
                self.assertLogs("archflow.project.watch", level="ERROR"):
            self.put({"record": 1})
            self.assertTrue(until(lambda: not layout.latest().fingerprint.stable, SETTLE_S))
        self.assertIs(watch._Tree.refresh, real_refresh)
        refreshed = layout.refresh()
        self.assertTrue(self.agrees(layout))
        self.assertEqual(refreshed.moved, ())


class ListenerTests(_LayoutCase):
    def test_a_listener_is_told_the_last_reading_at_once_then_each_one(self) -> None:
        layout = self.opened()
        heard: list[watch.LayoutSighting] = []
        remove = layout.add_listener(heard.append)
        self.assertEqual(len(heard), 1)
        self.assertIs(heard[0].layout, layout.latest())

        (self.records / "outside").mkdir()
        layout.refresh()
        self.assertEqual(len(heard), 2)
        self.assertGreater(heard[1].layout.generation, heard[0].layout.generation)
        self.assertEqual(heard[1].layout.fingerprint.digest, layout_fingerprint(self.root).digest)
        remove()
        layout.refresh()
        self.assertEqual(len(heard), 2)

    def test_its_lines_are_the_fingerprint_s_and_name_what_moved(self) -> None:
        layout = self.opened()
        heard: list[watch.LayoutSighting] = []
        layout.add_listener(heard.append)
        (self.records / "outside").mkdir()
        layout.refresh()
        first, second = heard[0], heard[1]
        moved = set(second.lines) - set(first.lines)
        self.assertTrue(any(line.startswith("d runs/run-001/records/outside ") for line in moved))
        self.assertTrue(any(line.startswith("d runs/run-001/records ") for line in moved))
        self.assertEqual(len(second.lines), len(first.lines) + 1)

    def test_a_listener_that_raises_does_not_stop_a_reading(self) -> None:
        layout = self.opened()

        def broken(sighting):
            raise RuntimeError("simulated listener failure")

        with self.assertLogs("archflow.project.watch", level="ERROR"):
            layout.add_listener(broken)
            (self.records / "outside").mkdir()
            refreshed = layout.refresh()
        self.assertTrue(refreshed.changed)
        self.assertTrue(self.agrees(layout))


if __name__ == "__main__":
    unittest.main()
