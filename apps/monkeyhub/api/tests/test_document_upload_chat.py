"""A chat registers an image or a PDF it was given as a project document (gap 37).

A Board image names a registered document, and the chat could not register one: video frames and a
presentation's pages stayed off the Board. The chat now sends one attachment by its id; the Hub moves the
bytes, and only a PNG, a JPEG or a PDF, checked by its content, becomes a document.
"""
import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from monkeyhub_api.chat import media, preparation, tool_calls, transport
from monkeyhub_api.models import HubFailure

CHAT = "11111111-1111-4111-8111-111111111111"
ATTACHMENT = "22222222-2222-4222-8222-222222222222"


def png() -> bytes:
    from PIL import Image
    out = BytesIO()
    Image.new("RGB", (4, 3), "red").save(out, format="PNG")
    return out.getvalue()


class DocumentUploadChatTests(unittest.TestCase):
    def setUp(self):
        self.session = {"projectId": "demo", "projectDir": "C:/projects/demo", "status": "running"}
        binding = patch.object(preparation, "_bound_studio", return_value=("http://127.0.0.1:8791", self.session))
        binding.start()
        self.addCleanup(binding.stop)

    def test_an_image_attachment_becomes_a_project_document(self):
        calls = []
        source = {"fileName": "frame-0295.png", "mimeType": "image/png", "contentBase64": "aW1hZ2U="}

        def request(base, path, method="GET", body=None, **kwargs):
            calls.append((base, path, method, body))
            return source if path.endswith("/document-source") else {"assetSha256": "a" * 64}

        with patch.object(transport, "_request_json", side_effect=request):
            result = tool_calls.call_tool("http://127.0.0.1:8790", CHAT, "studio_request", {
                "method": "POST", "path": "/api/documents", "body": {"attachmentId": ATTACHMENT}})
        self.assertEqual(result["assetSha256"], "a" * 64)
        self.assertIn(f"/sessions/{CHAT}/attachments/{ATTACHMENT}/document-source", calls[0][1])
        self.assertIn("/studio/api/documents", calls[1][1])
        self.assertEqual(calls[1][3], {"projectId": "demo", **source})

    def test_a_document_is_named_by_one_attachment_never_by_its_bytes(self):
        for body in ({"attachmentId": ATTACHMENT, "contentBase64": "aW1hZ2U="}, {"fileName": "a.png"}, {}):
            with self.subTest(body=body), self.assertRaises(HubFailure) as caught:
                tool_calls.call_tool("http://127.0.0.1:8790", CHAT, "studio_request", {
                    "method": "POST", "path": "/api/documents", "body": body})
            self.assertEqual(caught.exception.error.code, "CHAT_DOCUMENT_INVALID")

    def test_the_schema_offers_an_attachment_and_no_base64(self):
        document = {"paths": {"/api/documents": {"post": {"requestBody": {"content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/DocumentUploadDto"}}}}}}},
            "components": {"schemas": {"DocumentUploadDto": {"properties": {
                "projectId": {"type": "string"}, "fileName": {"type": "string"}, "mimeType": {"type": "string"},
                "contentBase64": {"type": "string"}}, "required": ["projectId", "fileName", "mimeType", "contentBase64"]}}}}
        with patch.object(transport, "_request_json", return_value=document):
            result = tool_calls.call_tool("http://127.0.0.1:8790", CHAT, "studio_schema", {
                "method": "POST", "path": "/api/documents"})
        schema = result["components"]["schemas"]["DocumentUploadDto"]
        self.assertEqual(set(schema["properties"]), {"attachmentId"})
        self.assertEqual(schema["required"], ["attachmentId"])

    def test_only_a_png_a_jpeg_or_a_pdf_by_its_content_is_a_document_source(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        cases = (("frame.png", "image/png", png(), True), ("slides.pdf", "application/pdf", b"%PDF-1.7\n...", True),
                 ("notes.txt", "text/plain", b"hello", False), ("fake.png", "image/png", b"not a png", False),
                 ("fake.pdf", "application/pdf", b"hello", False))
        for name, mime, data, ok in cases:
            path = root / name
            path.write_bytes(data)
            attachment = SimpleNamespace(name=name, mimeType=mime)
            with self.subTest(name=name):
                if ok:
                    source = media.document_source(attachment, path)
                    self.assertEqual((source["fileName"], source["mimeType"]), (name, mime))
                    self.assertEqual(base64.b64decode(source["contentBase64"]), data)
                else:
                    with self.assertRaises(HubFailure) as caught:
                        media.document_source(attachment, path)
                    self.assertEqual(caught.exception.error.code, "CHAT_DOCUMENT_SOURCE_INVALID")
