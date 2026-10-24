"""Drawings solved in the drawing pool's worker processes and retained by the Runtime (#300 并发出图)."""

from __future__ import annotations

import base64
from pathlib import Path
import pickle
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

    def test_a_named_refusal_keeps_its_code_across_processes(self) -> None:
        refusal = pickle.loads(pickle.dumps(SectionPerspectiveError("SECTION_PLANE_MISSES_MODEL", "the plane misses")))
        self.assertEqual((refusal.code, str(refusal)), ("SECTION_PLANE_MISSES_MODEL", "the plane misses"))


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
        self.client = TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID,
                                                           cad_export="off")))
        self.addCleanup(self.client.close)
        drawing_pool.start(2)
        self.addCleanup(drawing_pool.stop)

    def test_a_native_model_is_read_only_by_workers(self) -> None:
        from monkeycad.backends.occt import native_models

        data = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        asset = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "fileName": "wall.3dm", "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(asset.status_code, 201, asset.text)
        source = {"runId": asset.json()["runId"], "assetSha256": asset.json()["sha256"]}
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


if __name__ == "__main__":
    unittest.main()
