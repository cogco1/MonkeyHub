"""A render discussion from Board reaches the Hub's own chat turn bound to exact pages (#253).

Real P036 projects and the real Studio document and render routes; the CLI is
the local fake shared with test_chat.py and the image provider is a labelled
fake adapter. Nothing here calls a model or a paid image service, so these
tests prove binding, refusal, routing and request identity, not how well a
model understands an image.
"""

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter

from archflow.project.repository import FilesystemProjectRepository
from project_runtime.application.render_contract import RenderCapability, RenderOutput
from project_runtime.main import create_app as studio_app
from project_runtime.settings import StudioSettings
from monkeyhub_api import chat
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import ChatCreateRequest, ChatPostRequest, ChatPresentationBindRequest, HubFailure

from test_chat import FAKE_CLI, _tools_of, wait_for


def _image(format_name: str, color: str, size=(48, 32)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format=format_name)
    return output.getvalue()


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=300)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _page(document: dict, page_index: int = 0) -> dict:
    return {"runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": page_index}


class LabelledFakeImageAdapter:
    """A test-only image provider: it records requests and returns a fixed PNG; it reaches no network."""

    provider_id = "labelled-fake-image"

    def __init__(self):
        self.calls = []

    def capability(self):
        return RenderCapability(self.provider_id, "Labelled fake image adapter (tests only)", None, True, max_references=3)

    def generate(self, request):
        self.calls.append(request)
        return RenderOutput(_image("PNG", "green"), "image/png")


class RenderContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub render 测试 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        # A Studio binds a project whose folder carries its id.
        self.project = self.root / "chat-project"
        self.other = self.root / "other-project"
        for path, name in ((self.project, "chat-project"), (self.other, "other-project")):
            FilesystemProjectRepository.initialize(path, project_id=name, initial_state={"project_id": name, "version": 0})
        environment = patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.fake = self.root / "fake cli.py"
        self.fake.write_text(FAKE_CLI, encoding="utf-8")
        self.log = self.root / "calls.jsonl"
        self.commands = {name: (sys.executable, str(self.fake), str(self.log)) for name in ("codex", "claude")}
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.addCleanup(self.close_store)
        # Project memory is read over the Hub's own HTTP; no Hub runs here.
        memory = patch.object(chat, "_project_memory", return_value=[])
        memory.start()
        self.addCleanup(memory.stop)
        self.source = self.register(self.project, "chat-project", "Courtyard-A.png", "image/png", _image("PNG", "blue"))
        self.reference = self.register(self.project, "chat-project", "AI-Result.jpg", "image/jpeg", _image("JPEG", "red"))
        self.second = self.register(self.project, "chat-project", "Material.png", "image/png", _image("PNG", "yellow"))
        self.drawing = self.register(self.project, "chat-project", "Plan.pdf", "application/pdf", _pdf())
        self.elsewhere = self.register(self.other, "other-project", "Elsewhere.png", "image/png", _image("PNG", "purple"))

    def close_store(self):
        for row in self.store.list():
            self.store.stop(row.id)
        self.store.shutdown()

    def register(self, project: Path, project_id: str, name: str, mime: str, data: bytes, **extra) -> dict:
        """Register one original through the Studio's own document route."""

        with TestClient(studio_app(StudioSettings(project_dir=project, cad_export="off"))) as client:
            response = client.post("/api/documents", json={
                "projectId": project_id, "fileName": name, "mimeType": mime,
                "contentBase64": base64.b64encode(data).decode("ascii"), **extra})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create(self, provider="claude"):
        return self.store.create(ChatCreateRequest(projectDir=str(self.project), provider=provider))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def finished(self, session):
        return wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running")

    def context(self, source=None, references=()):
        return {"source": _page(source or self.source), "references": [_page(row) for row in references]}

    def test_exact_image_pages_bind_with_roles_and_reach_the_cli_turn(self):
        """A plain uploaded PNG needs no model or design context; its roles and exact pages reach the turn."""

        self.assertIsNone(self.source["modelSource"], "the source is a plain image with no model")
        session = self.create()
        with patch.object(chat, "_prepared_context", side_effect=AssertionError("no design context is prepared")):
            posted = self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="参考右边这张的材料感觉，屋顶和视角别动。",
                renderContext=self.context(references=[self.reference])))
            detail = self.finished(session)
        self.assertEqual(detail.status, "idle", detail.error)
        asked = posted.messages[0]
        self.assertEqual([(row.role, row.fileName, row.mimeType) for row in asked.documents],
                         [("source", "Courtyard-A.png", "image/png"), ("reference", "AI-Result.jpg", "image/jpeg")])
        self.assertEqual([row.model_dump(include={"runId", "assetSha256", "revisionRef", "pageIndex"}) for row in asked.documents],
                         [_page(self.source), _page(self.reference)])
        prompt = self.calls()[-1]["prompt"]
        self.assertIn("参考右边这张的材料感觉，屋顶和视角别动。", prompt)
        facts = json.loads(prompt.split(chat._RENDER_NOTE, 1)[1].strip().splitlines()[0])
        self.assertEqual([(row["role"], row["fileName"], row["page"]) for row in facts],
                         [("source", "Courtyard-A.png", _page(self.source)), ("reference", "AI-Result.jpg", _page(self.reference))])
        self.assertIn("POST /api/board/export", chat._RENDER_NOTE)
        self.assertIn("pathPrefix /api/render", chat._RENDER_NOTE)
        # The transcript serves the exact bound original for its preview.
        document, data = self.store.presentation_document(session.id, asked.id, 0)
        self.assertEqual((document.file_name, document.mime_type), ("Courtyard-A.png", "image/png"))
        self.assertEqual(data, _image("PNG", "blue"))

    def test_mismatched_stale_or_unsupported_pages_are_refused_before_any_cli_starts(self):
        session = self.create()
        wrong_revision = {**_page(self.source), "revisionRef": "project://chat-project/runs/studio-documents/records/other.json"}
        missing_page = {**_page(self.source), "pageIndex": 1}
        cases = [
            ("another project's image", {"source": _page(self.elsewhere), "references": []}, "CHAT_RENDER_IMAGE_UNAVAILABLE", 409),
            ("a wrong revision", {"source": wrong_revision, "references": []}, "CHAT_RENDER_IMAGE_UNAVAILABLE", 409),
            ("a page the image does not have", {"source": missing_page, "references": []}, "CHAT_RENDER_IMAGE_UNAVAILABLE", 409),
            ("a reference from another project", self.context(references=[self.elsewhere]), "CHAT_RENDER_IMAGE_UNAVAILABLE", 409),
            ("a PDF drawing page", self.context(references=[self.drawing]), "CHAT_RENDER_IMAGE_UNSUPPORTED", 422),
        ]
        for label, context, code, status in cases:
            with self.subTest(label):
                with self.assertRaises(HubFailure) as refused:
                    self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="warmer concrete",
                                                                renderContext=context))
                self.assertEqual((refused.exception.error.code, refused.exception.status), (code, status))
                self.assertIn("nothing was sent", refused.exception.error.detail.lower())
        # A page someone replaced since it was selected is stale: it is refused, never swapped for the new one.
        replacement = self.register(self.project, "chat-project", "Courtyard-A v2.png", "image/png", _image("PNG", "navy"),
                                    replacesPages=[{**_page(self.source), "newPageIndex": 0}])
        with self.assertRaises(HubFailure) as stale:
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="warmer concrete",
                                                        renderContext=self.context()))
        self.assertEqual((stale.exception.error.code, stale.exception.status), ("CHAT_RENDER_IMAGE_STALE", 409))
        self.assertIn("Courtyard-A.png", stale.exception.error.detail)
        self.assertNotIn(replacement["assetSha256"], stale.exception.error.detail, "the refusal does not pick the newer page")
        # A message for another project is refused as such.
        with self.assertRaises(HubFailure) as swapped:
            self.store.post(session.id, ChatPostRequest(projectId="other-project", content="warmer concrete",
                                                        renderContext={"source": _page(self.elsewhere), "references": []}))
        self.assertEqual(swapped.exception.error.code, "CHAT_PROJECT_MISMATCH")
        self.assertEqual(self.store.get(session.id).messages, [], "no refused message was kept")
        self.assertEqual(self.calls(), [], "no CLI started")

    def test_a_replaced_reference_is_stale_and_unreadable_documents_send_nothing(self):
        session = self.create()
        context = self.context(references=[self.second, self.reference])
        replacement = self.register(self.project, "chat-project", "AI-Result v2.jpg", "image/jpeg", _image("JPEG", "orange"),
                                    replacesPages=[{**_page(self.reference), "newPageIndex": 0}])
        with self.assertRaises(HubFailure) as stale:
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="warmer concrete",
                                                        renderContext=context))
        self.assertEqual((stale.exception.error.code, stale.exception.status), ("CHAT_RENDER_IMAGE_STALE", 409))
        self.assertIn("Reference 2, «AI-Result.jpg» page 1", stale.exception.error.detail)
        self.assertNotIn(replacement["assetSha256"], stale.exception.error.detail, "the refusal does not pick the newer page")
        # Every page is registered, but the project's documents cannot be read to check for a replacement.
        from project_runtime.application import artifacts

        listed = artifacts.list_documents

        def unreadable(binding, run_id=None, **options):
            if run_id is None:
                raise OSError("The documents folder could not be read.")
            return listed(binding, run_id, **options)

        with patch.object(artifacts, "list_documents", side_effect=unreadable):
            with self.assertRaises(HubFailure) as failed:
                self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="warmer concrete",
                                                            renderContext=self.context(references=[self.second])))
        self.assertEqual((failed.exception.error.code, failed.exception.status), ("CHAT_RENDER_IMAGE_UNREADABLE", 503))
        self.assertIn("nothing was sent", failed.exception.error.detail)
        self.assertEqual(self.store.get(session.id).messages, [], "no refused message was kept")
        self.assertEqual(self.calls(), [], "no CLI started")

    def test_a_chat_that_takes_no_message_says_so_before_any_image_is_read(self):
        """An external, archived or closing chat answers as such; its images are never read."""

        external = self.store.bind_presentation(ChatPresentationBindRequest(projectDir=str(self.project),
                                                                            sourceSessionId="external-session"))
        archived = self.create()
        self.store.set_archived(archived.id, True)
        closing = self.create()
        cases = (("an external conversation", external.chatId, "CHAT_EXTERNAL_SOURCE", False),
                 ("an archived conversation", archived.id, "CHAT_ARCHIVED", False),
                 ("a closing Hub", closing.id, "CHAT_CLOSING", True))
        for label, chat_id, code, shutting in cases:
            with self.subTest(label), patch.object(chat, "_render_images", side_effect=AssertionError("no image is read")), \
                    patch.object(self.store, "_closing", shutting):
                with self.assertRaises(HubFailure) as refused:
                    self.store.post(chat_id, ChatPostRequest(projectId="chat-project", content="warmer concrete",
                                                             renderContext=self.context(references=[self.reference])))
                self.assertEqual((refused.exception.error.code, refused.exception.status), (code, 409))
                self.assertEqual(self.store.get(chat_id).messages, [])
        self.assertEqual(self.calls(), [], "no CLI started")

    def test_the_request_shape_names_one_source_and_at_most_three_distinct_references(self):
        pages = [_page(self.reference), _page(self.second), _page(self.drawing)]
        for label, context in (
            ("four references", {"source": _page(self.source), "references": [*pages, _page(self.elsewhere)]}),
            ("the source is also a reference", {"source": _page(self.source), "references": [_page(self.source)]}),
            ("a repeated reference", {"source": _page(self.source), "references": [_page(self.reference)] * 2}),
            ("a short digest", {"source": {**_page(self.source), "assetSha256": "abc"}, "references": []}),
            ("an unknown field", {"source": {**_page(self.source), "fileName": "Courtyard-A.png"}, "references": []}),
        ):
            with self.subTest(label), self.assertRaises(ValidationError):
                ChatPostRequest(projectId="chat-project", content="warmer", renderContext=context)
        # Three distinct references are accepted as they are, in order.
        accepted = ChatPostRequest(projectId="chat-project", content="warmer",
                                   renderContext={"source": _page(self.source), "references": pages})
        self.assertEqual([row.assetSha256 for row in accepted.renderContext.references], [row["assetSha256"] for row in pages])
        # The Hub route answers the same refusal as a request error, before anything runs.
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        session = self.create()
        with patch.object(app.state.applications, "start"), TestClient(app, base_url=self.store.hub_url) as client:
            response = client.post(f"/api/chat/sessions/{session.id}/messages", json={
                "projectId": session.projectId, "content": "warmer",
                "renderContext": {"source": _page(self.source), "references": [_page(self.source)]}})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.calls(), [])

    def test_corrections_resend_the_context_through_the_native_continuation(self):
        session = self.create()
        context = self.context(references=[self.reference])
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="参考右边这张的材料感觉。",
                                                    renderContext=context))
        first = self.finished(session)
        self.assertEqual(first.status, "idle", first.error)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="不是改光，是混凝土太冷。",
                                                    renderContext=context))
        second = self.finished(session)
        self.assertEqual(second.status, "idle", second.error)
        opened, continued = self.calls()
        self.assertNotIn("--resume", opened["args"])
        native = opened["args"][opened["args"].index("--session-id") + 1]
        self.assertEqual(continued["args"][continued["args"].index("--resume") + 1], native,
                         "the correction continues the same native CLI conversation")
        self.assertIn("不是改光，是混凝土太冷。", continued["prompt"])
        self.assertIn(chat._RENDER_NOTE, continued["prompt"])
        asked = [row for row in second.messages if row.role == "user"]
        self.assertEqual([[row.role for row in message.documents] for message in asked],
                         [["source", "reference"], ["source", "reference"]])

    def test_a_running_turn_takes_no_new_images(self):
        session = self.create()
        with patch.object(self.store, "_run"):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="first"))
            self.store._running[session.id] = chat._Running()
            try:
                with self.assertRaises(HubFailure) as refused:
                    self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="warmer",
                                                                renderContext=self.context()))
            finally:
                self.store._running.pop(session.id, None)
        self.assertEqual(refused.exception.error.code, "CHAT_INTERJECTION_IMAGES")
        self.assertEqual([row.content for row in self.store.get(session.id).messages], ["first"])


