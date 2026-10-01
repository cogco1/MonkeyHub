"""Conditional reads, the per-binding memo and immutable bytes (GH-363, ADR-008 phase 0a).

A view of the decision tree is re-derived from hundreds of retained records.
Read twice under an unchanged project it is the same view: the second read
answers 304 to a client that holds it and from memory to one that does not,
and any write - this process's at once, another's once the project's layout
watch has seen it - gives it a new tag. No request ever walks the project, so
on a disk slow enough to make a walk take seconds an unchanged read still
answers in milliseconds.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import tempfile
import threading
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from archflow.project.layout import FINGERPRINT_SETTLED_NS
from archflow.project.record_kinds import STUDIO_BOARD_SCENE
from archflow.project.refs import record_file_name
from project_runtime.binding import MEMO_ENTRIES, ProjectBinding, bound_project
from project_runtime.application.boards import BOARD_RUN_ID
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from project_runtime.api.conditional import CONDITIONAL_READS

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, retain_rhino_receipt
from .test_documents import image_bytes

ROUTES = (
    "/api/design-history?branchId=main",
    "/api/worktrees",
    "/api/artifacts",
    "/api/documents",
    "/api/working-source?workspace=modeling",
    "/api/board",
    "/api/render/jobs",
    "/api/trash",
)
IMMUTABLE = "private, max-age=31536000, immutable"
MODEL_BYTES = b"conditional-read-3dm"
MODEL_SHA256 = hashlib.sha256(MODEL_BYTES).hexdigest()


def settle(root: Path) -> None:
    """Age every time in the project, as if its last write were long ago."""

    old = time.time_ns() - 10 * FINGERPRINT_SETTLED_NS
    for folder, _, names in os.walk(root):
        for name in names:
            os.utime(os.path.join(folder, name), ns=(old, old))
    for folder, _, _ in os.walk(root, topdown=False):
        os.utime(folder, ns=(old, old))


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@contextmanager
def slow_disk(root: Path, seconds: float = 0.05):
    """Make every ``stat`` and listing below ``root`` take ``seconds``, and name the threads that asked.

    A walk of the project then costs its directory count times ``seconds``:
    the busy disk of #363, where a 25 ms walk took a second.
    """

    below = os.path.normcase(os.fspath(root))
    callers: list[str] = []
    real_stat, real_scandir = os.stat, os.scandir

    def mine(path) -> bool:
        try:
            return os.path.normcase(os.fsdecode(path)).startswith(below)
        except TypeError:
            return False

    def stat(path, *args, **kwargs):
        if mine(path):
            callers.append(threading.current_thread().name)
            time.sleep(seconds)
        return real_stat(path, *args, **kwargs)

    def scandir(path=".", *args, **kwargs):
        if mine(path):
            callers.append(threading.current_thread().name)
            time.sleep(seconds)
        return real_scandir(path, *args, **kwargs)

    with mock.patch("os.stat", stat), mock.patch("os.scandir", scandir):
        yield callers


def board(base: str | None, title: str) -> dict:
    return {"projectId": PROJECT_ID, "baseRevisionSha256": base, "title": title, "elements": [], "seenDocuments": []}


class ConditionalReadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        self.repository, _ = make_project(temporary)
        self.root = self.repository.layout.root
        retain_rhino_receipt(self.repository, self.repository.load_run(REFERENCE_RUN_ID),
                             stage_id="studio-stage", file_name="model.3dm", payload_bytes=MODEL_BYTES)
        self.app = create_app(StudioSettings(project_dir=self.root))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        saved = self.client.put("/api/board", json=board(None, "first"))
        self.assertEqual(saved.status_code, 200, saved.text)
        document = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "fileName": "sketch.png",
            "mimeType": "image/png", "contentBase64": base64.b64encode(image_bytes()).decode("ascii"),
        })
        self.assertEqual(document.status_code, 201, document.text)
        self.document = document.json()
        self.binding = bound_project(self.app.state)
        self.addCleanup(self.binding.close)
        settle(self.root)
        self.catch_up()
        self.assertTrue(self.binding.read_token().stable)

    def catch_up(self) -> None:
        """Let the layout watch see what the test did behind its back: one walk, waited for."""

        self.binding.layout_watch().sync()

    def test_every_listed_read_carries_a_strong_tag(self) -> None:
        self.assertEqual({route.split("?")[0] for route in ROUTES}, set(CONDITIONAL_READS))
        for route in ROUTES:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertRegex(response.headers["etag"], r'^"[0-9a-f]{32}"$')
                self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_if_none_match_answers_not_modified(self) -> None:
        for route in ROUTES:
            with self.subTest(route=route):
                tag = self.client.get(route).headers["etag"]
                for header in (tag, f"W/{tag}", f'"elsewhere", {tag}'):
                    response = self.client.get(route, headers={"If-None-Match": header})
                    self.assertEqual(response.status_code, 304, header)
                    self.assertEqual(response.content, b"")
                    self.assertEqual(response.headers["etag"], tag)
                    self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(self.client.get(route, headers={"If-None-Match": '"elsewhere"'}).status_code, 200)

    def test_the_query_is_part_of_the_tag(self) -> None:
        main = self.client.get("/api/design-history", params={"branchId": "main"}).headers["etag"]
        other = self.client.get("/api/design-history", params={"branchId": "other"}).headers["etag"]
        drawing = self.client.get("/api/working-source", params={"workspace": "drawing"}).headers["etag"]

        self.assertEqual(len({main, other, drawing}), 3)

    def test_the_memo_answers_the_same_bytes_without_reading_again(self) -> None:
        first = self.client.get("/api/design-history", params={"branchId": "main"})
        with mock.patch("project_runtime.api.routes.episodes.read_design_history",
                        side_effect=AssertionError("read again")):
            second = self.client.get("/api/design-history", params={"branchId": "main"})

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, first.content)
        for header in ("etag", "cache-control", "content-type", "content-length"):
            self.assertEqual(second.headers[header], first.headers[header], header)

    def test_a_board_saved_here_is_read_at_once_under_a_new_tag(self) -> None:
        before = self.client.get("/api/board")
        saved = self.client.put("/api/board", json=board(before.json()["revisionSha256"], "second"))
        self.assertEqual(saved.status_code, 200, saved.text)
        after = self.client.get("/api/board", headers={"If-None-Match": before.headers["etag"]})

        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.json()["title"], "second")
        self.assertNotEqual(after.headers["etag"], before.headers["etag"])

    def test_another_writer_is_read_once_the_layout_watch_sees_it(self) -> None:
        before = self.client.get("/api/board")
        token = self.binding.read_token()
        payload = {"schema": "StudioBoardScene@1", "projectId": PROJECT_ID,
                   "previousRevisionSha256": before.json()["revisionSha256"],
                   "title": "outside", "elements": [], "seenDocuments": []}
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        records = self.repository.layout.run(BOARD_RUN_ID).records
        # Written straight into the record directory, as another process would:
        # this process's write serial does not move, only the files do.
        (records / record_file_name(STUDIO_BOARD_SCENE, hashlib.sha256(data).hexdigest())).write_bytes(data)

        # Nobody tells the watch; it sees the change itself, within about a
        # second (a notification on Windows, a scan elsewhere).
        self.assertTrue(wait_until(lambda: self.binding.read_token().fingerprint != token.fingerprint),
                        "the layout watch never saw another process's write")
        self.assertEqual(self.binding.read_token().serial, token.serial)
        after = self.client.get("/api/board", headers={"If-None-Match": before.headers["etag"]})

        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.json()["title"], "outside")
        self.assertNotEqual(after.headers["etag"], before.headers["etag"])

    def test_an_unsettled_project_neither_answers_not_modified_nor_remembers(self) -> None:
        from project_runtime.application import boards

        before = self.client.get("/api/board")
        self.client.put("/api/board", json=board(before.json()["revisionSha256"], "moving"))
        with mock.patch.object(boards, "read_board", wraps=boards.read_board) as read:
            moving = self.client.get("/api/board")
            again = self.client.get("/api/board", headers={"If-None-Match": moving.headers["etag"]})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.content, moving.content)
        self.assertEqual(read.call_count, 2)

        # Settled, the same view is tagged anew and from then on answers 304.
        settle(self.root)
        self.catch_up()
        settled = self.client.get("/api/board", headers={"If-None-Match": moving.headers["etag"]})
        self.assertEqual(settled.status_code, 200)
        self.assertNotEqual(settled.headers["etag"], moving.headers["etag"])
        self.assertEqual(
            self.client.get("/api/board", headers={"If-None-Match": settled.headers["etag"]}).status_code, 304,
        )

    def test_a_job_transition_changes_the_worktrees_tag(self) -> None:
        worktrees = self.client.get("/api/worktrees").headers["etag"]
        board_tag = self.client.get("/api/board").headers["etag"]
        job = self.app.state.jobs.submit(candidate_id="studio-cand-noop", proposal_id="proposal-noop", work=lambda: None)
        deadline = time.monotonic() + 30
        while self.app.state.jobs.get(job.job_id).status != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.app.state.jobs.get(job.job_id).status, "succeeded")

        moved = self.client.get("/api/worktrees").headers["etag"]
        self.assertNotEqual(moved, worktrees)
        self.assertEqual(self.client.get("/api/board").headers["etag"], board_tag)

        renders = self.client.get("/api/render/jobs").headers["etag"]
        self.app.state.render_jobs._changed()
        self.assertNotEqual(self.client.get("/api/worktrees").headers["etag"], moved)
        self.assertNotEqual(self.client.get("/api/render/jobs").headers["etag"], renders)

    def test_an_error_answer_is_left_as_it_was(self) -> None:
        response = self.client.get("/api/working-source", params={"workspace": "nowhere"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "WORKSPACE_INVALID")
        self.assertNotIn("etag", response.headers)
        self.assertNotIn("cache-control", response.headers)

    def test_other_methods_and_paths_pass_through(self) -> None:
        self.assertNotIn("etag", self.client.get("/api/project").headers)
        self.assertNotIn("etag", self.client.head("/api/board").headers)
        saved = self.client.put("/api/board", json=board(self.client.get("/api/board").json()["revisionSha256"], "put"))
        self.assertNotIn("etag", saved.headers)

    def test_the_bytes_routes_are_immutable(self) -> None:
        artifact = self.client.get(f"/api/artifacts/{MODEL_SHA256}/bytes")
        self.assertEqual(artifact.status_code, 200, artifact.text)
        self.assertEqual(artifact.headers["cache-control"], IMMUTABLE)
        self.assertEqual(artifact.headers["etag"], f'"{MODEL_SHA256}"')
        digest = self.document["assetSha256"]
        page = self.client.get(f"/api/documents/{digest}/bytes", params={"runId": REFERENCE_RUN_ID})
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.headers["cache-control"], IMMUTABLE)
        self.assertEqual(page.headers["etag"], f'"{digest}"')

    def test_artifact_bytes_find_their_receipt_once_and_hash_every_read(self) -> None:
        self.assertEqual(self.client.get(f"/api/artifacts/{MODEL_SHA256}/bytes").status_code, 200)
        model = self.repository.layout.run(REFERENCE_RUN_ID).workspaces / "cad-studio-stage" / "model.3dm"
        with mock.patch("project_runtime.application.artifacts.list_artifacts",
                        side_effect=AssertionError("listed again")):
            self.assertEqual(self.client.get(f"/api/artifacts/{MODEL_SHA256}/bytes").content, MODEL_BYTES)
            # Rewritten in place, so no directory moves: only the hash can tell.
            model.write_bytes(b"edited in place")
            refused = self.client.get(f"/api/artifacts/{MODEL_SHA256}/bytes")

        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["code"], "ARTIFACT_DIGEST_MISMATCH")

    def test_document_bytes_find_their_registration_once_and_hash_every_read(self) -> None:
        digest = self.document["assetSha256"]
        url = f"/api/documents/{digest}/bytes"
        first = self.client.get(url, params={"runId": REFERENCE_RUN_ID})
        self.assertEqual(first.status_code, 200)
        with mock.patch("project_runtime.application.artifacts.list_documents",
                        side_effect=AssertionError("listed again")):
            self.assertEqual(self.client.get(url, params={"runId": REFERENCE_RUN_ID}).content, first.content)


class ReadTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        self.repository, _ = make_project(temporary)
        self.app = create_app(StudioSettings(project_dir=self.repository.layout.root))
        self.binding = bound_project(self.app.state)
        self.addCleanup(self.binding.close)

    def catch_up(self) -> None:
        self.binding.layout_watch().sync()

    def test_this_process_writes_move_the_token_at_once(self) -> None:
        settle(self.repository.layout.root)
        self.catch_up()
        before = self.binding.read_token()
        self.assertTrue(before.stable)
        self.assertEqual(self.binding.read_token(), before)
        self.repository.create_run("run-later")
        after = self.binding.read_token()

        # At once, before the watch has seen the write: the serial moved, and
        # a fingerprint taken before it is not trusted.
        self.assertGreater(after.serial, before.serial)
        self.assertFalse(after.stable)
        self.catch_up()
        seen = self.binding.read_token()
        self.assertEqual(seen.serial, after.serial)
        self.assertNotEqual(seen.fingerprint, before.fingerprint)
        self.assertFalse(seen.stable, "just written: its times have not settled")

    def test_the_memo_keeps_only_what_a_stable_token_read(self) -> None:
        calls: list[int] = []

        def compute() -> list[int]:
            calls.append(1)
            return [len(calls)]

        # Just written: every call computes, and nothing is kept.
        self.assertEqual(self.binding.memo(("probe",), compute), [1])
        self.assertEqual(self.binding.memo(("probe",), compute), [2])
        settle(self.repository.layout.root)
        self.catch_up()
        kept = self.binding.memo(("probe",), compute)
        self.assertIs(self.binding.memo(("probe",), compute), kept)
        self.assertEqual(len(calls), 3)

    def test_the_memo_forgets_the_least_recently_used(self) -> None:
        settle(self.repository.layout.root)
        self.catch_up()
        first = self.binding.memo(("entry", 0), lambda: object())
        self.assertIs(self.binding.memo(("entry", 0), lambda: object()), first)
        for index in range(1, MEMO_ENTRIES + 1):
            self.binding.memo(("entry", index), lambda: object())

        self.assertIsNot(self.binding.memo(("entry", 0), lambda: object()), first)

    def test_every_binding_on_the_project_shares_one_watch_until_the_last_closes(self) -> None:
        others = [ProjectBinding.open(StudioSettings(project_dir=self.repository.layout.root)) for _ in range(3)]
        for other in others:
            self.addCleanup(other.close)
        watches = {id(binding.layout_watch().watch) for binding in (self.binding, *others)}
        watch = self.binding.layout_watch().watch

        self.assertEqual(len(watches), 1)
        self.assertEqual(watch.leases, 4)
        for other in others:
            other.close()
        self.assertTrue(watch.running)
        self.binding.close()
        self.assertFalse(watch.running, "the last binding closed and its watch kept running")
        # Reading again watches again.
        self.assertIsNotNone(self.binding.read_token())
        self.assertIsNot(self.binding.layout_watch().watch, watch)


class SlowDiskTests(unittest.TestCase):
    """A disk so busy that every stat and listing takes 50 ms (#363).

    A walk of this project then takes seconds. An unchanged read - answered
    304, or from the memo - must not notice: no request walks the project,
    and none waits for the watch that does.
    """

    def setUp(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        self.repository, _ = make_project(temporary)
        self.root = self.repository.layout.root
        retain_rhino_receipt(self.repository, self.repository.load_run(REFERENCE_RUN_ID),
                             stage_id="studio-stage", file_name="model.3dm", payload_bytes=MODEL_BYTES)
        self.app = create_app(StudioSettings(project_dir=self.root))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.put("/api/board", json=board(None, "first")).status_code, 200)
        self.binding = bound_project(self.app.state)
        self.addCleanup(self.binding.close)
        settle(self.root)
        self.binding.layout_watch().sync()
        self.assertTrue(self.binding.read_token().stable)

    def timed(self, route: str, **headers: str) -> tuple[int, float]:
        started = time.perf_counter()
        response = self.client.get(route, headers=headers)
        return response.status_code, (time.perf_counter() - started) * 1000

    def test_an_unchanged_read_answers_in_milliseconds_and_never_walks(self) -> None:
        routes = ("/api/board", "/api/design-history?branchId=main", "/api/artifacts")
        tags = {}
        for route in routes:
            first = self.client.get(route)
            self.assertEqual(first.status_code, 200, first.text)
            tags[route] = first.headers["etag"]
        with slow_disk(self.root) as callers:
            # The watch's own reads slow down too; the requests must not.
            started = time.perf_counter()
            watched = self.binding.layout_watch().sync()
            walk_ms = (time.perf_counter() - started) * 1000
            timings = {route: ([self.timed(route) for _ in range(5)],
                               [self.timed(route, **{"If-None-Match": tags[route]}) for _ in range(5)])
                       for route in routes}

        self.assertTrue(watched.fingerprint.stable)
        self.assertGreater(walk_ms, 500, "the disk was not slow: the test proves nothing")
        for route, (kept, unchanged) in timings.items():
            with self.subTest(route=route, walk_ms=round(walk_ms), kept=kept, unchanged=unchanged):
                self.assertEqual({status for status, _ in kept}, {200})
                self.assertEqual({status for status, _ in unchanged}, {304})
                for answered in (kept, unchanged):
                    milliseconds = [ms for _, ms in answered]
                    self.assertLess(statistics.median(milliseconds), 20)
                    # One stat on the request path would cost 50 ms by itself.
                    self.assertLess(max(milliseconds), 45)
        self.assertTrue(callers)
        self.assertEqual({name for name in callers if not name.startswith("layout-watch:")}, set(),
                         "a request thread read the project's layout")

    def test_a_write_under_a_slow_disk_still_moves_the_token_at_once(self) -> None:
        before = self.client.get("/api/board")
        with slow_disk(self.root):
            saved = self.client.put("/api/board", json=board(before.json()["revisionSha256"], "slow"))
            self.assertEqual(saved.status_code, 200, saved.text)
            started = time.perf_counter()
            token = self.binding.read_token()
            read_ms = (time.perf_counter() - started) * 1000
            after = self.client.get("/api/board", headers={"If-None-Match": before.headers["etag"]})

        self.assertFalse(token.stable)
        self.assertLess(read_ms, 20)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.json()["title"], "slow")


if __name__ == "__main__":
    unittest.main()
