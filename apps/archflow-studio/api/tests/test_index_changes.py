"""``GET /api/index?since=&epoch=``, ``index.committed`` and a write's revision (GH-366, ADR-008 phase 2).

A client store keeps what the index holds as entities and catches up by
asking for the changes since the revision it holds: an empty answer when it is
current, the changes while the change log reaches back that far, and a whole
snapshot (``reset``) for another epoch, a revision ahead of the index or one
older than the log. Every commit is announced on the event stream as a hint,
and a write's answer names the revision that holds it.
"""

from __future__ import annotations

import base64
from pathlib import Path
import shutil
import tempfile
from unittest import mock

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_conditional_reads import wait_until
from .test_design_history import DesignHistoryFixture
from .test_documents import image_bytes


class IndexChangesTests(DesignHistoryFixture):
    def setUp(self) -> None:
        super().setUp()
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cache, True)
        self.app_ = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off",
                                              cache_dir=cache / "projects" / "runtime-test"))
        self.indexed = TestClient(self.app_)
        self.addCleanup(self.indexed.close)
        self.addCleanup(self.app_.state.stop_index_events)
        binding = bound_project(self.app_.state)
        self.addCleanup(binding.close)
        self.assertIsNotNone(binding.await_index(30), "the index loads")

    def write(self, name: str):
        response = self.indexed.post("/api/documents", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "fileName": name, "mimeType": "image/png",
            "contentBase64": base64.b64encode(image_bytes(color="red", size=(64 + sum(map(ord, name)) % 400, 80)))
            .decode("ascii"),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def snapshot(self) -> dict:
        response = self.indexed.get("/api/index")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_snapshot_is_every_entity_tagged_with_epoch_and_revision(self) -> None:
        response = self.indexed.get("/api/index")
        answer = response.json()

        self.assertTrue(answer["reset"])
        self.assertIsNone(answer["from"])
        self.assertEqual(answer["to"], answer["revision"])
        self.assertEqual(response.headers["etag"], f'"{answer["epoch"]}:{answer["revision"]}"')
        ids = {entity["id"] for entity in answer["upserts"]}
        self.assertIn(f"run:{REFERENCE_RUN_ID}", ids)
        self.assertIn("tree", ids)
        self.assertTrue(any(entity_id.startswith("area:") for entity_id in ids))
        self.assertEqual(self.indexed.get("/api/index", headers={"If-None-Match": response.headers["etag"]})
                         .status_code, 304)

    def test_the_current_revision_answers_nothing_changed(self) -> None:
        current = self.snapshot()
        answer = self.indexed.get("/api/index", params={"since": current["revision"], "epoch": current["epoch"]}).json()

        self.assertFalse(answer["reset"])
        self.assertEqual((answer["from"], answer["to"]), (current["revision"], current["revision"]))
        self.assertEqual((answer["upserts"], answer["deletes"]), ([], []))

    def test_a_write_names_its_revision_and_the_changes_since_hold_it(self) -> None:
        before = self.snapshot()
        written = self.write("plan.png").headers["x-monkey-index"]
        epoch, revision = written.split(":")

        self.assertEqual(epoch, before["epoch"])
        self.assertGreater(int(revision), before["revision"])
        answer = self.indexed.get("/api/index", params={"since": before["revision"], "epoch": epoch}).json()
        self.assertFalse(answer["reset"])
        self.assertEqual(answer["from"], before["revision"])
        self.assertGreaterEqual(answer["to"], int(revision))
        run = next(entity for entity in answer["upserts"] if entity["id"] == f"run:{REFERENCE_RUN_ID}")
        self.assertIn("plan.png", str(run["body"]["documents"]))
        self.assertEqual(answer["deletes"], [])
        self.assertNotIn("x-monkey-index", self.indexed.get("/api/documents").headers, "a read names no revision")

    def test_another_epoch_a_revision_ahead_or_below_the_log_answers_a_snapshot(self) -> None:
        current = self.snapshot()
        for since, epoch in ((current["revision"], "another-epoch"), (current["revision"] + 1, current["epoch"]),
                             (current["revision"], None)):
            with self.subTest(since=since, epoch=epoch):
                answer = self.indexed.get("/api/index", params={"since": since, **({"epoch": epoch} if epoch else {})}).json()
                self.assertTrue(answer["reset"])
                self.assertEqual(answer["epoch"], current["epoch"])
                self.assertEqual({entity["id"] for entity in answer["upserts"]},
                                 {entity["id"] for entity in current["upserts"]})
        with mock.patch("archflow.project.index.store.CHANGE_LOG_REVISIONS", 1):
            self.write("one.png")
            self.write("two.png")
        answer = self.indexed.get("/api/index", params={"since": current["revision"], "epoch": current["epoch"]}).json()
        self.assertTrue(answer["reset"], "older than the change log: a whole snapshot")

    def test_saving_the_local_recovery_moves_no_working_position(self) -> None:
        """Modeling's autosave rewrites the working pointer; the position a head is read from is its own entity."""
        before = self.snapshot()
        position = next(entity for entity in before["upserts"] if entity["id"] == "working")
        self.assertEqual(set(position["body"]), {"current", "runs", "active"})
        digest = self.indexed.get("/api/state").json()["stateDigest"]
        draft = {"source": {"projectId": PROJECT_ID, "sourceRunId": REFERENCE_RUN_ID, "sourceStageRef": None,
                            "stateDigest": digest},
                 "commands": [{"kind": "translate", "offset": [1, 0, 0]}], "attempt": {"syncedCommands": [], "pending": None}}
        revision = self.indexed.get("/api/working-draft").json()["revisionSha256"]
        saved = self.indexed.put("/api/working-draft/local", json={"projectId": PROJECT_ID, "baseRevisionSha256": revision,
                                                                   "draft": draft})
        self.assertEqual(saved.status_code, 200, saved.text)
        epoch, written = saved.headers["x-monkey-index"].split(":")

        answer = self.indexed.get("/api/index", params={"since": before["revision"], "epoch": epoch}).json()
        self.assertGreaterEqual(answer["to"], int(written))
        moved = {entity["id"] for entity in answer["upserts"]}
        self.assertIn("area:working", moved, "the pointer file moved")
        self.assertNotIn("working", moved, "the position a head is read from did not")
        self.assertNotIn("tree", moved)

    def test_each_commit_is_announced_on_the_event_stream(self) -> None:
        events = self.app_.state.events
        revision = int(self.write("plan.png").headers["x-monkey-index"].split(":")[1])

        self.assertTrue(wait_until(lambda: any(
            event["type"] == "index.committed" and event["revision"] >= revision for event in events.replay()), 10))
        committed = [event for event in events.replay() if event["type"] == "index.committed"]
        self.assertIn("run", committed[-1]["domains"])
        self.assertEqual(committed[-1]["epoch"], self.snapshot()["epoch"])

    def test_a_process_that_keeps_no_index_says_so(self) -> None:
        self.assertEqual(self.client.get("/api/index").status_code, 503)
        self.assertEqual(self.client.get("/api/index").json()["code"], "INDEX_UNAVAILABLE")
