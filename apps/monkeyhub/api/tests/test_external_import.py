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

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.dev import source_roots
source_roots.put_first(ROOT)

from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.chat.acp_session import NativeSessionMetadata
from monkeyhub_api.chat.external_import import ExternalImports, import_history, public_items
from monkeyhub_api.chat.continuation import release_boundary
from monkeyhub_api.chat.store import ChatStore, _SavedChat
from monkeyhub_api.models import ChatAttachment, ChatMessage, ChatPresentationBindRequest, HubFailure


# GH-618 owns adding the proposed DTO field to the shared files. Until its
# integration patch lands, the independent module exercises the actual store
# with this field-only record extension; no store behavior is replaced.
class _Origin(BaseModel):
    sourceSessionId: str
    providerCwd: str
    importedAt: str
    sourceReferences: list[dict] = Field(default_factory=list)


class _ImportRecord(_SavedChat):
    nativeImport: _Origin | None = None
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


if __name__ == "__main__":
    unittest.main()
