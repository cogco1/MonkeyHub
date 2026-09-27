"""The layout watch: a project's fingerprint kept current off the request path (GH-363, ADR-008).

The watch must always come back to what ``layout_fingerprint`` says of the
same tree, whatever path it took there: notifications, this process's write
observer, a scan every ``POLL_S`` where no notification arrives, a full walk
after a lost batch or a failure. A reader never waits for it, and one watch
per root and process holds at most one handle, which it gives up when the
folder is renamed away or the last lease is released.

The notification tests need Windows (``ReadDirectoryChangesW``) and skip
elsewhere; the scanning tests run everywhere.
"""

from __future__ import annotations

import gc
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
from archflow.project.repository import FilesystemProjectRepository

PROJECT_ID = "project-w"
WINDOWS = sys.platform == "win32"


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


class _WatchCase(unittest.TestCase):
    notify = False

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="layout-watch-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"phase": "request"},
        )
        self.run = self.repository.create_run("run-001")
        self.records = self.repository.layout.run("run-001").records

    def lease(self, root: Path | str | None = None) -> watch.LayoutLease:
        lease = watch.watch_layout(self.root if root is None else root, notify=self.notify)
        self.addCleanup(lease.release)
        lease.latest()
        return lease

    def agrees(self, lease: watch.LayoutLease) -> bool:
        return lease.latest().fingerprint.digest == layout_fingerprint(self.root).digest

    def assertAgrees(self, lease: watch.LayoutLease, timeout: float = 5.0) -> None:
        if not until(lambda: self.agrees(lease), timeout):
            self.fail(f"the watch never agreed with a full walk; passes {lease.watch.passes}")


