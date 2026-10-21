"""Camera passes: an ID, a line and a depth image of one exact model through a perspective camera (#19)."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from archflow.project.record_kinds import STUDIO_CAMERA_PASS, require_registered
from archflow.project.repository import FilesystemProjectRepository
from project_runtime.application import artifacts, drawings
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

PROJECT_ID = "camera-passes"
FIXTURE = Path(__file__).parent / "fixtures" / "model-source-a.3dm"


class CameraPassRouteTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="camera passes ")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name) / PROJECT_ID
        FilesystemProjectRepository.initialize(project, project_id=PROJECT_ID,
                                               initial_state={"project_id": PROJECT_ID, "version": 0})
        self.client = TestClient(create_app(StudioSettings(project_dir=project, cad_export="off")))
        self.addCleanup(self.client.close)
        self.binding = bound_project(self.client.app.state)
        record = artifacts.register_model_asset(self.binding, None, None, "site.3dm",
                                                base64.b64encode(FIXTURE.read_bytes()).decode("ascii"))
        self.asset = {"runId": f"studio-model-{record.sha256}", "assetSha256": record.sha256}
        _, _, receipt, _ = drawings.load_drawing_source(self.binding, source_asset=dict(self.asset))
        boxes = [row["bbox"] for row in receipt["readback"].values() if isinstance(row, dict) and "bbox" in row]
        low = [min(box["min"][i] for box in boxes) for i in range(3)]
        high = [max(box["max"][i] for box in boxes) for i in range(3)]
        centre = [(low[i] + high[i]) / 2 for i in range(3)]
        reach = max(high[i] - low[i] for i in range(3))
        # A three-quarter view from the south-east, above the model.
        self.camera = {"position": [centre[0] + reach, centre[1] - 1.5 * reach, centre[2] + reach],
                       "target": centre, "up": [0, 0, 1], "fovDeg": 40}

    def start(self, status=202, **body):
        response = self.client.post("/api/render/views/passes", json={"projectId": PROJECT_ID, **body})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def finish(self, started):
        for _ in range(1200):
            record = self.client.get(started["statusPath"]).json()
            if record["status"] in ("succeeded", "failed"):
                return record
            time.sleep(0.05)
        self.fail("the camera pass did not finish")

    def test_the_record_is_a_registered_kind(self):
        self.assertEqual(require_registered(STUDIO_CAMERA_PASS).schema, "StudioCameraPass@1")

    def test_three_aligned_passes_are_registered_beside_the_model(self):
        started = self.start(camera=self.camera, size=[1600, 900], longEdge=800, idBy="layer", sourceAsset=self.asset)
        self.assertEqual((started["status"], started["size"]), ("queued", [800, 450]))
        record = self.finish(started)
        self.assertEqual(record["status"], "succeeded", record)
        self.assertGreater(record["seenObjects"], 0)
        self.assertTrue(record["legend"], "the ID image names what each colour stands for")
        self.assertEqual(record["depth"]["unit"], record["unit"])
        self.assertLess(record["depth"]["near"], record["depth"]["far"])
        listed = {row["assetSha256"]: row for row in self.client.get(
            "/api/documents", params={"runId": self.asset["runId"]}).json()["documents"]}
        for name, mode in (("id", "RGB"), ("line", "L"), ("depth", "I;16")):
            with self.subTest(name=name):
                document = record["documents"][name]
                row = listed[document["assetSha256"]]
                recipe = row["viewRecipe"]
                self.assertEqual((recipe["kind"], recipe["pass"], recipe["size"], recipe["idBy"]),
                                 ("camera-pass", name, [800, 450], "layer"))
                self.assertEqual(recipe["sourceAsset"], self.asset)
                data = self.client.get(f"/api/documents/{document['assetSha256']}/bytes",
                                       params={"runId": document["runId"]}).content
                with Image.open(BytesIO(data)) as image:
                    self.assertEqual(image.size, (800, 450))
                    self.assertIn(image.mode, (mode, "I") if name == "depth" else (mode,))
        self.assertIn("legend", listed[record["documents"]["id"]["assetSha256"]]["viewRecipe"])
        self.assertIn("depth", listed[record["documents"]["depth"]["assetSha256"]]["viewRecipe"])

    def test_a_pass_cut_at_a_live_section_keeps_the_cut_in_its_record_and_images(self):
        # G5 step 4: the plane x = 6, kept toward +X, leaves only the fixture's second box.
        clip = {"origin": [6, 0, 0], "normal": [1, 0, 0]}
        whole = self.finish(self.start(camera=self.camera, size=[400, 300], longEdge=400, idBy="object", sourceAsset=self.asset))
        cut = self.finish(self.start(camera=self.camera, size=[400, 300], longEdge=400, idBy="object",
                                     sourceAsset=self.asset, clip=clip))
        self.assertEqual((whole["status"], cut["status"]), ("succeeded", "succeeded"), cut)
        self.assertEqual(cut["clip"], {"origin": [6.0, 0.0, 0.0], "normal": [1.0, 0.0, 0.0]})
        self.assertLess(cut["seenObjects"], whole["seenObjects"])
        listed = {row["assetSha256"]: row for row in self.client.get(
            "/api/documents", params={"runId": self.asset["runId"]}).json()["documents"]}
        self.assertEqual(listed[cut["documents"]["depth"]["assetSha256"]]["viewRecipe"]["clip"], cut["clip"])
        self.assertNotIn("clip", listed[whole["documents"]["depth"]["assetSha256"]]["viewRecipe"])
        self.start(status=422, camera=self.camera, size=[400, 300], sourceAsset=self.asset,
                   clip={"origin": [0, 0, 0], "normal": [0, 0, 0]})

    def test_a_request_names_one_view_and_one_model(self):
        both = self.start(status=422, camera=self.camera, size=[100, 100], sourceAsset=self.asset,
                          view={"runId": self.asset["runId"], "assetSha256": self.asset["assetSha256"]})
        self.assertEqual(both["code"], "CAMERA_PASS_INVALID")
        self.assertEqual(self.start(status=422, camera=self.camera, sourceAsset=self.asset)["code"], "CAMERA_PASS_INVALID")
        self.assertEqual(self.start(status=422, camera=self.camera, size=[100, 100], sourceAsset=self.asset,
                                    longEdge=4097)["code"], "REQUEST_INVALID")
        looking_nowhere = dict(self.camera, target=self.camera["position"])
        self.assertEqual(self.start(status=422, camera=looking_nowhere, size=[100, 100],
                                    sourceAsset=self.asset)["code"], "CAMERA_PASS_INVALID")


if __name__ == "__main__":
    unittest.main()
