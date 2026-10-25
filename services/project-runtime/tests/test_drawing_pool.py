"""Drawings solved in the drawing pool's worker processes and retained by the Runtime (#300 并发出图)."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import pickle
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.refs import record_ref_from_uri
from monkeycad.backends.occt.kernel import occt_available
from monkeydiagram import drawing_runs
from monkeydiagram.drawing_runs import read_model_axis_elevation
from monkeydiagram.projection.views import SectionPerspectiveError
from project_runtime.application import drawing_pool
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project
from .test_candidate import CandidateTestCase


class DrawingPoolSizeTests(unittest.TestCase):
    def test_the_size_follows_the_setting_then_the_processors(self) -> None:
        with patch.dict("os.environ", {drawing_pool.WORKERS_ENV: "3"}):
            self.assertEqual(drawing_pool.pool_size(), 3)
        with patch.dict("os.environ", {drawing_pool.WORKERS_ENV: "0"}):
            self.assertIsNone(drawing_pool.start(), "0 solves drawings in the Runtime's own process")
        with patch.dict("os.environ", {drawing_pool.WORKERS_ENV: ""}), patch("os.cpu_count", return_value=16):
            self.assertEqual(drawing_pool.pool_size(), 4)
        with patch.dict("os.environ", {drawing_pool.WORKERS_ENV: ""}), patch("os.cpu_count", return_value=1):
            self.assertEqual(drawing_pool.pool_size(), 1)

    @unittest.skipUnless(os.name == "posix", "a signal ends a POSIX Runtime after it gave its pool back")
    def test_the_workers_end_with_a_runtime_that_a_signal_stopped(self) -> None:
        # uvicorn gives the pool back on SIGTERM, then raises the signal again: the Runtime ends at once. Its workers
        # must end with it, or they keep its output open and the Hub reading it never sees the end.
        script = "\n".join((
            "import os, signal",
            "from project_runtime.application import drawing_pool",
            "pool = drawing_pool.start(2, warm=True)",
            "for future in [pool._running().submit(drawing_pool._ready) for _ in range(2)]: future.result(timeout=120)",
            "print('ready', flush=True)",
            "drawing_pool.stop()",
            "os.kill(os.getpid(), signal.SIGTERM)"))
        runtime = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True, text=True,
                                   env=dict(os.environ, PYTHONPATH=os.pathsep.join(path for path in sys.path if path)))
        try:
            output, _ = runtime.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(runtime.pid, signal.SIGKILL)
            runtime.communicate()
            self.fail("the drawing workers outlived the Runtime and kept its output open")
        self.assertIn("ready", output)
        self.assertEqual(runtime.returncode, -signal.SIGTERM)

    def test_a_named_refusal_keeps_its_code_across_processes(self) -> None:
        refusal = pickle.loads(pickle.dumps(SectionPerspectiveError("SECTION_PLANE_MISSES_MODEL", "the plane misses")))
        self.assertEqual((refusal.code, str(refusal)), ("SECTION_PLANE_MISSES_MODEL", "the plane misses"))


class DrawingWorkerReuseTests(unittest.TestCase):
    """S15: what a drawing spends besides solving its view."""

    def test_a_worker_draws_the_model_it_read_last_without_reading_it_again(self) -> None:
        reads = []

        def read(repository, source):
            reads.append(source)
            return f"verified {source}"

        drawing_runs._LAST_READ.clear()
        self.addCleanup(drawing_runs._LAST_READ.clear)
        same = {"model-a": True}
        with patch.object(drawing_runs, "_opened", return_value=object()),                 patch.object(drawing_runs, "read_elevation_source", side_effect=read),                 patch.object(drawing_runs, "_same_bytes", side_effect=lambda root, source: same.get(source, True)):
            self.assertEqual(drawing_runs._read_source("/p", "model-a"), "verified model-a")
            self.assertEqual(drawing_runs._read_source("/p", "model-a"), "verified model-a")
            self.assertEqual(drawing_runs._read_source("/p", "model-b"), "verified model-b")
            self.assertEqual(drawing_runs._read_source("/p", "model-a"), "verified model-a")
            self.assertEqual(drawing_runs._read_source("/q", "model-a"), "verified model-a")
            same["model-a"] = False  # its bytes changed on disk: read, and verified, again
            self.assertEqual(drawing_runs._read_source("/q", "model-a"), "verified model-a")
        self.assertEqual(reads, ["model-a", "model-b", "model-a", "model-a", "model-a"],
                         "the same model in the same project is read once; another one, or changed bytes, read again")
        self.assertEqual(len(drawing_runs._LAST_READ), 1, "a worker keeps only the model it drew last")

    def test_the_other_workers_read_a_source_while_the_first_verifies_it(self) -> None:
        from concurrent.futures import Future

        submitted = []

        class Executor:
            def submit(self, function, *arguments):
                submitted.append((function, arguments))
                future = Future()
                future.set_result(f"verified {arguments[1]}")
                return future

        pool = drawing_pool.DrawingPool(3)
        with patch.object(pool, "_running", return_value=Executor()):
            self.assertEqual(pool.source("/p", "model-a"), "verified model-a")
            self.assertEqual(pool.source("/p", "model-a"), "verified model-a")
        self.assertEqual(submitted, [(drawing_runs.verify_draw_source, ("/p", "model-a")),
                                     (drawing_pool._read_ahead, ("/p", "model-a")),
                                     (drawing_pool._read_ahead, ("/p", "model-a"))],
                         "one verification answers; the two other workers read the model meanwhile, and only once")
        with patch.object(drawing_pool, "verify_draw_source", side_effect=OSError("gone")):
            self.assertIsNone(drawing_pool._read_ahead("/p", "model-a"), "a read ahead that fails is the asking drawing's to report")

    def test_the_source_is_checked_again_only_when_the_project_may_have_changed(self) -> None:
        from contextlib import ExitStack, nullcontext

        from project_runtime.application import drawings
        from project_runtime.binding import ReadToken

        class Repository:
            @staticmethod
            def working_draft_guard():
                return nullcontext()

        class Binding:
            repository = Repository()
            token: ReadToken | None = None

            def read_token(self, *, wait=True):
                return self.token

        binding, checks = Binding(), []
        first = ReadToken("e", 3, "f", False)
        with patch.object(drawings, "_complete_source", side_effect=lambda *args: checks.append(args)):
            # Only settled since: the same files. A write of this process, a changed layout, or no token: check again.
            for now, checked in ((ReadToken("e", 3, "f", True), 0), (ReadToken("e", 4, "f", False), 1),
                                 (ReadToken("e", 3, "g", False), 1), (None, 1)):
                checks.clear()
                binding.token = now
                with ExitStack() as held:
                    token = drawings._HELD_GUARD.set((binding, held, first))
                    try:
                        with drawings._retaining(binding, "model", None)():
                            pass
                    finally:
                        drawings._HELD_GUARD.reset(token)
                self.assertEqual(len(checks), checked, now)


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class DrawingPoolTests(CandidateTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client.close()
        self.cache = self.root / "cache"
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt", cache_dir=self.cache)
        self.app = create_app(self.settings)
        self.addCleanup(self.app.state.stop_index_events)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        binding = bound_project(self.app.state)
        self.addCleanup(binding.close)
        self.assertIsNotNone(binding.await_index(30), "the index loads")
        self.addCleanup(lambda: self.app.state.projections and self.app.state.projections.shutdown())
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        self.model = next(row for row in candidate["artifacts"] if row["format"] == "3dm")["modelSource"]
        initialized = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": self.model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        self.stage = initialized.json()
        self.head = self.repository.read_head()
        self.pool = drawing_pool.start(2)
        self.addCleanup(drawing_pool.stop)

    def elevation(self, drawing_id: str, **body: object):
        response = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "modelSource": self.model, "view": "front", "drawingId": drawing_id, **body})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def retained(self, document: dict):
        return read_model_axis_elevation(self.repository, record_ref_from_uri(document["revisionRef"], PROJECT_ID))

    def test_a_worker_solves_the_view_and_the_runtime_retains_it(self) -> None:
        with patch.object(drawing_runs, "project_model_axis_elevation",
                          wraps=drawing_runs.project_model_axis_elevation) as here, \
                patch.object(drawing_pool.DrawingPool, "__call__", autospec=True,
                             side_effect=drawing_pool.DrawingPool.__call__) as solved:
            first = self.elevation("front-a")
            # The same content and view under another id: the cache answers for the source a worker verified.
            second = self.elevation("front-b")
            right = self.elevation("right", view="right")
        self.assertEqual(here.call_count, 0, "the Runtime's own process solves nothing")
        self.assertEqual(solved.call_count, 2, "front once, right once")
        self.assertEqual(first["assetSha256"], second["assetSha256"])
        self.assertNotEqual(first["assetSha256"], right["assetSha256"])
        for document in (first, second, right):
            retained = self.retained(document)
            self.assertEqual(retained.receipt["source"]["run_id"], self.model["runId"])
            self.assertGreater(retained.receipt["projection"]["visible_polylines"], 0)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_a_section_perspective_is_solved_by_a_worker(self) -> None:
        body = {"projectId": PROJECT_ID, "modelSource": self.model,
                "section": {"line": [[-5, 0.5], [5, 0.5]], "keep": "right"}, "scaleDenominator": 100}
        with patch.object(drawing_runs, "project_section_perspective",
                          wraps=drawing_runs.project_section_perspective) as here:
            drawn = self.client.post("/api/drawings/section-perspectives", json=body)
        self.assertEqual(drawn.status_code, 201, drawn.text)
        self.assertEqual(here.call_count, 0, "the Runtime's own process solves nothing")
        self.assertEqual(self.retained(drawn.json()).receipt["view"]["kind"], "section-perspective")

    def test_a_cut_plan_is_solved_by_a_worker_and_a_repeat_reads_the_registered_one(self) -> None:
        body = {"projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"], "drawingId": "ground-plan",
                "cutHeight": 1.0, "bottom": 0, "scaleDenominator": 50, "dimensions": []}
        with patch.object(drawing_runs, "section_occt_lines", wraps=drawing_runs.section_occt_lines) as here:
            first = self.client.post("/api/drawings/plans", json=body)
            self.assertEqual(first.status_code, 201, first.text)
            again = self.client.post("/api/drawings/plans", json=body)
            self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(here.call_count, 0, "the Runtime's own process solves nothing")
        self.assertEqual(again.json()["revisionRef"], first.json()["revisionRef"])
        listed = self.client.get("/api/documents", params={"projectId": PROJECT_ID}).json()
        rows = listed["documents"] if isinstance(listed, dict) else listed
        self.assertEqual(sum(1 for row in rows if row.get("drawingId") == "ground-plan"), 1)


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class NativeDrawingPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID,
                                             cad_export="off", cache_dir=self.root / "cache"))
        self.addCleanup(self.app.state.stop_index_events)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        binding = bound_project(self.app.state)
        self.addCleanup(binding.close)
        self.assertIsNotNone(binding.await_index(30), "the index loads")
        self.addCleanup(lambda: self.app.state.projections and self.app.state.projections.shutdown())
        drawing_pool.start(2)
        self.addCleanup(drawing_pool.stop)

    def upload(self) -> dict:
        data = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        asset = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "fileName": "wall.3dm", "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(asset.status_code, 201, asset.text)
        return {"runId": asset.json()["runId"], "assetSha256": asset.json()["sha256"]}

    def elevation(self, source, view="front"):
        response = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "sourceAsset": source, "view": view, "drawingId": f"wall-{view}"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_a_native_model_is_read_only_by_workers(self) -> None:
        from monkeycad.backends.occt import native_models

        source = self.upload()
        with patch.object(native_models, "read_three_dm", wraps=native_models.read_three_dm) as here:
            front = self.client.post("/api/drawings/elevations", json={
                "projectId": PROJECT_ID, "sourceAsset": source, "view": "front", "drawingId": "wall-front"})
            plan = self.client.post("/api/drawings/plans", json={
                "projectId": PROJECT_ID, "sourceAsset": source, "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50})
        self.assertEqual(front.status_code, 201, front.text)
        self.assertEqual(plan.status_code, 201, plan.text)
        self.assertEqual(here.call_count, 0, "the Runtime's own process never reads the 3DM's shapes")
        receipt = self.repository.load_json(record_ref_from_uri(front.json()["revisionRef"], PROJECT_ID))
        self.assertTrue(receipt["source"]["geometry_quality"], "each object's geometry quality comes back from the worker")

    def test_a_restarted_runtime_reads_the_model_s_source_from_the_cache_and_answers_a_repeat_at_once(self) -> None:
        source = self.upload()
        first = self.elevation(source)
        # A restart: the pool's memory of what its workers verified is gone; the projection cache is not.
        drawing_pool.stop()
        drawing_pool.start(2)
        solved = []
        original = drawing_pool.DrawingPool._solve

        def counted(pool, function, *arguments, **options):
            solved.append(function.__name__)
            return original(pool, function, *arguments, **options)

        with patch.object(drawing_pool.DrawingPool, "_solve", counted):
            again = self.elevation(source)
            right = self.elevation(source, "right")
        self.assertEqual(again["revisionRef"], first["revisionRef"], "the registered drawing answers the repeat")
        self.assertEqual(solved, ["run_draw_task"], "only the new view is solved; nobody reads the model to verify it")
        self.assertNotEqual(right["assetSha256"], first["assetSha256"])

    def test_a_worker_draws_again_from_the_model_it_read_but_not_from_one_changed_on_disk(self) -> None:
        source = self.upload()
        drawing_pool.stop()
        drawing_pool.start(1)  # one worker: the second and third views go to the worker that read the model
        self.elevation(source)
        self.elevation(source, "right")
        registration = next(row for row in self.client.get("/api/artifacts").json()["artifacts"]
                            if row["runId"] == source["runId"] and row["sha256"] == source["assetSha256"])
        self.repository.layout.resolve_relative(registration["relativePath"]).write_bytes(b"tampered")
        refused = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "sourceAsset": source, "view": "back", "drawingId": "wall-back"})
        self.assertGreaterEqual(refused.status_code, 400, refused.text)
        self.assertNotIn(refused.status_code, (500,), refused.text)


if __name__ == "__main__":
    unittest.main()
