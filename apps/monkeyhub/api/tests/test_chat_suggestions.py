"""A retained assistant suggestion continues the existing chat exactly once."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from fastapi.testclient import TestClient
from pydantic import ValidationError
from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.chat import guides, store as chat
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import (
    ChatCreateRequest, ChatMessage, ChatPostRequest, ChatPresentationRequest,
    ChatProvider, ChatSuggestion, ChatSuggestionEstimate, HubFailure,
)


def suggestion(**updates):
    return {"title": "Inspect the courtyard", "outcome": "A source-bound section comparison",
            "capability": "available", "rationale": "The connected drawing API exposes exact sections.",
            "tools": ["studio_schema", "studio_request"], "deliverables": ["Section PDF"],
            "prompt": "Generate the proposed courtyard section using the current exact source.", **updates}


class ChatSuggestionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="monkeyhub-suggestions-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        FilesystemProjectRepository.initialize(self.project, project_id="suggestion-project",
            initial_state={"project_id": "suggestion-project", "version": 0})
        self.runtime = self.root / "runtime"
        self.store = self.new_store()
        provider = ChatProvider(id="codex", label="Codex", available=True, installed=True, detail="Fixture")
        self.providers = patch.object(self.store, "providers", return_value=[provider])
        self.providers.start()
        self.addCleanup(self.providers.stop)
        self.session = self.store.create(ChatCreateRequest(projectDir=str(self.project), provider="codex"))
        # Establish the ordinary user's saved turn without starting a provider.
        with patch.object(self.store, "_start"):
            self.store.post(self.session.id, ChatPostRequest(projectId=self.session.projectId, content="Help me choose a next step"))
        self.turn = self.store.get(self.session.id).messages[-1].id
        self.card_id = str(uuid4())
        self.client = self.new_client(self.store)

    def new_store(self):
        store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands={})
        self.addCleanup(store.shutdown)
        return store

    def new_client(self, store):
        with patch("monkeyhub_api.main.ChatStore", return_value=store):
            client = TestClient(create_app(HubSettings(runtime_root=self.runtime)),
                                base_url="http://127.0.0.1:8790", client=("127.0.0.1", 42000))
        self.addCleanup(client.close)
        return client

    def present(self, *, expected=200, **changes):
        body = {"projectId": self.session.projectId, "sourceSessionId": f"hub:{self.session.id}",
                "turnId": self.turn, "messageId": self.card_id, "kind": "assistant", "revision": 0,
                "status": "complete", "suggestion": suggestion(), **changes}
        response = self.client.post(f"/api/chat/sessions/{self.session.id}/presentation", json=body,
            headers={"Authorization": "Bearer " + self.store.presentation_token(self.session.id)})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def finish(self):
        saved = self.store._sessions[self.session.id]
        saved.status = "idle"
        self.store._save(saved)

    def select(self, *, client=None, message_id=None, revision=0, **changes):
        return (client or self.client).post(f"/api/chat/sessions/{self.session.id}/messages", json={
            "projectId": self.session.projectId,
            "suggestionSelection": {"messageId": message_id or self.card_id, "revision": revision}, **changes})

    def saved(self):
        return json.loads((self.runtime / "chats" / f"{self.session.id}.json").read_text(encoding="utf-8"))

    def test_card_only_revision_updates_and_exact_conflict(self):
        self.present(status="streaming")
        self.present(status="streaming")  # exact retry is idempotent
        self.assertEqual(self.present(expected=409, suggestion=suggestion(title="Different"))["code"], "CHAT_PRESENTATION_CONFLICT")
        updated = self.present(revision=1, suggestion=suggestion(title="Updated"))
        row = next(row for row in updated["messages"] if row["id"] == self.card_id)
        self.assertEqual(row["suggestion"]["title"], "Updated")
        self.assertEqual(row["presentationRevision"], 1)
        self.assertEqual(row["content"], "")
        self.finish()
        self.assertEqual(self.select().json()["code"], "CHAT_SUGGESTION_EXPIRED")
        with patch.object(self.store, "_start") as start:
            self.assertEqual(self.select(revision=1).status_code, 202)
            self.assertEqual(start.call_count, 1)

    def test_selection_is_saved_before_native_continuation_and_never_replayed(self):
        self.present()
        self.finish()
        native = str(uuid4())
        self.store._sessions[self.session.id].nativeSessionId = native
        self.store._save(self.store._sessions[self.session.id])
        fake = self.root / "provider.py"
        log = self.root / "provider.json"
        fake.write_text('''import json,sys
from pathlib import Path
if "mcp" in sys.argv and "list" in sys.argv:
    print("[]");raise SystemExit
prompt=sys.stdin.read()
Path(sys.argv[1]).write_text(json.dumps({"args":sys.argv[3:],"prompt":prompt}),encoding="utf-8")
print(json.dumps({"type":"thread.started","thread_id":sys.argv[2]}),flush=True)
print(json.dumps({"type":"turn.completed"}),flush=True)
''', encoding="utf-8")
        self.store.commands = {"codex": (sys.executable, str(fake), str(log), native)}
        original_start = self.store._start
        def start(identifier, running, content):
            persisted = self.saved()["messages"][-1]
            self.assertEqual(persisted["suggestionSelection"], {"messageId": self.card_id, "revision": 0})
            self.assertEqual(persisted["content"], suggestion()["prompt"])
            self.assertIsNone(running.design_context)
            self.assertEqual(running.context_mode, "continue")
            self.assertEqual(running.attachments, ())
            original_start(identifier, running, content)
        with patch.object(self.store, "_start", side_effect=start):
            response = self.select()
            self.assertEqual(response.status_code, 202, response.text)
        deadline = time.monotonic() + 15
        while self.store.get(self.session.id).status == "running" and time.monotonic() < deadline:
            time.sleep(.02)
        result = self.store.get(self.session.id)
        self.assertEqual(result.status, "idle", result.error)
        invocation = json.loads(log.read_text(encoding="utf-8"))
        self.assertIn(native, invocation["args"])
        self.assertIn("resume", invocation["args"])
        self.assertTrue(invocation["prompt"].endswith(suggestion()["prompt"]))
        self.assertIn(guides._SUGGESTION_INSTRUCTIONS, invocation["prompt"])
        self.assertEqual(self.select().json()["code"], "CHAT_SUGGESTION_CONSUMED")
        reopened = self.new_store()
        self.assertEqual(reopened.get(self.session.id).model_dump(), result.model_dump())
        with patch.object(reopened, "_start", side_effect=AssertionError("Refresh must not execute")):
            again = self.select(client=self.new_client(reopened))
            self.assertEqual(again.status_code, 409)
            self.assertEqual(again.json()["code"], "CHAT_SUGGESTION_CONSUMED")

    def test_restart_can_read_and_choose_unconsumed_card(self):
        self.present(); self.finish()
        reopened = self.new_store()
        self.assertIsNotNone(reopened.get(self.session.id).messages[-1].suggestion)
        with patch.object(reopened, "providers", return_value=self.store.providers()), patch.object(reopened, "_start") as start:
            response = self.select(client=self.new_client(reopened))
            self.assertEqual(response.status_code, 202, response.text)
            start.assert_called_once()

    def test_running_archived_external_and_wrong_project_refuse_selection(self):
        self.present()
        self.assertEqual(self.select().json()["code"], "CHAT_RUNNING")
        self.finish()
        session = self.store._sessions[self.session.id]
        for status in ("failed", "interrupted"):
            with self.subTest(status=status), patch.object(self.store, "_start") as start:
                session.status = status
                self.assertEqual(self.select().json()["code"], "CHAT_SUGGESTION_EXPIRED")
                start.assert_not_called()
        session.status = "idle"
        for field, value, code in [("archived", True, "CHAT_ARCHIVED"), ("sourceSessionId", "external", "CHAT_EXTERNAL_SOURCE")]:
            original = getattr(session, field)
            setattr(session, field, value)
            with patch.object(self.store, "_start", side_effect=AssertionError("Must not dispatch")):
                self.assertEqual(self.select().json()["code"], code)
            setattr(session, field, original)
        self.assertEqual(self.select(projectId="other-project").json()["code"], "CHAT_PROJECT_MISMATCH")
        self.assertEqual(self.select(message_id=str(uuid4())).json()["code"], "CHAT_SUGGESTION_EXPIRED")

    def test_older_same_turn_card_and_wrong_user_turn_are_stale(self):
        self.present()
        newer = str(uuid4())
        self.present(messageId=newer)
        self.finish()
        self.assertEqual(self.select().json()["code"], "CHAT_SUGGESTION_EXPIRED")
        with patch.object(self.store, "_start"):
            self.store.post(self.session.id, ChatPostRequest(projectId=self.session.projectId, content="A different request"))
        self.finish()
        self.assertEqual(self.select(message_id=newer).json()["code"], "CHAT_SUGGESTION_EXPIRED")

    def test_unfinished_failed_or_non_assistant_card_cannot_be_chosen(self):
        self.present(status="streaming")
        self.finish()
        for status in ("streaming", "failed", "interrupted"):
            self.store._sessions[self.session.id].messages[-1].status = status
            self.assertEqual(self.select().json()["code"], "CHAT_SUGGESTION_EXPIRED")
        row = self.store._sessions[self.session.id].messages[-1]
        row.status, row.role = "complete", "tool"
        self.assertEqual(self.select().json()["code"], "CHAT_SUGGESTION_EXPIRED")

    def test_selection_refuses_client_overrides_even_empty_defaults(self):
        self.present(); self.finish()
        for change in ({"content": "Run something else"}, {"content": ""}, {"attachments": []},
                       {"designContext": None}, {"contextMode": "continue"}):
            with self.subTest(change=change), patch.object(self.store, "_start") as start:
                self.assertEqual(self.select(**change).status_code, 422)
                start.assert_not_called()
        self.assertEqual(len([row for row in self.saved()["messages"] if row["role"] == "user"]), 1)

    def test_selection_write_failure_prevents_dispatch_and_consumption(self):
        self.present(); self.finish()
        request = ChatPostRequest(projectId=self.session.projectId,
            suggestionSelection={"messageId": self.card_id, "revision": 0})
        with patch.object(self.store, "_save", side_effect=OSError("disk full")), patch.object(self.store, "_start") as start:
            with self.assertRaises(OSError):
                self.store.post(self.session.id, request)
            start.assert_not_called()
        self.assertFalse(any(row.suggestionSelection for row in self.store.get(self.session.id).messages))

    def test_all_nested_card_strings_are_redacted_before_display_and_save(self):
        secret = "sk-suggestion-secret-123456789"
        payload = suggestion(title=secret, outcome=secret, rationale=secret, tools=[secret],
            deliverables=[secret], prompt=secret, timeEstimate={"value": secret, "basis": secret},
            costEstimate={"value": secret, "basis": secret})
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": secret}):
            result = self.present(suggestion=payload)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, json.dumps(self.saved()))
        self.assertEqual(result["messages"][-1]["suggestion"]["prompt"], "[redacted]")

    def test_contract_estimates_roles_bounds_and_legacy_messages(self):
        self.assertIsNone(ChatSuggestion.model_validate(suggestion()).timeEstimate.value)
        for estimate in ({"value": "2 min"}, {"value": "2 min", "basis": " "}, {"value": "x" * 81, "basis": "measured"}):
            with self.assertRaises(ValidationError):
                ChatSuggestionEstimate.model_validate(estimate)
        self.assertEqual(ChatSuggestionEstimate(value="2 min", basis="Measured last comparable run").value, "2 min")
        for change in ({"tools": ["x"] * 9}, {"deliverables": ["x"] * 7}, {"prompt": " "}, {"tools": ["x" * 121]}):
            with self.assertRaises(ValidationError):
                ChatSuggestion.model_validate(suggestion(**change))
        for kind in ("user", "progress"):
            self.present(kind=kind, expected=422)
        self.present(suggestion=suggestion(timeEstimate={"value": "2 min"}), expected=422)
        old = ChatMessage(id="old", role="assistant", content="Answer", createdAt="then")
        self.assertIsNone(old.suggestion)
        self.assertIsNone(old.suggestionSelection)

    def test_ordinary_message_still_interjects(self):
        with patch.object(self.store, "_interject", return_value=self.store.get(self.session.id)) as interject:
            self.store._running[self.session.id] = chat._Running()
            try:
                self.store.post(self.session.id, ChatPostRequest(projectId=self.session.projectId, content="Also keep the stair"))
                interject.assert_called_once()
            finally:
                self.store._running.pop(self.session.id)


if __name__ == "__main__":
    unittest.main()
