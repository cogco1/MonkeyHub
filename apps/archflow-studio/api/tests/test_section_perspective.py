"""POST /api/drawings/section-perspectives on a real room built through the Studio.

The exact geometry, the projector convention and the retention boundary are tested
in tests/test_drawing_section_perspective.py; here the route registers, reads back,
reuses and refuses by name.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from archflow.adapters.occt_backend import occt_available
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.drawings import generate_section_perspective
from monkeydiagram.drawing_elevation import SECTION_PERSPECTIVE_KIND, read_model_axis_elevation
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase
from .test_drawing_plans import room_edit


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class SectionPerspectiveApiTests(CandidateTestCase):
    """POST /api/drawings/section-perspectives on a real room built through the Studio."""

    def setUp(self):
        super().setUp()
        self.client.close()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        # The cut-plan room (four walls, a door in the passage wall) with a floor and a roof slab.
        edit = room_edit()
        for name, base, height in (("room-floor", -0.3, 0.3), ("room-roof", 3.0, 0.3)):
            edit["entities"].append({
                "entity_id": name, "schema": "Element@1", "parent_id": "portico", "basis_refs": ["studio:intent"],
                "fields": {"component_id": "portico", "producer": "prism", "references": {"base": {"elevation": base}},
                           "params": {"profile": [[0, 0], [4, 0], [4, 4], [0, 4]], "height": height}},
            })
        request = self.client.post("/api/proposals", json={"projectId": PROJECT_ID, "stateDigest": self.state_digest,
                                                           "sourceRunId": REFERENCE_RUN_ID, "semanticEdit": edit})
        self.assertEqual(request.status_code, 201, request.text)
        result = self.start(request.json()["proposalId"])
        self.assertEqual(self.finished(result["jobId"])["status"], "succeeded")
        candidate = self.client.get(f"/api/candidates/{result['candidateId']}").json()
        self.model = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
        self.step = next(row for row in candidate["artifacts"] if row["format"] == "step")
        accepted = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": self.model})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.stage = accepted.json()
        self.head = self.repository.read_head()

    def post(self, **changes):
        # Cut the 4 m room at y = 2.5, keep its front half with the door and look at it from the back.
        # Python's encoder writes NaN as the literal a hand-written body could carry; standard JSON has none.
        return self.client.post("/api/drawings/section-perspectives", headers={"content-type": "application/json"},
                                content=json.dumps({
            "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"],
            "section": {"line": [[0, 2.5], [4, 2.5]], "keep": "right"}, "scaleDenominator": 50, **changes,
        }))

    def test_a_section_perspective_is_registered_read_back_and_reused(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        document = response.json()
        self.assertEqual((document["drawingId"], document["mimeType"]), ("section-perspective", "image/png"))
        self.assertEqual((document["modelSource"], document["sourceStageRef"]), (self.model, self.stage["stageRef"]))
        recipe = document["viewRecipe"]
        self.assertEqual(recipe["kind"], SECTION_PERSPECTIVE_KIND)
        self.assertEqual(recipe["section"]["normal"], [0.0, 1.0, 0.0])
        self.assertEqual(recipe["right"], [-1.0, 0.0, 0.0], "standing in the back half, the picture's right is -X")
        drawing = read_model_axis_elevation(self.repository, record_ref_from_uri(document["revisionRef"], PROJECT_ID))
        self.assertEqual(drawing.receipt["source"]["step"]["sha256"], self.step["sha256"])
        self.assertEqual(drawing.receipt["view"], recipe)
        projection = drawing.receipt["projection"]
        cut = set(projection["cut_object_ids"])
        self.assertTrue({"obj-room-floor", "obj-room-roof"} <= cut, cut)
        self.assertEqual(len([name for name in cut if "room-left" in name or "room-right" in name]), 2, cut)
        self.assertTrue(any("room-back" in name for name in projection["removed_object_ids"]), projection["removed_object_ids"])
        self.assertTrue(any("passage-wall" in name for name in projection["drawn_object_ids"]))
        listed = self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
        self.assertIn(document, listed)
        data = self.client.get(f"/api/documents/{document['assetSha256']}/bytes",
                               params={"runId": document["runId"], "revisionRef": document["revisionRef"]})
        self.assertEqual((data.status_code, data.content), (200, drawing.png))
        with TestClient(create_app(self.settings)) as reopened:
            repeated = reopened.post("/api/drawings/section-perspectives", json={
                "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"],
                "section": {"line": [[0, 2.5], [4, 2.5]], "keep": "right"}, "scaleDenominator": 50,
            })
            self.assertEqual((repeated.status_code, repeated.json()), (201, document), "an identical request reads its revision back")
        moved = self.post(camera={"eye": [1.0, 9.0, 1.6], "target": [1.0, 0.0, 1.6], "fovDeg": 70})
        self.assertEqual(moved.status_code, 201, moved.text)
        self.assertNotEqual(moved.json()["revisionRef"], document["revisionRef"])
        self.assertEqual(moved.json()["viewRecipe"]["camera"]["eye"], [1.0, 9.0, 1.6])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_material_hatch_and_beyond_fade_reach_the_svg_and_old_requests_are_unchanged(self):
        svg_ns = "{http://www.w3.org/2000/svg}"
        plain = self.post()
        self.assertEqual(plain.status_code, 201, plain.text)
        self.assertEqual(set(plain.json()["viewRecipe"]["graphics"]), {"cutLineMm", "visibleLineMm", "hatchSpacingMm"})
        cut = read_model_axis_elevation(
            self.repository, record_ref_from_uri(plain.json()["revisionRef"], PROJECT_ID)).receipt["projection"]["cut_object_ids"]
        self.assertIn("obj-room-floor", cut)
        # The Studio room's export names no materials; its floor is given one as a CAD program's user text would.
        binding = bound_project(self.app.state)
        with patch("monkeydiagram.drawing_elevation.object_semantics", return_value={"obj-room-floor": {"material": "concrete"}}):
            document = generate_section_perspective(
                binding, source_stage_ref=self.stage["stageRef"], model_source=None,
                section={"line": [[0, 2.5], [4, 2.5]], "keep": "right"}, scale_denominator=50,
                hatch={"byMaterial": {"concrete": {"poche": True}}}, beyond={"fade": 0.5})
        self.assertEqual(document.view_recipe["request"]["graphics"], {
            "cutLineMm": 0.5, "visibleLineMm": 0.25, "hatchSpacingMm": 0.5,
            "hatch": {"byMaterial": {"concrete": {"spacingMm": 0.5, "angleDeg": 45.0, "poche": True}}},
            "beyond": {"fade": 0.5}})
        ruled = read_model_axis_elevation(self.repository, record_ref_from_uri(document.revision_ref, PROJECT_ID))
        root = ElementTree.fromstring(ruled.svg)
        filled = root.find(f"{svg_ns}g[@id='section-hatch']").findall(f"{svg_ns}polygon")
        self.assertEqual([(polygon.get("data-object"), polygon.get("data-material")) for polygon in filled],
                         [("obj-room-floor", "concrete")])
        self.assertEqual(root.find(f"{svg_ns}g[@id='visible']").get("stroke"), "#808080")
        self.assertNotEqual(document.revision_ref, plain.json()["revisionRef"])
        # An empty byMaterial and a zero fade are the old request: its registered revision, not a redraw.
        cleared = generate_section_perspective(
            binding, source_stage_ref=self.stage["stageRef"], model_source=None,
            section={"line": [[0, 2.5], [4, 2.5]], "keep": "right"}, scale_denominator=50,
            hatch={"byMaterial": {}}, beyond={"fade": 0})
        self.assertEqual(cleared.revision_ref, plain.json()["revisionRef"])
        self.assertEqual(self.post().json(), plain.json())
        self.assertEqual(self.repository.read_head(), self.head)

    def test_refusals_are_named_and_register_nothing(self):
        for code, changes in (
            ("SECTION_PLANE_MISSES_MODEL", {"section": {"line": [[0, 50], [4, 50]], "keep": "right"}}),
            ("SECTION_EYE_ON_KEPT_SIDE", {"camera": {"eye": [2, 1, 1.6], "target": [2, 0, 1.6]}}),
            ("SECTION_EYE_ON_PLANE", {"camera": {"eye": [2, 2.5, 1.6], "target": [2, 0, 1.6]}}),
            ("SECTION_CAMERA_DEGENERATE", {"camera": {"eye": [2, 9, 1.6], "target": [2, 9, 1.6]}}),
            ("SECTION_LINE_DEGENERATE", {"section": {"line": [[1, 2.5], [1, 2.5]], "keep": "right"}}),
            ("SECTION_VALUE_NOT_FINITE", {"section": {"line": [[0, 2.5], [4, float("nan")]], "keep": "right"}}),
            ("DRAWING_OBJECT_UNKNOWN", {"hiddenObjectIds": ["not-a-physical-object"]}),
            ("REQUEST_INVALID", {"section": {"line": [[0, 2.5], [4, 2.5]], "keep": "behind"}}),
            # The cut plan's material rule and fade refusals.
            ("REQUEST_INVALID", {"hatch": {"byMaterial": {"concrete": {"spacingMm": .1}}}}),
            ("REQUEST_INVALID", {"beyond": {"fade": 1.5}}),
            ("REQUEST_INVALID", {"hatch": {"byMaterial": {"": {"poche": True}}}}),
            ("REQUEST_INVALID", {"hatch": {"byMaterial": {"stone": {"angleDeg": 180}}}}),
            ("REQUEST_INVALID", {"hatch": {"byMaterial": {"stone": {"colour": "grey"}}}}),
        ):
            with self.subTest(code=code):
                response = self.post(**changes)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], code)
        self.assertEqual(self.post(projectId="other-project").status_code, 403)
        documents = self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
        self.assertFalse([row for row in documents if (row.get("viewRecipe") or {}).get("kind") == SECTION_PERSPECTIVE_KIND])
        self.assertEqual(self.repository.read_head(), self.head)


if __name__ == "__main__":
    unittest.main()
