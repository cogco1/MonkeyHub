"""``POST /api/markup/resolve`` over real pages and a real model: what a circle on each lies on (#300 3-2).

A candidate of the reference project is exported to STEP, drawn as an
elevation and as camera passes, and marks are drawn where the drawing itself
put an object; the record answers for its component. A picture names no
object, and a stroke on the model is answered for as a click is.
"""

from __future__ import annotations

import base64
from io import BytesIO
import math
import time
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from monkeycad.backends.occt.kernel import occt_available
from monkeydiagram.rendering.svg import svg_object_marks
from monkeydiagram.sources import object_semantics
from project_runtime.application import artifacts, drawings
from project_runtime.application.artifacts import ModelSource
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase
from .test_pick import object_strings


def circle(mark_id: str, cx: float, cy: float, rx: float, ry: float | None = None) -> dict:
    """A circle round (cx, cy) in page fractions, as Board ink writes it."""

    ry = rx if ry is None else ry
    points = [[min(max(cx + rx * math.cos(i * math.pi / 32), 0.0), 1.0),
               min(max(cy + ry * math.sin(i * math.pi / 32), 0.0), 1.0)] for i in range(64)]
    return {"id": mark_id, "kind": "circle", "points": points, "color": "#ff0000", "lineWidth": 0.003}


def stroke(mark_id: str, *objects: str) -> dict:
    """A stroke on the model whose samples fell on ``objects``, one sample each, as the viewer casts them."""

    return {"id": mark_id, "kind": "freehand", "screen": [[10, 10 + i] for i in range(len(objects))],
            "camera": {"position": [10, 10, 10], "target": [0, 0, 0], "up": [0, 0, 1], "fov": 40},
            "hits": [{"objectName": name, "world": [0, 0, 0],
                      "userStrings": object_strings("portico", object_name=name, producer_op=name.removeprefix("obj-"))}
                     for name in objects]}


