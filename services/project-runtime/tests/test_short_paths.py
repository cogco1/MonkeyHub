"""A candidate on Windows with long paths off (GH-300/short-paths).

Windows refuses a path of 260 characters or more unless ``LongPathsEnabled``
is set, and a student's machine or a build server leaves it off. A project in
an ordinary folder (``D:\\PROJECTS\\…``, or the Hub's own workspace under
``%LOCALAPPDATA%``) must still produce a candidate there. The audit hook below
refuses every open, mkdir, rename, link and replace of such a path as Windows
does, so a machine with long paths on measures the same limit; what native code
writes (the CAD export) is measured afterwards from the files it left.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from monkeycad.backends.occt.kernel import occt_available
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, make_project

MAX_PATH = 260
# What D:\PROJECTS\<course>\<project> folders and the Hub's own workspace come to.
FOLDER = 80
_PATH_ARGUMENTS = {
    "open": (0,), "os.mkdir": (0,), "os.rename": (0, 1), "os.replace": (0, 1), "os.link": (0, 1),
    "os.remove": (0,), "os.rmdir": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.chmod": (0,),
}
_refusing = False


def _refuse_long_paths(event: str, args: tuple) -> None:
    if not _refusing or event not in _PATH_ARGUMENTS:
        return
    for index in _PATH_ARGUMENTS[event]:
        value = args[index] if index < len(args) else None
        if not isinstance(value, (str, bytes, os.PathLike)):
            continue
        path = os.path.abspath(os.fsdecode(value))
        if len(path) >= MAX_PATH and not path.startswith("\\\\?\\"):
            raise FileNotFoundError(errno.ENOENT, f"{event}: {len(path)} characters, longer than MAX_PATH", path)


sys.addaudithook(_refuse_long_paths)


@contextmanager
def long_paths_off():
    global _refusing
    _refusing = True
    try:
        yield
    finally:
        _refusing = False


class CandidateWithoutLongPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        if not occt_available():
            self.skipTest("cadquery-ocp is not installed")
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        pad = FOLDER - len(str(base / PROJECT_ID)) - 1
        if pad < 1:
            self.skipTest(f"the temporary directory {base} is itself too long to place an {FOLDER}-character project")
        (base / ("p" * pad)).mkdir()
        make_project(base / ("p" * pad))
        self.project = base / ("p" * pad) / PROJECT_ID
        self.assertEqual(len(str(self.project)), FOLDER)

    def test_a_hub_candidate_completes_in_an_ordinary_project_folder(self) -> None:
        with long_paths_off():
            app = create_app(StudioSettings(cad_export="occt", project_dir=self.project))
            app.state.managed_instance_id = "hub-1"
            with TestClient(app) as client:
                digest = client.get("/api/state").json()["stateDigest"]
                proposed = client.post("/api/proposals", json={
                    "stateDigest": digest, "targetComponentId": "portico", "elementId": "portico-base",
                    "utterance": "set height to 2.2"})
                self.assertEqual(proposed.status_code, 201, proposed.text)
                # The id a Hub mints for the request (operations.py), as the Hub sends it.
                candidate_id = "hub-cand-0123456789abcdef"
                started = client.post(f"/api/proposals/{proposed.json()['proposalId']}/candidate",
                                      headers={"X-Monkey-Candidate": candidate_id, "X-Monkey-Worker": "hub-1"})
                self.assertEqual(started.status_code, 202, started.text)
                self.assertEqual(started.json()["candidateId"], candidate_id)
                deadline = time.monotonic() + 300
                while (job := client.get(f"/api/jobs/{started.json()['jobId']}").json())["status"] not in {
                        "succeeded", "failed"}:
                    self.assertLess(time.monotonic(), deadline, "the candidate never finished")
                    time.sleep(0.05)
        self.assertEqual(job["status"], "succeeded", job)
        longest = max((str(path) for path in self.project.rglob("*")), key=len)
        self.assertLess(len(longest), MAX_PATH, longest)


if __name__ == "__main__":
    unittest.main()
