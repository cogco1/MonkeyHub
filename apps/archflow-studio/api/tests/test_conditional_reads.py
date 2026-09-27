"""Conditional reads, the per-binding memo and immutable bytes (GH-363, ADR-008 phase 0a).

A view of the decision tree is re-derived from hundreds of retained records.
Read twice under an unchanged project it is the same view: the second read
answers 304 to a client that holds it and from memory to one that does not,
and any write - this process's at once, another's once the fingerprint is
taken again - gives it a new tag.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from archflow.project.layout import FINGERPRINT_SETTLED_NS
from archflow.project.record_kinds import STUDIO_BOARD_SCENE
from archflow.project.refs import record_file_name
from archflow_studio_api.application.binding import MEMO_ENTRIES, bound_project
from archflow_studio_api.application.boards import BOARD_RUN_ID
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.conditional import CONDITIONAL_READS

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
        self.now = [1000.0]
        self.binding.clock = lambda: self.now[0]
        settle(self.root)
        self.expire()
        self.assertTrue(self.binding.read_token().stable)

    def expire(self) -> None:
        """Let the fingerprint's time to live pass, on the binding's own clock."""

        self.now[0] += 1.5

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
        with mock.patch("archflow_studio_api.routes.episodes.read_design_history",
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

    def test_another_writer_is_read_once_the_fingerprint_is_taken_again(self) -> None:
        before = self.client.get("/api/board")
        payload = {"schema": "StudioBoardScene@1", "projectId": PROJECT_ID,
                   "previousRevisionSha256": before.json()["revisionSha256"],
                   "title": "outside", "elements": [], "seenDocuments": []}
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        records = self.repository.layout.run(BOARD_RUN_ID).records
        # Written straight into the record directory, as another process would.
        (records / record_file_name(STUDIO_BOARD_SCENE, hashlib.sha256(data).hexdigest())).write_bytes(data)

        # Within its time to live the fingerprint still answers for the project.
        within = self.client.get("/api/board", headers={"If-None-Match": before.headers["etag"]})
        self.assertEqual(within.status_code, 304)
        self.expire()
        after = self.client.get("/api/board", headers={"If-None-Match": before.headers["etag"]})

        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.json()["title"], "outside")
        self.assertNotEqual(after.headers["etag"], before.headers["etag"])

    def test_an_unsettled_project_neither_answers_not_modified_nor_remembers(self) -> None:
        from archflow_studio_api.application import boards

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
        self.expire()
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
        with mock.patch("archflow_studio_api.application.artifacts.list_artifacts",
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
        with mock.patch("archflow_studio_api.application.artifacts.list_documents",
                        side_effect=AssertionError("listed again")):
            self.assertEqual(self.client.get(url, params={"runId": REFERENCE_RUN_ID}).content, first.content)


class ReadTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        self.repository, _ = make_project(temporary)
        self.app = create_app(StudioSettings(project_dir=self.repository.layout.root))
        self.binding = bound_project(self.app.state)
        self.now = [50.0]
        self.binding.clock = lambda: self.now[0]

    def test_this_process_writes_move_the_token_at_once(self) -> None:
        settle(self.repository.layout.root)
        before = self.binding.read_token()
        self.assertTrue(before.stable)
        self.assertEqual(self.binding.read_token(), before)
        self.repository.create_run("run-later")
        after = self.binding.read_token()

        self.assertGreater(after.serial, before.serial)
        self.assertNotEqual(after.fingerprint, before.fingerprint)
        self.assertFalse(after.stable)

    def test_the_memo_keeps_only_what_a_stable_token_read(self) -> None:
        calls: list[int] = []

        def compute() -> list[int]:
            calls.append(1)
            return [len(calls)]

        # Just written: every call computes, and nothing is kept.
        self.assertEqual(self.binding.memo(("probe",), compute), [1])
        self.assertEqual(self.binding.memo(("probe",), compute), [2])
        settle(self.repository.layout.root)
        self.now[0] += 1.5
        kept = self.binding.memo(("probe",), compute)
        self.assertIs(self.binding.memo(("probe",), compute), kept)
        self.assertEqual(len(calls), 3)

    def test_the_memo_forgets_the_least_recently_used(self) -> None:
        settle(self.repository.layout.root)
        first = self.binding.memo(("entry", 0), lambda: object())
        for index in range(1, MEMO_ENTRIES + 1):
            self.binding.memo(("entry", index), lambda: object())

        self.assertIsNot(self.binding.memo(("entry", 0), lambda: object()), first)


if __name__ == "__main__":
    unittest.main()
