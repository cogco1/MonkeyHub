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
from monkeyhub_api.chat.continuation import public_history, check_native_version, release_boundary
from monkeyhub_api.models import ChatMessage, ChatAttachment, ChatPresentationBindRequest, ChatPostRequest, HubFailure


def event(identifier, text, role="assistant", session="native-1"):
    return {"sessionId": session, "update": {"sessionUpdate": "user_message_chunk" if role == "user" else "agent_message_chunk",
            "messageId": identifier, "content": {"type": "text", "text": text}}}


class FakeNative:
    default_model = "fixture-model"
    closed = False
    ownership_uncertain = False
    history_verified = True
    def __init__(self, events=None, failure=None):
        self.events = events if events is not None else [event("u1", "Asked in Codex", "user"), event("a1", "Answered in Codex")]
        self.failure, self.calls = failure, []
    def restore(self, identifier, cwd, timeout):
        self.calls.append((identifier, cwd))
        if self.failure:
            raise RuntimeError(self.failure)
        return self.events
    def confirm_history(self, identifier):
        self.history_verified = True
    def snapshot(self, identifier, timeout):
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
        self.assertEqual([m.content for m in detail.messages], ["Original presentation", "Asked in Codex", "Answered in Codex"])
        self.assertEqual(detail.priorMessages, [])
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
        self.assertEqual(len(refreshed.messages), 4)
        self.assertEqual(len(refreshed.priorMessages), 0)
        cold = ChatStore(self.store.runtime_root, self.store.hub_url, commands={})
        self.addCleanup(cold.shutdown)
        self.assertEqual(cold.get(self.id).messages, refreshed.messages)
        self.assertEqual(cold.get(self.id).priorMessages, refreshed.priorMessages)

    def test_mapped_native_reuses_original_chat_and_does_not_confuse_project_with_execution_cwd(self):
        source = self.store._sessions[self.id]
        native = source.model_copy(deep=True)
        native.id, native.sourceSessionId, native.acpSessionId, native.transport = str(uuid4()), "native-1", "native-1", "acp"
        native.nativeReleaseBoundary = release_boundary([], "native-1", 1)
        self.store._sessions[native.id] = native; self.store._save(native)
        detail, client = self.restore()
        self.assertEqual(detail.id, native.id)
        self.assertEqual(client.calls, [("native-1", None)])
        with patch.object(self.store, "_new_acp_client") as make:
            repeated = self.store.continue_native(self.id)
        make.assert_not_called()
        self.assertEqual(repeated, detail)
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
            self.store._acp_sessions[self.id] = FakeNative([event("u2", "New context", "user", session="native-2")])
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