class MarkupRequestTests(CandidateTestCase):
    """The reference project as it is: strokes on its model, a picture, and requests that name the wrong thing."""

    def resolve(self, status: int = 200, **body: object) -> dict:
        response = self.client.post("/api/markup/resolve", json={"projectId": PROJECT_ID, **body})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_a_stroke_mostly_on_one_element_names_it_as_a_click_would(self) -> None:
        read = self.resolve(stateDigest=self.state_digest, sourceRunId=REFERENCE_RUN_ID, strokes=[
            stroke("base", "obj-portico-base-0", "obj-portico-base-0", "obj-portico-base-0", "obj-portico-cornice-0")])
        self.assertEqual((read["source"], read["stateDigest"]), ("model", self.state_digest))
        reading, = read["readings"]
        self.assertEqual((reading["markId"], reading["zone"], reading["ambiguous"]), ("base", "model", False))
        first = reading["hits"][0]
        self.assertEqual((first["componentId"], first["elementId"], first["target"], first["share"]),
                         ("portico", "portico-base", True, 0.75))
        self.assertEqual([hit["elementId"] for hit in reading["hits"][1:]], ["portico-cornice"])

    def test_strokes_on_another_state_are_refused_as_a_click_is(self) -> None:
        refused = self.resolve(409, stateDigest="0" * 64, strokes=[stroke("base", "obj-portico-base-0")])
        self.assertEqual(refused["code"], "STALE_BASE")

    def test_a_picture_names_no_object_so_every_mark_on_it_goes_back_to_the_architect(self) -> None:
        image = BytesIO()
        Image.new("RGB", (120, 80), (200, 200, 200)).save(image, format="PNG")
        document = artifacts.save_document(bound_project(self.app.state), REFERENCE_RUN_ID, "site photo.png",
                                           "image/png", base64.b64encode(image.getvalue()).decode("ascii"), None)
        read = self.resolve(page={"runId": REFERENCE_RUN_ID, "assetSha256": document.asset_sha256, "pageIndex": 0},
                            marks=[circle("here", 0.5, 0.5, 0.2),
                                   {"id": "note", "kind": "text", "points": [[0.1, 0.1]], "color": "#000000",
                                    "lineWidth": 0.003, "label": "这里加高", "fontSize": 0.03}])
        self.assertEqual((read["source"], read["stateDigest"]), ("page", None))
        self.assertEqual(read["readings"], [{"markId": "here", "kind": "circle", "zone": "area", "hits": [],
                                             "ambiguous": True, "reason": "nothing"}], "text is the sentence")
        self.resolve(422, page={"runId": REFERENCE_RUN_ID, "assetSha256": document.asset_sha256, "pageIndex": 1},
                     marks=[circle("here", 0.5, 0.5, 0.2)])

    def test_a_request_names_one_surface(self) -> None:
        page = {"runId": REFERENCE_RUN_ID, "assetSha256": "a" * 64, "pageIndex": 0}
        self.resolve(422, page=page, stateDigest=self.state_digest, marks=[])
        self.resolve(422)
        self.resolve(422, stateDigest=self.state_digest, marks=[circle("here", 0.5, 0.5, 0.2)])
        self.assertEqual(self.resolve(403, projectId="another", stateDigest=self.state_digest)["code"],
                         "PROJECT_MISMATCH")


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class MarkupDrawingTests(CandidateTestCase):
    """A candidate's exact STEP drawn as an elevation and as camera passes, and circles drawn on them."""

    def setUp(self) -> None:
        super().setUp()
        self.client.close()
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt"))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        self.model = next(row for row in candidate["artifacts"] if row["format"] == "3dm")["modelSource"]
        self.binding = bound_project(self.app.state)
        receipt = drawings.drawing_receipt(self.binding, model_source=ModelSource.from_dict(self.model))
        self.components = {object_id: row["component"] for object_id, row in object_semantics(receipt).items()
                           if "component" in row}

    def resolve(self, status: int = 200, **body: object) -> dict:
        response = self.client.post("/api/markup/resolve", json={"projectId": PROJECT_ID, **body})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_a_circle_round_an_object_of_an_elevation_names_its_component(self) -> None:
        drawn = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "modelSource": self.model, "view": "front", "drawingId": "front"})
        self.assertEqual(drawn.status_code, 201, drawn.text)
        document = drawn.json()
        svg = self.client.get(f"/api/drawings/{document['assetSha256']}/files/svg",
                              params={"runId": document["runId"], "revisionRef": document["revisionRef"]})
        self.assertEqual(svg.status_code, 200, svg.text)
        (width, height), marks = svg_object_marks(svg.content)
        # The object the drawing drew largest, and the box round all of its lines.
        boxes: dict[str, list[float]] = {}
        for mark in marks:
            if mark.component is None:
                continue
            box = boxes.setdefault(mark.object_id, [math.inf, math.inf, -math.inf, -math.inf])
            for x, y in mark.points_mm:
                box[:] = [min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y)]
        chosen, (x0, y0, x1, y1) = max(boxes.items(), key=lambda row: (row[1][2] - row[1][0]) * (row[1][3] - row[1][1]))
        page = {"runId": document["runId"], "assetSha256": document["assetSha256"], "pageIndex": 0,
                "drawingRevisionRef": document["revisionRef"]}
        mark = circle("round", (x0 + x1) / 2 / width, (y0 + y1) / 2 / height,
                      0.75 * (x1 - x0) / width + 0.01, 0.75 * (y1 - y0) / height + 0.01)
        read = self.resolve(page=page, marks=[mark, circle("corner", 0.01, 0.01, 0.005)])
        self.assertEqual((read["source"], read["stateDigest"]), ("drawing", self.model["stateDigest"]))
        round_, corner = read["readings"]
        targets = [hit for hit in round_["hits"] if hit["target"]]
        self.assertIn(self.components[chosen], {hit["componentId"] for hit in targets})
        self.assertIn(chosen, {object_id for hit in targets for object_id in hit["objectIds"]})
        self.assertEqual(round_["hits"][:len(targets)], targets, "targets first")
        self.assertEqual((corner["hits"], corner["reason"]), ([], "nothing"))
        self.assertEqual(self.resolve(page=page, marks=[mark, circle("corner", 0.01, 0.01, 0.005)]), read,
                         "the same marks read the same")
        unrevised = {key: value for key, value in page.items() if key != "drawingRevisionRef"}
        self.assertEqual(self.resolve(422, page=unrevised, marks=[mark])["code"], "DRAWING_REVISION_REQUIRED")

    def test_a_circle_on_an_object_of_a_camera_pass_names_its_component_on_each_of_its_images(self) -> None:
        receipt = drawings.drawing_receipt(self.binding, model_source=ModelSource.from_dict(self.model))
        boxes = [row["bbox"] for row in (receipt.get("readback") or {}).values()
                 if isinstance(row, dict) and isinstance(row.get("bbox"), dict)]
        self.assertTrue(boxes, "the receipt reads back each object's bounds")
        low = [min(box["min"][i] for box in boxes) for i in range(3)]
        high = [max(box["max"][i] for box in boxes) for i in range(3)]
        centre = [(low[i] + high[i]) / 2 for i in range(3)]
        reach = max(high[i] - low[i] for i in range(3))
        started = self.client.post("/api/render/views/passes", json={
            "projectId": PROJECT_ID, "modelSource": self.model, "idBy": "object", "size": [400, 300],
            "camera": {"position": [centre[0] + reach, centre[1] - 1.5 * reach, centre[2] + reach],
                       "target": centre, "up": [0, 0, 1], "fovDeg": 40}})
        self.assertEqual(started.status_code, 202, started.text)
        for _ in range(1200):
            record = self.client.get(started.json()["statusPath"]).json()
            if record["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(record["status"], "succeeded", record)
        id_document = record["documents"]["id"]
        _, png = artifacts.document_bytes(self.binding, id_document["runId"], id_document["assetSha256"])
        # A pixel deep inside the object the image shows largest, by its legend colour.
        with Image.open(BytesIO(png)) as image:
            pixels = image.convert("RGB")
            width, height = pixels.size
            colours = {tuple(int(row["color"][i:i + 2], 16) for i in (1, 3, 5)): row["key"] for row in record["legend"]
                       if row["key"] in self.components}
            counts = {colour: count for count, colour in pixels.getcolors(width * height) if colour in colours}
            colour = max(counts, key=counts.get)
            inside = next((x, y) for y in range(4, height - 4) for x in range(4, width - 4)
                          if all(pixels.getpixel((x + dx, y + dy)) == colour for dx in (-4, 0, 4) for dy in (-4, 0, 4)))
        chosen = colours[colour]
        mark = circle("on", inside[0] / width, inside[1] / height, 2.5 / width, 2.5 / height)
        for name in ("id", "line", "depth"):
            with self.subTest(image=name):
                document = record["documents"][name]
                read = self.resolve(page={"runId": document["runId"], "assetSha256": document["assetSha256"],
                                          "pageIndex": 0}, marks=[mark])
                self.assertEqual((read["source"], read["stateDigest"]), ("id-image", self.model["stateDigest"]))
                reading, = read["readings"]
                first = reading["hits"][0]
                self.assertEqual((first["componentId"], first["objectIds"], first["target"]),
                                 (self.components[chosen], [chosen], True))