class ScanningWatchTests(_WatchCase):
    """No notifications: the path every platform has, and the one a failed watch falls back to."""

    def test_the_first_reading_is_the_walk_of_the_same_tree(self) -> None:
        lease = self.lease()
        seen = lease.latest()

        self.assertEqual(seen.fingerprint.digest, layout_fingerprint(self.root).digest)
        self.assertFalse(seen.notified)
        self.assertFalse(seen.fingerprint.stable, "just written: its times have not settled")

    def test_another_process_s_change_is_seen_within_a_scan(self) -> None:
        lease = self.lease()
        started = time.monotonic()
        (self.records / "external" / "deeper").mkdir(parents=True)
        self.assertAgrees(lease, timeout=watch.POLL_S * 3 + 2)
        self.assertLess(time.monotonic() - started, watch.POLL_S * 3 + 2)
        shutil.rmtree(self.records / "external")
        self.assertAgrees(lease, timeout=watch.POLL_S * 3 + 2)
        (self.base / "moved").mkdir()
        os.rename(self.repository.layout.run("run-001").root, self.base / "moved" / "run-001")
        self.assertAgrees(lease, timeout=watch.POLL_S * 3 + 2)

    def test_this_process_s_write_is_read_without_waiting_for_a_scan(self) -> None:
        with mock.patch.object(watch, "POLL_S", 60.0):
            lease = self.lease()
            before = lease.latest()
            self.repository.put_json(
                run=self.run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="run-001"),
                record_kind=STATE_RECORD, payload={"record": 1},
            )
            self.repository.create_run("run-002")
            self.assertAgrees(lease, timeout=3.0)
        self.assertGreater(lease.latest().serial, before.serial)
        self.assertEqual(lease.watch.passes["poll"], 0, "a scan, not the write observer, saw the write")
        self.assertGreaterEqual(lease.watch.passes["dirty"], 1)

    def test_a_pointer_file_replaced_in_place_is_seen(self) -> None:
        lease = self.lease()
        working = self.repository.layout.working_draft
        working.parent.mkdir(parents=True, exist_ok=True)
        temporary = working.with_name(".working.tmp")
        temporary.write_bytes(b"{}\n")
        os.replace(temporary, working)
        self.assertAgrees(lease, timeout=watch.POLL_S * 3 + 2)

    def test_it_settles_by_itself_and_stays_settled_while_nothing_moves(self) -> None:
        settle(self.root)
        lease = self.lease()
        self.assertTrue(lease.latest().fingerprint.stable)
        (self.records / "fresh").mkdir()
        self.assertTrue(until(lambda: self.agrees(lease) and not lease.latest().fingerprint.stable, 5.0))
        self.assertTrue(until(lambda: lease.latest().fingerprint.stable, 2 * FINGERPRINT_SETTLED_NS / 1e9 + 3),
                        "a project nobody touched never settled")
        self.assertAgrees(lease)

    def test_the_whole_project_is_walked_again_on_the_safety_net(self) -> None:
        with mock.patch.object(watch, "SAFETY_S", 0.5):
            lease = self.lease()
            walks = lease.watch.passes["walk"]
            self.assertTrue(until(lambda: lease.watch.passes["walk"] >= walks + 2, 5.0))

    def test_a_reader_never_waits_for_a_slow_scan(self) -> None:
        lease = self.lease()
        real_stat = os.stat
        below = os.path.normcase(str(self.root))

        def slow_stat(path, *args, **kwargs):
            if os.path.normcase(os.fsdecode(path)).startswith(below):
                time.sleep(0.05)
            return real_stat(path, *args, **kwargs)

        with mock.patch("os.stat", slow_stat):
            polls = lease.watch.passes["poll"]
            (self.records / "external").mkdir()
            # A scan now takes about a second; readers keep getting the last
            # result at once, through two whole scans.
            readings = []
            deadline = time.monotonic() + 20
            while lease.watch.passes["poll"] < polls + 2 and time.monotonic() < deadline:
                started = time.perf_counter()
                lease.latest()
                readings.append(time.perf_counter() - started)
                time.sleep(0.01)
        self.assertGreaterEqual(lease.watch.passes["poll"], polls + 2)
        # A stat alone would cost 50 ms.
        self.assertLess(max(readings), 0.02)
        self.assertAgrees(lease, timeout=10.0)

    def test_a_failed_pass_distrusts_the_last_reading_and_walks_again(self) -> None:
        settle(self.root)
        lease = self.lease()
        self.assertTrue(lease.latest().fingerprint.stable)
        tree = lease.watch._tree
        real_refresh = tree.refresh
        failed = threading.Event()

        def refresh(*args, **kwargs):
            if not failed.is_set():
                failed.set()
                raise RuntimeError("simulated watcher failure")
            return real_refresh(*args, **kwargs)

        tree.refresh = refresh
        walks = lease.watch.passes["walk"]
        with self.assertLogs("archflow.project.watch", level="ERROR"):
            (self.records / "external").mkdir()
            self.assertTrue(failed.wait(watch.POLL_S * 3 + 2))
            self.assertTrue(until(lambda: lease.watch.passes["walk"] > walks, 5.0))
        self.assertEqual(lease.watch.failures, 1)
        self.assertTrue(lease.watch.running)
        self.assertAgrees(lease)

    def test_a_first_walk_that_fails_does_not_hold_its_readers(self) -> None:
        real_walk = watch._Tree.walk
        failed: list[bool] = []

        def walk(tree, stopping):
            if tree.root == str(self.root) and not failed:
                failed.append(True)
                raise RuntimeError("simulated: the first walk failed")
            return real_walk(tree, stopping)

        with mock.patch.object(watch._Tree, "walk", walk), \
                self.assertLogs("archflow.project.watch", level="ERROR"):
            started = time.monotonic()
            lease = watch.watch_layout(self.root, notify=self.notify)
            self.addCleanup(lease.release)
            first = lease.latest()
            self.assertLess(time.monotonic() - started, 5.0, "a reader waited out the failed first walk")
            self.assertFalse(first.fingerprint.stable)
            self.assertAgrees(lease, timeout=watch.POLL_S * 3 + 3)

    def test_a_missing_root_is_never_stable_and_is_seen_coming_back(self) -> None:
        lease = self.lease()
        aside = self.base / "aside"
        os.rename(self.root, aside)
        self.assertTrue(until(lambda: not lease.latest().fingerprint.stable
                              and lease.latest().fingerprint.digest == layout_fingerprint(self.root).digest,
                              watch.POLL_S * 3 + 3))
        os.rename(aside, self.root)
        self.assertAgrees(lease, timeout=watch.RETRY_S + watch.POLL_S * 3 + 3)