class NativeHistoryBoundaryTests(unittest.TestCase):
    """The release snapshot fences internal provider inputs without guessing text."""

    def setUp(self):
        from monkeyhub_api.chat.continuation import release_boundary
        self.release_boundary = release_boundary
        self.prefix = [event("u1", "Internal Hub wrapper\nActual request", "user"),
                       event("a1", "Earlier answer")]
        self.boundary = self.release_boundary(self.prefix, "native-1", 1)

    def assert_refused(self, events, boundary=None):
        with self.assertRaises(HubFailure) as caught:
            public_history(events, "native-1", "now", {}, boundary)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_HISTORY")

    def test_boundary_serializes_only_identity_role_and_raw_digest_without_mutating_inputs(self):
        from copy import deepcopy
        from archflow.contracts.canonical import canonical_digest
        from monkeyhub_api.chat.continuation import NativeHistoryBoundary
        before = deepcopy(self.prefix)
        boundary = self.release_boundary(self.prefix, "native-1", 7)
        expected = {"sessionId": "native-1", "generation": 7, "items": [
            {"id": "u1", "role": "user", "contentDigest": canonical_digest(self.prefix[0]["update"]["content"]["text"])},
            {"id": "a1", "role": "assistant", "contentDigest": canonical_digest("Earlier answer")}]}
        self.assertEqual(boundary.model_dump(), expected)
        self.assertNotIn("Internal Hub wrapper", boundary.model_dump_json())
        self.assertEqual(NativeHistoryBoundary.model_validate_json(boundary.model_dump_json()), boundary)
        self.assertEqual(public_history(self.prefix, "native-1", "now", {}, boundary), [])
        self.assertEqual(self.prefix, before)
        self.assertEqual(boundary.model_dump(), expected)

    def test_exact_prefix_returns_only_suffix_and_keeps_same_text_with_new_identity(self):
        suffix = [event("u2", self.prefix[0]["update"]["content"]["text"], "user"),
                  event("a2", "Latest native answer")]
        events = [*self.prefix, *suffix]
        rows = public_history(events, "native-1", "now", {}, self.boundary)
        self.assertEqual([row.id for row in rows], ["native:native-1:u2", "native:native-1:a2"])
        self.assertEqual([row.role for row in rows], ["user", "assistant"])
        self.assertEqual([row.content for row in rows], [suffix[0]["update"]["content"]["text"], "Latest native answer"])
        self.assertEqual(len(public_history(events, "native-1", "now", {})), 4)
        refreshed = public_history(events, "native-1", "later", {}, self.boundary)
        self.assertEqual([(row.id, row.role, row.content) for row in refreshed],
                         [(row.id, row.role, row.content) for row in rows])

    def test_prefix_verification_uses_raw_text_before_any_redaction(self):
        prefix = [event("u", "first-test-secret", "user")]
        with patch("monkeyhub_api.chat.continuation.providers._redact", return_value="same redacted text") as redact:
            boundary = self.release_boundary(prefix, "native-1", 1)
            redact.assert_not_called()
            self.assert_refused([event("u", "second-test-secret", "user")], boundary)
            redact.assert_not_called()
            rows = public_history([*prefix, event("a", "suffix-secret")], "native-1", "now", {}, boundary)
            self.assertEqual(rows[0].content, "same redacted text")
            redact.assert_called_once_with("suffix-secret", {})

    def test_missing_reordered_replaced_changed_and_retyped_prefixes_refuse(self):
        cases = [[], self.prefix[:1], self.prefix[::-1],
                 [event("different-id", "Internal Hub wrapper\nActual request", "user"), self.prefix[1]],
                 [event("u1", "changed content", "user"), self.prefix[1]],
                 [event("u1", "Internal Hub wrapper\nActual request"), self.prefix[1]],
                 [event("prepended", "unexpected", "user"), *self.prefix]]
        for events in cases:
            with self.subTest(events=events):
                self.assert_refused(events, self.boundary)
        self.assert_refused(self.prefix, self.boundary.model_copy(update={"sessionId": "native-2"}))

    def test_contiguous_chunks_match_one_chunk_but_repeated_noncontiguous_id_refuses(self):
        boundary = self.release_boundary([event("u", "part", "user"), event("u", "s", "user")], "native-1", 1)
        rows = public_history([event("u", "parts", "user"), event("a", "new")], "native-1", "now", {}, boundary)
        self.assertEqual([row.content for row in rows], ["new"])
        for events in ([event("u", "one", "user"), event("a", "between"), event("u", "two", "user")],
                       [event("u", "one", "user"), event("u", "two")]):
            with self.subTest(events=events):
                self.assert_refused(events)
                with self.assertRaises(HubFailure) as caught:
                    self.release_boundary(events, "native-1", 1)
                self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_HISTORY")

    def test_malformed_or_foreign_public_events_fail_closed(self):
        from copy import deepcopy
        malformed = [event(value, "text") for value in (None, "", " ", "bad\nidentity", "x" * 501, 12, True)]
        malformed += [event("a", "text", session="native-2"), None,
                      {"sessionId": "native-1", "update": "bad update"}]
        for replacement in (None, 12, True):
            row = event("a", "text")
            row["update"]["content"]["text"] = replacement
            malformed.append(row)
        row = event("a", "text")
        row["update"]["sessionUpdate"] = []
        malformed.append(row)
        row = event("a", "text")
        row["update"]["content"] = "bad content"
        malformed.append(row)
        for row in malformed:
            with self.subTest(row=row):
                before = deepcopy(row)
                self.assert_refused([row])
                self.assertEqual(row, before)
        for identifier in ("", " ", "x" * 201):
            with self.subTest(session=identifier), self.assertRaises(HubFailure):
                public_history([], identifier, "now", {})

    def test_raw_character_and_chunk_caps_refuse_without_truncating_or_redacting_first(self):
        with patch("monkeyhub_api.chat.continuation._MAX_HISTORY_CHARACTERS", 5), \
             patch("monkeyhub_api.chat.continuation.providers._redact", return_value="") as redact:
            self.assert_refused([event("u", "123456", "user")])
            redact.assert_not_called()
            boundary = self.release_boundary([event("u", "12345", "user")], "native-1", 1)
            self.assertEqual(public_history([event("u", "12345", "user")], "native-1", "now", {}, boundary), [])
            self.assert_refused([event("u", "12345", "user"), event("a", "6")], boundary)
        with patch("monkeyhub_api.chat.continuation._MAX_HISTORY_CHUNKS", 3):
            self.assertEqual(len(public_history([event("u", "", "user")] * 3, "native-1", "now", {})), 1)
            self.assert_refused([event("u", "", "user")] * 4)
            with self.assertRaises(HubFailure):
                self.release_boundary([event("u", "", "user")] * 4, "native-1", 1)

    def test_private_and_native_media_events_do_not_enter_public_projection(self):
        private = {"sessionId": "native-1", "update": {"sessionUpdate": "agent_thought_chunk",
                   "content": {"type": "text", "text": "private reasoning"}}}
        media = {"sessionId": "native-1", "update": {"sessionUpdate": "user_message_chunk", "messageId": "u",
                 "content": {"type": "image", "url": "file:///ungranted.png"}}}
        rows = public_history([event("u", "one", "user"), private, media, event("u", "two", "user")],
                              "native-1", "now", {})
        self.assertEqual([(row.id, row.content) for row in rows], [("native:native-1:u", "onetwo")])
        self.assert_refused([{**private, "sessionId": "foreign"}])

    def test_boundary_model_refuses_invalid_generation_session_digest_role_and_duplicate_ids(self):
        from copy import deepcopy
        from pydantic import ValidationError
        from monkeyhub_api.chat.continuation import NativeHistoryBoundary
        original = self.boundary.model_dump()
        invalid = [{**original, "generation": value} for value in (0, -1, True, "1")]
        invalid += [{**original, "sessionId": value} for value in ("", " ", "x" * 201)]
        invalid += [{**original, "items": [*original["items"], original["items"][0]]}]
        for changes in ({"role": "tool"}, {"contentDigest": "A" * 64}, {"contentDigest": "bad"},
                        {"id": "bad\nidentity"}, {"content": "raw wrapper must not be stored"}):
            data = deepcopy(original)
            data["items"][0].update(changes)
            invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                NativeHistoryBoundary.model_validate(data)
        with self.assertRaises(ValidationError):
            self.boundary.generation = 2
        self.assertEqual(self.boundary.model_dump(), original)


