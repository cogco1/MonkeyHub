"""Editable publication behavior through HTTP and real retained project files."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import base64
import unittest

from PIL import Image
from pptx import Presentation
from pypdf import PdfReader

from . import test_boards as board_helpers
from .test_documents import image_bytes, two_page_pdf
from .support import PROJECT_ID


def source(document, page=0):
    return {"runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document.get("revisionRef"), "pageIndex": page}


def page(identifier, title="Spatial sequence", document=None):
    elements = [{"id": identifier + "-title", "kind": "text", "x": 20, "y": 20,
                 "width": 520, "height": 60, "text": title, "fontSize": 20}]
    if document:
        elements.append({"id": identifier + "-image", "kind": "image", "x": 20, "y": 100,
                         "width": 520, "height": 260, "source": source(document)})
    return {"id": identifier, "elements": elements}


def request(pages, base=None, title="Design review"):
    return {"projectId": PROJECT_ID, "baseRevisionSha256": base, "title": title,
            "spec": {"width": 600, "height": 400, "template": "hero"}, "pages": pages}


class PublicationTests(unittest.TestCase):
    # Share the small real-project fixture without inheriting the Board suite.
    setUp = board_helpers.BoardTests.setUp
    new_client = board_helpers.BoardTests.new_client
    files = board_helpers.BoardTests.files
    upload = board_helpers.BoardTests.upload

    def save_publication(self, body):
        response = self.client.put("/api/publication", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def export(self, revision, format="pdf"):
        return self.client.post("/api/publication/export", json={
            "projectId": PROJECT_ID, "revisionSha256": revision, "format": format})

    def assert_design_unchanged(self):
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(self.repository.read_design_branches(), {})

    def test_empty_read_and_invalid_first_save_do_not_write(self):
        before = self.files()
        response = self.client.get("/api/publication")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["pages"], [])
        self.assertIsNone(response.json()["revisionSha256"])
        bad = page("outside")
        bad["elements"][0]["x"] = 590
        self.assertEqual(self.client.put("/api/publication", json=request([bad])).status_code, 422)
        self.assertEqual(self.files(), before)
        self.assert_design_unchanged()

    def test_order_text_and_exact_image_survive_reopen_and_idempotent_save(self):
        image = self.upload(image_bytes(), "section.png", "image/png")
        saved = self.save_publication(request([page("second", "Second first", image), page("first", "First last")]))
        before = self.files()
        reopened = self.new_client().get("/api/publication").json()
        self.assertEqual(reopened, saved)
        self.assertEqual([row["id"] for row in reopened["pages"]], ["second", "first"])
        self.assertEqual(reopened["pages"][0]["elements"][1]["source"], source(image))
        unchanged = self.save_publication(request(saved["pages"], saved["revisionSha256"]))
        self.assertEqual(unchanged, saved)
        self.assertEqual(self.files(), before)
        self.assert_design_unchanged()

    def test_concurrent_saves_have_one_winner_and_history_stays_readable(self):
        first = self.save_publication(request([page("p1")]))
        client_a, client_b = self.new_client(), self.new_client()
        bodies = [request([page("p1", title)], first["revisionSha256"]) for title in ("Option A", "Option B")]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda pair: pair[0].put("/api/publication", json=pair[1]), zip((client_a, client_b), bodies)))
        self.assertEqual(sorted(row.status_code for row in results), [200, 409])
        winner = next(row.json() for row in results if row.status_code == 200)
        self.assertEqual(self.new_client().get("/api/publication").json(), winner)
        self.assertEqual(self.client.get("/api/publication", params={"revision": first["revisionSha256"]}).json(), first)
        self.assert_design_unchanged()

    def test_cross_project_requests_and_unknown_revisions_are_rejected_without_writes(self):
        saved = self.save_publication(request([page("p1")]))
        before = self.files()
        bad = request([page("p1", "Overwrite")], saved["revisionSha256"]) | {"projectId": "other-project"}
        self.assertEqual(self.client.put("/api/publication", json=bad).status_code, 403)
        self.assertEqual(self.client.post("/api/publication/export", json={"projectId": "other-project",
            "revisionSha256": saved["revisionSha256"], "format": "pdf"}).status_code, 403)
        self.assertEqual(self.client.post("/api/publication/from-board", json={"projectId": "other-project",
            "baseRevisionSha256": saved["revisionSha256"], "boardRevisionSha256": "a" * 64, "elementIds": ["any"]}).status_code, 403)
        self.assertEqual(self.client.get("/api/publication", params={"revision": "b" * 64}).status_code, 404)
        self.assertEqual(self.export("b" * 64).status_code, 404)
        self.assertEqual(self.files(), before)

    def test_pdf_is_repeatable_and_pptx_has_editable_text_with_matching_page_order(self):
        image = self.upload(image_bytes(color="blue"), "section.png", "image/png")
        saved = self.save_publication(request([page("p2", "Review title\nEditable judgment", image), page("p1", "Second page")]))
        before = self.files()
        pdf = self.export(saved["revisionSha256"])
        self.assertEqual(pdf.status_code, 200, pdf.text[:200] if pdf.status_code != 200 else "")
        self.assertEqual(pdf.content, self.export(saved["revisionSha256"]).content)
        reader = PdfReader(BytesIO(pdf.content))
        self.assertEqual(len(reader.pages), 2)
        self.assertIn("Editable judgment", reader.pages[0].extract_text())
        self.assertIn("Second page", reader.pages[1].extract_text())
        self.assertEqual(tuple(map(float, reader.pages[0].mediabox)), (0, 0, 600, 400))
        ppt = self.export(saved["revisionSha256"], "pptx")
        self.assertEqual(ppt.status_code, 200)
        deck = Presentation(BytesIO(ppt.content))
        self.assertEqual(len(deck.slides), 2)
        title = next(shape for shape in deck.slides[0].shapes if shape.has_text_frame)
        self.assertEqual(title.text, "Review title\nEditable judgment")
        title.text_frame.paragraphs[1].text = "Changed in PowerPoint"
        edited = BytesIO(); deck.save(edited)
        self.assertIn("Changed in PowerPoint", Presentation(BytesIO(edited.getvalue())).slides[0].shapes[0].text)
        picture = next(shape for shape in deck.slides[0].shapes if hasattr(shape, "image"))
        with Image.open(BytesIO(picture.image.blob)) as pixels:
            self.assertEqual(pixels.getpixel((pixels.width // 2, pixels.height // 2))[:3], (0, 0, 255))
        self.assertEqual(self.files(), before, "exports do not mint project content or advance state")
        self.assert_design_unchanged()

    def test_replacement_marks_old_source_stale_and_freezing_preserves_original_export(self):
        original = self.upload(image_bytes(color="blue"), "section.png", "image/png")
        saved = self.save_publication(request([page("p1", document=original)]))
        baseline = self.export(saved["revisionSha256"]).content
        replacement = self.client.post("/api/documents", json={"projectId": PROJECT_ID, "fileName": "section-revised.png",
            "mimeType": "image/png", "contentBase64": base64.b64encode(image_bytes(color="red")).decode(),
            "replacesPages": [source(original) | {"newPageIndex": 0}]})
        self.assertEqual(replacement.status_code, 201, replacement.text)
        revised = replacement.json()
        stale = self.new_client().get("/api/publication").json()
        self.assertEqual(stale["sources"][0]["status"], "stale")
        self.assertEqual(stale["sources"][0]["replacement"], source(revised))
        self.assertEqual(stale["pages"], saved["pages"], "source changes must not silently rewrite composed pages")
        frozen_pages = deepcopy(saved["pages"])
        frozen_pages[0]["elements"][1]["frozen"] = True
        frozen = self.save_publication(request(frozen_pages, saved["revisionSha256"]))
        self.assertEqual(frozen["sources"][0]["status"], "frozen")
        self.assertEqual(self.export(frozen["revisionSha256"]).content, baseline)
        updated_pages = deepcopy(frozen_pages)
        updated_pages[0]["elements"][1].update(source=source(revised), frozen=False)
        updated = self.save_publication(request(updated_pages, frozen["revisionSha256"]))
        self.assertEqual(updated["sources"][0]["status"], "current")
        self.assertNotEqual(self.export(updated["revisionSha256"]).content, baseline)
        self.assert_design_unchanged()

    def test_missing_retained_source_allows_text_repair_but_blocks_export_until_restored(self):
        original = self.upload(image_bytes(), "section.png", "image/png")
        saved = self.save_publication(request([page("p1", document=original)]))
        digest = original["assetSha256"]
        blob = self.repository.layout.resolve_relative(f"objects/sha256/{digest[:2]}/{digest}")
        original_bytes = blob.read_bytes()
        blob.write_bytes(b"damaged retained file")
        missing = self.new_client().get("/api/publication")
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertEqual(missing.json()["sources"][0]["status"], "missing")
        edited = deepcopy(saved["pages"])
        edited[0]["elements"][0]["text"] = "Waiting for source recovery"
        repaired = self.save_publication(request(edited, saved["revisionSha256"]))
        self.assertEqual(self.export(repaired["revisionSha256"]).status_code, 409)
        blob.write_bytes(original_bytes)
        self.assertEqual(self.new_client().get("/api/publication").json()["sources"][0]["status"], "current")
        self.assertEqual(self.export(repaired["revisionSha256"]).status_code, 200)
        self.assert_design_unchanged()

    def test_unknown_image_or_pdf_page_cannot_be_introduced(self):
        document = self.upload(two_page_pdf())
        invalid = page("p1", document=document)
        invalid["elements"][1]["source"]["pageIndex"] = 2
        before = self.files()
        self.assertEqual(self.client.put("/api/publication", json=request([invalid])).status_code, 422)
        invalid["elements"][1]["source"].update(pageIndex=0, assetSha256="0" * 64)
        self.assertEqual(self.client.put("/api/publication", json=request([invalid])).status_code, 422)
        invalid["elements"][1]["source"] = source(document) | {"revisionRef": "unretained-revision"}
        self.assertEqual(self.client.put("/api/publication", json=request([invalid])).status_code, 422)
        self.assertEqual(self.files(), before)

    def test_board_selection_uses_named_saved_revision_and_frame_order_only(self):
        document = self.upload(two_page_pdf())
        later = board_helpers.image_element(document, 1, "later") | {"x": 30, "y": 300, "frameId": "review-frame"}
        earlier = board_helpers.image_element(document, 0, "earlier") | {"x": 30, "y": 30, "frameId": "review-frame"}
        unselected = board_helpers.image_element(document, 0, "unselected")
        removed = board_helpers.image_element(document, 0, "deleted") | {"isDeleted": True, "frameId": "review-frame"}
        frame = {"id": "review-frame", "type": "frame", "x": 0, "y": 0, "width": 800, "height": 700, "name": "Selected pages"}
        board = self.client.put("/api/board", json=board_helpers.body([later, unselected, frame, earlier, removed]))
        self.assertEqual(board.status_code, 200, board.text)
        revision = board.json()["revisionSha256"]
        changed = self.client.put("/api/board", json=board_helpers.body([unselected], revision))
        self.assertEqual(changed.status_code, 200, changed.text)
        appended = self.client.post("/api/publication/from-board", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": None, "boardRevisionSha256": revision, "elementIds": ["review-frame"]})
        self.assertEqual(appended.status_code, 200, appended.text)
        saved = appended.json()
        self.assertEqual(len(saved["pages"]), 2)
        sources = [element["source"] for row in saved["pages"] for element in row["elements"] if element["kind"] == "image"]
        self.assertEqual(sources, [source(document, 0), source(document, 1)])
        self.assertEqual(self.client.get("/api/board").json(), changed.json())
        self.assertEqual(self.new_client().get("/api/publication").json(), saved)
        before = self.files()
        invalid = self.client.post("/api/publication/from-board", json={"projectId": PROJECT_ID,
            "baseRevisionSha256": saved["revisionSha256"], "boardRevisionSha256": revision, "elementIds": ["not-saved"]})
        self.assertEqual(invalid.status_code, 409, invalid.text)
        self.assertEqual(self.files(), before)
        self.assertEqual(len(PdfReader(BytesIO(self.export(saved["revisionSha256"]).content)).pages), 2)
        self.assert_design_unchanged()

    def test_text_overflow_is_reported_for_both_formats_without_writes(self):
        content = page("p1", "This text cannot fit in a single short line")
        content["elements"][0].update(width=45, height=25, fontSize=20)
        saved = self.save_publication(request([content]))
        before = self.files()
        for format in ("pdf", "pptx"):
            result = self.export(saved["revisionSha256"], format)
            self.assertEqual(result.status_code, 422, result.text)
            self.assertIn("PUBLICATION_TEXT_OVERFLOW", result.text)
        self.assertEqual(self.files(), before)

    def test_image_crop_is_identical_in_pdf_and_pptx_and_preserves_source_bytes(self):
        pixels = Image.new("RGB", (200, 100), "red")
        pixels.paste("blue", (100, 0, 200, 100))
        original = BytesIO(); pixels.save(original, format="PNG")
        document = self.upload(original.getvalue(), "two-halves.png", "image/png")
        content = page("p1", document=document)
        content["elements"][1].update(crop=[.5, 0, 0, 0], x=20, y=100, width=400, height=200)
        saved = self.save_publication(request([content]))
        before = self.files()
        pdf = PdfReader(BytesIO(self.export(saved["revisionSha256"]).content))
        deck = Presentation(BytesIO(self.export(saved["revisionSha256"], "pptx").content))
        picture = next(shape for shape in deck.slides[0].shapes if hasattr(shape, "image"))
        exported_images = [pdf.pages[0].images[0].image, Image.open(BytesIO(picture.image.blob))]
        for image in exported_images:
            self.assertEqual(image.size, (100, 100))
            self.assertEqual(image.convert("RGB").getextrema(), ((0, 0), (0, 0), (255, 255)))
        self.assertEqual((picture.left.pt, picture.top.pt, picture.width.pt, picture.height.pt), (120, 100, 200, 200))
        self.assertEqual(self.client.get(f"/api/documents/{document['assetSha256']}/bytes", params={"runId": document["runId"]}).content, original.getvalue())
        self.assertEqual(self.files(), before)
