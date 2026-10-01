"""The material view and readback show the colours an exact model asset's own material table gives its objects (#580).

``fixtures/model-materials.3dm`` was written with rhino3dm as an export labels
its objects: four closed 1 m deep box meshes side by side along X, in metres.
``insulation`` (x 0-4, 3 m high) and ``frame`` (x 5-7, 3 m) wear the declared
materials "hemp insulation" #C8B98F and "timber" #A0522D (from object,
``archflow:material`` on the object, ``archflow:material_id`` on the
material); ``plinth`` (x 8-11, 1 m) wears none and says
``archflow:material_status`` = ``undeclared``; ``base`` (x 12-14, 2 m) wears
its layer's render material "Imported paint" #3C5A78, which nothing declares.
Their GUIDs are 00000000-0000-0000-0000-00000000000{1,2,3,4}.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from monkeycad.backends.occt.kernel import occt_available
from project_runtime.application import drawings
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest

FIXTURE = Path(__file__).parent / "fixtures/model-materials.3dm"
INSULATION, TIMBER, PAINT = (200, 185, 143), (160, 82, 45), (60, 90, 120)


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class MaterialViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="material-view-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        settings = StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.client = TestClient(create_app(settings))
        self.addCleanup(self.client.close)
        self.data = FIXTURE.read_bytes()
        response = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID,
            "stateDigest": runner_state_digest(self.repository, REFERENCE_RUN_ID),
            "fileName": "materials.3dm", "contentBase64": base64.b64encode(self.data).decode()})
        self.assertEqual(response.status_code, 201, response.text)
        self.source = response.json()["modelSource"]
        self.assertEqual(self.source["assetSha256"], hashlib.sha256(self.data).hexdigest())
        # Each test draws from scratch: a view another test drew stays out of its answers.
        patcher = patch.object(drawings, "_MODEL_VIEWS", OrderedDict())
        patcher.start()
        self.addCleanup(patcher.stop)

    def view(self, view="front", **params):
        response = self.client.get("/api/drawings/model-view",
                                   params={**self.source, "view": view, "display": "material", **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def image(answer) -> Image.Image:
        with Image.open(BytesIO(base64.b64decode(answer["data"], validate=True))) as image:
            image.load()
            return image.copy()

    def test_each_object_is_drawn_in_the_colour_its_material_table_gives_it_and_the_unknown_one_is_marked(self):
        project_root = self.repository.layout.root
        before = {path: path.read_bytes() for path in project_root.rglob("*") if path.is_file()}
        answer = self.view()
        self.assertEqual((answer["source"], answer["view"], answer["display"]), (self.source, "front", "material"))
        self.assertEqual((answer["mimeType"], answer["representation"]), ("image/png", "orthographic-material-projection"))
        image = self.image(answer)
        self.assertEqual((image.mode, image.size), ("RGB", (answer["width"], answer["height"])))
        self.assertEqual(max(image.size), 1024)
        # The front view's frame: every object's bounds and a 5% margin of the longer side, X right and Z up.
        margin = 14 * drawings.VIEW_MARGIN
        u0, v1, unit = -margin, 3 + margin, (14 + 2 * margin) / 1024

        def at(u, v):
            return image.getpixel((int((u - u0) / unit), int((v1 - v) / unit)))

        # A pixel inside a face is the material table's diffuse colour exactly.
        self.assertEqual(at(2.0, 1.5), INSULATION)
        self.assertEqual(at(6.0, 2.5), TIMBER)
        self.assertEqual(at(13.0, 1.0), PAINT, "a layer's render material is the material its objects wear")
        self.assertEqual(at(7.5, 2.0), (255, 255, 255))
        # The object that wears no material is plainly unknown: neutral grey under hatch lines, no material colour.
        x0, y0 = int((8.5 - u0) / unit), int((v1 - 0.8) / unit)
        plinth = {image.getpixel((x, y)) for x in range(x0, x0 + 40) for y in range(y0, y0 + 20)}
        self.assertTrue({(200, 200, 200), (120, 120, 120)} <= plinth, plinth)
        self.assertTrue(all(len(set(colour)) == 1 for colour in plinth), plinth)
        self.assertEqual(answer["legend"], {
            "materials": [
                {"name": "Imported paint", "color": "#3C5A78", "source": "file", "objects": 1, "visible": 1},
                {"name": "hemp insulation", "color": "#C8B98F", "source": "declared", "objects": 1, "visible": 1},
                {"name": "timber", "color": "#A0522D", "source": "declared", "objects": 1, "visible": 1},
            ],
            "undeclared": 1, "undeclaredVisible": 1,
        })
        after = {path: path.read_bytes() for path in project_root.rglob("*") if path.is_file()}
        self.assertEqual(after, before, "a material view writes no project file or record")

    def test_every_line_view_direction_has_a_material_view_and_the_legend_says_what_shows(self):
        # From -X the tall insulation hides the rest; from +X the base and the frame above it hide the others.
        shown = {"back": {INSULATION, TIMBER, PAINT}, "top": {INSULATION, TIMBER, PAINT},
                 "axon": {INSULATION, TIMBER, PAINT}, "left": {INSULATION}, "right": {TIMBER, PAINT}}
        for view, expected in shown.items():
            with self.subTest(view=view):
                answer = self.view(view)
                colours = {colour for _, colour in self.image(answer).getcolors(maxcolors=1 << 20)}
                self.assertEqual(colours & {INSULATION, TIMBER, PAINT}, expected)
                legend = answer["legend"]
                self.assertEqual({tuple(bytes.fromhex(row["color"][1:])) for row in legend["materials"] if row["visible"]},
                                 expected)
                self.assertEqual([row["objects"] for row in legend["materials"]], [1, 1, 1],
                                 "a hidden object still counts as drawn")
                self.assertEqual(legend["undeclared"], 1)
        line = self.client.get("/api/drawings/model-view", params={**self.source, "view": "front"})
        self.assertEqual(line.status_code, 200, line.text)
        self.assertEqual((line.json()["display"], line.json()["representation"], line.json()["legend"]),
                         ("line", "orthographic-line-projection", None))

    def test_the_same_exact_asset_draws_the_same_bytes(self):
        first = self.view("axon")
        drawings._MODEL_VIEWS.clear()
        with patch("project_runtime.application.drawings.mesh_line_view", wraps=drawings.mesh_line_view) as drawn:
            second = self.view("axon")
            again = self.view("axon")
        self.assertEqual(drawn.call_count, 1, "a repeated view of the same exact source is not drawn again")
        self.assertEqual(second["data"], first["data"], "a fresh drawing of the same exact asset is byte-identical")
        self.assertEqual((again, second["legend"]), (second, first["legend"]))

    def test_a_source_that_is_not_exactly_retained_is_refused_before_anything_is_drawn(self):
        with patch("project_runtime.application.drawings.mesh_line_view",
                   side_effect=AssertionError("a refused source is never drawn")):
            for params, code in (({"stateDigest": "0" * 64}, "MODEL_SOURCE_MISMATCH"),
                                 ({"assetSha256": "0" * 64}, "MODEL_SOURCE_UNREGISTERED")):
                response = self.client.get("/api/drawings/model-view",
                                           params={**self.source, **params, "view": "front", "display": "material"})
                self.assertEqual((response.status_code, response.json()["code"]), (409, code), response.text)
        self.assertEqual(self.client.get("/api/drawings/model-view", params={
            **self.source, "view": "front", "display": "rendered"}).status_code, 422)

    def test_the_objects_readback_names_each_objects_material_colour_and_source(self):
        response = self.client.get(f"/api/model-assets/{self.source['assetSha256']}/index",
                                   params={"runId": self.source["runId"], "stateDigest": self.source["stateDigest"]})
        self.assertEqual(response.status_code, 200, response.text)
        materials = {row["name"]: row["material"] for row in response.json()["objects"]}
        self.assertEqual(materials, {
            "insulation": {"name": "hemp insulation", "color": "#C8B98F", "source": "declared"},
            "frame": {"name": "timber", "color": "#A0522D", "source": "declared"},
            "plinth": {"name": None, "color": None, "source": "undeclared"},
            "base": {"name": "Imported paint", "color": "#3C5A78", "source": "file"},
        })


if __name__ == "__main__":
    unittest.main()
