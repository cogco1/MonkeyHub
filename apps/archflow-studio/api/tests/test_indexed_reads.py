"""Routes that read the project index answer exactly what the runs answer (GH-365, ADR-008 phase 1b).

Two applications serve one project: one reads the runs, the other keeps a
project index in a cache directory outside the project. Every route that
reads the index - the artifact and document listings and the two byte
lookups - is compared byte for byte before a write, after this process's
writes and after another process's write, and is shown to answer from its
rows rather than from the runs. The index is written only by its keeper's
thread; a request only reads a snapshot of it, and falls back to the runs
whenever the index cannot answer.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import tempfile
import threading
import time
from unittest import mock

from fastapi.testclient import TestClient

from archflow.project.index import INDEX_FILE, IndexLocked, ProjectIndex
from archflow.project.index.store import _WriterLease
from archflow_studio_api.application import artifacts
from archflow_studio_api.application import binding as binding_module
from archflow_studio_api.application.binding import ProjectBinding, bound_project
from archflow_studio_api.application.index import StudioProjector, attach_project_index
from archflow_studio_api.application.synchronization import pull_shared_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import SettingsError, StudioSettings
from archflow_studio_api.transport.conditional import INDEXED_READS

from .support import PROJECT_ID, REFERENCE_RUN_ID, retain_rhino_receipt
from .test_conditional_reads import settle, slow_disk, wait_until
from .test_design_history import DesignHistoryFixture
from .test_documents import image_bytes

# Every listing route that reads the index, and nothing else.
READS = (
    "/api/artifacts",
    "/api/documents",
    f"/api/documents?runId={REFERENCE_RUN_ID}",
)
MODEL_BYTES = b"project-index-3dm"
MODEL_SHA256 = hashlib.sha256(MODEL_BYTES).hexdigest()


class IndexedReadTests(DesignHistoryFixture):
    def setUp(self) -> None:
        super().setUp()
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cache, True)
        self.index_dir = cache / "projects" / "runtime-test"
        self.project_dir = self.root / PROJECT_ID
        retain_rhino_receipt(self.repository, self.repository.load_run(REFERENCE_RUN_ID),
                             stage_id="index-stage", file_name="model.3dm", payload_bytes=MODEL_BYTES)
        self.indexed_app = self.app_with_index()
        self.indexed = TestClient(self.indexed_app)
        self.addCleanup(self.indexed.close)

    def app_with_index(self):
        return create_app(StudioSettings(project_dir=self.project_dir, cad_export="off", index_dir=self.index_dir))

    def binding(self, app=None):
        binding = bound_project((app or self.indexed_app).state)
        self.addCleanup(binding.close)
        keeper = binding.await_index(30)
        self.assertIsNotNone(keeper, "the index loads")
        return binding

    def caught_up(self, binding) -> None:
        """Let the watch see what the test did behind its back, and the index apply it."""

        seen = binding.layout_watch().sync()
        keeper = binding.await_index(30)
        self.assertTrue(wait_until(lambda: keeper.state is not None
                                   and keeper.state.digest == seen.fingerprint.digest, 10))
        self.assertIsNotNone(keeper.wait_readable(10))

    def document(self, name: str) -> dict:
        response = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "fileName": name,
            "mimeType": "image/png",
            # A size of its own per name: distinct bytes, so a distinct document.
            "contentBase64": base64.b64encode(image_bytes(color="red", size=(64 + sum(map(ord, name)) % 400, 80)))
            .decode("ascii"),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def assert_same(self, label: str) -> None:
        for route in READS:
            with self.subTest(label=label, route=route):
                plain, indexed = self.client.get(route), self.indexed.get(route)
                self.assertEqual(indexed.status_code, plain.status_code, indexed.text)
                self.assertEqual(indexed.content, plain.content)

    def test_only_the_routes_that_read_the_index_are_listed(self) -> None:
        self.assertEqual({route.split("?")[0] for route in READS}, set(INDEXED_READS))

    def test_every_indexed_view_is_the_same_bytes_before_and_after_writes(self) -> None:
        stage = self.initialize()
        candidate_id = self.candidate_from(stage)
        document = self.document("plan.png")
        binding = self.binding()
        self.caught_up(binding)
        self.assert_same("before")
        listed = self.indexed.get("/api/artifacts").json()["artifacts"]
        self.assertTrue(any(row["sourceStageRef"] for row in listed), "a candidate's committed source is listed")

        # This process's writes: accepting a candidate moves the design tree.
        accepted = self.accept(candidate_id, stage)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.document("section.png")
        self.assert_same("after this process's writes")

        with mock.patch.object(artifacts, "_run_artifacts", side_effect=AssertionError("read the runs")), \
                mock.patch.object(artifacts, "_run_documents", side_effect=AssertionError("read the runs")):
            self.assertEqual(self.indexed.get("/api/artifacts").status_code, 200)
            self.assertEqual(self.indexed.get("/api/documents").status_code, 200)
            model = self.indexed.get(f"/api/artifacts/{MODEL_SHA256}/bytes")
            page = self.indexed.get(f"/api/documents/{document['assetSha256']}/bytes",
                                    params={"runId": REFERENCE_RUN_ID})
        self.assertEqual(model.content, MODEL_BYTES)
        self.assertEqual(page.content, self.client.get(f"/api/documents/{document['assetSha256']}/bytes",
                                                       params={"runId": REFERENCE_RUN_ID}).content)

        # Another process's write: the exported model replaced behind this process's back.
        exported = self.repository.layout.run(REFERENCE_RUN_ID).workspaces / "cad-index-stage" / "model.3dm"
        replacement = exported.with_name("model.3dm.outside")
        replacement.write_bytes(b"replaced by another process")
        os.replace(replacement, exported)
        self.caught_up(binding)
        self.assert_same("after another process's write")
        rows = {row["fileName"]: row["available"] for row in self.indexed.get("/api/artifacts").json()["artifacts"]
                if row["runId"] == REFERENCE_RUN_ID}
        self.assertFalse(rows["model.3dm"])

    def test_no_request_projects_and_this_process_writes_are_read_at_once(self) -> None:
        binding = self.binding()
        projected: list[str] = []
        original = StudioProjector.project_run

        def project_run(projector, run_id):
            projected.append(threading.current_thread().name)
            return original(projector, run_id)

        with mock.patch.object(StudioProjector, "project_run", project_run):
            for number in range(3):
                self.document(f"sheet-{number}.png")
                self.assertEqual(self.indexed.get("/api/documents").content, self.client.get("/api/documents").content)
                self.assertEqual(self.indexed.get(f"/api/index/document",
                                                  params={"runId": REFERENCE_RUN_ID}).status_code, 200)
        self.assertTrue(projected, "the keeper applied the writes")
        self.assertEqual({name for name in projected if not name.startswith("project-index:")}, set(),
                         "a request thread projected")
        self.assertIsNotNone(binding.index_reader(wait=0))

    def test_the_tag_names_the_index_and_answers_not_modified_only_when_it_is_current(self) -> None:
        binding = self.binding()
        settle(self.project_dir)
        self.caught_up(binding)
        before = self.indexed.get("/api/artifacts")
        state = binding.index_state()
        self.assertEqual(self.indexed.get("/api/artifacts", headers={"If-None-Match": before.headers["etag"]})
                         .status_code, 304)

        self.document("later.png")
        after = self.indexed.get("/api/documents")
        self.assertEqual(self.indexed.get("/api/artifacts", headers={"If-None-Match": before.headers["etag"]})
                         .status_code, 200)
        self.assertGreater(binding.index_state().token.revision, state.token.revision)
        self.assertEqual(len(after.json()["documents"]), len(self.client.get("/api/documents").json()["documents"]))

    def test_an_indexed_view_keeps_no_answer_and_the_others_keep_theirs(self) -> None:
        binding = self.binding()
        settle(self.project_dir)
        self.caught_up(binding)
        for route in ("/api/artifacts", "/api/documents", "/api/design-history?branchId=main", "/api/board"):
            self.indexed.get(route)
        kept = {key[1][1] for key in binding._memo if key[1][0] == "conditional-read"}
        tags = {route: self.indexed.get(route).headers["etag"]
                for route in ("/api/artifacts", "/api/documents", "/api/board")}

        self.assertIn(tags["/api/board"], kept, "a view the index does not serve still keeps its answer")
        self.assertNotIn(tags["/api/artifacts"], kept)
        self.assertNotIn(tags["/api/documents"], kept)

    def test_a_restarted_process_never_answers_not_modified_to_the_last_one(self) -> None:
        binding = self.binding()
        settle(self.project_dir)
        self.caught_up(binding)
        before = self.indexed.get("/api/artifacts")
        state = binding.index_state()
        binding.close()

        # A new process: a new read epoch, and the same kept index.
        with mock.patch.object(binding_module, "READ_EPOCH", "another-process"):
            restarted = self.app_with_index()
            again = self.binding(restarted)
            self.assertEqual(again.await_index().index.loaded, "reused")
            self.assertEqual(again.index_state().token, state.token)
            self.caught_up(again)
            with TestClient(restarted) as client:
                answer = client.get("/api/artifacts", headers={"If-None-Match": before.headers["etag"]})
        self.assertEqual(answer.status_code, 200, "the index's epoch survived; the process's did not")
        self.assertEqual(answer.content, before.content)

    def test_reads_while_the_index_applies_are_whole_and_never_fail(self) -> None:
        self.binding()
        stop = threading.Event()
        statuses: list[int] = []
        counts: list[int] = []

        def read() -> None:
            while not stop.is_set():
                response = self.indexed.get("/api/documents")
                statuses.append(response.status_code)
                if response.status_code == 200:
                    counts.append(len(response.json()["documents"]))

        with ThreadPoolExecutor(4) as pool:
            readers = [pool.submit(read) for _ in range(4)]
            try:
                for number in range(8):
                    self.document(f"busy-{number}.png")
            finally:
                stop.set()
            for reader in readers:
                reader.result(60)

        self.assertTrue(statuses)
        self.assertEqual(set(statuses), {200})
        final = len(self.client.get("/api/documents").json()["documents"])
        self.assertEqual(len(self.indexed.get("/api/documents").json()["documents"]), final)
        self.assertLessEqual(max(counts), final)

    def test_an_index_sqlite_refuses_is_read_from_the_runs(self) -> None:
        self.binding()
        plain = self.client.get("/api/artifacts")

        def refused(index):
            raise sqlite3.OperationalError("disk I/O error")

        with mock.patch.object(ProjectIndex, "snapshot", refused):
            answered = self.indexed.get("/api/artifacts")
            model = self.indexed.get(f"/api/artifacts/{MODEL_SHA256}/bytes")
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(answered.content, plain.content)
        self.assertEqual(model.content, MODEL_BYTES)

    def test_concurrent_first_requests_open_one_binding_and_one_index(self) -> None:
        app = self.app_with_index()
        barrier = threading.Barrier(6)

        def first():
            barrier.wait()
            return bound_project(app.state)

        with ThreadPoolExecutor(6) as pool:
            bindings = list(pool.map(lambda _: first(), range(6)))
        self.indexed.close()  # the other app has not opened its index; this one never will
        self.assertEqual({id(item) for item in bindings}, {id(bindings[0])})
        keeper = self.binding(app).await_index(30)
        self.assertIsNotNone(keeper)
        self.assertIsNone(keeper.failure)

    def test_a_shared_project_pull_binds_again_and_the_index_is_kept_by_one_keeper(self) -> None:
        old = self.binding()
        old_keeper = old.await_index(30)
        old_thread = old_keeper._thread
        pulled = pull_shared_project(self.indexed_app.state, client=_SharedProject(self.repository))
        self.assertEqual(pulled.project_id, PROJECT_ID)

        self.assertFalse(old_thread.is_alive(), "the old binding's keeper stopped")
        self.assertIsNone(old_keeper.index._lease, "and gave up index.lock")
        new = self.binding()
        self.assertIsNot(new, old)
        keeper = new.await_index(30)
        self.assertIsNotNone(keeper, "the next binding has the index loaded")
        self.assertIsNone(keeper.failure)
        self.assertEqual(new.index_status(), "ready")
        self.assertEqual([thread for thread in threading.enumerate() if thread.name.startswith("project-index:")],
                         [keeper._thread], "one keeper runs")
        self.assertIsNotNone(keeper.index._lease)
        with self.assertRaises(IndexLocked, msg="index.lock is held"):
            _WriterLease(self.index_dir)
        self.assertEqual(self.indexed.get("/api/artifacts").content, self.client.get("/api/artifacts").content)

    def test_a_binding_nobody_holds_stops_its_keeper(self) -> None:
        binding = ProjectBinding.open(self.indexed_app.state.settings)
        keeper = attach_project_index(binding, self.index_dir)
        self.assertIsNotNone(keeper.wait_loaded(30), keeper.failure)
        thread = keeper._thread
        del binding
        gc.collect()
        self.assertTrue(wait_until(lambda: not thread.is_alive(), 10), "the keeper outlived its binding")
        self.assertIsNone(keeper.index._lease, "index.lock was given up")

    def test_a_deleted_index_is_rebuilt_and_the_project_still_opens(self) -> None:
        binding = self.binding()
        epoch = binding.index_state().token.epoch
        binding.close()
        shutil.rmtree(self.index_dir)

        restarted = self.app_with_index()
        again = self.binding(restarted)
        self.assertEqual(again.await_index().index.loaded, "rebuilt")
        self.assertNotEqual(again.index_state().token.epoch, epoch)
        with TestClient(restarted) as client:
            self.assertEqual(client.get("/api/artifacts").content, self.client.get("/api/artifacts").content)

    def test_a_corrupt_index_is_rebuilt_and_the_project_still_opens(self) -> None:
        self.binding().close()
        (self.index_dir / INDEX_FILE).write_bytes(b"\0garbage" * 512)
        restarted = self.app_with_index()
        with self.assertLogs("archflow.project.index.store", level="WARNING"):
            again = self.binding(restarted)
        self.assertEqual(again.await_index().index.loaded, "rebuilt")
        self.assertTrue(any(path.name.startswith(INDEX_FILE + ".corrupt-") for path in self.index_dir.iterdir()))
        with TestClient(restarted) as client:
            self.assertEqual(client.get("/api/artifacts").status_code, 200)

    def test_agents_query_the_index_read_only(self) -> None:
        self.binding()
        found = self.indexed.get("/api/index/artifact", params={"sha256": MODEL_SHA256})
        self.assertEqual(found.status_code, 200, found.text)
        answer = found.json()
        self.assertEqual(answer["projectId"], PROJECT_ID)
        self.assertEqual([row["run_id"] for row in answer["rows"]], [REFERENCE_RUN_ID])
        self.assertTrue(answer["rows"][0]["body"]["receipt_ref"].startswith("project://"))
        self.assertFalse(answer["truncated"])
        records = self.indexed.get("/api/index/record", params={"runId": REFERENCE_RUN_ID, "limit": 1}).json()
        self.assertEqual(len(records["rows"]), 1)
        self.assertTrue(records["truncated"])

        self.assertEqual(self.indexed.get("/api/index/secrets").json()["code"], "INDEX_TABLE_NOT_FOUND")
        self.assertEqual(self.indexed.get("/api/index/run", params={"body": "x"}).json()["code"],
                         "INDEX_FILTER_INVALID")
        self.assertEqual(self.client.get("/api/index/run").json()["code"], "INDEX_UNAVAILABLE",
                         "a process that keeps no index says so")
        self.assertEqual(self.indexed.post("/api/index/run").status_code, 405)

    def test_the_index_is_never_kept_inside_the_project(self) -> None:
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=self.project_dir, index_dir=self.project_dir / "cache")
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=self.project_dir, index_dir=Path("relative"))

    def test_an_unchanged_indexed_read_under_a_slow_disk_never_touches_the_project(self) -> None:
        binding = self.binding()
        settle(self.project_dir)
        self.caught_up(binding)
        tags = {route: self.indexed.get(route).headers["etag"] for route in READS}
        with slow_disk(self.project_dir) as callers:
            timings = {route: ([self.timed(route) for _ in range(5)],
                               [self.timed(route, **{"If-None-Match": tags[route]}) for _ in range(5)])
                       for route in READS}
        for route, (read, unchanged) in timings.items():
            with self.subTest(route=route, read=read, unchanged=unchanged):
                self.assertEqual({status for status, _ in read}, {200})
                self.assertEqual({status for status, _ in unchanged}, {304})
                for answered in (read, unchanged):
                    self.assertLess(statistics.median([ms for _, ms in answered]), 20)
        self.assertEqual({name for name in callers if not name.startswith(("layout-watch:", "project-index:"))},
                         set(), "a request thread read the project")

    def timed(self, route: str, **headers: str) -> tuple[int, float]:
        started = time.perf_counter()
        response = self.indexed.get(route, headers=headers)
        return response.status_code, (time.perf_counter() - started) * 1000


class _SharedProject:
    """A shared project that holds exactly what the local one does: a pull transfers nothing."""

    project_id = PROJECT_ID

    def __init__(self, repository) -> None:
        self.repository = repository

    def manifest(self) -> dict:
        return self.repository.export_transfer(include_contents=False)

    def request(self, *args, **kwargs):
        raise AssertionError("every file is already local")