@unittest.skipUnless(WINDOWS, "elsewhere the class above already runs this way")
class ScanningWithoutWin32Tests(ScanningWatchTests):
    """The scanning path exactly as another platform runs it: no Win32 event, a ``threading.Event``."""

    def setUp(self) -> None:
        patcher = mock.patch.object(watch, "_WINDOWS", False)
        patcher.start()
        # Registered first, so it is undone last: after every watch it made has stopped.
        self.addCleanup(patcher.stop)
        super().setUp()

    def lease(self, root: Path | str | None = None) -> watch.LayoutLease:
        lease = super().lease(root)
        self.assertFalse(lease.watch.notify)
        self.assertFalse(hasattr(lease.watch._signal, "handle"))
        return lease


class LeaseTests(_WatchCase):
    def test_one_watch_per_root_however_it_is_spelled(self) -> None:
        first = self.lease()
        spellings = [str(self.root) + os.sep, str(self.root / "runs" / ".."), str(self.root).upper() if WINDOWS
                     else str(self.root)]
        if WINDOWS:
            spellings.append("\\\\?\\" + str(self.root))
        others = [self.lease(spelling) for spelling in spellings]

        self.assertEqual({id(lease.watch) for lease in (first, *others)}, {id(first.watch)})
        self.assertEqual(first.watch.leases, 1 + len(others))
        self.assertIs(watch.running_watches()[first.watch.key], first.watch)

    def test_the_last_release_stops_the_thread(self) -> None:
        first, second = self.lease(), self.lease()
        thread = first.watch._thread
        first.release()
        first.release()  # a second release of one lease gives back nothing more
        self.assertTrue(thread.is_alive())
        started = time.monotonic()
        second.release()

        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertNotIn(first.watch.key, watch.running_watches())
        self.assertIsNotNone(first.latest(), "a stopped watch still answers with what it saw last")
        third = self.lease()
        self.assertIsNot(third.watch, first.watch)

    def test_a_holder_that_was_collected_gives_its_lease_back(self) -> None:
        class Holder:
            pass

        holder = Holder()
        lease = watch.watch_layout(self.root, notify=self.notify)
        watch.release_when_collected(holder, lease)
        lease.latest()
        thread = lease.watch._thread
        del holder
        gc.collect()

        self.assertTrue(until(lambda: not thread.is_alive(), watch.POLL_S * 3 + 2))
        self.assertTrue(lease.released)