class RenderRoutesTests(unittest.TestCase):
    """The existing studio.render routes as the chat reaches them: allow-list, guide, admission and refusal."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub render routes ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "render-project"
        FilesystemProjectRepository.initialize(self.project, project_id="render-project",
                                               initial_state={"project_id": "render-project", "version": 0})
        self.session = {"id": str(uuid4()), "projectId": "render-project",
                        "projectDir": str(self.project.resolve()), "status": "running", "messages": []}
        self.hub = "http://127.0.0.1:8790"
        self.base = "http://127.0.0.1:8791"
        self.sent = []

    def studio(self, adapter=None):
        app = studio_app(StudioSettings(project_dir=self.project, cad_export="off"), render_adapter=adapter)
        client = TestClient(app)
        self.addCleanup(client.close)
        self.addCleanup(app.state.render_jobs.shutdown)
        return client

    def route(self, client):
        """Answer the chat's calls from the real Studio app, through the Hub admission path for writes."""

        runtime_id = uuid5(NAMESPACE_URL, f"render-project:{os.path.normcase(str(self.project.resolve()))}")
        admission = f"/api/runtime/projects/{runtime_id}/studio"

        def forward(base, path, method="GET", body=None, timeout=180, *, headers=None, png=False):
            if method != "GET":
                self.assertEqual(base, self.hub, "a write goes through the Hub's operation admission")
                self.assertTrue(path.startswith(admission + "/api/"), path)
                self.assertEqual(set(headers or {}), {"Idempotency-Key", "X-Monkey-Chat"})
                path = path.removeprefix(admission)
            self.sent.append((method, urlsplit(path).path, body, dict(headers or {})))
            response = client.request(method, path, json=body)
            if response.status_code >= 400:
                payload = response.json()
                raise HubFailure(response.status_code, payload.get("code", "CHAT_TOOL_FAILED"), str(payload.get("detail")))
            return response.json()
        return forward

    def call(self, client, name, arguments):
        with patch.object(chat, "_bound_studio", return_value=(self.base, dict(self.session))), \
                patch.object(chat, "_request_json", side_effect=self.route(client)):
            return chat.call_tool(self.hub, self.session["id"], name, arguments)

    def register(self, client, name="Courtyard-A.png"):
        response = client.post("/api/documents", json={
            "projectId": "render-project", "fileName": name, "mimeType": "image/png",
            "contentBase64": base64.b64encode(_image("PNG", "blue")).decode("ascii")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_render_routes_join_the_allow_lists_and_discovery_with_their_guide(self):
        for path in ("/api/render/capabilities", "/api/render/jobs", "/api/render/jobs/render-" + "a" * 32):
            self.assertIsNotNone(chat._READ.fullmatch(path), path)
        self.assertIsNotNone(chat._POST.fullmatch("/api/render/jobs"))
        # Freezing a Modeling camera view is the browser's own act, never the chat's.
        self.assertIsNone(chat._POST.fullmatch("/api/render/views"))
        self.assertTrue(chat._schema_allowed("GET", "/api/render/jobs/{job_id}"))
        client = self.studio()
        listing = self.call(client, "studio_schema", {"pathPrefix": "/api/render"})
        self.assertEqual({(row["method"], row["path"]) for row in listing["actions"]}, {
            ("GET", "/api/render/capabilities"), ("GET", "/api/render/jobs"),
            ("GET", "/api/render/jobs/{job_id}"), ("POST", "/api/render/jobs")})
        self.assertEqual(listing["guide"], chat._GUIDES["/api/render"])
        contract = self.call(client, "studio_schema", {"method": "POST", "path": "/api/render/jobs"})
        self.assertEqual(contract["path"], "/api/render/jobs")
        tools = {tool["name"]: tool for tool in _tools_of(chat)}
        self.assertIn("pathPrefix /api/render", tools["studio_request"]["description"])
        guide = chat._GUIDES["/api/render"]
        for words in ("POST /api/board/export", "explicit", "infer", "count", "different viewpoint",
                      "correction replaces", "never an accepted design decision", "GET /api/render/capabilities",
                      "placeholder providerId", "requestId", "chat_present"):
            self.assertIn(words, guide)

    def test_no_configured_provider_is_answered_honestly_and_nothing_is_submitted(self):
        client = self.studio()
        source = self.register(client)
        self.assertEqual(self.call(client, "studio_request", {"method": "GET", "path": "/api/render/capabilities"}),
                         {"providers": []})
        body = {"requestId": str(uuid4()), "providerId": "unselected", "source": _page(source), "references": [],
                "direction": "Warmer concrete; keep the roof and the viewpoint."}
        with self.assertRaises(HubFailure) as refused:
            self.call(client, "studio_request", {"method": "POST", "path": "/api/render/jobs", "body": body})
        self.assertEqual((refused.exception.status, refused.exception.error.code), (503, "RENDER_UNAVAILABLE"))
        self.assertIn("not configured", refused.exception.error.detail)
        self.assertEqual([row for row in self.sent if row[0] == "POST"], [], "nothing was admitted or dispatched")
        self.assertEqual(client.get("/api/render/jobs").json()["jobs"], [])

    def test_one_request_id_is_one_admitted_dispatch_through_the_existing_owner(self):
        adapter = LabelledFakeImageAdapter()
        client = self.studio(adapter)
        source, reference = self.register(client), self.register(client, "Reference.png")
        with self.assertRaises(HubFailure) as unselected:
            self.call(client, "studio_request", {"method": "POST", "path": "/api/render/jobs", "body": {
                "requestId": str(uuid4()), "providerId": "unselected", "source": _page(source),
                "direction": "Warmer concrete."}})
        self.assertEqual(unselected.exception.error.code, "RENDER_PROVIDER_INVALID")
        self.assertIn(adapter.provider_id, unselected.exception.error.detail)
        body = {"requestId": str(uuid4()), "providerId": adapter.provider_id, "source": _page(source),
                "references": [_page(reference)], "direction": "Warmer concrete; keep the roof and the viewpoint.",
                "output": {"size": "1K", "aspectRatio": "source"}}
        first = self.call(client, "studio_request", {"method": "POST", "path": "/api/render/jobs", "body": body})
        again = self.call(client, "studio_request", {"method": "POST", "path": "/api/render/jobs", "body": body})
        self.assertEqual(first["jobId"], again["jobId"])
        self.assertEqual(first["requestId"], body["requestId"])
        posts = [row for row in self.sent if row[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertTrue(all(row[2]["projectId"] == "render-project" for row in posts), "the chat fills projectId")
        self.assertNotEqual(posts[0][3]["Idempotency-Key"], posts[1][3]["Idempotency-Key"])
        deadline = time.monotonic() + 10
        while (job := self.call(client, "studio_request", {"method": "GET", "path": f"/api/render/jobs/{first['jobId']}"}))["status"] in {"queued", "running"}:
            self.assertLess(time.monotonic(), deadline, "the fake attempt did not finish")
            time.sleep(0.05)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(len(adapter.calls), 1, "one request id is one dispatch")
        self.assertEqual(adapter.calls[0].source.ref.asset_sha256, source["assetSha256"])
        self.assertEqual([row.ref.asset_sha256 for row in adapter.calls[0].references], [reference["assetSha256"]])
        self.assertEqual(job["document"]["runId"], first["jobId"], "the result is a registered document Board discovers")
        with self.assertRaises(HubFailure) as changed:
            self.call(client, "studio_request", {"method": "POST", "path": "/api/render/jobs",
                                                 "body": {**body, "direction": "Colder concrete."}})
        self.assertEqual(changed.exception.error.code, "RENDER_REQUEST_CONFLICT")
        self.assertEqual(len(adapter.calls), 1)


if __name__ == "__main__":
    unittest.main()
