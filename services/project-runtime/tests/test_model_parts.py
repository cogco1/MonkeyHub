"""A model from outside the project, read as building parts, and a mark on it naming the part (#300 item 22, C and A).

The reading is checked on plain index rows first: an object's own name, its
layer's, its shape, then the model for the rest. Then a real snapshot-like
3DM is attached, drawn as a camera pass, and a circle on its ID image names
the part under it.
"""

from __future__ import annotations

import base64
from io import BytesIO
import math
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from archflow.project.repository import FilesystemProjectRepository
from project_runtime.application import artifacts
from project_runtime.application.model_parts import read_model_parts
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

PROJECT_ID = "snapshot-parts"


def row(object_id, *, name="", layer="01", size=(1.0, 1.0, 1.0), at=(0.0, 0.0, 0.0), visible=True, kind="Brep"):
    return {"object_id": object_id, "name": name, "layer_path": layer, "type": kind, "visible": visible,
            "is_instance_definition_object": False,
            "bounds": {"min": list(at), "max": [at[axis] + size[axis] for axis in range(3)]}}


class ReadingTests(unittest.TestCase):
    def test_a_name_a_layer_and_a_shape_each_name_a_part_and_the_rest_goes_to_the_model(self):
        index = {"units": {"name": "Feet"}, "objects": [
            row("o1", name="02_FULLHEIGHT_STORAGE::living-1"),
            row("o2", layer="Latest / Glazing"),
            row("o3", size=(28.0, 48.0, 1.0)),            # 8.5 x 14.6 m and 0.3 m thick: a floor
            row("o4", size=(1.0, 26.5, 10.0)),            # 0.3 m thick, 3 m high: a wall
            *[row(f"t{n}", size=(1.0, 3.0, 1.0), at=(0.0, 3.0 * n, n)) for n in range(4)],
            row("hidden", size=(1.0, 1.0, 1.0), visible=False),
        ]}
        alone = read_model_parts(index)
        self.assertEqual({key: alone["objects"][key]["by"] for key in ("o1", "o2", "o3", "o4")},
                         {"o1": "name", "o2": "layer", "o3": "shape", "o4": "shape"})
        self.assertEqual([alone["objects"][key]["part"] for key in ("o1", "o2", "o3", "o4", "t0")],
                         ["cabinet", "glazing", "floor", "wall", "other"])
        asked = []

        def namer(groups):
            asked.extend(groups)
            return {group["id"]: {"id": group["id"], "part": "stair", "name": "楼梯踏步"}
                    for group in groups if group["count"] == 4}

        named = read_model_parts(index, namer)
        self.assertEqual((named["objects"]["t2"]["part"], named["objects"]["t2"]["name"], named["objects"]["t2"]["by"]),
                         ("stair", "楼梯踏步", "model"))
        group = next(group for group in asked if group["count"] == 4)
        self.assertEqual(group["sizeMetres"], [0.3, 0.91, 0.3], "the model is shown metres")
        self.assertNotIn("objectIds", group, "the model is shown sizes and places, not ids")
        self.assertEqual(named["objects"]["hidden"]["part"], "other", "the model's other answers stay other")
        self.assertEqual(named["recognized"], round(8 / 9, 4))
        self.assertEqual(named["visibleRecognized"], 1.0)
        self.assertEqual(named["layers"]["Latest / Glazing"], "glazing")

    def test_a_model_that_answers_nothing_useful_leaves_its_groups_other(self):
        index = {"units": {"name": "Meters"}, "objects": [row("a"), row("b", size=(2.0, 2.0, 2.0))]}
        reading = read_model_parts(index, lambda groups: {"g1": {"id": "g1", "part": "spaceship", "name": "x"}})
        self.assertEqual({value["part"] for value in reading["objects"].values()}, {"other"})


#: A slab, a wall and a cabinet named as Rhino would name them, in metres, on layers named 01 and 02.
SNAPSHOT = Path(__file__).parent / "fixtures" / "snapshot-parts.3dm"


class SnapshotMarkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="snapshot parts ")
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name) / PROJECT_ID
        FilesystemProjectRepository.initialize(project, project_id=PROJECT_ID,
                                               initial_state={"project_id": PROJECT_ID, "version": 0})
        self.client = TestClient(create_app(StudioSettings(project_dir=project, cad_export="off")))
        self.addCleanup(self.client.close)
        data = SNAPSHOT.read_bytes()
        record = artifacts.register_model_asset(bound_project(self.client.app.state), None, None, "snapshot.3dm",
                                                base64.b64encode(data).decode("ascii"))
        self.asset = {"runId": f"studio-model-{record.sha256}", "assetSha256": record.sha256}

    def test_the_parts_route_reads_a_snapshot_by_name_and_shape(self):
        reading = self.client.get(f"/api/model-assets/{self.asset['assetSha256']}/parts")
        self.assertEqual(reading.status_code, 200, reading.text)
        parts = {value["part"] for value in reading.json()["objects"].values()}
        self.assertEqual(parts, {"floor", "wall", "cabinet"})
        self.assertEqual(reading.json()["recognized"], 1.0)

    def test_a_circle_on_a_snapshots_camera_pass_names_the_part_under_it(self):
        started = self.client.post("/api/render/views/passes", json={
            "projectId": PROJECT_ID, "sourceAsset": self.asset, "idBy": "object", "size": [400, 300],
            "camera": {"position": [16, -12, 12], "target": [5, 5, 1], "up": [0, 0, 1], "fovDeg": 45}})
        self.assertEqual(started.status_code, 202, started.text)
        for _ in range(600):
            record = self.client.get(started.json()["statusPath"]).json()
            if record["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(record["status"], "succeeded", record)
        document = record["documents"]["id"]
        _, png = artifacts.document_bytes(bound_project(self.client.app.state), document["runId"], document["assetSha256"])
        reading = self.client.get(f"/api/model-assets/{self.asset['assetSha256']}/parts").json()
        floor = next(key for key, value in reading["objects"].items() if value["part"] == "floor")
        colour = next(tuple(int(row["color"][i:i + 2], 16) for i in (1, 3, 5)) for row in record["legend"]
                      if row["key"] == floor)
        with Image.open(BytesIO(png)) as image:
            pixels = image.convert("RGB")
            width, height = pixels.size
            x, y = next((x, y) for y in range(4, height - 4) for x in range(4, width - 4)
                        if all(pixels.getpixel((x + dx, y + dy)) == colour for dx in (-4, 0, 4) for dy in (-4, 0, 4)))
        points = [[(x + 2.5 * math.cos(i * math.pi / 16)) / width, (y + 2.5 * math.sin(i * math.pi / 16)) / height]
                  for i in range(32)]
        read = self.client.post("/api/markup/resolve", json={"projectId": PROJECT_ID, "page": {
            "runId": document["runId"], "assetSha256": document["assetSha256"], "pageIndex": 0},
            "marks": [{"id": "on", "kind": "circle", "points": points, "color": "#ff0000", "lineWidth": 0.003}]})
        self.assertEqual(read.status_code, 200, read.text)
        reading, = read.json()["readings"]
        self.assertEqual((reading["hits"][0]["componentId"], reading["hits"][0]["objectIds"]), ("floor", [floor]))
        self.assertIsNone(read.json()["stateDigest"], "a snapshot has no design record behind it")


if __name__ == "__main__":
    unittest.main()
