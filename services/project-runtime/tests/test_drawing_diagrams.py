"""Diagrams drawn from data, with no model (gap 38): a network through POST /api/drawings/diagrams.

A level breakdown had no model, and MonkeyDiagram drew only from one, so its plan topology was drawn by hand on the
Board. A diagram is now drawn from a spec and registered as a sheet is: its PDF in the documents list, its SVG and PNG
beside it, its revisions under one drawing id, and it reaches the Board by hand only.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from project_runtime.application.artifacts import save_document
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from .support import PROJECT_ID, make_project
from .test_documents import image_bytes

SPEC = {
    "groups": [{"id": "ground", "label": "地面层", "tone": "grey"}, {"id": "city", "label": "倒悬城市", "tone": "lavender"}],
    "nodes": [{"id": "p1", "label": "P1 入口", "group": "ground", "at": [50, 50], "size": [60, 40]},
              {"id": "a", "label": "02 初见", "group": "city", "at": [200, 50], "size": [60, 40]},
              {"id": "b", "label": "03 迷宫", "group": "city", "at": [200, 200], "size": [60, 40]}],
    "edges": [{"from": "p1", "to": "a", "mark": "1"}, {"from": "a", "to": "b", "mark": "2"},
              {"from": "b", "to": "p1", "line": "return"}, {"from": "b", "to": [320, 200], "line": "branch"}],
    "lines": {"main": "推进主线", "return": "回返", "branch": "支线"},
    "north": 0,
}


class DiagramRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off")))
        self.addCleanup(self.client.close)
        self.head = self.repository.read_head()

    def post(self, status=201, **body):
        payload = {"projectId": PROJECT_ID, "drawingId": "plan-topology", "kind": "network", "spec": SPEC,
                   "title": "平面拓扑", **body}
        response = self.client.post("/api/drawings/diagrams", json=payload)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def file(self, document, file_format):
        response = self.client.get(f"/api/drawings/{document['assetSha256']}/files/{file_format}",
                                   params={"runId": document["runId"]})
        self.assertEqual(response.status_code, 200, response.text[:300])
        return response.content

    def test_a_network_is_registered_with_its_files_and_stays_off_the_board(self):
        document = self.post(subtitle="依据 PPT31")
        self.assertEqual((document["mimeType"], document["drawingId"], document["pageCount"]),
                         ("application/pdf", "plan-topology", 1))
        recipe = document["viewRecipe"]
        self.assertEqual((recipe["kind"], recipe["diagramKind"], recipe["styleId"], recipe["board"]),
                         ("diagram", "network", "diagram-colour", "on-request"))
        self.assertEqual([node["id"] for node in recipe["spec"]["nodes"]], ["p1", "a", "b"])
        self.assertEqual(len(recipe["specSha256"]), 64)
        self.assertEqual(recipe["warnings"], [])
        pdf = self.file(document, "pdf")
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), document["assetSha256"])
        self.assertTrue(self.file(document, "png").startswith(b"\x89PNG"))
        svg = self.file(document, "svg").decode("utf-8")
        self.assertIn("平面拓扑", svg)
        self.assertIn("推进主线", svg)
        self.assertEqual(self.repository.read_head(), self.head, "a diagram changes no design state")

    def test_the_same_request_is_the_same_document_and_a_change_is_a_new_revision(self):
        first = self.post()
        self.assertEqual(self.post()["assetSha256"], first["assetSha256"])
        changed = {**SPEC, "edges": [*SPEC["edges"][:2], {"from": "b", "to": "p1", "line": "return", "style": "dotted"}]}
        second = self.post(spec=changed)
        self.assertNotEqual(second["assetSha256"], first["assetSha256"])
        listed = [row for row in self.client.get("/api/documents").json()["documents"] if row.get("drawingId") == "plan-topology"]
        self.assertEqual({row["assetSha256"] for row in listed}, {first["assetSha256"], second["assetSha256"]},
                         "the earlier revision stays listed")
        mono = self.post(styleId="diagram-mono", drawingId="plan-topology-print")
        self.assertEqual(mono["viewRecipe"]["styleId"], "diagram-mono")

    def test_a_spec_that_cannot_be_drawn_is_refused_by_its_path(self):
        for spec, path in (({**SPEC, "nodes": [{"id": "p1", "label": "P1"}]}, "spec.nodes[0].at"),
                           ({**SPEC, "edges": [{"from": "p1", "to": "nowhere"}]}, "spec.edges[0].to"),
                           ({**SPEC, "colours": {}}, "spec"),
                           ({**SPEC, "groups": [{"id": "g", "tone": "neon"}]}, "spec.groups[0].tone")):
            with self.subTest(path=path):
                refused = self.post(status=422, spec=spec)
                self.assertEqual(refused["code"], "DIAGRAM_INVALID")
                self.assertTrue(refused["detail"].startswith(path), refused["detail"])
        self.assertEqual(self.post(status=422, kind="mindmap")["code"], "REQUEST_INVALID")

    def test_a_drawing_id_that_names_another_kind_is_refused(self):
        save_document(bound_project(self.client.app.state), None, "sheet.png", "image/png",
                      base64.b64encode(image_bytes()).decode("ascii"), drawing_id="plan-topology",
                      view_recipe={"kind": "review-sheet"})
        self.assertEqual(self.post(status=409)["code"], "DRAWING_KIND_CHANGED")


    def test_a_section_and_a_timeline_are_registered_as_a_network_is(self):
        section = {"axis": {"x": [0, 100], "y": [0, 50]}, "ground": {"y": 0},
                   "spans": [{"id": "hall", "label": "门厅", "x0": 10, "x1": 40, "y0": 0, "y1": 12, "tone": "grey"}],
                   "moves": [{"from": [45, 2], "to": [45, 38], "line": "return"}]}
        timeline = {"axis": {"start": 0, "end": 10}, "lanes": [{"id": "z", "label": "片区"}],
                    "segments": [{"lane": "z", "start": 1, "end": 4, "label": "P1", "colour": "#5c7cfa"}],
                    "events": [{"lane": "z", "at": 6, "mark": "tick"}]}
        for kind, spec in (("section", section), ("timeline", timeline)):
            with self.subTest(kind=kind):
                document = self.post(drawingId=f"breakdown-{kind}", kind=kind, spec=spec, title=kind)
                self.assertEqual((document["viewRecipe"]["diagramKind"], document["viewRecipe"]["board"]), (kind, "on-request"))
                self.assertTrue(self.file(document, "png").startswith(b"\x89PNG"))
