"""Single-operator boards survive restart and retain exact document references."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote
from zipfile import ZipFile

from fastapi.testclient import TestClient
from pypdf import PdfReader

from archflow.adapters import occt_backend
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application.boards import BOARD_RUN_ID, MAX_BOARD_BYTES
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID
from .test_candidate import CandidateTestCase
from .test_documents import image_bytes, two_page_pdf


def body(elements: list[dict], base: str | None = None, seen: list[str] | None = None) -> dict:
    return {"projectId": PROJECT_ID, "baseRevisionSha256": base, "title": "设计讨论",
            "elements": elements, "seenDocuments": seen or []}


def image_element(document: dict, page_index: int = 0, identifier: str = "drawing") -> dict:
    return {"id": identifier, "type": "image", "fileId": identifier + "-file", "x": 30, "y": 40,
            "width": 400, "height": 300, "angle": 0, "isDeleted": False, "scale": [1, 1],
            "customData": {"sourceDocument": {"runId": document["runId"], "assetSha256": document["assetSha256"],
                                             "revisionRef": document.get("revisionRef"), "pageIndex": page_index},
                           "label": "会议图纸"}}


class BoardTests(unittest.TestCase):
    def test_empty_board_project_can_create_its_first_real_model_and_drawing(self) -> None:
        import time
        import json
        from archflow.adapters.occt_backend import occt_available
        from archflow.state.state_record import StateRecord

        if not occt_available():
            self.skipTest("cadquery-ocp is not installed")
        # Hub-created projects have an empty authored record; uploaded assets
        # and saved Board content must survive first modeling initialization.
        self.repository.layout.authored_record.parent.mkdir(parents=True, exist_ok=True)
        self.repository.layout.authored_record.write_text(
            json.dumps(StateRecord(project_id=PROJECT_ID, run_id="authored", entities=()).to_dict()), encoding="utf-8",
        )
        document = self.upload(two_page_pdf())
        scene = self.save(body([image_element(document)]))
        before = self.files()
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(project_dir=self.root, cad_export="occt")))
        self.addCleanup(self.client.close)
        self.assertIsNone(self.client.get("/api/state").json()["stateDigest"])
        initialized = self.client.post("/api/project/modeling", json={"projectId": PROJECT_ID})
        self.assertEqual(initialized.status_code, 200, initialized.text)
        self.assertTrue(initialized.json()["initialized"])
        self.assertFalse(self.client.post("/api/project/modeling", json={"projectId": PROJECT_ID}).json()["initialized"])
        for path, data in before.items():
            if path != "input/runner/state-record.json":
                self.assertEqual((self.root / path).read_bytes(), data, path)
        state = self.client.get("/api/state").json()
        self.assertIsNotNone(state["stateDigest"])
        self.assertEqual(state["elements"], [])
        self.assertEqual(self.client.get("/api/board").json(), scene)
        self.assertEqual(self.client.get("/api/documents").json()["documents"], [document])
        self.assertFalse((self.repository.layout.runs / "studio-projection").exists())
        proposed = self.client.post("/api/proposals/sketch", json={
            "projectId": PROJECT_ID, "stateDigest": state["stateDigest"], "componentId": "model",
            "elementId": "first-block", "profile": [[0, 0], [1.2, 0], [1.2, 0.4], [0, 0.4]],
            "height": 1.8, "baseLevel": "ground",
        })
        self.assertEqual(proposed.status_code, 201, proposed.text)
        started = self.client.post(f"/api/proposals/{proposed.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{started.json()['jobId']}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{job['candidateId']}").json()
        self.assertEqual({row["format"] for row in candidate["artifacts"]}, {"3dm", "step"})
        model = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
        drawing = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "modelSource": model, "view": "front", "drawingId": "first-elevation",
        })
        self.assertEqual(drawing.status_code, 201, drawing.text)
        reopened = self.new_client()
        self.assertIn(drawing.json(), reopened.get("/api/documents").json()["documents"])
        self.assertIn(document, reopened.get("/api/documents").json()["documents"])
        self.assertEqual(reopened.get("/api/board").json(), scene)
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(self.repository.read_design_branches(), {})

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-board-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        self.head = self.repository.read_head()
        self.client = self.new_client()

    def new_client(self) -> TestClient:
        client = TestClient(create_app(StudioSettings(project_dir=self.root, cad_export="off")))
        self.addCleanup(client.close)
        return client

    def files(self) -> dict[str, bytes]:
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def upload(self, data: bytes, name: str = "图纸.pdf", mime: str = "application/pdf") -> dict:
        response = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "fileName": name, "mimeType": mime,
            "contentBase64": base64.b64encode(data).decode(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def save(self, request: dict) -> dict:
        response = self.client.put("/api/board", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_empty_read_and_invalid_first_save_create_no_run(self) -> None:
        before = self.files()
        response = self.client.get("/api/board")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"projectId": PROJECT_ID, "title": "MonkeyBoard", "elements": [],
                                          "seenDocuments": [], "revisionSha256": None})
        invalid = self.client.put("/api/board", json=body([{"id": "image", "type": "image"}]))
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(self.files(), before)
        self.assertEqual(list(self.repository.layout.runs.iterdir()), [])

    def test_geometry_text_frames_and_exact_pages_survive_cold_restart(self) -> None:
        document = self.upload(two_page_pdf())
        scene = [
            {"id": "frame", "type": "frame", "x": -300, "y": -200, "width": 1200, "height": 900, "name": "入口讨论"},
            {"id": "note", "type": "text", "x": 700.5, "y": 80, "width": 220, "height": 80,
             "text": "Data: 保留原图，入口加宽\n待设计师确认", "frameId": "frame", "fontSize": 24,
             "customData": {"discussionOnly": True}},
            {"id": "arrow", "type": "arrow", "x": 600, "y": 150, "width": 60, "height": 50,
             "points": [[0, 0], [-60, 50]], "strokeColor": "#1971c2", "frameId": "frame"},
            image_element(document, 1),
        ]
        saved = self.save(body(scene, seen=["received-page-1"]))
        self.assertEqual(saved["elements"], scene)
        self.assertEqual(self.new_client().get("/api/board").json(), saved)
        run = self.repository.load_run(BOARD_RUN_ID)
        self.assertEqual(run.base, self.head)
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(self.repository.read_design_branches(), {})
        self.assertEqual(self.client.get(f"/api/documents/{document['assetSha256']}/bytes",
                                         params={"runId": document["runId"]}).content, two_page_pdf())

    def test_deleted_received_document_stays_seen_and_unchanged_save_is_idempotent(self) -> None:
        document = self.upload(image_bytes(), "平面.png", "image/png")
        first = self.save(body([image_element(document)], seen=["received-page-0"]))
        second = self.save(body([], first["revisionSha256"], ["received-page-0"]))
        before = self.files()
        self.assertEqual(self.save(body([], second["revisionSha256"], ["received-page-0"])), second)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.new_client().get("/api/board").json(), second)
        records = self.repository.list_json(
            run=self.repository.load_run(BOARD_RUN_ID),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=BOARD_RUN_ID),
        )
        payloads = {ref.sha256: self.repository.load_json(ref) for ref in records}
        self.assertEqual(payloads[second["revisionSha256"]]["previousRevisionSha256"], first["revisionSha256"])
        self.assertEqual(len(payloads), 2)

    def test_export_uses_only_caller_ordered_registered_pages_without_writing(self) -> None:
        document = self.upload(two_page_pdf())
        pages = [{"runId": document["runId"], "assetSha256": document["assetSha256"],
                  "revisionRef": document["revisionRef"], "pageIndex": index} for index in (1, 0)]
        before = self.files()
        merged = self.client.post("/api/board/export", json={"projectId": PROJECT_ID, "pages": pages,
                                   "format": "merged-pdf", "zip": False})
        self.assertEqual(merged.status_code, 200, merged.text)
        self.assertEqual(merged.headers["content-type"], "application/pdf")
        self.assertEqual(len(PdfReader(BytesIO(merged.content)).pages), 2)
        self.assertEqual(self.files(), before)
        archive = self.client.post("/api/board/export", json={"projectId": PROJECT_ID, "pages": pages,
                                    "format": "page-pdfs", "zip": True})
        self.assertEqual(archive.status_code, 200, archive.text)
        self.assertEqual(archive.headers["content-type"], "application/zip")
        with ZipFile(BytesIO(archive.content)) as bundle:
            self.assertEqual(bundle.namelist(), ["001-图纸.pdf", "002-图纸.pdf"])
            self.assertTrue(all(len(PdfReader(BytesIO(bundle.read(name))).pages) == 1 for name in bundle.namelist()))
        self.assertEqual(self.files(), before)
        image = self.upload(image_bytes(), "讨论图纸.png", "image/png")
        after_upload = self.files()
        raster = self.client.post("/api/board/export", json={"projectId": PROJECT_ID, "pages": [{
            "runId": image["runId"], "assetSha256": image["assetSha256"], "revisionRef": image["revisionRef"], "pageIndex": 0,
        }], "format": "jpeg", "zip": False})
        self.assertEqual(raster.status_code, 200, raster.text)
        self.assertEqual(raster.headers["content-type"], "image/jpeg")
        self.assertIn("filename*=UTF-8''" + quote("001-讨论图纸.jpeg"), raster.headers["content-disposition"])
        self.assertTrue(raster.headers["content-disposition"].isascii())
        self.assertTrue(raster.content.startswith(b"\xff\xd8"))
        self.assertEqual(self.files(), after_upload)

    def test_stale_and_wrong_project_saves_do_not_write(self) -> None:
        saved = self.save(body([]))
        before = self.files()
        stale = self.client.put("/api/board", json=body([{"id": "note", "type": "text", "text": "stale"}]))
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "BOARD_STALE")
        wrong = self.client.put("/api/board", json={**body([], saved["revisionSha256"]), "projectId": "other-project"})
        self.assertEqual(wrong.status_code, 403, wrong.text)
        self.assertEqual(self.files(), before)

    def test_concurrent_saves_from_same_revision_keep_one_winner(self) -> None:
        saved = self.save(body([]))
        clients = [self.new_client(), self.new_client()]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(client.put, "/api/board", json=body(
                [{"id": "note", "type": "text", "text": str(index)}], saved["revisionSha256"],
            )) for index, client in enumerate(clients)]
            responses = [future.result() for future in futures]
        self.assertCountEqual([response.status_code for response in responses], [200, 409])
        winner = next(response.json() for response in responses if response.status_code == 200)
        self.assertEqual(self.new_client().get("/api/board").json(), winner)

    def test_unknown_document_wrong_page_revision_and_claimed_model_source_do_not_write(self) -> None:
        document = self.upload(two_page_pdf())
        valid = image_element(document)
        mutations = [
            ("assetSha256", "0" * 64, 404), ("pageIndex", 2, 422), ("pageIndex", True, 422),
            ("runId", "missing-run", 404), ("revisionRef", "unknown-revision", 404),
        ]
        before = self.files()
        for key, value, status in mutations:
            with self.subTest(key=key, value=value):
                element = deepcopy(valid)
                element["customData"]["sourceDocument"][key] = value
                response = self.client.put("/api/board", json=body([element]))
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(self.files(), before)
        valid["customData"]["modelSource"] = {"runId": "client-claim"}
        response = self.client.put("/api/board", json=body([valid]))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.files(), before)

    def test_raw_pixels_transient_state_bad_json_and_large_scenes_do_not_write(self) -> None:
        note = {"id": "note", "type": "text", "text": "讨论"}
        invalid = [
            {**body([note]), "appState": {"scrollX": 0}},
            {**body([note]), "files": {"pixel": {"dataURL": "data:image/png;base64,AAAA"}}},
            body([{**note, "customData": {"dataURL": "data:image/png;base64,AAAA"}}]),
            body([{**note, "x": float("nan")}]),
            body([note, note]),
            body([note] * 10_001),
            body([{**note, "text": "a" * (MAX_BOARD_BYTES + 1)}]),
        ]
        before = self.files()
        for index, request in enumerate(invalid):
            with self.subTest(index=index):
                response = self.client.put("/api/board", content=json.dumps(request), headers={"Content-Type": "application/json"})
                self.assertIn(response.status_code, (413, 422), response.text[:200])
                self.assertEqual(self.files(), before)


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class BoardDrawingTests(CandidateTestCase):
    def test_generated_revision_must_be_exact_and_reopens_without_changing_stage(self) -> None:
        self.client.close()
        settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.client = TestClient(create_app(settings))
        self.addCleanup(self.client.close)
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        source = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
        initialized = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": source})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        stage = initialized.json()
        branches, head = self.repository.read_design_branches(), self.repository.read_head()
        drawing_response = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "sourceStageRef": stage["stageRef"], "view": "front", "drawingId": "main-elevation",
        })
        self.assertEqual(drawing_response.status_code, 201, drawing_response.text)
        drawing = drawing_response.json()
        image = image_element(drawing)
        unpinned = deepcopy(image)
        del unpinned["customData"]["sourceDocument"]["revisionRef"]
        response = self.client.put("/api/board", json=body([unpinned]))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(self.repository.layout.run(BOARD_RUN_ID).root.exists())
        saved = self.client.put("/api/board", json=body([image], seen=[drawing["revisionRef"]]))
        self.assertEqual(saved.status_code, 200, saved.text)
        with TestClient(create_app(settings)) as reopened:
            self.assertEqual(reopened.get("/api/board").json(), saved.json())
        self.assertEqual(self.repository.read_design_branches(), branches)
        self.assertEqual(self.repository.read_head(), head)
