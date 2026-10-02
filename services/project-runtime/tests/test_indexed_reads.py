"""Routes that read the project index answer exactly what the runs answer (GH-365, ADR-008 phase 1b).

Two applications serve one project: one reads the runs, the other keeps a
project index in a cache directory outside the project. Every route that
reads the index - the artifact and document listings, the two byte lookups
and, since #599, the design tree's views - is compared byte for byte before
a write, after this process's writes and after another process's write, and
is shown to answer from its rows rather than from the runs. The index is
written only by its keeper's thread; a request only reads a snapshot of it,
and falls back to the runs whenever the index cannot answer. Nothing watches
the project (ADR-012): another process's write is read when the project is
read again (``POST /api/project/refresh``).
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

from archflow.project.index import INDEX_FILE, IndexKeeper, IndexLocked, ProjectIndex
from archflow.project.index.store import _WriterLease
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_CANDIDATE_DELTA
from project_runtime.application import artifacts
from project_runtime import binding as binding_module
from project_runtime.binding import ProjectBinding, bound_project
from project_runtime.index import StudioProjector, attach_project_index, indexed_runs
from project_runtime.synchronization import pull_shared_project
from project_runtime.main import create_app
from project_runtime.settings import SettingsError, StudioSettings
from project_runtime.api.conditional import INDEXED_READS

from .support import PROJECT_ID, REFERENCE_RUN_ID, retain_rhino_receipt
from .test_conditional_reads import settle, slow_disk, wait_until
from .test_documents import image_bytes
from .test_working_source import WorkingSourceFixture

# Every listing route that reads the index, and nothing else.
READS = (
    "/api/artifacts",
    "/api/documents",
    f"/api/documents?runId={REFERENCE_RUN_ID}",
)
# The design tree's views (#599): they read the index for what every run says, and the project for the rest.
TREE_READS = (
    "/api/design-history?branchId=main",
    "/api/worktrees",
    "/api/working-source?workspace=modeling",
)
MODEL_BYTES = b"project-index-3dm"
MODEL_SHA256 = hashlib.sha256(MODEL_BYTES).hexdigest()
# The threads that keep the index and re-check the layout, which read the runs by design.
BACKGROUND = ("project-index:", "layout-recheck:", "projection-")


class IndexedReadTests(WorkingSourceFixture):
    def setUp(self) -> None:
        super().setUp()
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cache, True)
        self.cache_dir = cache / "projects" / "runtime-test"
        self.index_dir = self.cache_dir / "index"
        self.project_dir = self.root / PROJECT_ID
        retain_rhino_receipt(self.repository, self.repository.load_run(REFERENCE_RUN_ID),
                             stage_id="index-stage", file_name="model.3dm", payload_bytes=MODEL_BYTES)
        self.indexed_app = self.app_with_index()
        self.indexed = TestClient(self.indexed_app)
        self.addCleanup(self.indexed.close)

    def app_with_index(self):
        return create_app(StudioSettings(project_dir=self.project_dir, cad_export="off", cache_dir=self.cache_dir))

    def binding(self, app=None):
        binding = bound_project((app or self.indexed_app).state)
        self.addCleanup(binding.close)
        keeper = binding.await_index(30)
        self.assertIsNotNone(keeper, "the index loads")
        return binding

    def caught_up(self, binding) -> None:
        """Read the project again, as a refresh does: the test changed it behind both processes' backs.

        The indexed binding's index applies the reading before ``refresh``
        returns; the plain process reads it again too.
        """

        binding.refresh()
        keeper = binding.await_index(30)
        self.assertTrue(wait_until(lambda: keeper.state is not None and keeper.state.digest
                                   == binding.known_layout().latest().fingerprint.digest, 10))
        self.assertIsNotNone(keeper.wait_readable(10))
        bound_project(self.app.state).refresh()

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
        for route in READS + TREE_READS:
            with self.subTest(label=label, route=route):
                plain, indexed = self.client.get(route), self.indexed.get(route)
                self.assertEqual(indexed.status_code, plain.status_code, indexed.text)
                self.assertEqual(indexed.content, plain.content)

    def test_only_the_routes_that_read_the_index_are_listed(self) -> None:
        self.assertEqual({route.split("?")[0] for route in READS + TREE_READS}, set(INDEXED_READS))

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

    def test_an_outside_edit_is_read_on_refresh_and_not_before(self) -> None:
        """ADR-012: nothing watches the project; what changed outside MonkeyHub is read when it is read again."""

        stage = self.initialize()
        candidate = self.candidate_from(stage)
        binding = self.binding()
        settle(self.project_dir)
        self.caught_up(binding)
        worktrees = self.indexed.get("/api/worktrees")
        self.assertEqual(worktrees.status_code, 200, worktrees.text)
        self.assertIn(candidate, worktrees.text)
        index = self.indexed.get("/api/index").json()
        self.assertIn(f"run:{candidate}", [entity["id"] for entity in index["upserts"]])
        since = {"since": index["revision"], "epoch": index["epoch"]}

        # Moved out of the project folder by hand: this process wrote nothing.
        os.rename(self.repository.layout.runs / candidate, self.project_dir / f"{candidate}.outside")
        time.sleep(1.5)  # a watch would have seen it by now
        self.assertEqual(self.indexed.get("/api/worktrees", headers={"If-None-Match": worktrees.headers["etag"]})
                         .status_code, 304)
        unmoved = self.indexed.get("/api/index", params=since).json()
        self.assertEqual((unmoved["to"], unmoved["upserts"], unmoved["deletes"]), (index["revision"], [], []))

        refreshed = self.indexed.post("/api/project/refresh", json={"projectId": PROJECT_ID})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        answer = refreshed.json()
        self.assertTrue(answer["changedOutside"])
        self.assertIn(f"runs/{candidate}", answer["moved"])
        self.assertEqual(answer["movedCount"], len(answer["moved"]))
        self.assertEqual(refreshed.headers["x-monkey-index"].split(":")[0], index["epoch"])

        after = self.indexed.get("/api/worktrees", headers={"If-None-Match": worktrees.headers["etag"]})
        self.assertEqual(after.status_code, 200)
        self.assertNotEqual(after.content, worktrees.content)
        moved = self.indexed.get("/api/index", params=since).json()
        self.assertGreater(moved["to"], index["revision"])
        self.assertIn(f"run:{candidate}", moved["deletes"])
        self.assertEqual(int(refreshed.headers["x-monkey-index"].split(":")[1]), moved["to"])
        # The plain process, read again too, answers the same bytes.
        self.assertTrue(self.client.post("/api/project/refresh", json={"projectId": PROJECT_ID}).json()["changedOutside"])
        self.assertEqual(after.content, self.client.get("/api/worktrees").content)
        self.assertFalse(self.indexed.post("/api/project/refresh", json={"projectId": PROJECT_ID})
                         .json()["changedOutside"], "read again with nothing moved since")

    def test_a_refresh_answers_only_for_its_own_project(self) -> None:
        self.binding()
        refused = self.indexed.post("/api/project/refresh", json={"projectId": "another-project"})
        self.assertEqual((refused.status_code, refused.json()["code"]), (404, "PROJECT_NOT_FOUND"))
        self.assertEqual(self.indexed.post("/api/project/refresh", json={}).status_code, 422)

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
        routes = ("/api/artifacts", "/api/documents", *TREE_READS, "/api/board")
        for route in routes:
            self.indexed.get(route)
        kept = {key[1][1] for key in binding._memo if key[1][0] == "conditional-read"}
        tags = {route: self.indexed.get(route).headers["etag"] for route in routes}

        self.assertIn(tags["/api/board"], kept, "a view the index does not serve still keeps its answer")
        for route in ("/api/artifacts", "/api/documents", *TREE_READS):
            with self.subTest(route=route):
                self.assertNotIn(tags[route], kept)
                self.assertEqual(self.indexed.get(route, headers={"If-None-Match": tags[route]}).status_code, 304)

    def tree_with_lines(self) -> dict[str, str]:
        """A Working Head two steps from its Stage, a result diverged from that Stage, and a run two changes claim."""

        stage = self.initialize()
        first = self.candidate_from(stage)
        self.adopt(first)
        head = self.continue_from(first)
        self.adopt(head)
        other = self.candidate_from(stage, 2.8)
        broken = self.candidate_from(stage, 3.1)
        run = self.repository.load_run(broken)
        [ref] = self.repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD,
                                                                                      run_id=broken),
                                          record_kind=STUDIO_CANDIDATE_DELTA)
        # A second retained change for the same run: every reader refuses it (CANDIDATE_DELTA_INVALID).
        self.repository.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=broken),
                                 record_kind=STUDIO_CANDIDATE_DELTA,
                                 payload={**self.repository.load_json(ref), "request": "a competing change"})
        return {"stage": stage["candidateId"], "first": first, "head": head, "other": other, "broken": broken}

    def test_the_tree_reads_every_runs_change_and_survey_from_one_snapshot(self) -> None:
        """#599: the tree's views read each run's change and the reference survey from the index, not the runs."""

        runs = self.tree_with_lines()
        binding = self.binding()
        self.caught_up(binding)
        self.assert_same("a line, a diverged result and a run with competing changes")
        graph = self.indexed.get("/api/worktrees").json()
        self.assertEqual(graph["head"]["lineage"], [runs["head"], runs["first"], runs["stage"]])
        self.assertTrue(any(runs["broken"] in warning for warning in graph["warnings"]), graph["warnings"])

        asked: list[tuple[str, str]] = []

        def counted(name, original):
            def reader(this, run_id, *args, **kwargs):
                if not threading.current_thread().name.startswith(BACKGROUND):
                    asked.append((name, run_id))
                return original(this, run_id, *args, **kwargs)
            return reader

        def reads(client) -> list[tuple[str, str]]:
            asked.clear()
            with mock.patch.object(ProjectBinding, "candidate_delta",
                                   counted("change", ProjectBinding.candidate_delta)), \
                    mock.patch.object(ProjectBinding, "_survey_run", counted("survey", ProjectBinding._survey_run)), \
                    mock.patch.object(ProjectBinding, "_receipts_of", counted("receipts", ProjectBinding._receipts_of)):
                for route in TREE_READS:
                    self.assertEqual(client.get(route).status_code, 200, route)
            return list(asked)

        indexed = reads(self.indexed)
        # Only the Working Head's own projection reads its change, and the run whose change the index
        # could not read is read from the project, so its refusal is the project's own.
        self.assertEqual({run_id for name, run_id in indexed if name == "change"}, {runs["head"], runs["broken"]})
        self.assertEqual([entry for entry in indexed if entry[0] != "change"], [],
                         "no run was surveyed and no run's receipts were listed")

    def test_the_tree_reads_the_runs_when_the_index_cannot_answer(self) -> None:
        self.tree_with_lines()
        binding = self.binding()
        self.caught_up(binding)
        surveyed: list[str] = []
        original = ProjectBinding._survey_run

        def counted(this, run_id):
            if not threading.current_thread().name.startswith(BACKGROUND):
                surveyed.append(run_id)
            return original(this, run_id)

        def refused(index):
            raise sqlite3.OperationalError("disk I/O error")

        with mock.patch.object(ProjectBinding, "_survey_run", counted):
            for route in TREE_READS:
                self.assertEqual(self.indexed.get(route).status_code, 200, route)
            self.assertEqual(surveyed, [], "the index answers the Working Head's survey")
            with mock.patch.object(ProjectIndex, "snapshot", refused):
                for route in TREE_READS:
                    with self.subTest(route=route):
                        surveyed.clear()
                        answered = self.indexed.get(route)
                        self.assertEqual(set(surveyed), set(self.repository.run_ids()), "the runs were surveyed")
                        self.assertEqual(answered.status_code, 200, answered.text)
                        self.assertEqual(answered.content, self.client.get(route).content)

        # An index still behind this process's own writes once the wait ends is not read either.
        with mock.patch.object(IndexKeeper, "wait_readable", lambda keeper, timeout=None: None):
            self.assertIsNone(indexed_runs(binding))
            for route in TREE_READS:
                self.assertEqual(self.indexed.get(route).content, self.client.get(route).content, route)

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
        binding = ProjectBinding.open(self.indexed_app.state.settings, follows_layout=True)
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
            StudioSettings(project_dir=self.project_dir, cache_dir=self.project_dir / "cache")
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=self.project_dir, cache_dir=Path("relative"))

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
        self.assertEqual({name for name in callers if not name.startswith(("layout-recheck:", "project-index:"))},
                         set(), "a request thread read the project")

    def test_a_started_worker_prepares_its_first_views_before_any_request_asks(self) -> None:
        """#449: a worker with an index prepares its first reads; one asked meanwhile waits for them.

        Once the index answers, the tree's views read it on every request and
        keep no prepared answer (#599).
        """

        from project_runtime.api.routes import episodes, runtime

        stage = self.initialize()
        candidate_id = self.candidate_from(stage)
        settle(self.project_dir)
        derived, release = {"history": 0, "worktrees": 0}, threading.Event()

        def counted(name, derive, *, hold=False):
            def wrapper(*args, **kwargs):
                derived[name] += 1
                if hold:
                    release.wait(30)
                return derive(*args, **kwargs)
            return wrapper

        app = self.app_with_index()
        app.state.prepare_first_reads = True
        with mock.patch.object(episodes, "read_design_history", counted("history", episodes.read_design_history, hold=True)), \
                mock.patch.object(runtime, "worktree_graph", counted("worktrees", runtime.worktree_graph)), \
                TestClient(app) as client, ThreadPoolExecutor(1) as pool:
            self.assertTrue(wait_until(lambda: derived["history"] == 1, 30), "the preparation derives the history")
            asked = pool.submit(client.get, "/api/design-history?branchId=main")
            time.sleep(0.3)
            self.assertFalse(asked.done(), "a first read waits for the preparation")
            self.assertIsNotNone(app.state.binding.await_index(30), "the index loaded")
            release.set()
            first = asked.result(30)
            for thread in threading.enumerate():
                if thread.name == "studio-first-reads":
                    thread.join(30)
            self.assertEqual(derived["history"], 2, "the index answered the history asked for, not the preparation")
            before = dict(derived)
            worktrees = client.get("/api/worktrees")
            self.assertEqual(derived["worktrees"], before["worktrees"] + 1, "and the worktrees read it again")
            self.assertEqual((first.status_code, worktrees.status_code), (200, 200))
            self.assertEqual(first.content, self.client.get("/api/design-history?branchId=main").content)
            self.assertEqual(worktrees.content, self.client.get("/api/worktrees").content)

            # The project moves: the prepared answer is not given again.
            self.assertEqual(self.accept(candidate_id, stage).status_code, 200)
            self.assertTrue(wait_until(lambda: client.get("/api/design-history?branchId=main").content
                                       == self.client.get("/api/design-history?branchId=main").content
                                       != first.content, 10))
            self.assertGreater(derived["history"], 1)

    def test_a_started_worker_with_a_loaded_index_lists_no_run_before_it_serves(self) -> None:
        """#449, #599: the preparation walks every run only when no index answers in time."""

        self.tree_with_lines()
        self.binding().close()  # the kept index: the next process reuses it
        listed: list[str] = []
        original = ProjectBinding.record_refs

        def counted(this, run_id, *, kind=None):
            if threading.current_thread().name == "studio-first-reads":
                listed.append(run_id)
            return original(this, run_id, kind=kind)

        for app, walks in ((self.app_with_index(), False),
                           (create_app(StudioSettings(project_dir=self.project_dir, cad_export="off")), True)):
            app.state.prepare_first_reads = True
            listed.clear()
            # A slow machine may take longer than a second to load the kept index: give it time.
            with self.subTest(index=not walks), mock.patch.object(ProjectBinding, "record_refs", counted), \
                    mock.patch.object(binding_module, "INDEX_CATCH_UP_S", 30.0), TestClient(app) as client:
                answer = client.get("/api/worktrees")
                for thread in threading.enumerate():
                    if thread.name == "studio-first-reads":
                        thread.join(30)
                self.assertEqual(answer.content, self.client.get("/api/worktrees").content)
                if walks:
                    self.assertEqual(set(listed), set(self.repository.run_ids()), "without an index, every run")
                else:
                    self.assertEqual(listed, [], "with a loaded index no run is listed")
                    self.assertIn(app.state.binding.await_index(0).index.loaded, ("reused", "reconciled"))
                app.state.binding.close()

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
