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
from archflow.project.record_kinds import STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectRecordRef, record_file_name
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application.artifacts import _work_copy, list_document_work_copies, list_documents
from archflow_studio_api.application.binding import bound_project
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


def sized_pdf(*sizes: tuple[float, float]) -> bytes:
    writer = PdfWriter()
    for width, height in sizes:
        writer.add_blank_page(width=width, height=height)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def replacement_page(document: dict, page_index: int = 0, new_page_index: int = 0) -> dict:
    return {"runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": page_index, "newPageIndex": new_page_index}


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

    def upload(self, data: bytes, name: str = "研究图纸.pdf", mime: str = "application/pdf",
               run_id: str | None = REFERENCE_RUN_ID, **extra):
        return self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "runId": run_id, "fileName": name,
            "mimeType": mime, "contentBase64": base64.b64encode(data).decode("ascii"),
            **extra,
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
        self.assertEqual(document["replacesPages"], [])
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

    def test_explicit_replacements_cross_runs_and_reorder_pdf_pages_after_restart(self) -> None:
        before = self.repository.read_head()
        original = self.upload(sized_pdf((400, 300), (300, 200), (500, 600))).json()
        updated_bytes = sized_pdf((1000, 1200), (800, 600))
        mapping = [replacement_page(original, 2, 0), replacement_page(original, 0, 1)]
        response = self.upload(updated_bytes, "updated.pdf", run_id=None, replacesPages=mapping)
        self.assertEqual(response.status_code, 201, response.text)
        updated = response.json()
        self.assertEqual(updated["runId"], "studio-documents")
        self.assertEqual(updated["replacesPages"], mapping)
        self.assertEqual(self.upload(updated_bytes, "retry.pdf", run_id=None, replacesPages=list(reversed(mapping))).json(), updated)
        self.assertEqual(self.upload(updated_bytes, run_id=None).json(), updated)
        reopened = self.new_client()
        self.assertCountEqual(reopened.get("/api/documents").json()["documents"], [original, updated])
        self.assertEqual(reopened.get(f"/api/documents/{updated['assetSha256']}/bytes",
                                      params={"runId": updated["runId"]}).content, updated_bytes)
        retained = self.repository.list_json(
            run=self.repository.load_run("studio-documents"),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="studio-documents"),
        )
        self.assertEqual(len(retained), 1, "Retries reuse the same source registration.")
        self.assertEqual(retained[0].record_kind, "studio-source-document")
        self.assertEqual(self.repository.read_head(), before)

    def test_invalid_replacements_do_not_create_a_run_object_or_registration(self) -> None:
        original = self.upload(image_bytes(), "original.png", "image/png").json()
        mapping = replacement_page(original)
        new_bytes = image_bytes(color="red")
        invalid_mappings = [
            [{**mapping, "runId": "missing-run"}],
            [{**mapping, "assetSha256": "0" * 64}],
            [{**mapping, "revisionRef": "project://wrong/records/revision.json"}],
            [{**mapping, "pageIndex": 1}],
            [{**mapping, "newPageIndex": 1}],
            [mapping, mapping],
            [{**mapping, "pageIndex": True}],
            [{**mapping, "newPageIndex": -1}],
        ]
        runs_before = sorted(path.name for path in self.repository.layout.runs.iterdir())
        for replacements in invalid_mappings:
            with self.subTest(replacements=replacements):
                response = self.upload(new_bytes, "updated.png", "image/png", None, replacesPages=replacements)
                self.assertIn(response.status_code, (404, 422), response.text)
        self_reference = self.upload(image_bytes(), "self.png", "image/png", replacesPages=[mapping])
        self.assertEqual(self_reference.status_code, 422, self_reference.text)
        wrong_ratio = self.upload(sized_pdf((400, 300)), run_id=None, replacesPages=[mapping])
        self.assertEqual(wrong_ratio.status_code, 422, wrong_ratio.text)
        digest = hashlib.sha256(new_bytes).hexdigest()
        self.assertFalse((self.repository.layout.objects / digest[:2] / digest).exists())
        self.assertEqual(sorted(path.name for path in self.repository.layout.runs.iterdir()), runs_before)
        self.assertEqual(self.client.get("/api/documents").json()["documents"], [original])

    def test_replacement_requires_readable_original_bytes_before_any_write(self) -> None:
        original = self.upload(image_bytes(), "original.png", "image/png").json()
        digest = original["assetSha256"]
        original_path = self.repository.layout.objects / digest[:2] / digest
        original_path.write_bytes(b"corrupt")
        for expected in ("DOCUMENT_DIGEST_MISMATCH", "DOCUMENT_UNAVAILABLE"):
            response = self.upload(image_bytes(color="red"), "updated.png", "image/png", None,
                                   replacesPages=[replacement_page(original)])
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], expected)
            self.assertFalse((self.repository.layout.runs / "studio-documents").exists())
            if original_path.exists():
                original_path.unlink()

    def test_replacement_mapping_is_immutable_and_competing_successors_are_refused(self) -> None:
        original = self.upload(image_bytes(), "original.png", "image/png").json()
        other = self.upload(image_bytes(color="yellow"), "other.png", "image/png").json()
        updated_bytes = image_bytes(color="red")
        updated = self.upload(updated_bytes, "updated.png", "image/png", None,
                              replacesPages=[replacement_page(original)]).json()
        rebound = self.upload(updated_bytes, "updated.png", "image/png", None,
                              replacesPages=[replacement_page(other)])
        self.assertEqual(rebound.status_code, 409, rebound.text)
        self.assertEqual(rebound.json()["code"], "DOCUMENT_SOURCE_IMMUTABLE")
        unmapped_rebind = self.upload(image_bytes(color="yellow"), "other.png", "image/png",
                                      replacesPages=[replacement_page(original)])
        self.assertEqual(unmapped_rebind.status_code, 409, unmapped_rebind.text)
        competing = self.upload(image_bytes(color="green"), "competing.png", "image/png", None,
                                replacesPages=[replacement_page(original)])
        self.assertEqual(competing.status_code, 409, competing.text)
        self.assertEqual(competing.json()["code"], "DOCUMENT_REPLACEMENT_CONFLICT")
        self.assertEqual(len(self.client.get("/api/documents").json()["documents"]), 3)
        next_revision = self.upload(image_bytes(color="green"), "next.png", "image/png", None,
                                    replacesPages=[replacement_page(updated)])
        self.assertEqual(next_revision.status_code, 201, next_revision.text)

    def test_replacements_use_visible_crop_rotation_and_aspect_ratio_tolerance(self) -> None:
        original = self.upload(two_page_pdf()).json()
        mapped = self.upload(sized_pdf((1000, 1200)), run_id=None,
                             replacesPages=[replacement_page(original, 1)])
        self.assertEqual(mapped.status_code, 201, mapped.text)
        landscape = self.upload(sized_pdf((1200, 800))).json()
        outside = self.upload(sized_pdf((15011, 10000)), run_id=None,
                              replacesPages=[replacement_page(landscape)])
        self.assertEqual(outside.status_code, 422, outside.text)
        inside = self.upload(sized_pdf((15009, 10000)), run_id=None,
                             replacesPages=[replacement_page(landscape)])
        self.assertEqual(inside.status_code, 201, inside.text)

    def test_encrypted_pdf_is_refused_before_it_is_retained(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=300)
        writer.encrypt("private")
        output = BytesIO()
        writer.write(output)
        response = self.upload(output.getvalue())
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "DOCUMENT_ENCRYPTED")

    def work_copy(self, document: dict, *, revision_ref: str | None = "document"):
        body = {"projectId": PROJECT_ID, "runId": document["runId"],
                "revisionRef": document["revisionRef"] if revision_ref == "document" else revision_ref}
        return self.client.post(f"/api/documents/{document['assetSha256']}/work-copy", json=body)

    def test_work_copy_is_explicit_seeded_once_and_never_overwrites_an_edit(self) -> None:
        before = self.repository.read_head()
        original = self.upload(image_bytes(), "plan.png", "image/png").json()
        # Nothing exists until it is asked for: registering a document, and
        # listing them, materialises no editable file anywhere.
        work_root = self.repository.layout.run(REFERENCE_RUN_ID).workspaces / "studio-documents" / "work"
        self.assertFalse(work_root.exists())
        self.assertEqual(self.client.get("/api/documents").status_code, 200)
        self.assertFalse(work_root.exists())

        response = self.work_copy(original)
        self.assertEqual(response.status_code, 201, response.text)
        copy = response.json()
        self.assertEqual(copy["relativePath"],
                         f"runs/{REFERENCE_RUN_ID}/workspaces/studio-documents/work/{original['assetSha256']}/plan.png")
        self.assertEqual((copy["runId"], copy["assetSha256"], copy["revisionRef"], copy["pageIndex"]),
                         (REFERENCE_RUN_ID, original["assetSha256"], None, 0))
        self.assertEqual((copy["headRunId"], copy["headAssetSha256"], copy["headPageIndex"]),
                         (REFERENCE_RUN_ID, original["assetSha256"], 0))
        path = self.repository.layout.root / Path(*copy["relativePath"].split("/"))
        self.assertEqual(path.read_bytes(), image_bytes())

        # An architect's own edit is theirs. Asking again answers with the same
        # file, untouched, rather than restoring the registered bytes over it.
        edited = image_bytes(color="red")
        path.write_bytes(edited)
        again = self.work_copy(original)
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.json(), copy)
        self.assertEqual(path.read_bytes(), edited)
        # Registered bytes and canonical position are untouched throughout.
        self.assertEqual(self.client.get(f"/api/documents/{original['assetSha256']}/bytes",
                                         params={"runId": REFERENCE_RUN_ID}).content, image_bytes())
        self.assertEqual(self.repository.read_head(), before)

    def test_work_copy_names_one_exact_registration_and_refuses_the_rest(self) -> None:
        image = self.upload(image_bytes(), "plan.png", "image/png").json()
        # A second registration is present throughout: none of the refusals
        # below may be answered by simply finding the other document.
        self.upload(two_page_pdf())
        # A revisionRef is part of the identity, not a filter: naming one this
        # registration does not carry selects nothing rather than the newest
        # other registration of the same bytes.
        mismatched = self.work_copy(image, revision_ref="some-drawing-revision")
        self.assertEqual(mismatched.status_code, 404, mismatched.text)
        self.assertEqual(self.client.post(f"/api/documents/{'0' * 64}/work-copy", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID}).status_code, 404)
        self.assertEqual(self.client.post("/api/documents/not-a-digest/work-copy", json={
            "projectId": PROJECT_ID, "runId": REFERENCE_RUN_ID}).status_code, 422)
        self.assertEqual(self.client.post(f"/api/documents/{image['assetSha256']}/work-copy", json={
            "projectId": "another-project", "runId": REFERENCE_RUN_ID}).status_code, 403)
        self.assertFalse((self.repository.layout.run(REFERENCE_RUN_ID).workspaces / "studio-documents").exists())

    def test_work_copy_seeds_from_the_page_its_origin_now_answers_for(self) -> None:
        original = self.upload(image_bytes(), "plan.png", "image/png").json()
        replacement_bytes = image_bytes(color="red")
        self.upload(replacement_bytes, "plan.png", "image/png", None,
                    replacesPages=[replacement_page(original)])
        # The copy is still identified by the page the architect placed, but it
        # is seeded from what that page has become.
        copy = self.work_copy(original).json()
        self.assertEqual(copy["assetSha256"], original["assetSha256"])
        self.assertEqual(copy["headAssetSha256"], hashlib.sha256(replacement_bytes).hexdigest())
        path = self.repository.layout.root / Path(*copy["relativePath"].split("/"))
        self.assertEqual(path.read_bytes(), replacement_bytes)

    def test_work_copy_of_a_name_p036_cannot_hold_is_named_after_its_page(self) -> None:
        document = self.upload(image_bytes(), "研究图纸.png", "image/png").json()
        copy = self.work_copy(document).json()
        self.assertEqual(copy["fileName"], "研究图纸.png")
        self.assertTrue(copy["relativePath"].endswith(f"/{document['assetSha256'][:32]}.png"), copy["relativePath"])
        self.assertTrue((self.repository.layout.root / Path(*copy["relativePath"].split("/"))).is_file())

    def test_work_copy_null_registration_does_not_read_or_bind_a_drawing_with_equal_bytes(self) -> None:
        data = image_bytes()
        original = self.upload(data, "plan.png", "image/png").json()
        binding = bound_project(self.client.app.state)
        run = binding.load_run(REFERENCE_RUN_ID)
        original_payload = next(self.repository.load_json(ref) for ref in binding.record_refs(run.run_id)
                                if ref.record_kind == STUDIO_SOURCE_DOCUMENT)
        # Two retained drawing registrations have the same pixels as the plain
        # upload. Their referenced drawings are unavailable: selecting the
        # valid plain upload must neither read them nor opt them into watching.
        for revision_run in ("drawing-a", "drawing-b"):
            revision = ProjectRecordRef(
                PROJECT_ID, f"runs/{revision_run}/records/{record_file_name('drawing-projection-receipt', 'a' * 64)}",
                "a" * 64,
            )
            self.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                record_kind=STUDIO_SOURCE_DOCUMENT,
                payload={**original_payload, "revisionRef": revision.uri, "drawingId": "plan",
                         "generatedAt": "2026-09-01T00:00:00Z"},
            )
        documents = list_documents(binding, run.run_id)
        self.assertEqual(len(documents), 3)
        # Full revision identity includes its run, even if both receipt names
        # and content digests happen to be the same.
        copies = [_work_copy(binding, document, {}, documents) for document in documents]
        self.assertEqual(len({copy.relative_path for copy in copies}), 3)

        opened = self.work_copy(original, revision_ref=None)
        self.assertEqual(opened.status_code, 201, opened.text)
        path = self.repository.layout.root / Path(*opened.json()["relativePath"].split("/"))
        self.assertEqual(path.read_bytes(), data)
        watched = list_document_work_copies(binding)
        self.assertEqual(len(watched), 1)
        self.assertIsNone(watched[0].revision_ref)
        # The exact unavailable drawing remains refused by its original reader.
        drawing = next(document for document in documents if document.revision_ref is not None)
        refused = self.work_copy(original, revision_ref=drawing.revision_ref)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "DOCUMENT_UNAVAILABLE")

    def test_work_copy_refuses_a_pdf_replacement_before_creating_an_image_file(self) -> None:
        original = self.upload(image_bytes(), "plan.png", "image/png").json()
        replacement = self.upload(sized_pdf((120, 80)), "plan.pdf", "application/pdf", None,
                                  replacesPages=[replacement_page(original)])
        self.assertEqual(replacement.status_code, 201, replacement.text)
        opened = self.work_copy(original)
        self.assertEqual(opened.status_code, 422, opened.text)
        self.assertEqual(opened.json()["code"], "DOCUMENT_NOT_EDITABLE")
        work_root = self.repository.layout.run(REFERENCE_RUN_ID).workspaces / "studio-documents" / "work"
        self.assertFalse(work_root.exists())

    @staticmethod
    def pdf_pages(*sizes: tuple[float, float], title: str = "plan") -> bytes:
        """A PDF with the given page sizes whose bytes change with ``title``."""

        writer = PdfWriter()
        for width, height in sizes:
            writer.add_blank_page(width=width, height=height)
        writer.add_metadata({"/Title": title})
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    @staticmethod
    def portrait_png(color: str = "red") -> bytes:
        """A 3:4 PNG: the visible ratio of the second page used below."""

        output = BytesIO()
        Image.new("RGB", (300, 400), color).save(output, format="PNG")
        return output.getvalue()

    def test_pdf_work_copy_covers_the_whole_document(self) -> None:
        first = self.pdf_pages((400, 300), (300, 400), title="first")
        original = self.upload(first, "plan.pdf").json()
        self.assertEqual(original["pageCount"], 2)
        response = self.work_copy(original)
        self.assertEqual(response.status_code, 201, response.text)
        copy = response.json()
        self.assertEqual((copy["mimeType"], copy["pageCount"], copy["pageIndex"]),
                         ("application/pdf", 2, 0))
        self.assertEqual(copy["relativePath"],
                         f"runs/{REFERENCE_RUN_ID}/workspaces/studio-documents/work/{original['assetSha256']}/plan.pdf")
        path = self.repository.layout.root / Path(*copy["relativePath"].split("/"))
        self.assertEqual(path.read_bytes(), first)
        # Asking again answers with the same copy, untouched.
        edited = self.pdf_pages((400, 300), (300, 400), title="edited")
        path.write_bytes(edited)
        again = self.work_copy(original).json()
        self.assertEqual(again["relativePath"], copy["relativePath"])
        self.assertEqual(path.read_bytes(), edited)

    def test_non_portable_pdf_name_gets_a_pdf_segment(self) -> None:
        original = self.upload(self.pdf_pages((400, 300)), "研究图纸.pdf").json()
        copy = self.work_copy(original).json()
        self.assertTrue(copy["relativePath"].endswith(f"/{original['assetSha256'][:32]}.pdf"), copy["relativePath"])
        self.assertTrue((self.repository.layout.root / Path(*copy["relativePath"].split("/"))).is_file())

    def test_a_pdf_whose_page_was_replaced_individually_is_not_editable(self) -> None:
        original = self.upload(self.pdf_pages((400, 300), (300, 400))).json()
        # Replace page 1 alone with an image of the same visible aspect ratio.
        replacement = self.upload(self.portrait_png(), "page-two.png", "image/png", run_id=None,
                                  replacesPages=[replacement_page(original, 1, 0)])
        self.assertEqual(replacement.status_code, 201, replacement.text)
        response = self.work_copy(original)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "DOCUMENT_NOT_EDITABLE")
        # A document whose pages no longer travel together is watched nowhere.
        self.assertEqual(list_document_work_copies(bound_project(self.client.app.state)), ())
        work_root = self.repository.layout.run(REFERENCE_RUN_ID).workspaces / "studio-documents" / "work"
        self.assertFalse(work_root.exists())

    def test_pdf_work_copy_registration_replaces_every_page(self) -> None:
        original = self.upload(self.pdf_pages((400, 300), (300, 400), title="first"), "plan.pdf").json()
        copy = self.work_copy(original).json()
        edited = self.pdf_pages((400, 300), (300, 400), title="second")
        registered = self.upload(edited, "plan.pdf", run_id=None,
                                 replacesPages=[replacement_page(original, 0, 0), replacement_page(original, 1, 1)])
        self.assertEqual(registered.status_code, 201, registered.text)
        document = registered.json()
        self.assertEqual(sorted((page["pageIndex"], page["newPageIndex"]) for page in document["replacesPages"]),
                         [(0, 0), (1, 1)])
        # The copy keeps its own identity and now answers for the new document,
        # page for page.
        head = self.work_copy(original).json()
        self.assertEqual(head["relativePath"], copy["relativePath"])
        self.assertEqual((head["headAssetSha256"], head["headPageIndex"], head["pageCount"]),
                         (document["assetSha256"], 0, 2))

    def test_a_replacement_answering_for_several_pages_must_answer_for_all_of_them(self) -> None:
        original = self.upload(self.pdf_pages((400, 300), (300, 400), title="first"), "plan.pdf").json()
        grown = self.pdf_pages((400, 300), (300, 400), (400, 300), title="three")
        response = self.upload(grown, "plan.pdf", run_id=None,
                               replacesPages=[replacement_page(original, 0, 0), replacement_page(original, 1, 1)])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "DOCUMENT_REPLACEMENT_INVALID")
        self.assertEqual([row["assetSha256"] for row in self.client.get("/api/documents").json()["documents"]],
                         [original["assetSha256"]])
        # One page answered for on its own may still come from any page of a
        # longer file: that is the Board's own replacement dialog.
        picked = self.upload(self.pdf_pages((400, 300), (300, 400)), "picked.pdf", run_id=None,
                             replacesPages=[replacement_page(original, 1, 1)])
        self.assertEqual(picked.status_code, 201, picked.text)

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
