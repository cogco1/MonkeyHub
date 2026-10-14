"""First import of Codex-created history; all providers and projects are fixtures."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.dev import source_roots
source_roots.put_first(ROOT)

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.app.composition import HubSettings, create_app
from monkeyhub_api.chat.acp_session import NativeSessionMetadata
from monkeyhub_api.chat.external_import import ExternalImports, import_history, public_items, same_directory
from monkeyhub_api.chat.continuation import release_boundary
from monkeyhub_api.chat.store import ChatStore, _SavedChat
from monkeyhub_api.models import (
    ChatAttachment, ChatImportRequest, ChatMessage, ChatNativeImportSource, ChatPresentationBindRequest, HubFailure,
)


# GH-618 owns the shared-source field. Until it lands, this field-only record
# extension lets the read-only refusal run against the actual store; no store
# behavior is replaced.
class _ImportRecord(_SavedChat):
    sharedSource: dict | None = None


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
NATIVE = "fixture-codex-created-session"


def event(identifier, text=None, *, role="assistant", content=None, session=NATIVE):
    return {"sessionId": session, "update": {
        "sessionUpdate": "user_message_chunk" if role == "user" else "agent_message_chunk",
        "messageId": identifier, "content": content or {"type": "text", "text": text}}}


class FakeNative:
    default_model = "fixture-model"
    history_verified = False
    ownership_uncertain = False

    def __init__(self, state):
        self.state, self.calls, self.closed = state, [], False

    def inspect_native(self, identifier, timeout):
        self.calls.append(("inspect", identifier))
        return NativeSessionMetadata(identifier, self.state["cwd"], self.state.get("can_restore", True))

    def restore(self, identifier, cwd, timeout, *, include_media=False):
        self.calls.append(("restore", identifier, cwd, include_media))
        if cwd != self.state["cwd"]:
            raise RuntimeError("This native session is not bound to this project's directory.")
        if self.state.get("busy"):
            raise RuntimeError("already has an active writer")
        if self.state.get("entered"):
            self.state["entered"].set()
            self.state["release"].wait(5)
        if self.state.get("during_load"):
            self.state["during_load"]()
        return self.state["events"]

    def confirm_history(self, identifier):
        self.history_verified = True

    def close(self):
        self.closed = True
        if self.state.get("close_failure"):
            raise RuntimeError("fixture native close failed")


class ExternalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "thi-hemp-study-01"
        FilesystemProjectRepository.initialize(self.project, project_id="thi-hemp-study-01",
            initial_state={"project_id": "thi-hemp-study-01", "version": 0})
        self.cwd = self.root / "codex-original-workspace"
        self.cwd.mkdir()
        self.state = {"cwd": str(self.cwd.resolve()), "events": [
            event("user-1", "Original Codex question", role="user"), event("answer-1", "Original Codex answer")]}
        self.source = self.root / "synthetic-native-source.json"
        self.source.write_text(json.dumps(self.state), encoding="utf-8")
        self.original_source = self.source.read_bytes()
        self.record_patch = patch("monkeyhub_api.chat.store._SavedChat", _ImportRecord)
        self.record_patch.start(); self.addCleanup(self.record_patch.stop)
        self.store = ChatStore(self.root / "runtime", "http://127.0.0.1:8790",
                               commands={"codex": ("unused",)}, acp_command=("unused",))
        self.addCleanup(self.store.shutdown)
        self.clients = []
        self.preview_clients = []
        def make_client(*_):
            client = FakeNative(self.state)
            self.clients.append(client)
            return client
        def metadata_client(_):
            client = FakeNative(self.state)
            self.preview_clients.append(client)
            return client
        self.client_patch = patch.object(self.store, "_new_acp_client", side_effect=make_client)
        self.client_patch.start(); self.addCleanup(self.client_patch.stop)
        self.version_patch = patch("monkeyhub_api.chat.external_import.check_native_version")
        self.version_patch.start(); self.addCleanup(self.version_patch.stop)
        self.clock = 100.0
        self.imports = ExternalImports(self.store, _ImportRecord, metadata_client=metadata_client, clock=lambda: self.clock)

    def preview(self):
        return self.imports.preview(NATIVE, "thi-hemp-study-01", str(self.project.resolve()))

    def confirm(self, preview, **changes):
        args = dict(source_session_id=NATIVE, project_id="thi-hemp-study-01", project_dir=str(self.project.resolve()),
                    preview_token=preview.previewToken, confirmed=True)
        args.update(changes)
        return self.imports.confirm(**args)

    def assert_no_retained_import(self):
        self.assertEqual(list(self.store.root.glob("*.json")), [])
        self.assertEqual(self.store._sessions, {})
        self.assertEqual(self.source.read_bytes(), self.original_source)

    def test_legacy_wrong_cwd_failure_reproduces_then_explicit_import_reuses_original_presentation(self):
        binding = self.store.bind_presentation(ChatPresentationBindRequest(
            projectDir=str(self.project.resolve()), sourceSessionId=NATIVE))
        row = self.store._sessions[binding.chatId]
        old_file = ChatAttachment(id=str(uuid4()), name="original-note.txt", mimeType="text/plain", size=4)
        row.messages = [ChatMessage(id="original-presentation", role="assistant", content="Retained Hub display",
                                    createdAt=row.createdAt, attachments=[old_file])]
        self.store._save(row, ((old_file, b"keep"),))
        saved = (self.store.root / f"{row.id}.json").read_bytes()
        with patch("monkeyhub_api.chat.continuation.check_native_version"), self.assertRaises(HubFailure) as caught:
            self.store.continue_native(row.id)
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_FAILED")
        self.assertIn("not bound", caught.exception.error.detail)
        self.assertEqual((self.store.root / f"{row.id}.json").read_bytes(), saved)
        result = self.confirm(self.preview())
        self.assertEqual(result.chat.id, row.id)
        self.assertEqual([m.content for m in result.chat.messages],
                         ["Retained Hub display", "Original Codex question", "Original Codex answer"])
        self.assertEqual(self.store.attachment(row.id, old_file.id)[1].read_bytes(), b"keep")
        self.assertEqual(result.providerCwd, str(self.cwd.resolve()))
        self.assertEqual(result.chat.continuationSessionId, NATIVE)
        self.assertEqual(self.source.read_bytes(), self.original_source)

    def test_first_import_without_hub_binding_keeps_native_cwd_and_same_id(self):
        preview = self.preview()
        self.assertFalse(preview.sameDirectory)
        self.assertTrue(preview.canContinue)
        self.assert_no_retained_import()
        self.assertEqual(self.clients, [])
        self.assertEqual(self.preview_clients[0].calls, [("inspect", NATIVE)])
        result = self.confirm(preview)
        self.assertEqual(result.executionMode, "original-directory")
        self.assertEqual(result.state, "native-ready")
        self.assertEqual(self.clients[0].calls, [("restore", NATIVE, str(self.cwd.resolve()), True)])
        self.assertIsNone(result.chat.sourceSessionId)
        self.assertEqual(result.chat.continuationSessionId, NATIVE)
        self.assertIsNone(self.store._sessions[result.chat.id].sharedSource)
        self.assertEqual(self.source.read_bytes(), self.original_source)

    def test_repeated_import_and_restart_reuse_one_record_without_duplicate_history(self):
        preview = self.preview()
        first = self.confirm(preview)
        second = self.confirm(preview)
        self.assertEqual(first, second)
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(list(self.store.root.glob("*.json"))), 1)
        cold = ChatStore(self.store.runtime_root, self.store.hub_url, commands={}, acp_command=("unused",))
        self.addCleanup(cold.shutdown)
        service = ExternalImports(cold, _ImportRecord)
        again = service.confirm(NATIVE, "thi-hemp-study-01", str(self.project.resolve()), "expired-after-restart", confirmed=True)
        self.assertEqual(again.chat.id, first.chat.id)
        self.assertEqual(again.chat.messages, first.chat.messages)
        self.assertEqual(again.providerCwd, first.providerCwd)
        self.assertEqual(again.state, "native-bound")

    def test_confirmed_import_retains_inline_image_and_document_bytes(self):
        self.state["events"] += [event("answer-1", content={"type": "image", "mimeType": "image/png",
            "data": base64.b64encode(PNG).decode(), "uri": "file:///source/render.png"}),
            event("file-1", content={"type": "resource", "resource": {"uri": "file:///source/details.txt",
                "mimeType": "text/plain", "text": "Complete document\nSecond line"}})]
        result = self.confirm(self.preview())
        attached = [a for m in result.chat.messages for a in m.attachments]
        self.assertEqual([a.name for a in attached], ["render.png", "details.txt"])
        self.assertEqual(self.store.attachment(result.chat.id, attached[0].id)[1].read_bytes(), PNG)
        self.assertEqual(self.store.attachment(result.chat.id, attached[1].id)[1].read_text(), "Complete document\nSecond line")
        self.assertEqual(self.confirm(self.preview()).chat.messages, result.chat.messages)

    def test_unavailable_attachment_links_are_retained_and_never_read_as_files(self):
        original = "Original text [@input.png](file:///private/path/input.png) after image"
        events = [event("linked", original), event("resource", content={"type": "resource_link",
                   "uri": "file:///private/path/design.pdf", "name": "design.pdf"})]
        with patch.object(Path, "read_bytes", side_effect=AssertionError("No implicit native file reads")):
            imported = import_history(events, NATIVE, "now", {})
        self.assertEqual(imported.messages[0].content, original)
        self.assertEqual(imported.attachments, ())
        self.assertEqual([r["availability"] for r in imported.source_references], ["source-only", "source-only"])
        self.assertEqual(imported.source_references[0]["uri"], "file:///private/path/input.png")

    def test_confirmation_must_match_selected_project_source_and_unexpired_preview(self):
        preview = self.preview()
        for changes in ({"confirmed": False}, {"project_id": "wrong-project"},
                        {"source_session_id": "other-source"}, {"preview_token": "not-issued"}):
            with self.subTest(changes=changes), self.assertRaises(HubFailure):
                self.confirm(preview, **changes)
            self.assert_no_retained_import()
        self.clock += 601
        with self.assertRaises(HubFailure):
            self.confirm(preview)
        self.assertEqual(self.clients, [])

    def test_cwd_change_after_preview_is_refused_without_rewriting_native_source(self):
        preview = self.preview()
        changed = self.root / "different-codex-workspace"
        changed.mkdir(); self.state["cwd"] = str(changed.resolve())
        with self.assertRaises(HubFailure) as caught:
            self.confirm(preview)
        self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_FAILED")
        self.assert_no_retained_import()

    def test_shared_readonly_source_and_different_project_cannot_be_adopted(self):
        for shared, project_id in (({"actorId": "remote"}, "thi-hemp-study-01"), (None, "another-project")):
            with self.subTest(shared=shared, project_id=project_id):
                row = _ImportRecord(id=str(uuid4()), projectId=project_id, projectDir=str(self.project.resolve()),
                    title="Source", provider="codex", sourceSessionId=NATIVE, createdAt="now", updatedAt="now", sharedSource=shared)
                self.store._loaded = True; self.store._sessions[row.id] = row
                with self.assertRaises(HubFailure):self.preview()
                self.store._sessions.clear()
        self.assertEqual(self.preview_clients, [])
        self.assertEqual(self.clients, [])

    def test_provider_busy_and_no_restore_capability_have_honest_failures(self):
        self.state["can_restore"] = False
        preview = self.preview()
        self.assertFalse(preview.canContinue)
        with self.assertRaises(HubFailure) as caught:self.confirm(preview)
        self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_UNSUPPORTED")
        self.assert_no_retained_import()
        self.state["can_restore"] = True; self.state["busy"] = True
        with self.assertRaises(HubFailure) as caught:self.confirm(self.preview())
        self.assertIn("active writer", caught.exception.error.detail)
        self.assert_no_retained_import()

    def test_save_failure_rolls_back_new_record_and_preserves_original(self):
        preview = self.preview()
        with patch.object(self.store, "_save", side_effect=OSError("fixture disk write failed")), self.assertRaises(HubFailure):
            self.confirm(preview)
        self.assert_no_retained_import()
        self.assertTrue(self.clients[0].closed)

    def test_parallel_confirm_has_one_writer_and_one_retained_import(self):
        preview = self.preview()
        self.state.update(entered=threading.Event(), release=threading.Event())
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.confirm, preview)
            self.assertTrue(self.state["entered"].wait(5))
            try:
                with self.assertRaises(HubFailure) as caught:self.confirm(preview)
                self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_BUSY")
            finally:self.state["release"].set()
            result = first.result(5)
        self.assertEqual(self.confirm(preview).chat.id, result.chat.id)
        self.assertEqual(len(self.clients), 1)

    def test_closing_project_during_load_prevents_commit(self):
        def close_project():self.store._cancelled_handoffs.update(self.store._handoffs)
        self.state["during_load"] = close_project
        with self.assertRaises(HubFailure) as caught:self.confirm(self.preview())
        self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_CANCELLED")
        self.assert_no_retained_import()

    def test_closing_project_during_client_creation_prevents_native_restore(self):
        preview = self.preview()
        client = FakeNative(self.state)

        def close_before_registration(*_):
            self.store.close_project(str(self.project.resolve()))
            return client

        with patch.object(self.store, "_new_acp_client", side_effect=close_before_registration), self.assertRaises(HubFailure) as caught:
            self.confirm(preview)
        self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_CANCELLED")
        self.assertEqual(client.calls, [])
        self.assertTrue(client.closed)
        self.assertFalse(self.store._handoff_clients)
        self.assert_no_retained_import()

    def test_binding_created_in_another_project_during_load_prevents_commit(self):
        other = self.root / "another-project"
        FilesystemProjectRepository.initialize(other, project_id="another-project",
            initial_state={"project_id": "another-project", "version": 0})
        competing = []
        def bind_elsewhere():
            competing.append(self.store.bind_presentation(ChatPresentationBindRequest(
                projectDir=str(other.resolve()), sourceSessionId=NATIVE)))
        self.state["during_load"] = bind_elsewhere
        with self.assertRaises(HubFailure) as caught:
            self.confirm(self.preview())
        self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_PROJECT")
        self.assertEqual(set(self.store._sessions), {competing[0].chatId})
        self.assertEqual(len(list(self.store.root.glob("*.json"))), 1)
        self.assertFalse(self.store._acp_sessions)
        self.assertFalse(self.store._handoffs)
        self.assertTrue(self.clients[0].closed)
        self.assertEqual(self.source.read_bytes(), self.original_source)

    def test_failed_cleanup_fences_the_native_source_instead_of_silent_retry(self):
        preview = self.preview()
        self.state.update(busy=True, close_failure=True)
        with self.assertRaises(HubFailure):self.confirm(preview)
        self.state.update(busy=False, close_failure=False)
        with self.assertRaises(HubFailure) as caught:self.confirm(preview)
        self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_INCOMPLETE")
        self.assertEqual(len(self.clients), 1)

    def test_malformed_public_history_fails_whole_import_without_partial_record(self):
        for events in ([event("foreign", "bad", session="foreign")],
                       [event("same", "one"), event("same", "two", role="user")],
                       [event("one", "a"), event("two", "b"), event("one", "c")],
                       [event("bad id", "content")],
                       [event("media", content={"type": "image", "mimeType": "image/png", "data": "not base64"})]):
            with self.subTest(events=events):
                self.state["events"] = events
                with self.assertRaises(HubFailure):self.confirm(self.preview())
                self.assert_no_retained_import()

    def test_text_chunk_segmentation_does_not_change_public_item_identity(self):
        split = public_items([event("a", "hello"), event("a", " world")], NATIVE)
        complete = public_items([event("a", "hello world")], NATIVE)
        self.assertEqual(split, complete)

    def test_release_prefix_binds_media_and_return_imports_only_the_new_suffix(self):
        image = event("image-1", content={"type": "image", "mimeType": "image/png", "data": base64.b64encode(PNG).decode()})
        old = [event("u1", "Native words", role="user"), image]
        boundary = release_boundary(old, NATIVE, 1, include_media=True)
        self.assertEqual(boundary.contentMode, "blocks")
        suffix = event("file-2", content={"type": "resource", "resource": {
            "uri": "file:///source/new.txt", "mimeType": "text/plain", "text": "New native attachment"}})
        imported = import_history([*old, suffix], NATIVE, "now", {}, boundary=boundary)
        self.assertEqual([row.id for row in imported.messages], [f"native:{NATIVE}:file-2"])
        self.assertEqual(imported.attachments[0][1], b"New native attachment")
        self.assertEqual(import_history(old, NATIVE, "now", {}, boundary=boundary).messages, ())
        changed = json.loads(json.dumps(old))
        changed[1]["update"]["content"]["data"] = base64.b64encode(b"changed source bytes").decode()
        with self.assertRaises(HubFailure):import_history(changed, NATIVE, "now", {}, boundary=boundary)
        with self.assertRaises(HubFailure):import_history(list(reversed(old)), NATIVE, "now", {}, boundary=boundary)

    def test_media_size_limit_refuses_entire_projection(self):
        events = [event("one", content={"type": "resource", "resource": {
            "uri": "file:///one.txt", "text": "one", "mimeType": "text/plain"}}),
            event("two", content={"type": "resource", "resource": {
                "uri": "file:///two.txt", "text": "two", "mimeType": "text/plain"}})]
        with patch("monkeyhub_api.chat.external_import._MAX_IMPORT_BYTES", 5), self.assertRaises(HubFailure) as caught:
            import_history(events, NATIVE, "now", {})
        self.assertEqual(caught.exception.error.code, "CHAT_IMPORT_LIMIT")


class Native(FakeNative):
    def snapshot(self, native_id, timeout):
        self.calls.append(("snapshot", native_id))
        return self.state["events"]


class WiredImportTests(unittest.TestCase):
    """The Hub's own routes, handlers and store; only the native adapter is a fixture."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="import-wiring-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "thi-hemp-study-01"
        FilesystemProjectRepository.initialize(self.project, project_id="thi-hemp-study-01",
            initial_state={"project_id": "thi-hemp-study-01", "version": 0})
        self.cwd = self.root / "original-codex-directory"
        self.cwd.mkdir()
        self.state = {"cwd": str(self.cwd.resolve()), "events": [
            event("user-1", "Original question", role="user"),
            event("image-1", content={"type": "image", "mimeType": "image/png",
                  "data": base64.b64encode(PNG).decode()}),
            event("link-1", "Original [@source.pdf](file:///unread/source.pdf)")]}
        self.clients = []
        self.metadata = []
        self.runtime = self.root / "runtime"
        self.store = ChatStore(self.runtime, "http://127.0.0.1:8790",
                               commands={"codex": ("unused",)}, acp_command=("unused",))
        self.addCleanup(self.store.shutdown)
        def make_client(*args):
            client = Native(self.state)
            self.clients.append(client)
            return client
        def inspect(*args):
            client = Native(self.state)
            self.metadata.append(client)
            return client
        self.enterContext(patch.object(self.store, "_new_acp_client", side_effect=make_client))
        self.store._external_imports._metadata_client = inspect
        self.enterContext(patch("monkeyhub_api.chat.external_import.check_native_version"))
        self.enterContext(patch("monkeyhub_api.chat.continuation.check_native_version"))
        with patch("monkeyhub_api.app.composition.ChatStore", return_value=self.store):
            self.app = create_app(HubSettings(runtime_root=self.runtime))
        # No lifespan: these requests start no runtime, Monitor or provider.
        self.http = TestClient(self.app, base_url="http://127.0.0.1:8790", client=("127.0.0.1", 42000))
        self.addCleanup(self.http.close)
        self.body = {"sourceSessionId": NATIVE, "projectId": "thi-hemp-study-01",
                     "projectDir": str(self.project.resolve())}

    def preview(self):
        response = self.http.post("/api/chat/imports/preview", json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def confirm(self, preview):
        response = self.http.post("/api/chat/imports", json={**self.body,
            "previewToken": preview["previewToken"], "confirmed": True})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_http_preview_then_confirm_retains_typed_origin_and_inline_bytes(self):
        preview = self.preview()
        self.assertFalse(preview["sameDirectory"])
        self.assertEqual(self.clients, [])
        self.assertFalse(list(self.store.root.glob("*.json")))
        result = self.confirm(preview)
        chat = result["chat"]
        self.assertEqual(result["providerCwd"], str(self.cwd.resolve()))
        self.assertEqual(result["executionMode"], "original-directory")
        self.assertEqual(result["sourceReferences"][0]["availability"], "source-only")
        self.assertEqual(chat["nativeImport"]["sourceReferences"], result["sourceReferences"])
        self.assertEqual(chat["nativeImport"]["sourceSessionId"], NATIVE)
        saved = _SavedChat.model_validate_json((self.store.root / (chat["id"] + ".json")).read_text())
        self.assertIsInstance(saved.nativeImport, ChatNativeImportSource)
        self.assertEqual(saved.nativeImport.sourceReferences[0].availability, "source-only")
        attachment = next(a for m in chat["messages"] for a in m["attachments"])
        response = self.http.get(f'/api/chat/sessions/{chat["id"]}/attachments/{attachment["id"]}')
        self.assertEqual(response.content, PNG)
        self.assertEqual(self.confirm(preview), result)
        listed = self.http.get("/api/chat/sessions").json()
        self.assertEqual(listed[0]["nativeImport"], chat["nativeImport"])

    def test_import_release_return_and_cold_read_preserve_complete_media_prefix(self):
        first = self.confirm(self.preview())["chat"]
        released = self.http.post(f'/api/chat/sessions/{first["id"]}/release-native').json()
        self.assertEqual(released["sourceSessionId"], NATIVE)
        self.assertEqual(self.store._sessions[first["id"]].nativeReleaseBoundary.contentMode, "blocks")
        self.state["events"].append(event("doc-2", content={"type": "resource", "resource": {
            "uri": "file:///native/details.txt", "mimeType": "text/plain", "text": "New native document"}}))
        response = self.http.post(f'/api/chat/sessions/{first["id"]}/continue-native')
        self.assertEqual(response.status_code, 200, response.text)
        returned = response.json()
        self.assertEqual(returned["messages"][:len(first["messages"])], first["messages"])
        self.assertEqual(len(returned["messages"]), len(first["messages"]) + 1)
        self.assertEqual(self.clients[-1].calls[0], ("restore", NATIVE, str(self.cwd.resolve()), True))
        new_file = returned["messages"][-1]["attachments"][0]
        self.assertEqual(self.store.attachment(first["id"], new_file["id"])[1].read_bytes(), b"New native document")
        self.assertEqual(self.http.post(f'/api/chat/sessions/{first["id"]}/continue-native').json(), returned)
        cold = ChatStore(self.store.runtime_root, self.store.hub_url, commands={}, acp_command=("unused",))
        self.addCleanup(cold.shutdown)
        repeated = cold.import_native(ChatImportRequest(**self.body, previewToken="expired", confirmed=True))
        self.assertEqual(repeated.chat.model_dump(mode="json"), returned)
        self.assertEqual(repeated.sourceReferences[0]["availability"], "source-only")
        self.assertEqual(repeated.state, "native-bound")

    def test_external_session_in_another_directory_imports_and_round_trips_on_one_thread(self):
        project_dir = str(self.project.resolve())
        self.assertFalse(same_directory(self.state["cwd"], project_dir))
        # Codex presented its conversation in the Hub while it runs outside the project.
        bound = self.http.post("/api/chat/presentation/bind", json={
            "projectDir": project_dir, "sourceSessionId": NATIVE, "provider": "codex", "title": "External study"})
        self.assertEqual(bound.status_code, 200, bound.text)
        chat_id = bound.json()["chatId"]
        # Returning without an import expects the project directory. The refusal
        # names its code in the response and in the Hub log, not only "409".
        with self.assertLogs("monkeyhub_api.app.composition", "WARNING") as logged:
            refused = self.http.post(f"/api/chat/sessions/{chat_id}/continue-native")
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "CHAT_CONTINUATION_FAILED")
        self.assertIn(f"POST /api/chat/sessions/{chat_id}/continue-native refused 409 CHAT_CONTINUATION_FAILED",
                      "\n".join(logged.output))
        self.assertEqual(self.clients[-1].calls, [("restore", NATIVE, project_dir, False)])
        preview = self.preview()
        self.assertEqual((preview["existingChatId"], preview["providerCwd"], preview["sameDirectory"]),
                         (chat_id, self.state["cwd"], False))
        chat = self.confirm(preview)["chat"]
        self.assertEqual((chat["id"], chat["continuationSessionId"], chat["sourceSessionId"]), (chat_id, NATIVE, None))
        self.assertEqual(chat["projectDir"], project_dir)
        self.assertEqual(chat["nativeImport"]["providerCwd"], self.state["cwd"])
        messages = chat["messages"]
        for turn in (1, 2):
            with self.subTest(turn=turn):
                if turn == 1:
                    # A Hub turn: the record keeps its rows and the native thread records the same turn.
                    row = self.store._sessions[chat_id]
                    row.messages += [ChatMessage(id=str(uuid4()), role="user", content="Hub question", createdAt=row.updatedAt),
                                     ChatMessage(id=str(uuid4()), role="assistant", content="Hub answer", createdAt=row.updatedAt)]
                    self.store._save(row)
                    self.state["events"] += [event("hub-user", "Hub question", role="user"), event("hub-answer", "Hub answer")]
                    messages = self.http.get(f"/api/chat/sessions/{chat_id}").json()["messages"]
                released = self.http.post(f"/api/chat/sessions/{chat_id}/release-native")
                self.assertEqual(released.status_code, 200, released.text)
                released = released.json()
                self.assertEqual((released["id"], released["sourceSessionId"], released["continuationSessionId"]),
                                 (chat_id, NATIVE, NATIVE))
                # One more turn in Codex while the Hub holds no writer.
                self.state["events"] += [event(f"codex-user-{turn}", f"Codex question {turn}", role="user"),
                                         event(f"codex-answer-{turn}", f"Codex answer {turn}")]
                returned = self.http.post(f"/api/chat/sessions/{chat_id}/continue-native")
                self.assertEqual(returned.status_code, 200, returned.text)
                returned = returned.json()
                self.assertEqual((returned["id"], returned["continuationSessionId"], returned["sourceSessionId"]),
                                 (chat_id, NATIVE, None))
                self.assertEqual(returned["messages"][:len(messages)], messages)
                self.assertEqual([m["content"] for m in returned["messages"][len(messages):]],
                                 [f"Codex question {turn}", f"Codex answer {turn}"])
                identities = [m["id"] for m in returned["messages"]]
                self.assertEqual(len(identities), len(set(identities)))
                messages = returned["messages"]
        self.assertEqual([m["content"] for m in messages].count("Hub answer"), 1)
        # After the import, every native connection loads the same thread in its original directory.
        restores = [call for client in self.clients[1:] for call in client.calls if call[0] == "restore"]
        self.assertEqual(restores, [("restore", NATIVE, self.state["cwd"], True)] * 3)
        self.assertEqual(len(list(self.store.root.glob("*.json"))), 1)
        cold = ChatStore(self.store.runtime_root, self.store.hub_url, commands={}, acp_command=("unused",))
        self.addCleanup(cold.shutdown)
        self.assertEqual(cold.get(chat_id).model_dump(mode="json")["messages"], messages)

    def test_http_requires_exact_explicit_target_boolean_confirmation_and_local_client(self):
        preview = self.preview()
        for extra in ({"confirmed": False}, {"confirmed": "true"}, {"confirmed": True, "projectId": "wrong"},
                      {"confirmed": True, "previewToken": "wrong"}):
            response = self.http.post("/api/chat/imports", json={**self.body, "previewToken": preview["previewToken"], **extra})
            self.assertIn(response.status_code, (409, 422), response.text)
            self.assertFalse(list(self.store.root.glob("*.json")))
        remote = TestClient(self.app, base_url="http://127.0.0.1:8790", client=("192.0.2.1", 9000))
        self.addCleanup(remote.close)
        response = remote.post("/api/chat/imports/preview", json=self.body)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.clients)

    def test_real_factory_uses_retained_native_cwd_and_refuses_missing_directory(self):
        chat = self.confirm(self.preview())["chat"]
        row = self.store._sessions[chat["id"]]
        with patch("monkeyhub_api.chat.acp_session.CodexAcpSession") as client, \
             patch("monkeyhub_api.chat.providers._native_codex", return_value="fixture-codex"), \
             patch("monkeyhub_api.chat.providers._source_checkout", return_value=self.project), \
             patch.object(self.store, "_codex_mcp", return_value={}):
            ChatStore._new_acp_client(self.store, row.id, row, {})
            self.assertEqual(client.call_args.kwargs["cwd"], str(self.cwd.resolve()))
            row.nativeImport.providerCwd = str(self.root / "missing-original")
            with self.assertRaises(HubFailure):
                ChatStore._new_acp_client(self.store, row.id, row, {})
            self.assertEqual(client.call_count, 1)


if __name__ == "__main__":
    unittest.main()
