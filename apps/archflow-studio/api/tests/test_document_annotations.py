"""Page ink survives reopening and follows its exact source into an intent/run."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, retain_rhino_receipt, runner_state_digest
from .test_documents import image_bytes, two_page_pdf
from .test_gestures import Scripted, gesture, hit
from .test_intents import scripted, semantic_wall_edit


def stroke(id: str = "blue-stroke", kind: str = "freehand") -> dict:
    return {"id": id, "kind": kind, "points": [[0.1, 0.2], [0.25, 0.4], [0.4, 0.3]], "color": "#2277dd", "lineWidth": 0.004, "label": None}


def text_annotation() -> dict:
    return {
        "id": "page-text", "kind": "text", "points": [[0.2, 0.35]],
        "color": "#2277dd", "lineWidth": 0.004, "fontSize": 0.024,
        "label": "入口净宽保持\n把门向右移动 100 mm",
    }


def page_ref(page: dict) -> dict:
    return {key: page[key] for key in ("runId", "assetSha256", "pageIndex", "revisionSha256")}


def page_visual(page: dict, *, size: tuple[int, int] = (120, 80), role: str = "edit",
                annotated: bool = True, color: str = "white", note: str | None = None) -> dict:
    def png(fill: str) -> str:
        output = BytesIO()
        Image.new("RGB", size, fill).save(output, format="PNG")
        return base64.b64encode(output.getvalue()).decode("ascii")

    return {
        "role": role, **page_ref(page), "pagePngBase64": png(color),
        "annotatedPngBase64": png("blue") if annotated else None, "referenceNote": note,
    }


class DocumentAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-document-ink-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)
        # This suite tests page persistence against a retained native source.
        # Composed continuation needs real prior programs and is exercised by
        # the OCCT candidate integration tests.
        model = (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        with mock.patch("tests.support.RHINO_DESIGN_STATE_DIGEST", self.state_digest):
            retain_rhino_receipt(self.repository, self.repository.load_run(REFERENCE_RUN_ID),
                                 stage_id="document-source", file_name="source.3dm", payload_bytes=model)
        self.model_source = {"runId": REFERENCE_RUN_ID, "stateDigest": self.state_digest,
                             "assetSha256": hashlib.sha256(model).hexdigest()}
        self.pdf = self.upload(two_page_pdf(), "two-pages.pdf", "application/pdf")
        self.png = self.upload(image_bytes(), "reference.png", "image/png")

    def upload(self, data: bytes, name: str, mime: str) -> dict:
        response = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID, "fileName": name,
            "mimeType": mime, "contentBase64": base64.b64encode(data).decode(),
            "modelSource": self.model_source,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def query(self, document: dict, page: int = 0) -> dict:
        return {"runId": document["runId"], "assetSha256": document["assetSha256"], "pageIndex": page}

    def save(self, document: dict, annotations: list[dict], page: int = 0, base: str | None = None, comment: str = "") -> dict:
        response = self.client.put("/api/document-annotations", json={
            "projectId": PROJECT_ID, **self.query(document, page), "baseRevisionSha256": base,
            "annotations": annotations, "comment": comment,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def intent(self, refs: list[dict], **extra):
        documents = {}
        if refs:
            visuals = []
            for reference in refs:
                visible = next((document["pages"][reference["pageIndex"]]
                                for document in (self.pdf, self.png)
                                if document["assetSha256"] == reference["assetSha256"]
                                and reference["pageIndex"] < len(document["pages"])), None)
                size = (int(visible["width"]), int(visible["height"])) if visible else (120, 80)
                visuals.append(page_visual(reference, size=size))
            documents = {"documentAnnotations": refs, "documentVisuals": visuals}
        return self.client.post("/api/intents", json={
            "projectId": PROJECT_ID, "stateDigest": self.state_digest,
            "sourceRunId": REFERENCE_RUN_ID, "utterance": "set height to 2.2",
            "targetComponentId": "portico", "elementId": "portico-base",
            **documents, **extra,
        })

    def historical_reference(self) -> dict:
        self.repository.create_run("reference-only")
        response = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "runId": "reference-only", "fileName": "material-reference.png",
            "mimeType": "image/png", "contentBase64": base64.b64encode(image_bytes(color="red")).decode(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_two_pages_image_and_file_versions_keep_separate_ink_through_erase_undo_reopen(self) -> None:
        before_head = self.repository.read_head()
        before_input = self.repository.layout.authored_record.read_bytes()
        a, b = stroke(), stroke("second-stroke", "line")
        page0 = self.save(self.pdf, [a, b], comment="保留原图，入口加宽")
        page1 = self.save(self.pdf, [stroke("rotated-page", "arrow")], page=1, comment="第二页裁切旋转图")
        image_page = self.save(self.png, [stroke("image-stroke", "ruler")], comment="照片意见")
        erased = self.save(self.pdf, [b], base=page0["revisionSha256"], comment=page0["comment"])
        self.assertEqual(erased["annotations"], [b])
        old = self.client.get("/api/document-annotations", params={**self.query(self.pdf), "revisionSha256": page0["revisionSha256"]})
        self.assertEqual(old.json()["annotations"], [a, b])
        undone = self.save(self.pdf, [a, b], base=erased["revisionSha256"], comment=page0["comment"])
        newer_file = self.upload(image_bytes(color="red"), "reference.png", "image/png")
        self.assertNotEqual(newer_file["assetSha256"], self.png["assetSha256"])
        with TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))) as reopened:
            for document, page, saved in ((self.pdf, 0, undone), (self.pdf, 1, page1), (self.png, 0, image_page)):
                self.assertEqual(reopened.get("/api/document-annotations", params=self.query(document, page)).json(), saved)
            fresh = reopened.get("/api/document-annotations", params=self.query(newer_file)).json()
            self.assertIsNone(fresh["revisionSha256"])
            self.assertEqual(fresh["annotations"], [])
            self.assertEqual(reopened.get(f"/api/documents/{self.pdf['assetSha256']}/bytes", params={"runId": REFERENCE_RUN_ID}).content, two_page_pdf())
        self.assertEqual(self.repository.read_head(), before_head)
        self.assertEqual(self.repository.layout.authored_record.read_bytes(), before_input)

    def test_stale_revision_cannot_overwrite_new_ink_and_invalid_page_cannot_be_saved(self) -> None:
        saved = self.save(self.pdf, [stroke()])
        body = {"projectId": PROJECT_ID, **self.query(self.pdf), "baseRevisionSha256": None, "annotations": []}
        response = self.client.put("/api/document-annotations", json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ANNOTATION_STALE")
        self.assertEqual(self.client.get("/api/document-annotations", params=self.query(self.pdf)).json(), saved)
        for document, page in ((self.pdf, 2), (self.png, 1)):
            response = self.client.put("/api/document-annotations", json={**body, **self.query(document, page)})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["code"], "DOCUMENT_PAGE_NOT_FOUND")
        response = self.client.get("/api/document-annotations", params={**self.query(self.pdf, 1), "revisionSha256": saved["revisionSha256"]})
        self.assertEqual(response.status_code, 404)
        response = self.client.put("/api/document-annotations", json={**body, "projectId": "other-project"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "PROJECT_MISMATCH")

    def test_normalized_ink_rejects_model_fields_and_invalid_coordinates(self) -> None:
        for mutation in ({"camera": {}}, {"hits": []}, {"worldDirection": [0, 0, 1]}, {"points": [[1.01, 0.1]]}, {"points": [[0.1, -0.01]]}, {"lineWidth": 2}, {"lineWidth": 0}):
            with self.subTest(mutation=mutation):
                response = self.client.put("/api/document-annotations", json={
                    "projectId": PROJECT_ID, **self.query(self.pdf), "baseRevisionSha256": None,
                    "annotations": [{**stroke(), **mutation}],
                })
                self.assertEqual(response.status_code, 422, response.text)
        response = self.client.put("/api/document-annotations", json={
            "projectId": PROJECT_ID, **self.query(self.pdf), "baseRevisionSha256": None,
            "annotations": [stroke(), stroke()],
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/document-annotations", params=self.query(self.pdf)).json()["annotations"], [])

    def test_submitted_words_and_exact_ink_follow_the_existing_candidate_and_survive_restart(self) -> None:
        before_head = self.repository.read_head()
        submitted_ink = [stroke(), text_annotation()]
        saved = self.save(self.pdf, submitted_ink, page=1, comment="旋转页上的范围")
        reference = page_ref(saved)
        old_3d = gesture("circle", hit("obj-portico-base"))
        response = self.intent([reference], gestures=[old_3d], utterance="set height to 0.8")
        self.assertEqual(response.status_code, 201, response.text)
        answer = response.json()
        self.assertIn("circle covering portico", answer["gestures"][0])
        self.assertEqual(answer["proposal"]["protected"], [])
        self.assertIn("page 2 (pageIndex 1)", " ".join(answer["gestures"]))
        self.assertIn(self.pdf["assetSha256"], " ".join(answer["gestures"]))
        self.assertIn("入口净宽保持", " ".join(answer["gestures"]))
        self.assertIn("font size 0.024", " ".join(answer["gestures"]))
        self.assertTrue(answer["documentCommentRef"].startswith(f"project://{PROJECT_ID}/runs/{REFERENCE_RUN_ID}/records/"))
        self.save(self.pdf, [], page=1, base=saved["revisionSha256"], comment="后续擦除")
        started = self.client.post(f"/api/proposals/{answer['proposal']['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        job = started.json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.client.get(f"/api/jobs/{job['jobId']}").json()
            if result["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual(result["status"], "succeeded", result)
        with TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))) as reopened:
            for run_id in (REFERENCE_RUN_ID, job["candidateId"]):
                comments = reopened.get("/api/document-comments", params={"runId": run_id})
                self.assertEqual(comments.status_code, 200, comments.text)
                self.assertEqual(len(comments.json()["comments"]), 1)
                comment = comments.json()["comments"][0]
                self.assertEqual(comment["utterance"], "set height to 0.8")
                self.assertEqual(comment["documentAnnotations"], [reference])
                original_ink = reopened.get("/api/document-annotations", params=reference)
                self.assertEqual(original_ink.json()["annotations"], submitted_ink)
        self.assertEqual(self.repository.read_head(), before_head)

    def test_proposal_continuation_retains_source_compilation_and_request_after_restart(self) -> None:
        from archflow.ports.model import ModelInvocationReceipt
        from archflow.project.repository import FilesystemProjectRepository
        from archflow_studio_api.application.gestures import require_document_comment_source
        from .test_candidate import _compilation_receipt, _load_kind

        saved = self.save(self.png, [stroke()], comment="Raise this base and retain the source page.")
        reference = page_ref(saved)
        receipt = ModelInvocationReceipt.from_dict(_compilation_receipt(self.state_digest))
        self.app.state.intent_compiler = scripted(
            utterance="set height to 2.2", component_id="portico", element_id="portico-base", receipt=receipt,
        )
        response = self.intent([reference], utterance="Raise the base to 2.2 metres.")
        self.assertEqual(response.status_code, 201, response.text)
        first = self.app.state.proposals.get(response.json()["proposal"]["proposalId"])
        self.assertIsNotNone(first.pending)
        self.assertTrue(first.pending.request_id)
        self.assertTrue(first.pending.known_slots)
        self.assertEqual(first.compilation_receipt, receipt.to_dict())
        original_comment = self.repository.load_json(first.document_comment_ref)
        before_runs = set(self.repository.layout.runs.iterdir())
        continued = self.client.post("/api/proposals", json={
            "stateDigest": self.state_digest, "sourceProposalId": first.proposal_id,
            "targetComponentId": "portico", "utterance": "set module to 1.5",
        })
        self.assertEqual(continued.status_code, 201, continued.text)
        final_id = continued.json()["proposalId"]
        final = self.app.state.proposals.get(final_id)
        self.assertEqual(final.document_comment_ref, first.document_comment_ref)
        self.assertEqual(set(self.repository.layout.runs.iterdir()), before_runs)
        with mock.patch("archflow_studio_api.application.gestures.require_document_comment_source",
                        wraps=require_document_comment_source) as verify_source:
            started = self.client.post(f"/api/proposals/{final_id}/candidate")
            self.assertEqual(started.status_code, 202, started.text)
            job = started.json()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                result = self.client.get(f"/api/jobs/{job['jobId']}").json()
                if result["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.02)
            self.assertEqual(result["status"], "succeeded", result)
            verify_source.assert_called_once()
            self.assertEqual(verify_source.call_args.args[1], original_comment)
        decided = self.client.post(f"/api/proposals/{final_id}/decision", json={
            "decision": "accepted", "candidateId": job["candidateId"], "reason": "Retain this fixture option.",
        })
        self.assertEqual(decided.status_code, 201, decided.text)
        repository = FilesystemProjectRepository.open(self.root / PROJECT_ID)
        retained = _load_kind(repository, job["candidateId"], "intent-compilation")
        self.assertEqual(retained["receipt"], receipt.to_dict())
        self.assertEqual(_load_kind(repository, job["candidateId"], "studio-document-comment"), original_comment)
        episode = _load_kind(repository, job["candidateId"], "deliberation-episode")
        self.assertEqual(episode["intent"]["requestId"], first.pending.request_id)
        self.assertEqual(episode["intent"]["knownSlots"], dict(first.pending.known_slots))
        self.assertEqual(episode["intent"]["utterance"], first.pending.original_utterance)
        with TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))) as reopened:
            comments = reopened.get("/api/document-comments", params={"runId": job["candidateId"]}).json()["comments"]
            self.assertEqual(comments[0]["documentAnnotations"], [reference])
            self.assertEqual(reopened.get("/api/document-annotations", params=reference).json()["annotations"], [stroke()])
        self.assertEqual(len(set(self.repository.layout.runs.iterdir()) - before_runs), 1)

    def test_document_keep_is_context_and_never_a_model_selection_or_keep_clause(self) -> None:
        saved = self.save(self.png, [stroke(kind="keep")], comment="图片中蓝色范围保持")
        compiler = Scripted("set height to 0.8", "portico-base")
        self.app.state.intent_compiler = compiler
        response = self.intent([page_ref(saved)], utterance="make the base a little taller")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["proposal"]["protected"], [])
        self.assertTrue(compiler.calls)
        facts = " ".join(compiler.calls[-1]["selection"].gestures)
        self.assertIn("page 1 (pageIndex 0)", facts)
        self.assertIn("not model geometry", facts)
        self.assertIn("图片中蓝色范围保持", facts)

    def test_edit_visual_and_only_selected_cross_run_reference_reach_the_agent(self) -> None:
        saved = self.save(self.png, [stroke(kind="keep"), text_annotation()])
        reference = self.historical_reference()
        reference_page = {**self.query(reference), "revisionSha256": None}
        visuals = [page_visual(saved), page_visual(reference_page, role="reference", annotated=False,
                                                  color="red", note="只参照材料与色彩")]
        compiler = Scripted("set height to 0.8", "portico-base")
        self.app.state.intent_compiler = compiler
        response = self.intent([page_ref(saved)], documentVisuals=visuals, utterance="make the base a little taller")
        self.assertEqual(response.status_code, 201, response.text)
        selection = compiler.calls[-1]["selection"]
        selected = selection.document_visuals
        self.assertEqual(len(selected), 2)
        self.assertEqual([visual.context["role"] for visual in selected], ["edit", "reference"])
        self.assertEqual(selected[0].context["revisionSha256"], saved["revisionSha256"])
        self.assertIn("入口净宽保持", json.dumps(selected[0].context["annotationSummary"], ensure_ascii=False))
        self.assertEqual(selected[1].context["runId"], "reference-only")
        self.assertEqual(selected[1].context["assetSha256"], reference["assetSha256"])
        self.assertIsNone(selected[1].context["revisionSha256"])
        self.assertEqual(selected[1].context["referenceNote"], "只参照材料与色彩")
        self.assertEqual(selected[0].page_png, base64.b64decode(visuals[0]["pagePngBase64"]))
        self.assertEqual(selected[0].annotated_png, base64.b64decode(visuals[0]["annotatedPngBase64"]))
        self.assertEqual(selected[1].page_png, base64.b64decode(visuals[1]["pagePngBase64"]))
        self.assertIsNone(selected[1].annotated_png)
        self.assertEqual((selection.component_id, selection.element_id), ("portico", "portico-base"))
        self.assertEqual(response.json()["proposal"]["protected"], [])
        self.assertEqual(response.json()["proposal"]["sourceRunId"], REFERENCE_RUN_ID)
        self.assertEqual(response.json()["proposal"]["modelSource"], self.model_source)
        self.assertEqual(self.client.get("/api/document-annotations", params=self.query(reference)).json()["revisionSha256"], None)

    def test_empty_saved_edit_page_keeps_its_revision_without_an_overlay(self) -> None:
        saved = self.save(self.png, [], comment="只修改原图所示入口")
        compiler = Scripted("set height to 0.8", "portico-base")
        self.app.state.intent_compiler = compiler
        response = self.intent([page_ref(saved)], documentVisuals=[page_visual(saved, annotated=False)],
                               utterance="make the base a little taller")
        self.assertEqual(response.status_code, 201, response.text)
        visual = compiler.calls[-1]["selection"].document_visuals[0]
        self.assertEqual(visual.context["revisionSha256"], saved["revisionSha256"])
        self.assertIsNone(visual.annotated_png)
        comment = self.client.get("/api/document-comments", params={"runId": REFERENCE_RUN_ID}).json()["comments"][0]
        self.assertEqual(comment["documentAnnotations"], [page_ref(saved)])

    def test_missing_empty_or_invalid_page_visual_refuses_model_call_and_preserves_saved_page(self) -> None:
        saved = self.save(self.png, [stroke()], comment="提交前保存的原页意见")
        valid = page_visual(saved)
        compiler = Scripted("set height to 0.8", "portico-base")
        self.app.state.intent_compiler = compiler
        cases = (
            ([], "DOCUMENT_VISUALS_REQUIRED"),
            ([{**valid, "pagePngBase64": ""}], "DOCUMENT_VISUAL_INVALID"),
            ([{**valid, "pagePngBase64": base64.b64encode(b"not a PNG").decode()}], "DOCUMENT_VISUAL_INVALID"),
        )
        for visuals, code in cases:
            with self.subTest(code=code, visuals=bool(visuals)):
                response = self.intent([page_ref(saved)], documentVisuals=visuals, utterance="make the base a little taller")
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], code)
                self.assertEqual(compiler.calls, [])
                comments = self.client.get("/api/document-comments", params={"runId": REFERENCE_RUN_ID}).json()["comments"]
                self.assertEqual(comments, [])
                self.assertEqual(self.client.get("/api/document-annotations", params=self.query(self.png)).json(), saved)

    def test_primary_visual_must_match_the_exact_submitted_document_page(self) -> None:
        saved = self.save(self.pdf, [stroke()], page=1)
        valid = page_visual(saved, size=(500, 600))
        compiler = Scripted("set height to 0.8", "portico-base")
        self.app.state.intent_compiler = compiler
        for mutation in ({"assetSha256": self.png["assetSha256"]}, {"pageIndex": 0}):
            with self.subTest(mutation=mutation):
                response = self.intent([page_ref(saved)], documentVisuals=[{**valid, **mutation}],
                                       utterance="make the base a little taller")
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], "DOCUMENT_VISUAL_MISMATCH")
                self.assertEqual(compiler.calls, [])

    def test_mismatched_revision_is_refused_before_any_comment_is_retained(self) -> None:
        saved = self.save(self.pdf, [stroke()])
        reference = page_ref(saved)
        for mutation in ({"pageIndex": 1}, {"assetSha256": self.png["assetSha256"]}, {"runId": "missing-run"}):
            with self.subTest(mutation=mutation):
                response = self.intent([{**reference, **mutation}])
                self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.get("/api/document-comments", params={"runId": REFERENCE_RUN_ID}).json()["comments"], [])

    def test_a_clarification_still_retains_submitted_document_words(self) -> None:
        saved = self.save(self.png, [stroke()])
        response = self.intent([page_ref(saved)], targetComponentId=None, elementId=None, utterance="please consider this drawing")
        self.assertEqual(response.status_code, 422, response.text)
        with TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))) as reopened:
            comments = reopened.get("/api/document-comments", params={"runId": REFERENCE_RUN_ID}).json()["comments"]
            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["utterance"], "please consider this drawing")
            self.assertEqual(comments[0]["documentAnnotations"], [page_ref(saved)])

    def test_scalar_clarification_carries_original_page_revision_without_resubmitting_it(self) -> None:
        saved = self.save(self.pdf, [stroke()], comment="这条蓝线是原始范围")
        reference = self.historical_reference()
        reference_page = {**self.query(reference), "revisionSha256": None}
        visuals = [page_visual(saved, size=(400, 300)), page_visual(reference_page, role="reference", annotated=False,
                                                                 color="red", note="保留所选参照")]
        questioner = scripted(status="question", question="What height?", component_id="portico", element_id="portico-base")
        self.app.state.intent_compiler = questioner
        first = self.intent([page_ref(saved)], documentVisuals=visuals, utterance="make the base a little taller")
        self.assertEqual(first.status_code, 422, first.text)
        token = first.json()["pendingIntent"]["continuationToken"]
        self.assertIsNotNone(token)
        self.save(self.pdf, [], base=saved["revisionSha256"])
        self.save(reference, [text_annotation()], comment="后续新写的参照意见")
        compiler = Scripted("set height to 0.8", "portico-base")
        self.app.state.intent_compiler = compiler
        second = self.intent([], utterance="please make it modestly taller", continuationToken=token)
        self.assertEqual(second.status_code, 201, second.text)
        original_visuals = questioner.calls[-1]["selection"].document_visuals
        resumed_visuals = compiler.calls[-1]["selection"].document_visuals
        self.assertEqual(resumed_visuals, original_visuals)
        self.assertEqual(resumed_visuals[0].context["revisionSha256"], saved["revisionSha256"])
        self.assertIsNone(resumed_visuals[1].context["revisionSha256"])
        self.assertIsNone(resumed_visuals[1].annotated_png)
        self.assertNotIn("后续新写的参照意见", compiler.calls[-1]["message"])
        self.assertIn("这条蓝线是原始范围", " ".join(second.json()["gestures"]))
        proposal = self.app.state.proposals.get(second.json()["proposal"]["proposalId"])
        comment = self.repository.load_json(proposal.document_comment_ref)
        self.assertEqual(comment["documentAnnotations"], [page_ref(saved)])
        self.assertEqual(comment["utterance"], "make the base a little taller\nArchitect's clarification: please make it modestly taller")

    def test_clarification_refuses_explicit_replacement_of_page_reference_or_pixels(self) -> None:
        saved = self.save(self.png, [stroke()])
        replacement = self.save(self.png, [], base=saved["revisionSha256"])
        reference = self.historical_reference()
        reference_visual = page_visual({**self.query(reference), "revisionSha256": None},
                                       role="reference", annotated=False, color="red")
        other_reference = self.upload(image_bytes(color="green"), "other-reference.png", "image/png")
        other_visual = page_visual({**self.query(other_reference), "revisionSha256": None},
                                   role="reference", annotated=False, color="green")
        contexts = (
            ("page revision", {"documentAnnotations": [page_ref(replacement)],
                               "documentVisuals": [page_visual(replacement, annotated=False), reference_visual]}),
            ("page pixels", {"documentVisuals": [page_visual(saved, color="green"), reference_visual]}),
            ("reference page", {"documentVisuals": [page_visual(saved), other_visual]}),
            ("clear visuals", {"documentVisuals": []}),
            ("clear page references", {"documentAnnotations": []}),
        )
        for replacement_kind, context in contexts:
            with self.subTest(replacement=replacement_kind):
                compiler = scripted(status="question", question="What height?", component_id="portico", element_id="portico-base")
                self.app.state.intent_compiler = compiler
                first = self.intent([page_ref(saved)], documentVisuals=[page_visual(saved), reference_visual],
                                    utterance="make the base a little taller")
                self.assertEqual(first.status_code, 422, first.text)
                token = first.json()["pendingIntent"]["continuationToken"]
                response = self.intent([], continuationToken=token, utterance="set height to 0.8", **context)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["code"], "DOCUMENT_CONTEXT_MISMATCH")
                self.assertEqual(len(compiler.calls), 1)

    def test_component_clarification_keeps_original_visuals_and_marked_reference_context(self) -> None:
        saved = self.save(self.png, [stroke()])
        reference = self.historical_reference()
        old_reference_comment = "旧参照意见：删除模型并保持所有构件"
        reference_saved = self.save(reference, [
            stroke("reference-keep", "keep"), stroke("reference-remove", "remove"),
            {**text_annotation(), "label": "参照页文字，只用于理解材料"},
        ], comment=old_reference_comment)
        visuals = [page_visual(saved), page_visual(reference_saved, role="reference", color="red", note="只参考材料")]
        questioner = scripted(status="question", question="How wide should the passage be?")
        self.app.state.intent_compiler = questioner
        first = self.intent([page_ref(saved)], documentVisuals=visuals, utterance="Add the passage wall.")
        self.assertEqual(first.status_code, 422, first.text)
        token = first.json()["pendingIntent"]["continuationToken"]
        self.assertIsNotNone(token)
        first_selection = questioner.calls[-1]["selection"]
        original_visuals = first_selection.document_visuals
        self.assertEqual({mark["kind"] for mark in original_visuals[1].context["annotationSummary"]},
                         {"keep", "remove", "text"})
        self.assertEqual(original_visuals[1].context["revisionSha256"], reference_saved["revisionSha256"])
        self.assertEqual((first_selection.component_id, first_selection.element_id), ("portico", "portico-base"))
        self.assertNotIn(old_reference_comment, questioner.calls[-1]["message"])
        self.assertNotIn(old_reference_comment, " ".join(first_selection.gestures))
        updated_primary = self.save(self.png, [], base=saved["revisionSha256"], comment="后续主图意见")
        updated_reference = self.save(reference, [], base=reference_saved["revisionSha256"], comment="后续参照意见")
        self.assertNotEqual(updated_primary["revisionSha256"], saved["revisionSha256"])
        self.assertNotEqual(updated_reference["revisionSha256"], reference_saved["revisionSha256"])
        semantic_edit = semantic_wall_edit()
        compiler = scripted(semantic_edit=semantic_edit, component_id="portico")
        self.app.state.intent_compiler = compiler
        second = self.intent([], utterance="Two metres.", continuationToken=token)
        self.assertEqual(second.status_code, 201, second.text)
        resumed_selection = compiler.calls[-1]["selection"]
        self.assertEqual(resumed_selection.document_visuals, original_visuals)
        self.assertNotIn(old_reference_comment, compiler.calls[-1]["message"])
        self.assertNotIn(old_reference_comment, " ".join(resumed_selection.gestures))
        self.assertEqual(second.json()["proposal"]["protected"], semantic_edit["protected"])
        self.assertEqual(second.json()["proposal"]["sourceRunId"], REFERENCE_RUN_ID)
        self.assertEqual(second.json()["proposal"]["modelSource"], self.model_source)
        proposal = self.app.state.proposals.get(second.json()["proposal"]["proposalId"])
        self.assertIsNotNone(proposal.state_record_operator)
        self.assertEqual(proposal.document_comment_ref.uri, second.json()["documentCommentRef"])
        comment = self.repository.load_json(proposal.document_comment_ref)
        self.assertEqual(comment["documentAnnotations"], [page_ref(saved)])
        self.assertEqual(len(comment["documentSources"]), 1)
        self.assertEqual(comment["documentSources"][0]["modelSource"], self.model_source)
        self.assertEqual([visual["revisionSha256"] for visual in comment["documentVisuals"]],
                         [saved["revisionSha256"], reference_saved["revisionSha256"]])
        self.assertEqual(comment["utterance"], "Add the passage wall.\nArchitect's clarification: Two metres.")

    def test_agent_semantic_answers_keep_document_context_with_and_without_a_model_target(self) -> None:
        saved = self.png
        ink = self.save(saved, [stroke()])
        for utterance, target, element in (("please consider this drawing", None, None), ("make the base a little taller", "portico", "portico-base")):
            with self.subTest(utterance=utterance):
                self.app.state.intent_compiler = scripted(semantic_edit=semantic_wall_edit(), component_id="portico")
                answer = self.intent([page_ref(ink)], utterance=utterance, targetComponentId=target, elementId=element)
                self.assertEqual(answer.status_code, 201, answer.text)
                proposal = self.app.state.proposals.get(answer.json()["proposal"]["proposalId"])
                self.assertIsNotNone(proposal.state_record_operator)
                self.assertEqual(proposal.document_comment_ref.uri, answer.json()["documentCommentRef"])
                self.assertEqual(self.repository.load_json(proposal.document_comment_ref)["documentAnnotations"], [page_ref(ink)])

    def test_page_text_edit_move_undo_redo_and_reopen_preserve_old_strokes_and_revision_bytes(self) -> None:
        original = self.save(self.pdf, [stroke()], page=1)
        records = self.repository.layout.run(REFERENCE_RUN_ID).records
        original_path = next(records.glob(f"*-{original['revisionSha256']}.json"))
        original_bytes = original_path.read_bytes()
        self.assertNotIn(b'"fontSize"', original_bytes)
        placed = self.save(self.pdf, [stroke(), text_annotation()], page=1, base=original["revisionSha256"])
        self.assertEqual(placed["annotations"][0], stroke())
        self.assertNotIn("fontSize", placed["annotations"][0])
        changed_text = {**text_annotation(), "points": [[0.63, 0.72]], "label": "修改后的第一行\n第二行\n第三行", "fontSize": 0.035}
        changed = self.save(self.pdf, [stroke(), changed_text], page=1, base=placed["revisionSha256"])
        stale = self.client.put("/api/document-annotations", json={
            "projectId": PROJECT_ID, **self.query(self.pdf, 1),
            "baseRevisionSha256": placed["revisionSha256"], "annotations": [stroke(), text_annotation()],
        })
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "ANNOTATION_STALE")
        undone = self.save(self.pdf, placed["annotations"], page=1, base=changed["revisionSha256"])
        self.assertEqual(undone["annotations"], placed["annotations"])
        redone = self.save(self.pdf, changed["annotations"], page=1, base=undone["revisionSha256"])
        erased = self.save(self.pdf, [stroke()], page=1, base=redone["revisionSha256"])
        restored = self.save(self.pdf, redone["annotations"], page=1, base=erased["revisionSha256"])
        with TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))) as reopened:
            self.assertEqual(reopened.get("/api/document-annotations", params=self.query(self.pdf, 1)).json(), restored)
            old = reopened.get("/api/document-annotations", params={**self.query(self.pdf, 1), "revisionSha256": original["revisionSha256"]})
            self.assertEqual(old.json(), original)
            self.assertEqual(reopened.get("/api/document-annotations", params=self.query(self.pdf)).json()["annotations"], [])
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(restored["annotations"], [stroke(), changed_text])

    def test_text_requires_content_one_anchor_and_its_own_font_size_without_extending_3d(self) -> None:
        for mutation in ({"label": None}, {"label": " \n "}, {"label": "x" * 2001}, {"fontSize": None}, {"fontSize": 0}, {"fontSize": 1.01}, {"points": [[0.1, 0.2], [0.2, 0.3]]}):
            with self.subTest(mutation=mutation):
                response = self.client.put("/api/document-annotations", json={
                    "projectId": PROJECT_ID, **self.query(self.png), "baseRevisionSha256": None,
                    "annotations": [{**text_annotation(), **mutation}],
                })
                self.assertEqual(response.status_code, 422, response.text)
        response = self.client.put("/api/document-annotations", json={
            "projectId": PROJECT_ID, **self.query(self.png), "baseRevisionSha256": None,
            "annotations": [{**stroke(), "fontSize": 0.024}],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/document-annotations", params=self.query(self.png)).json()["annotations"], [])
        old_3d = {**gesture("circle", hit("obj-portico-base")), "kind": "text"}
        self.assertEqual(self.intent([], gestures=[old_3d]).status_code, 422)


if __name__ == "__main__":
    unittest.main()
