"""A request the API cannot read is answered with what is wrong with it, not with every shape it is not.

A route that takes one of several request shapes makes the validator report each shape's failures: a sketch that
repeats a point also "fails" the batch and the tracing shapes with a long run of "Extra inputs are not permitted".
Only the shape the body came closest to is reported.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, make_project


class RequestErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.client = TestClient(create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)))
        self.addCleanup(self.client.close)

    def test_a_sketch_that_repeats_a_point_is_told_so_and_nothing_else(self) -> None:
        body = {"stateDigest": "a" * 64, "componentId": "tri", "elementId": "tri-1", "profile": [[0, 0], [3, 0], [3, 0], [2, 2]],
                "height": 0, "baseLevel": "L1"}
        answer = self.client.post("/api/proposals/sketch", json=body)
        self.assertEqual((answer.status_code, answer.json()["code"]), (422, "REQUEST_INVALID"))
        detail = answer.json()["detail"]
        self.assertIn("a profile cannot", detail)
        self.assertNotIn("Extra inputs are not permitted", detail, "the other shapes the route takes are not the problem")
        self.assertNotIn("SketchBatchRequestDto", detail)
        self.assertNotIn("DocumentTracingRequestDto", detail)

    def test_a_request_that_fits_no_shape_still_says_something(self) -> None:
        answer = self.client.post("/api/proposals/sketch", json={"nonsense": True})
        self.assertEqual(answer.status_code, 422)
        self.assertTrue(answer.json()["detail"])


if __name__ == "__main__":
    unittest.main()
