"""Real PDF/image source bytes are retained separately from model exports."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project


def two_page_pdf() -> bytes:
    writer = PdfWriter()
    for width, height in ((400, 300), (800, 600)):
        page = writer.add_blank_page(width=width, height=height)
        content = DecodedStreamObject()
        content.set_data(b"0 0 1 RG 4 w 120 80 220 160 re S\n")
        page[NameObject("/Contents")] = writer._add_object(content)
    writer.pages[1].cropbox = RectangleObject([100, 50, 700, 550])
    writer.pages[1].rotate(90)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def image_bytes(format: str = "PNG", *, color: str = "blue", orientation: int | None = None) -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (120, 80), color)
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    image.save(output, format=format, **options)
    return output.getvalue()


class SourceDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-documents-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.client = self.new_client()

    def new_client(self) -> TestClient:
        client = TestClient(create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off")))
        self.addCleanup(client.close)
        return client

    def upload(self, data: bytes, name: str = "研究图纸.pdf", mime: str = "application/pdf", run_id: str = REFERENCE_RUN_ID):
        return self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "runId": run_id, "fileName": name,
            "mimeType": mime, "contentBase64": base64.b64encode(data).decode("ascii"),
        })

    def test_real_pdf_pages_crop_and_rotation_are_read_from_retained_original(self) -> None:
        data = two_page_pdf()
        before = self.repository.read_head()
        response = self.upload(data)
        self.assertEqual(response.status_code, 201, response.text)
        document = response.json()
        self.assertEqual(document["pageCount"], 2)
        self.assertEqual(document["pages"], [
            {"pageIndex": 0, "width": 400, "height": 300, "rotation": 0},
            {"pageIndex": 1, "width": 500, "height": 600, "rotation": 90},
        ])
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(document["assetSha256"], digest)
        self.assertEqual(document["sizeBytes"], len(data))
        reopened = self.new_client()
        response = reopened.get(f"/api/documents/{digest}/bytes", params={"runId": REFERENCE_RUN_ID})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, data)
        self.assertTrue(response.headers["content-type"].startswith("application/pdf"))
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertEqual(response.headers["etag"], f'"{digest}"')
        self.assertEqual(len(PdfReader(BytesIO(response.content)).pages), 2)
        self.assertEqual(reopened.get("/api/documents", params={"runId": REFERENCE_RUN_ID}).json()["documents"], [document])
        self.assertEqual(reopened.get("/api/artifacts").json()["artifacts"], [])
        self.assertEqual(self.repository.read_head(), before)

    def test_png_and_exif_oriented_jpeg_keep_their_original_bytes(self) -> None:
        for format, mime, orientation, size in (("PNG", "image/png", None, (120, 80)), ("JPEG", "image/jpeg", 6, (80, 120))):
            with self.subTest(format=format):
                data = image_bytes(format, orientation=orientation)
                response = self.upload(data, f"image.{format.lower()}", mime)
                self.assertEqual(response.status_code, 201, response.text)
                document = response.json()
                self.assertEqual(document["pages"], [{"pageIndex": 0, "width": size[0], "height": size[1], "rotation": 0}])
                self.assertEqual(self.client.get(f"/api/documents/{document['assetSha256']}/bytes", params={"runId": REFERENCE_RUN_ID}).content, data)

    def test_content_version_not_file_name_and_import_is_idempotent(self) -> None:
        first = self.upload(image_bytes(), "same.png", "image/png").json()
        repeated = self.upload(image_bytes(), "same.png", "image/png").json()
        changed = self.upload(image_bytes(color="red"), "same.png", "image/png").json()
        self.assertEqual(first, repeated)
        self.assertNotEqual(first["assetSha256"], changed["assetSha256"])
        self.assertEqual(len(self.client.get("/api/documents", params={"runId": REFERENCE_RUN_ID}).json()["documents"]), 2)

    def test_project_listing_keeps_same_content_in_each_storage_run_after_reopen(self) -> None:
        before = self.repository.read_head()
        first = self.upload(image_bytes(), "same.png", "image/png").json()
        self.repository.create_run("other-run")
        second = self.upload(image_bytes(), "same.png", "image/png", "other-run").json()
        self.assertEqual(first["assetSha256"], second["assetSha256"])
        reopened = self.new_client()
        response = reopened.get("/api/documents")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["runId"])
        self.assertCountEqual(response.json()["documents"], [first, second])
        self.assertEqual(reopened.get("/api/documents", params={"runId": REFERENCE_RUN_ID}).json()["documents"], [first])
        self.assertEqual(reopened.get("/api/documents", params={"runId": "missing-run"}).status_code, 404)
        self.assertEqual(self.repository.read_head(), before)

    def test_upload_without_run_works_before_any_model_or_stage_and_reopens(self) -> None:
        empty_dir = self.root / "empty" / PROJECT_ID
        repository = FilesystemProjectRepository.initialize(
            empty_dir, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        before = repository.read_head()
        client = TestClient(create_app(StudioSettings(project_dir=empty_dir, cad_export="off")))
        self.addCleanup(client.close)
        body = {"projectId": PROJECT_ID, "fileName": "参考图纸.pdf", "mimeType": "application/pdf", "contentBase64": "YnJva2Vu"}
        self.assertEqual(client.get("/api/documents").json()["documents"], [])
        self.assertEqual(client.post("/api/documents", json=body).status_code, 422)
        self.assertEqual(list(repository.layout.runs.iterdir()), [])
        data = two_page_pdf()
        body["contentBase64"] = base64.b64encode(data).decode("ascii")
        self.assertEqual(client.post("/api/documents", json={**body, "runId": "missing-run"}).status_code, 404)
        self.assertEqual(list(repository.layout.runs.iterdir()), [])
        uploaded = client.post("/api/documents", json=body)
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document = uploaded.json()
        self.assertEqual(document["runId"], "studio-documents")
        self.assertEqual(document["pageCount"], 2)
        self.assertIsNone(document["modelSource"])
        self.assertIsNone(document["sourceStageRef"])
        reopened = TestClient(create_app(StudioSettings(project_dir=empty_dir, cad_export="off")))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get("/api/documents").json()["documents"], [document])
        self.assertEqual(reopened.post("/api/documents", json=body).json(), document)
        self.assertEqual(reopened.get(f"/api/documents/{document['assetSha256']}/bytes", params={"runId": document["runId"]}).content, data)
        run = repository.load_run(document["runId"])
        self.assertEqual(run.base, before)
        refs = repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id))
        self.assertEqual([ref.record_kind for ref in refs], ["studio-source-document"])
        self.assertEqual(repository.read_design_branches(), {})
        self.assertEqual(repository.read_head(), before)

    def test_wrong_project_missing_run_paths_and_invalid_bytes_are_refused(self) -> None:
        for data, name, mime in ((b"%PDF-not-complete", "broken.pdf", "application/pdf"), (image_bytes()[:-8], "broken.png", "image/png"), (image_bytes(), "fake.pdf", "application/pdf"), (b"junk", "broken.jpg", "image/jpeg"), (image_bytes(), "../private.png", "image/png"), (image_bytes(), "C:\\private.png", "image/png")):
            with self.subTest(name=name):
                self.assertEqual(self.upload(data, name, mime).status_code, 422)
        self.assertEqual(self.upload(two_page_pdf(), run_id="missing-run").status_code, 404)
        response = self.client.post("/api/documents", json={
            "projectId": "other-project", "runId": REFERENCE_RUN_ID, "fileName": "a.pdf",
            "mimeType": "application/pdf", "contentBase64": base64.b64encode(two_page_pdf()).decode(),
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "PROJECT_MISMATCH")
        self.assertEqual(self.client.get("/api/documents", params={"runId": REFERENCE_RUN_ID}).json()["documents"], [])

    def test_encrypted_pdf_is_refused_before_it_is_retained(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=300)
        writer.encrypt("private")
        output = BytesIO()
        writer.write(output)
        response = self.upload(output.getvalue())
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "DOCUMENT_ENCRYPTED")

    def test_unregistered_digest_cannot_read_an_object_and_tampering_is_named(self) -> None:
        document = self.upload(image_bytes(), "a.png", "image/png").json()
        digest = document["assetSha256"]
        other = self.repository.create_run("other-run")
        self.assertEqual(self.client.get(f"/api/documents/{digest}/bytes", params={"runId": other.run_id}).status_code, 404)
        path = self.repository.layout.objects / digest[:2] / digest
        path.write_bytes(image_bytes(color="red"))
        response = self.client.get(f"/api/documents/{digest}/bytes", params={"runId": REFERENCE_RUN_ID})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "DOCUMENT_DIGEST_MISMATCH")
        path.unlink()
        response = self.client.get(f"/api/documents/{digest}/bytes", params={"runId": REFERENCE_RUN_ID})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "DOCUMENT_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
