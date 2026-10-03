"""Explicit native handoff keeps identities, original media and fail-closed admission."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
import sys
sys.path.insert(0, str(ROOT))
from tools.dev import source_roots
source_roots.put_first(ROOT)

from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.chat.store import ChatStore, _SavedChat
from monkeyhub_api.chat.continuation import public_history, check_native_version
from monkeyhub_api.models import ChatMessage, ChatAttachment, ChatPresentationBindRequest, ChatPostRequest, HubFailure


def event(identifier, text, role="assistant", session="native-1"):
    return {"sessionId": session, "update": {"sessionUpdate": "user_message_chunk" if role == "user" else "agent_message_chunk",
            "messageId": identifier, "content": {"type": "text", "text": text}}}


class FakeNative:
    default_model = "fixture-model"
    closed = False
    def __init__(self, events=None, failure=None):
        self.events = events if events is not None else [event("u1", "Asked in Codex", "user"), event("a1", "Answered in Codex")]
        self.failure, self.calls = failure, []
    def restore(self, identifier, cwd, timeout):
        self.calls.append((identifier, cwd))
        if self.failure:
            raise RuntimeError(self.failure)
        return self.events
    def close(self):
        self.closed = True


class NativeContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        FilesystemProjectRepository.initialize(self.project, project_id="continuation-project",
            initial_state={"project_id": "continuation-project", "version": 0})
        self.store = ChatStore(self.root / "runtime", "http://127.0.0.1:8790", commands={"codex": ("unused",)}, acp_command=("unused",))
        self.addCleanup(self.store.shutdown)
        self.binding = self.store.bind_presentation(ChatPresentationBindRequest(projectDir=str(self.project), sourceSessionId="native-1"))
        self.id = self.binding.chatId
        self.version = patch("monkeyhub_api.chat.continuation.check_native_version")
        self.version.start(); self.addCleanup(self.version.stop)

    def restore(self, client=None):
        client = client or FakeNative()
        with patch.object(self.store, "_new_acp_client", return_value=client):
            return self.store.continue_native(self.id), client

    def test_adopts_same_row_preserves_provenance_and_media_and_refreshes_without_duplicates(self):
        row = self.store._sessions[self.id]
        attachment = ChatAttachment(id=str(uuid4()), name="retained.txt", mimeType="text/plain", size=3)
        row.messages = [ChatMessage(id="old", role="assistant", content="Original presentation", createdAt=row.createdAt, attachments=[attachment])]
        self.store._save(row, ((attachment, b"old"),))
        detail, client = self.restore()
        self.assertEqual(detail.id, self.id)
        self.assertIsNone(detail.sourceSessionId)
        self.assertEqual(detail.continuationSessionId, "native-1")
        self.assertEqual(client.calls, [("native-1", str(self.project.resolve()))])
        self.assertEqual([m.content for m in detail.messages], ["Asked in Codex", "Answered in Codex"])
        self.assertEqual(detail.priorMessages[0].content, "Original presentation")
        self.assertEqual(self.store.attachment(self.id, attachment.id)[1].read_bytes(), b"old")
        self.assertEqual(self.store._sessions[self.id].originalSourceSessionId, "native-1")
        again, unused = self.restore()
        self.assertEqual(again.messages, detail.messages)
        self.assertEqual(unused.calls, [])
        released = self.store.release_native(self.id)
        self.assertTrue(client.closed)
        self.assertEqual(released.sourceSessionId, "native-1")
        self.assertNotIn(self.id, self.store._presentation_tokens)
        refreshed, _ = self.restore(FakeNative([event("u1", "Asked in Codex", "user"), event("a1", "Answered in Codex"), event("a2", "Latest answer")]))
        self.assertEqual(len(refreshed.messages), 3)
        self.assertEqual(len(refreshed.priorMessages), 1)
        cold = ChatStore(self.store.runtime_root, self.store.hub_url, commands={})
        self.addCleanup(cold.shutdown)
        self.assertEqual(cold.get(self.id).messages, refreshed.messages)
        self.assertEqual(cold.get(self.id).priorMessages, refreshed.priorMessages)

    def test_mapped_native_reuses_original_chat_and_does_not_confuse_project_with_execution_cwd(self):
        source = self.store._sessions[self.id]
        native = source.model_copy(deep=True)
        native.id, native.sourceSessionId, native.acpSessionId, native.transport = str(uuid4()), None, "native-1", "acp"
        self.store._sessions[native.id] = native; self.store._save(native)
        detail, client = self.restore()
        self.assertEqual(detail.id, native.id)
        self.assertEqual(client.calls, [("native-1", None)])
        self.assertIn(self.id, self.store._sessions)
        with self.assertRaises(HubFailure):
            self.store.bind_presentation(ChatPresentationBindRequest(projectDir=str(self.project), sourceSessionId="native-1", chatId=self.id))
        self.store.release_native(native.id)
        binding = self.store.bind_presentation(ChatPresentationBindRequest(projectDir=str(self.project), sourceSessionId="native-1"))
        self.assertEqual(binding.chatId, native.id)

    def test_release_after_provider_rotation_binds_current_thread_not_historical_source(self):
        self.restore()
        with self.store._lock:
            self.store._fresh_provider_session(self.id)
            row = self.store._sessions[self.id]
            row.acpSessionId = "native-2"
            self.store._save(row)
        released = self.store.release_native(self.id)
        self.assertEqual(released.sourceSessionId, "native-2")
        self.assertEqual(released.continuationSessionId, "native-2")
        self.assertEqual(self.store._sessions[self.id].originalSourceSessionId, "native-1")
        binding = self.store.bind_presentation(ChatPresentationBindRequest(projectDir=str(self.project), sourceSessionId="native-2"))
        self.assertEqual(binding.chatId, self.id)

    def test_failed_or_malformed_restore_keeps_exact_external_record_and_guard(self):
        before = (self.store.root / f"{self.id}.json").read_bytes()
        for client in [FakeNative(failure="already has an active writer"), FakeNative([event("a", "other", session="wrong")]),
                       FakeNative([event(None, "no stable ID")])]:
            with self.subTest(client=client), self.assertRaises(HubFailure):
                self.restore(client)
            self.assertTrue(client.closed)
            self.assertEqual((self.store.root / f"{self.id}.json").read_bytes(), before)
            self.assertEqual(self.store.get(self.id).sourceSessionId, "native-1")
            with self.assertRaises(HubFailure) as refused:
                self.store.post(self.id, ChatPostRequest(projectId="continuation-project", content="must not run"))
            self.assertEqual(refused.exception.error.code, "CHAT_EXTERNAL_SOURCE")

    def test_running_archived_and_cross_project_bindings_refuse_before_provider(self):
        for field, value in [("status", "running"), ("archived", True), ("provider", "claude")]:
            row = self.store._sessions[self.id]; old = getattr(row, field); setattr(row, field, value)
            with patch.object(self.store, "_new_acp_client") as make, self.assertRaises(HubFailure):
                self.store.continue_native(self.id)
            make.assert_not_called(); setattr(row, field, old)
        native = row.model_copy(deep=True)
        native.id, native.projectDir, native.acpSessionId = str(uuid4()), "/another-project", "native-1"
        self.store._sessions[native.id] = native
        with self.assertRaises(HubFailure) as caught:
            self.restore()
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_MISMATCH")

    def test_handoff_fences_posts_and_archive_and_revokes_external_token_only_on_success(self):
        entered, proceed = threading.Event(), threading.Event()
        client = FakeNative()
        def restore(*args):
            entered.set(); self.assertTrue(proceed.wait(5)); return client.events
        client.restore = restore
        result = []
        def work():
            result.append(self.restore(client)[0])
        thread = threading.Thread(target=work)
        thread.start(); self.assertTrue(entered.wait(5))
        try:
            for action in [lambda: self.store.post(self.id, ChatPostRequest(projectId="continuation-project", content="blocked")),
                           lambda: self.store.set_archived(self.id, True), lambda: self.store.continue_native(self.id)]:
                with self.assertRaises(HubFailure): action()
        finally:
            proceed.set(); thread.join(5)
        self.assertEqual(len(result), 1)
        self.assertNotEqual(self.store._presentation_tokens[self.id], self.binding.token)

    def test_project_close_cancels_blocked_restore_without_late_writer_or_token_commit(self):
        entered, proceed = threading.Event(), threading.Event()
        client = FakeNative()
        def restore(*args):
            entered.set(); self.assertTrue(proceed.wait(5)); return client.events
        def close():
            client.closed = True
            proceed.set()
        client.restore, client.close = restore, close
        failures = []
        before = (self.store.root / f"{self.id}.json").read_bytes()
        def work():
            try:
                self.restore(client)
            except HubFailure as exc:
                failures.append(exc.error.code)
        thread = threading.Thread(target=work)
        thread.start(); self.assertTrue(entered.wait(5))
        self.store.close_project(str(self.project))
        thread.join(5)
        self.assertEqual(failures, ["CHAT_CONTINUATION_CANCELLED"])
        self.assertTrue(client.closed)
        self.assertNotIn(self.id, self.store._acp_sessions)
        self.assertEqual((self.store.root / f"{self.id}.json").read_bytes(), before)
        self.assertEqual(self.store.get(self.id).sourceSessionId, "native-1")

    def test_project_identity_is_revalidated_after_provider_load(self):
        before = (self.store.root / f"{self.id}.json").read_bytes()
        with patch("monkeyhub_api.chat.store.projects._project", side_effect=[
                ("continuation-project", str(self.project.resolve())), ("replaced-project", str(self.project.resolve()))]):
            with self.assertRaises(HubFailure) as caught:
                self.restore()
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_MISMATCH")
        self.assertEqual((self.store.root / f"{self.id}.json").read_bytes(), before)

    def test_version_floor_refuses_old_or_unrecognized_provider_before_restoration(self):
        from subprocess import CompletedProcess
        for value in ["codex-cli 0.152.0", "unknown fork 9.0.0"]:
            with patch("subprocess.run", return_value=CompletedProcess([], 0, value, "")), self.assertRaises(HubFailure):
                check_native_version(("codex",))
        with patch("subprocess.run", return_value=CompletedProcess([], 0, "codex-cli 0.153.4\n", "")):
            check_native_version(("codex",))

    def test_public_history_keeps_order_and_distinct_identical_messages_and_ignores_reasoning(self):
        rows = public_history([event("z", "same", "user"), event("a", "same"), event("a", " suffix"),
            {"sessionId": "native-1", "update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "private"}}}],
            "native-1", "now", {})
        self.assertEqual([row.content for row in rows], ["same", "same suffix"])
        self.assertEqual([row.role for row in rows], ["user", "assistant"])


if __name__ == "__main__":
    unittest.main()
