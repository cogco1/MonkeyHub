"""A real OCCT elevation stays bound to its committed Stage after reopening."""

from __future__ import annotations

from pathlib import Path
import base64
from datetime import datetime, timezone
from io import BytesIO
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from archflow.adapters import occt_backend
from archflow.project.refs import record_ref_from_uri
from monkeydiagram.drawing_elevation import read_model_axis_elevation
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.gestures import require_document_comment_source
from archflow_studio_api.application.projection import project_state

from .support import PROJECT_ID, retain_runner_receipt
from .test_candidate import CandidateTestCase
from .test_documents import image_bytes
from .test_working_copies import register_model


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class DrawingTests(CandidateTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client.close()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        model = next(row for row in candidate["artifacts"] if row["format"] == "3dm")
        self.model = model["modelSource"]
        self.step = next(row for row in candidate["artifacts"] if row["format"] == "step")
        initialized = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": self.model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        self.stage = initialized.json()
        self.head = self.repository.read_head()

    def generate(self, stage: dict | None = None, **body: object):
        return self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "sourceStageRef": (stage or self.stage)["stageRef"],
            "view": "front", "drawingId": "main-elevation", **body,
        })

    def save_drawing_page(self, drawing: dict, comment: str, *, pinned: bool = True) -> dict:
        response = self.client.put("/api/document-annotations", json={
            "projectId": PROJECT_ID, "runId": drawing["runId"], "assetSha256": drawing["assetSha256"],
            "pageIndex": 0, "baseRevisionSha256": None, "annotations": [], "comment": comment,
            **({"drawingRevisionRef": drawing["revisionRef"]} if pinned else {}),
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def intent_from_drawing(self, drawing: dict, saved: dict):
        reference = {key: saved[key] for key in ("runId", "assetSha256", "pageIndex", "revisionSha256")}
        if saved.get("drawingRevisionRef") is not None:
            reference["drawingRevisionRef"] = saved["drawingRevisionRef"]
        data = self.client.get(f"/api/documents/{drawing['assetSha256']}/bytes", params={
            "runId": drawing["runId"], "revisionRef": drawing["revisionRef"],
        })
        self.assertEqual(data.status_code, 200, data.text[:200])
        output = BytesIO()
        with Image.open(BytesIO(data.content)) as image:
            image.thumbnail((2048, 2048))
            image.save(output, format="PNG")
        return self.client.post("/api/intents", json={
            "projectId": PROJECT_ID, "sourceRunId": self.model["runId"], "stateDigest": self.model["stateDigest"],
            "sourceStageRef": self.stage["stageRef"], "modelSource": self.model,
            "utterance": "set height to 2.4", "targetComponentId": "portico", "elementId": "portico-base",
            "documentAnnotations": [reference], "documentVisuals": [{
                "role": "edit", **reference, "pagePngBase64": base64.b64encode(output.getvalue()).decode(),
                "annotatedPngBase64": None,
            }],
        })

    def test_same_png_drawing_revisions_have_independent_annotation_heads(self) -> None:
        first, second = self.generate(drawingId="elevation-a").json(), self.generate(drawingId="elevation-b").json()
        self.assertEqual(first["assetSha256"], second["assetSha256"])
        self.assertNotEqual(first["revisionRef"], second["revisionRef"])
        a = self.save_drawing_page(first, "the first drawing")
        b = self.save_drawing_page(second, "the second drawing")
        for drawing, saved in ((first, a), (second, b)):
            query = {"runId": drawing["runId"], "assetSha256": drawing["assetSha256"], "pageIndex": 0,
                     "drawingRevisionRef": drawing["revisionRef"]}
            self.assertEqual(self.client.get("/api/document-annotations", params=query).json(), saved)
        wrong = self.client.get("/api/document-annotations", params={**query, "revisionSha256": a["revisionSha256"]})
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(wrong.json()["code"], "ANNOTATION_REVISION_NOT_FOUND")
        response = self.intent_from_drawing(first, a)
        self.assertEqual(response.status_code, 201, response.text)
        comment = self.repository.load_json(record_ref_from_uri(response.json()["documentCommentRef"], PROJECT_ID))
        self.assertEqual(comment["documentAnnotations"][0]["drawingRevisionRef"], first["revisionRef"])
        self.assertEqual(comment["documentVisuals"][0]["drawingRevisionRef"], first["revisionRef"])
        self.assertEqual(comment["documentSources"][0]["drawingRevisionRef"], first["revisionRef"])

    def test_newer_identical_drawing_does_not_rebind_new_or_legacy_submitted_proposals(self) -> None:
        for pinned in (True, False):
            with self.subTest(pinned=pinned):
                first = self.generate(drawingId=f"elevation-a-{pinned}").json()
                saved = self.save_drawing_page(first, "keep this drawing source", pinned=pinned)
                response = self.intent_from_drawing(first, saved)
                self.assertEqual(response.status_code, 201, response.text)
                answer = response.json()
                comment_ref = record_ref_from_uri(answer["documentCommentRef"], PROJECT_ID)
                comment = self.repository.load_json(comment_ref)
                before = self.repository.layout.resolve_record(comment_ref).read_bytes()
                association = self.repository.load_json(record_ref_from_uri(comment["documentSources"][0]["bindingRef"], PROJECT_ID))
                self.assertEqual(association["revisionRef"], first["revisionRef"])
                if not pinned:
                    self.assertNotIn("drawingRevisionRef", comment["documentAnnotations"][0])
                    self.assertNotIn("drawingRevisionRef", comment["documentSources"][0])
                    self.assertNotIn("drawingRevisionRef", comment["documentVisuals"][0])
                newer = self.generate(drawingId=f"elevation-b-{pinned}").json()
                self.assertEqual(newer["assetSha256"], first["assetSha256"])
                self.assertNotEqual(newer["revisionRef"], first["revisionRef"])
                with TestClient(create_app(self.settings)) as reopened:
                    binding = bound_project(reopened.app.state)
                    projection = project_state(binding, source_stage_ref=self.stage["stageRef"])
                    require_document_comment_source(binding, binding.repository.load_json(comment_ref), projection)
                accepted = self.start(answer["proposal"]["proposalId"])
                self.assertEqual(self.finished(accepted["jobId"])["status"], "succeeded")
                self.assertEqual(self.repository.layout.resolve_record(comment_ref).read_bytes(), before)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_real_elevation_revisions_reopen_with_their_own_stage_and_bytes(self) -> None:
        first = self.generate()
        self.assertEqual(first.status_code, 201, first.text)
        r1 = first.json()
        self.assertEqual(r1["modelSource"], self.model)
        self.assertEqual(r1["sourceStageRef"], self.stage["stageRef"])
        self.assertEqual(r1["viewRecipe"]["look"], [0, 1, 0])
        self.assertEqual(self.generate().json(), r1)
        accepted, job = self.run_candidate(
            "set height to 3.5", elementId="portico-base", sourceRunId=self.stage["candidateId"],
            stateDigest=self.model["stateDigest"], sourceStageRef=self.stage["stageRef"],
        )
        self.assertEqual(job["status"], "succeeded", job)
        committed = self.client.post(f"/api/candidates/{accepted['candidateId']}/accept", json={
            "projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": self.stage["stageRef"],
        })
        self.assertEqual(committed.status_code, 200, committed.text)
        second = self.generate(committed.json())
        self.assertEqual(second.status_code, 201, second.text)
        r2 = second.json()
        self.assertEqual(r1["drawingId"], r2["drawingId"])
        self.assertNotEqual(r1["revisionRef"], r2["revisionRef"])
        self.assertNotEqual(r1["assetSha256"], r2["assetSha256"])
        with TestClient(create_app(self.settings)) as reopened:
            for revision in (r1, r2):
                listed = reopened.get("/api/documents", params={"runId": revision["runId"]})
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertIn(revision, listed.json()["documents"])
                data = reopened.get(f"/api/documents/{revision['assetSha256']}/bytes", params={
                    "runId": revision["runId"], "revisionRef": revision["revisionRef"],
                })
                self.assertEqual(data.status_code, 200, data.text[:200])
                drawing = read_model_axis_elevation(self.repository, record_ref_from_uri(revision["revisionRef"], PROJECT_ID))
                self.assertEqual(data.content, drawing.png)
                self.assertEqual(drawing.receipt["source"]["run_id"], revision["modelSource"]["runId"])
                self.assertEqual(drawing.run.base, self.head)
            wrong = reopened.get(f"/api/documents/{r1['assetSha256']}/bytes", params={"runId": r1["runId"], "revisionRef": r2["revisionRef"]})
            self.assertEqual(wrong.status_code, 404)
        self.assertEqual(self.generate().json(), r1)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_imported_complete_model_cannot_use_native_components_as_whole_building(self) -> None:
        imported = register_model(self.client, self.model["runId"], self.model["stateDigest"],
                                  (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes())
        composed = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID,
                                   "branchId": "imported", "modelSource": imported["modelSource"]})
        self.assertEqual(composed.status_code, 201, composed.text)
        result = self.generate(composed.json())
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["code"], "DRAWING_COMPLETE_SOURCE_UNAVAILABLE")
        self.assertEqual(self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], [])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_stage_uses_pinned_runner_and_view_recipe_does_not_overwrite(self) -> None:
        retain_runner_receipt(self.repository, self.repository.load_run(self.stage["candidateId"]), design_state_digest=None)
        first = self.generate()
        self.assertEqual(first.status_code, 201, first.text)
        other = self.generate(view="right")
        self.assertEqual(other.status_code, 201, other.text)
        self.assertNotEqual(first.json()["revisionRef"], other.json()["revisionRef"])
        self.assertEqual(other.json()["viewRecipe"]["look"], [-1, 0, 0])
        documents = self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
        self.assertEqual(len(documents), 2)

    def test_unavailable_exact_step_is_not_rebuilt_or_replaced_by_another_run(self) -> None:
        (self.repository.layout.root / self.step["relativePath"]).write_bytes(b"changed STEP")
        response = self.generate()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "DRAWING_COMPLETE_SOURCE_UNAVAILABLE")
        self.assertEqual(self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], [])

    def test_generation_order_and_original_time_survive_cold_idempotent_reads(self) -> None:
        times = (datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc), datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc))
        with patch("archflow_studio_api.application.drawings.datetime") as clock:
            clock.now.side_effect = times
            first = self.generate()
            self.assertEqual(first.status_code, 201, first.text)
            second = self.generate(scaleDenominator=50, hiddenLines=True)
            self.assertEqual(second.status_code, 201, second.text)
            r1, r2 = first.json(), second.json()
            self.assertEqual(r1["sourceStageRef"], r2["sourceStageRef"])
            self.assertEqual(r1["drawingId"], r2["drawingId"])
            self.assertNotEqual(r1["revisionRef"], r2["revisionRef"])
            self.assertEqual([r1["generatedAt"], r2["generatedAt"]], [time.isoformat() for time in times])
            uploaded = self.client.post("/api/documents", json={
                "projectId": PROJECT_ID, "runId": self.model["runId"], "fileName": "reference.png", "mimeType": "image/png",
                "contentBase64": base64.b64encode(image_bytes()).decode(),
            })
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            self.assertIsNone(uploaded.json()["generatedAt"])
            with TestClient(create_app(self.settings)) as reopened:
                docs = reopened.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
                self.assertEqual(docs, [r2, r1, uploaded.json()])
                repeated = reopened.post("/api/drawings/elevations", json={
                    "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"], "view": "front", "drawingId": "main-elevation",
                })
                self.assertEqual(repeated.status_code, 201, repeated.text)
                self.assertEqual(repeated.json(), r1)
                self.assertEqual(reopened.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], docs)
