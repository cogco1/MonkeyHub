"""The open project's Project Runtime is its only writer (ADR-012, #599).

A runtime holds the project's writer lease from before it serves until it has
stopped. Meanwhile every other process's write is refused and writes nothing,
a second runtime for the same project stops with ``PROJECT_WRITER_BUSY``, and
reads are never refused. The lease ends with the runtime, however it ends.
These start real runtime processes on a disposable fixture project.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request, urlopen

from archflow.project import writer_lease
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.repository import FilesystemProjectRepository, ProjectWriterBusy
from archflow.project.writer_lease import WRITER_LOCK, holds_writer_lease
from project_runtime.main import _hold_project
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_PYTHONPATH = os.pathsep.join((str(_SERVICE_ROOT.parents[1]), str(_SERVICE_ROOT / "src")))
# A runtime started the way the Hub starts one, with how long it waits for the lease set first.
_RUNTIME = ("import sys, project_runtime; from archflow.project import writer_lease; "
            "writer_lease.HOLD_WAIT_S = float(sys.argv[1]); "
            "from project_runtime.main import main; main(sys.argv[2:])")
_START_S = 90.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _png() -> bytes:
    from PIL import Image

    picture = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(picture, format="PNG")
    return picture.getvalue()


class _Runtime:
    """One runtime process this test owns, stopped through its managed stdin as the Hub stops one."""

    def __init__(self, test: unittest.TestCase, project: Path, *, wait_s: float) -> None:
        self.port = _free_port()
        self.log = project.parent / f"runtime-{self.port}.log"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("ARCHFLOW_STUDIO_") and key != "MONKEYMONITOR_DATA_DIR"}
        environment.update({"ARCHFLOW_STUDIO_CAD_EXPORT": "off", "ARCHFLOW_STUDIO_INTENT_PROVIDER": "deterministic",
                            "PYTHONPATH": _SOURCE_PYTHONPATH})
        with self.log.open("wb") as output:
            self.process = subprocess.Popen(
                [sys.executable, "-c", _RUNTIME, str(wait_s), "--port", str(self.port), "--project-dir", str(project),
                 "--managed-stdin", "--managed-instance-id", f"writer-lease-{self.port}"],
                env=environment, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        test.addCleanup(self.kill)
        self.url = f"http://127.0.0.1:{self.port}"

    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def wait_bound(self, test: unittest.TestCase) -> None:
        deadline = time.monotonic() + _START_S
        while time.monotonic() < deadline:
            test.assertIsNone(self.process.poll(), self.output())
            try:
                with urlopen(self.url + "/api/health", timeout=1) as answer:
                    if json.load(answer).get("projectBound") is True:
                        return
            except (OSError, URLError, ValueError):
                pass
            time.sleep(0.1)
        test.fail("the runtime did not bind its project: " + self.output())

    def stop(self) -> int:
        self.process.stdin.write(b"stop\n")
        self.process.stdin.flush()
        self.process.stdin.close()
        return self.process.wait(timeout=60)

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=30)
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()


class RuntimeWriterLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="runtime-writer-lease-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.project = self.repository.layout.root

    def state(self) -> dict[str, tuple[int, int]]:
        found = {".": (0, os.stat(self.project).st_mtime_ns)}
        for path in self.project.rglob("*"):
            stat = path.stat()
            found[path.relative_to(self.project).as_posix()] = (0 if path.is_dir() else stat.st_size, stat.st_mtime_ns)
        return found

    def test_the_open_runtime_holds_the_lease_until_it_stops_and_refuses_every_other_writer(self) -> None:
        runtime = _Runtime(self, self.project, wait_s=writer_lease.HOLD_WAIT_S)
        runtime.wait_bound(self)
        self.assertFalse(holds_writer_lease(self.project))
        run = self.repository.load_run(REFERENCE_RUN_ID)
        value, revision = self.repository.read_working_draft()
        writes = {
            "a run": lambda: self.repository.create_run("outside"),
            "a record": lambda: self.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                record_kind=STATE_RECORD, payload={"outside": True}),
            "the working position": lambda: self.repository.compare_and_swap_working_draft(
                expected_revision=revision, value={**value, "current": None}),
            "the recovery expiry": lambda: self.repository.prune_working_draft(now="2026-10-02T12:00:00+00:00"),
        }
        before = self.state()
        with patch.object(writer_lease, "WRITE_WAIT_S", 0.3):
            for name, write in writes.items():
                with self.subTest(write=name), self.assertRaises(ProjectWriterBusy) as refused:
                    write()
                self.assertEqual(refused.exception.code, "PROJECT_WRITER_BUSY")
        self.assertEqual(self.state(), before, "a refused write touched the project")
        # Reads are never refused.
        self.assertIn(REFERENCE_RUN_ID, FilesystemProjectRepository.open(self.project).run_ids())

        # The runtime writes as before: its own write route answers.
        request = Request(runtime.url + "/api/documents", method="POST", headers={"Content-Type": "application/json"},
                          data=json.dumps({"projectId": PROJECT_ID, "fileName": "plan.png", "mimeType": "image/png",
                                           "contentBase64": base64.b64encode(_png()).decode("ascii")}).encode("utf-8"))
        with urlopen(request, timeout=60) as answer:
            self.assertEqual(answer.status, 201)
            written = json.load(answer)["runId"]
        self.assertIn(written, self.repository.run_ids())

        # A second runtime for the same project stops at once, clearly, and writes nothing.
        before = self.state()
        second = _Runtime(self, self.project, wait_s=0.5)
        self.assertNotEqual(second.process.wait(timeout=_START_S), 0, second.output())
        self.assertIn("PROJECT_WRITER_BUSY", second.output())
        self.assertEqual(self.state(), before)

        # Stopped, the runtime gives the lease back: this process writes again.
        self.assertEqual(runtime.stop(), 0, runtime.output())
        self.repository.create_run("after-close")
        self.assertIn("after-close", self.repository.run_ids())

    def test_a_runtime_that_dies_leaves_no_lease_behind(self) -> None:
        runtime = _Runtime(self, self.project, wait_s=writer_lease.HOLD_WAIT_S)
        runtime.wait_bound(self)
        with patch.object(writer_lease, "WRITE_WAIT_S", 0.3), self.assertRaises(ProjectWriterBusy):
            self.repository.create_run("while-open")
        runtime.kill()
        # The operating system ended the lock with the process; the write waits out a slow release.
        self.repository.create_run("after-crash")
        self.assertIn("after-crash", self.repository.run_ids())
        # The next runtime opens the project as usual.
        again = _Runtime(self, self.project, wait_s=writer_lease.HOLD_WAIT_S)
        again.wait_bound(self)
        self.assertEqual(again.stop(), 0, again.output())

    def test_a_folder_without_a_project_takes_no_lease_and_gains_no_file(self) -> None:
        missing = self.root / "missing-project"
        self.assertIsNone(_hold_project(StudioSettings(project_dir=missing, cad_export="off")))
        self.assertFalse(missing.exists())
        empty = self.root / "empty-folder"
        empty.mkdir()
        self.assertIsNone(_hold_project(StudioSettings(project_dir=empty, cad_export="off")))
        self.assertEqual(list(empty.iterdir()), [])
        held = _hold_project(StudioSettings(project_dir=self.project, cad_export="off"))
        self.addCleanup(held.release)
        self.assertTrue(holds_writer_lease(self.project))
        self.assertTrue((self.project / WRITER_LOCK).is_file())
        held.release()
        self.assertFalse(holds_writer_lease(self.project))


if __name__ == "__main__":
    unittest.main()