class StoreReleaseBoundaryTests(unittest.TestCase):
    """Only a verified release prefix may separate Hub input from native additions."""

    def setUp(self):
        NativeContinuationTests.setUp(self)
        self.original_text = "Reply only GH613_HUB_START".ljust(121, ".")
        self.private_path = r"C:\disposable-gh613\attachments\retained.txt"
        self.wrapper = "Internal Hub instructions\nAttachment path: " + self.private_path + "\n\n" + self.original_text
        self.prefix = [event("hub-provider-user", self.wrapper, "user"), event("hub-provider-answer", "Original Hub answer")]
        self.attachment = ChatAttachment(id=str(uuid4()), name="retained.txt", mimeType="text/plain", size=3)
        row = self.store._sessions[self.id]
        row.sourceSessionId, row.transport, row.acpSessionId = None, "acp", "native-1"
        from datetime import datetime, timedelta
        answered_at = (datetime.fromisoformat(row.createdAt) + timedelta(microseconds=1)).isoformat()
        row.messages = [ChatMessage(id="hub-user", role="user", content=self.original_text, createdAt=row.createdAt,
                                   attachments=[self.attachment]),
                        ChatMessage(id="hub-answer", role="assistant", content="Original Hub answer", createdAt=answered_at)]
        self.store._save(row, ((self.attachment, b"old"),))
        self.client = FakeNative(self.prefix)
        self.store._acp_sessions[self.id] = self.client

    def restore(self, client=None):
        return NativeContinuationTests.restore(self, client)

    def record_bytes(self, store=None):
        return ((store or self.store).root / f"{self.id}.json").read_bytes()

    def cold_store(self):
        cold = ChatStore(self.store.runtime_root, self.store.hub_url,
                         commands={"codex": ("unused",)}, acp_command=("unused",))
        self.addCleanup(cold.shutdown)
        return cold

    def assert_hub_presentation(self, detail):
        self.assertEqual([(row.id, row.role, row.content) for row in detail.messages[:2]],
                         [("hub-user", "user", self.original_text), ("hub-answer", "assistant", "Original Hub answer")])
        self.assertEqual(detail.messages[0].attachments, [self.attachment])
        self.assertEqual(detail.priorMessages, [])
        self.assertEqual(self.store.attachment(self.id, self.attachment.id)[1].read_bytes(), b"old")

    def test_two_rounds_and_cold_return_preserve_original_rows_files_and_native_order(self):
        from unittest.mock import Mock
        self.client.snapshot = Mock(wraps=self.client.snapshot)
        first = self.store.release_native(self.id)
        self.assert_hub_presentation(first)
        first_boundary = self.store._sessions[self.id].nativeReleaseBoundary
        self.assertEqual(first_boundary.generation, 1)
        self.assertNotIn("Internal Hub instructions", self.record_bytes().decode("utf-8"))
        self.assertNotIn("disposable-gh613", self.record_bytes().decode("utf-8"))
        self.assertEqual(self.store.release_native(self.id), first)
        self.client.snapshot.assert_called_once()
        suffix = [event("z-native-user", self.original_text, "user"), event("a-native-answer", "First native reply")]
        restored, next_client = self.restore(FakeNative([*self.prefix, *suffix]))
        self.assert_hub_presentation(restored)
        self.assertEqual([row.id for row in restored.messages[2:]],
                         ["native:native-1:z-native-user", "native:native-1:a-native-answer"])
        self.assertEqual([row.content for row in restored.messages[2:]], [self.original_text, "First native reply"])
        self.assertFalse(any(self.private_path in row.content for row in restored.messages))
        again, unused = self.restore()
        self.assertEqual(again.messages, restored.messages)
        self.assertEqual(unused.calls, [])
        second = self.store.release_native(self.id)
        self.assertTrue(next_client.closed)
        self.assertEqual(self.store._sessions[self.id].nativeReleaseBoundary.generation, 2)
        self.assertEqual(len(self.store._sessions[self.id].nativeReleaseBoundary.items), 4)
        cold = self.cold_store()
        self.assertEqual(cold.get(self.id).messages, second.messages)
        suffix2 = [event("z-next-user", self.original_text, "user"), event("a-next-answer", "Second native reply")]
        with patch.object(cold, "_new_acp_client", return_value=FakeNative([*self.prefix, *suffix, *suffix2])):
            returned = cold.continue_native(self.id)
        self.assert_hub_presentation(returned)
        self.assertEqual(returned.messages[:4], restored.messages)
        self.assertEqual([row.id for row in returned.messages[4:]],
                         ["native:native-1:z-next-user", "native:native-1:a-next-answer"])
        self.assertEqual(len(returned.messages), 6)
        self.assertEqual(cold.get(self.id).messages, returned.messages)

    def test_native_user_copy_of_entire_wrapper_is_retained_by_its_new_id(self):
        self.store.release_native(self.id)
        detail, _ = self.restore(FakeNative([*self.prefix, event("native-copy", self.wrapper, "user")]))
        self.assert_hub_presentation(detail)
        self.assertEqual([row.id for row in detail.messages], ["hub-user", "hub-answer", "native:native-1:native-copy"])
        self.assertEqual(detail.messages[-1].content, self.wrapper)

    def test_changed_missing_reordered_retyped_and_duplicate_prefixes_preserve_exact_record(self):
        self.store.release_native(self.id)
        before = self.record_bytes()
        cases = [[], self.prefix[:1], self.prefix[::-1],
                 [event("hub-provider-user", self.wrapper + " changed", "user"), self.prefix[1]],
                 [event("hub-provider-user", self.wrapper), self.prefix[1]],
                 [*self.prefix, event("hub-provider-user", self.wrapper, "user")]]
        for events in cases:
            with self.subTest(events=events), self.assertRaises(HubFailure) as caught:
                self.restore(FakeNative(events))
            self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_HISTORY")
            self.assertEqual(self.record_bytes(), before)
            self.assertEqual(self.store.get(self.id).sourceSessionId, "native-1")
            self.assert_hub_presentation(self.store.get(self.id))

    def test_raw_history_caps_refuse_return_without_truncation_or_record_changes(self):
        self.store.release_native(self.id)
        before = self.record_bytes()
        events = [*self.prefix, event("new-user", "new", "user"), event("new-answer", "reply")]
        for name, limit in [("_MAX_HISTORY_CHARACTERS", len(self.wrapper) + len("Original Hub answer") + 1),
                            ("_MAX_HISTORY_CHUNKS", 3)]:
            with self.subTest(cap=name), patch("monkeyhub_api.chat.continuation." + name, limit), self.assertRaises(HubFailure) as caught:
                self.restore(FakeNative(events))
            self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_HISTORY")
            self.assertEqual(self.record_bytes(), before)
            self.assert_hub_presentation(self.store.get(self.id))

    def test_return_timeout_preserves_original_bytes_and_external_guard(self):
        self.store.release_native(self.id)
        before = self.record_bytes()
        with self.assertRaises(HubFailure) as caught:
            self.restore(FakeNative(failure="The provider did not finish loading the session in time."))
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_FAILED")
        self.assertEqual(self.record_bytes(), before)
        with self.assertRaises(HubFailure) as refused:
            self.store.post(self.id, ChatPostRequest(projectId="continuation-project", content="blocked"))
        self.assertEqual(refused.exception.error.code, "CHAT_EXTERNAL_SOURCE")
        from unittest.mock import Mock
        uncertain = FakeNative(failure="fixture restore timeout")
        uncertain.close = Mock(side_effect=RuntimeError("fixture cleanup close failure"))
        with self.assertRaises(HubFailure) as failed:
            self.restore(uncertain)
        self.assertEqual(failed.exception.error.code, "CHAT_CONTINUATION_FAILED")
        self.assertEqual(self.record_bytes(), before)
        self.assertIn(self.id, self.store._fenced_handoffs)
        with self.assertRaises(HubFailure) as fenced:
            self.store.post(self.id, ChatPostRequest(projectId="continuation-project", content="blocked"))
        self.assertEqual(fenced.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")
        self.assertEqual(self.record_bytes(), before)

    def test_pre_replace_save_failure_keeps_exact_record_and_owned_client(self):
        before = self.record_bytes()
        with patch("monkeyhub_api.chat.store.os.replace", side_effect=OSError("fixture replace failure")), self.assertRaises(HubFailure) as caught:
            self.store.release_native(self.id)
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_FAILED")
        self.assertEqual(self.record_bytes(), before)
        self.assertIs(self.store._acp_sessions[self.id], self.client)
        self.assertFalse(self.client.closed)
        self.assertIsNone(self.store.get(self.id).sourceSessionId)
        self.assertNotIn(self.id, self.store._fenced_handoffs)
        self.assertFalse((self.store.root / f"{self.id}.tmp").exists())

    def test_notification_failure_after_atomic_commit_does_not_report_failed_release(self):
        from unittest.mock import Mock
        self.store.on_change = Mock(side_effect=RuntimeError("fixture notification failure"))
        with self.assertLogs("monkeyhub_api.chat.store", level="ERROR"):
            detail = self.store.release_native(self.id)
        self.assertTrue(self.client.closed)
        self.assertEqual(detail.sourceSessionId, "native-1")
        persisted = _SavedChat.model_validate_json(self.record_bytes())
        self.assertFalse(persisted.nativeReleasePending)
        self.assertEqual(persisted.nativeReleaseBoundary, self.store._sessions[self.id].nativeReleaseBoundary)
        self.assertNotIn(self.id, self.store._fenced_handoffs)
        self.store.on_change = None

    def test_close_failure_persists_pending_fence_and_cold_return_refuses(self):
        from unittest.mock import Mock
        self.client.close = Mock(side_effect=RuntimeError("fixture close failure"))
        try:
            with self.assertRaises(HubFailure) as caught:
                self.store.release_native(self.id)
            self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")
            self.assertTrue(self.store._sessions[self.id].nativeReleasePending)
            self.assertIn(self.id, self.store._fenced_handoffs)
            self.assert_hub_presentation(self.store.get(self.id))
            before = self.record_bytes()
            for action in (lambda: self.store.release_native(self.id), lambda: self.store.continue_native(self.id)):
                with self.assertRaises(HubFailure) as refused:
                    action()
                self.assertEqual(refused.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")
            cold = self.cold_store()
            with patch.object(cold, "_new_acp_client") as make, self.assertRaises(HubFailure) as refused:
                cold.continue_native(self.id)
            make.assert_not_called()
            self.assertEqual(refused.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")
            self.assertEqual(self.record_bytes(), before)
        finally:
            self.client.close = lambda: setattr(self.client, "closed", True)

    def test_snapshot_rpc_failure_with_uncertain_ownership_fences_all_message_and_handoff_paths(self):
        before = self.record_bytes()
        def failed_snapshot(*args):
            self.client.ownership_uncertain = True
            raise RuntimeError("load failed after possible unsubscribe")
        self.client.snapshot = failed_snapshot
        with self.assertRaises(HubFailure) as caught:
            self.store.release_native(self.id)
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")
        self.assertEqual(self.record_bytes(), before)
        self.assertIn(self.id, self.store._fenced_handoffs)
        for action in (lambda: self.store.post(self.id, ChatPostRequest(projectId="continuation-project", content="blocked")),
                       lambda: self.store.release_native(self.id), lambda: self.store.continue_native(self.id)):
            with self.assertRaises(HubFailure) as refused:
                action()
            self.assertEqual(refused.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")
        self.assertEqual(self.record_bytes(), before)

    def test_pending_native_worker_refuses_release_and_new_idle_post_before_any_rpc(self):
        from unittest.mock import Mock
        entered, proceed = threading.Event(), threading.Event()
        workers = []
        thread_factory = threading.Thread
        def action():
            entered.set()
            self.assertTrue(proceed.wait(5))
        def tracked_thread(*args, **kwargs):
            worker = thread_factory(*args, **kwargs)
            workers.append(worker)
            return worker
        before = self.record_bytes()
        self.client.snapshot = Mock(wraps=self.client.snapshot)
        with patch("monkeyhub_api.chat.store.threading.Thread", side_effect=tracked_thread):
            with self.store._lock:
                self.store._queue_native_worker(self.id, action, "fixture-control")
        self.assertTrue(entered.wait(5))
        try:
            for operation in (lambda: self.store.release_native(self.id),
                              lambda: self.store.post(self.id, ChatPostRequest(projectId="continuation-project", content="blocked"))):
                with self.assertRaises(HubFailure) as refused:
                    operation()
                self.assertEqual(refused.exception.error.code, "CHAT_CONTINUATION_BUSY")
            self.client.snapshot.assert_not_called()
            self.assertEqual(self.record_bytes(), before)
        finally:
            proceed.set()
            workers[0].join(5)
        self.assertFalse(workers[0].is_alive())
        self.assertNotIn(self.id, self.store._native_workers)

    def test_late_started_new_turn_after_handoff_or_client_replacement_cannot_cancel_or_continue(self):
        from monkeyhub_api.chat.store import _Running
        from unittest.mock import Mock
        for fence in ("handoff", "replaced-client"):
            with self.subTest(fence=fence):
                entered, proceed = threading.Event(), threading.Event()
                row = self.store._sessions[self.id]
                row.messages.append(ChatMessage(id=f"interjection-{fence}", role="user", content="fixture follow-up",
                                                createdAt=row.createdAt, interjection="pending"))
                self.store._save(row)
                before = self.record_bytes()
                running = _Running(prompting=True, waiting=[(row.messages[-1].id, row.messages[-1].content)])
                self.store._running[self.id] = running
                self.store._acp_sessions[self.id] = self.client
                def delayed_steer(*args):
                    entered.set()
                    self.assertTrue(proceed.wait(5))
                    return "startedNewTurn"
                self.client.steer = delayed_steer
                self.client.cancel_turn = Mock()
                with patch.object(self.store, "_continue") as continuation:
                    worker = threading.Thread(target=self.store._steer, args=(self.id, running))
                    worker.start()
                    self.assertTrue(entered.wait(5))
                    try:
                        with self.store._lock:
                            self.store._running.pop(self.id)
                            if fence == "handoff":
                                self.store._handoffs.add(self.id)
                            else:
                                self.client.close()
                                self.store._acp_sessions[self.id] = FakeNative(self.prefix)
                        proceed.set()
                        worker.join(5)
                        self.assertFalse(worker.is_alive())
                        continuation.assert_not_called()
                        self.client.cancel_turn.assert_not_called()
                        self.assertEqual(self.record_bytes(), before)
                    finally:
                        proceed.set()
                        worker.join(5)
                        self.store._running.pop(self.id, None)
                        self.store._handoffs.discard(self.id)

    def test_project_close_cancels_snapshot_without_committing_a_late_boundary(self):
        entered, proceed = threading.Event(), threading.Event()
        failures = []
        before = self.record_bytes()
        def snapshot(*args):
            entered.set()
            self.assertTrue(proceed.wait(5))
            return self.prefix
        def close():
            self.client.closed = True
            proceed.set()
        self.client.snapshot, self.client.close = snapshot, close
        def release():
            try:
                self.store.release_native(self.id)
            except HubFailure as exc:
                failures.append(exc.error.code)
        worker = threading.Thread(target=release)
        worker.start()
        self.assertTrue(entered.wait(5))
        try:
            self.store.close_project(str(self.project))
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, ["CHAT_CONTINUATION_CANCELLED"])
            self.assertEqual(self.record_bytes(), before)
            self.assertNotIn(self.id, self.store._acp_sessions)
            self.assertIsNone(self.store._sessions[self.id].nativeReleaseBoundary)
        finally:
            proceed.set()
            worker.join(5)

    def test_legacy_released_hub_record_without_boundary_refuses_before_provider(self):
        row = self.store._sessions[self.id]
        row.sourceSessionId = "native-1"
        self.store._save(row)
        before = self.record_bytes()
        with patch.object(self.store, "_new_acp_client") as make, self.assertRaises(HubFailure) as caught:
            self.store.continue_native(self.id)
        make.assert_not_called()
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_HISTORY")
        self.assertEqual(self.record_bytes(), before)
        self.assert_hub_presentation(self.store.get(self.id))

    def test_rotated_old_native_id_cannot_be_imported_as_unbound_external(self):
        row = self.store._sessions[self.id]
        row.priorProviderSessionIds = ["old-native"]
        self.store._save(row)
        external = row.model_copy(deep=True)
        external.id, external.acpSessionId, external.sourceSessionId = str(uuid4()), None, "old-native"
        external.messages, external.priorProviderSessionIds = [], []
        self.store._sessions[external.id] = external
        self.store._save(external)
        before = (self.store.root / f"{external.id}.json").read_bytes()
        with patch.object(self.store, "_new_acp_client") as make, self.assertRaises(HubFailure) as caught:
            self.store.continue_native(external.id)
        make.assert_not_called()
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_HISTORY")
        self.assertEqual((self.store.root / f"{external.id}.json").read_bytes(), before)

    def test_missing_owned_client_and_cold_owned_record_refuse_release_without_reconnect(self):
        self.store._acp_sessions.pop(self.id)
        before = self.record_bytes()
        for store in (self.store, self.cold_store()):
            with patch.object(store, "_new_acp_client") as make, self.assertRaises(HubFailure) as caught:
                store.release_native(self.id)
            make.assert_not_called()
            self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_HISTORY")
            self.assertEqual(self.record_bytes(store), before)
        self.client.close()

    def test_rotation_partitions_boundary_and_failed_replacement_rolls_back_exact_continuity(self):
        self.store.release_native(self.id)
        self.restore(FakeNative(self.prefix))
        row = self.store._sessions[self.id]
        retained = self.store._retained_continuity(row)
        boundary = row.nativeReleaseBoundary.model_copy(deep=True)
        rotated = self.store._fresh_provider_session(self.id)
        self.assertIsNone(rotated.acpSessionId)
        self.assertEqual(rotated.nativeReleaseBoundary, boundary)
        self.assertEqual(rotated.nativeReleaseBoundary.sessionId, "native-1")
        self.assertIn("native-1", rotated.priorProviderSessionIds)
        self.store._restore_continuity(rotated, retained)
        self.store._save(rotated)
        self.assertEqual(rotated.acpSessionId, "native-1")
        self.assertEqual(rotated.nativeReleaseBoundary, boundary)
        self.assertEqual(rotated.priorProviderSessionIds, [])
        self.assert_hub_presentation(self.store.get(self.id))

    def test_new_owned_rotation_release_overwrites_old_partition_with_current_thread(self):
        self.store.release_native(self.id)
        self.restore(FakeNative(self.prefix))
        rotated = self.store._fresh_provider_session(self.id)
        rotated.acpSessionId = "native-2"
        current = FakeNative([event("new-user", "New Hub context", "user", session="native-2")])
        self.store._acp_sessions[self.id] = current
        self.store._save(rotated)
        released = self.store.release_native(self.id)
        self.assertEqual(released.sourceSessionId, "native-2")
        self.assertEqual(self.store._sessions[self.id].nativeReleaseBoundary.sessionId, "native-2")
        self.assertEqual([item.id for item in self.store._sessions[self.id].nativeReleaseBoundary.items], ["new-user"])
        self.assert_hub_presentation(released)

    def test_stop_control_outliving_running_turn_blocks_new_post_and_release(self):
        from monkeyhub_api.chat.store import _Running
        from unittest.mock import Mock
        entered, proceed = threading.Event(), threading.Event()
        running = _Running()
        self.store._running[self.id] = running
        self.store._sessions[self.id].status = "running"
        self.store._save(self.store._sessions[self.id])
        def cancel():
            entered.set()
            self.assertTrue(proceed.wait(5))
        self.client.cancel = cancel
        self.client.snapshot = Mock(wraps=self.client.snapshot)
        worker = threading.Thread(target=self.store.stop, args=(self.id,))
        worker.start()
        self.assertTrue(entered.wait(5))
        try:
            with self.store._lock:
                self.store._running.pop(self.id)
                self.store._sessions[self.id].status = "idle"
                self.store._save(self.store._sessions[self.id])
            before = self.record_bytes()
            for action in (lambda: self.store.release_native(self.id),
                           lambda: self.store.post(self.id, ChatPostRequest(projectId="continuation-project", content="blocked"))):
                with self.assertRaises(HubFailure) as refused:
                    action()
                self.assertEqual(refused.exception.error.code, "CHAT_CONTINUATION_BUSY")
            self.client.snapshot.assert_not_called()
            self.assertEqual(self.record_bytes(), before)
        finally:
            proceed.set()
            worker.join(5)
            self.store._running.pop(self.id, None)
        self.assertFalse(worker.is_alive())
        self.assertNotIn(self.id, self.store._native_workers)

    def test_completion_save_failure_after_close_keeps_persisted_pending_boundary(self):
        import os
        original_replace = os.replace
        replacements = []
        def fail_completion(source, destination):
            replacements.append(destination)
            if len(replacements) == 2:
                raise OSError("fixture completion replace failure")
            return original_replace(source, destination)
        with patch("monkeyhub_api.chat.store.os.replace", side_effect=fail_completion), self.assertRaises(HubFailure) as caught:
            self.store.release_native(self.id)
        self.assertEqual(caught.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")
        self.assertTrue(self.client.closed)
        self.assertIn(self.id, self.store._fenced_handoffs)
        saved = _SavedChat.model_validate_json(self.record_bytes())
        self.assertEqual(saved.sourceSessionId, "native-1")
        self.assertTrue(saved.nativeReleasePending)
        self.assertEqual(saved.nativeReleaseBoundary.sessionId, "native-1")
        self.assert_hub_presentation(self.store.get(self.id))
        cold = self.cold_store()
        with patch.object(cold, "_new_acp_client") as make, self.assertRaises(HubFailure) as refused:
            cold.continue_native(self.id)
        make.assert_not_called()
        self.assertEqual(refused.exception.error.code, "CHAT_CONTINUATION_INCOMPLETE")

if __name__ == "__main__":
    unittest.main()