@unittest.skipUnless(WINDOWS, "ReadDirectoryChangesW is Windows-only")
class NotifiedWatchTests(_WatchCase):
    notify = True

    def test_a_change_from_outside_is_seen_within_a_second_without_scanning(self) -> None:
        settle(self.root)
        lease = self.lease()
        self.assertTrue(lease.latest().notified)
        visits = []
        tree = lease.watch._tree
        original = tree._visit_one

        def visit(relative, force_list):
            visits.append(relative)
            return original(relative, force_list)

        with mock.patch.object(tree, "_visit_one", visit):
            for make in (lambda: (self.records / "external").mkdir(),
                         lambda: (self.records / "state-record-x.json").write_bytes(b"{}\n"),
                         lambda: (self.records / "external" / "a" / "b").mkdir(parents=True)):
                visits.clear()
                started = time.monotonic()
                make()
                self.assertAgrees(lease, timeout=1.0)
                self.assertLess(time.monotonic() - started, 1.0)
                # Only what the notification named was read, never the whole tree.
                self.assertLess(len(set(visits)), 8, visits)
        self.assertEqual(lease.watch.passes["poll"], 0)

    def test_idle_it_reads_nothing_but_the_root(self) -> None:
        settle(self.root)
        lease = self.lease()
        self.assertTrue(until(lambda: lease.latest().fingerprint.stable, 3.0))
        time.sleep(0.3)
        passes = dict(lease.watch.passes)
        time.sleep(2 * watch.POLL_S + 0.5)

        self.assertEqual(lease.watch.passes, passes)

    def test_a_lost_batch_costs_one_walk(self) -> None:
        lease = self.lease()
        notifier = lease.watch._notifier
        real_collect = notifier.collect
        lost = threading.Event()

        def collect():
            real_collect()
            lost.set()
            return None

        notifier.collect = collect
        walks = lease.watch.passes["walk"]
        (self.records / "external").mkdir()
        self.assertTrue(lost.wait(2.0))
        notifier.collect = real_collect
        self.assertTrue(until(lambda: lease.watch.passes["walk"] > walks, 2.0))
        self.assertAgrees(lease)
        self.assertTrue(lease.watch.notified)

    def test_a_real_overflow_costs_one_walk(self) -> None:
        # A buffer too small for a single change: the file system itself
        # reports every batch as lost (0 bytes).
        with mock.patch.object(watch, "NOTIFY_BUFFER_BYTES", 16):
            lease = self.lease()
        walks = lease.watch.passes["walk"]
        for index in range(20):
            (self.records / f"state-record-{index:04d}.json").write_bytes(b"{}\n")
        (self.records / "after-the-flood").mkdir()
        self.assertTrue(until(lambda: lease.watch.passes["walk"] > walks, 3.0))
        self.assertAgrees(lease)
        self.assertEqual(lease.watch.passes["dirty"], 0, "a lost batch was read as if it said what moved")
        self.assertTrue(lease.watch.notified)

    def test_a_watch_that_will_not_open_falls_back_to_scanning(self) -> None:
        with mock.patch.object(watch, "_Notifier", side_effect=OSError(5, "simulated: access denied")):
            lease = self.lease()
            self.assertFalse(lease.latest().notified)
            (self.records / "external").mkdir()
            self.assertAgrees(lease, timeout=watch.POLL_S * 3 + 2)
        self.assertGreaterEqual(lease.watch.passes["poll"], 1)

    def test_a_watch_that_fails_scans_and_opens_again(self) -> None:
        with mock.patch.object(watch, "RETRY_S", 0.5):
            lease = self.lease()
            notifier = lease.watch._notifier

            def broken():
                raise OSError(64, "simulated: the network name is no longer available")

            notifier.collect = broken
            (self.records / "external").mkdir()
            self.assertTrue(until(lambda: lease.watch._notifier is not notifier, 2.0))
            self.assertAgrees(lease, timeout=3.0)
            self.assertTrue(until(lambda: lease.watch.notified, 3.0), "the watch never opened again")
            (self.records / "after").mkdir()
            self.assertAgrees(lease, timeout=1.0)

    def test_the_root_can_be_renamed_while_watched_and_the_watch_lets_go(self) -> None:
        lease = self.lease()
        self.assertTrue(lease.latest().notified)
        renamed = self.base / "renamed"
        os.rename(self.root, renamed)  # FILE_SHARE_DELETE: this must not be refused

        self.assertTrue(until(lambda: not lease.watch.notified, watch.POLL_S * 2 + 1), "the watch kept the handle")
        self.assertTrue(until(lambda: not lease.latest().fingerprint.stable
                              and lease.latest().fingerprint.digest == layout_fingerprint(self.root).digest, 3.0))
        # With the handle closed, even the folder that holds it can move.
        moved = self.base.with_name(self.base.name + "-moved")
        os.rename(self.base, moved)
        os.rename(moved, self.base)
        os.rename(renamed, self.root)
        with mock.patch.object(watch, "RETRY_S", 0.5):
            lease.watch._retry_at = 0.0
            self.assertTrue(until(lambda: lease.watch.notified, 3.0), "the watch never came back to its root")
        self.assertAgrees(lease)

    def test_the_root_can_be_deleted_while_watched(self) -> None:
        lease = self.lease()
        shutil.rmtree(self.root)

        self.assertFalse(self.root.exists())
        self.assertTrue(until(lambda: not lease.watch.notified, watch.POLL_S * 2 + 1))
        self.assertTrue(until(lambda: not lease.latest().fingerprint.stable, 3.0))

    def test_an_extended_length_root_is_watched(self) -> None:
        lease = self.lease("\\\\?\\" + str(self.root))
        self.assertTrue(lease.latest().notified)
        self.assertEqual(lease.latest().fingerprint.digest, layout_fingerprint(self.root).digest)
        (self.records / "external").mkdir()
        self.assertAgrees(lease, timeout=1.0)
        self.assertIs(self.lease().watch, lease.watch)

    def test_stopping_closes_the_handle_at_once(self) -> None:
        lease = watch.watch_layout(self.root, notify=True)
        self.assertTrue(lease.latest().notified)
        thread = lease.watch._thread
        started = time.monotonic()
        lease.release()

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(lease.watch._notifier)
        moved = self.base.with_name(self.base.name + "-moved")
        os.rename(self.base, moved)  # refused while any handle below it is open
        os.rename(moved, self.base)


if __name__ == "__main__":
    unittest.main()
