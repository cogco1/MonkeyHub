"""GH-300: the chat summaries say which conversations wait on the architect.

`attention` is read from the transcript the Hub already holds: "permission"
while a permission request waits for a decision, the same request the
conversation shows with its options. The real ChatStore and the real ACP SDK
run against the local fake agent; no model or paid call is made.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from test_chat import FAKE_CLI, wait_for
from test_acp_chat import HUB_AGENT
from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api import chat
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import ChatCreateRequest, ChatPermissionRequest, ChatPostRequest


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ChatAttentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Hub attention 测试 ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project, self.runtime = self.root / "project", self.root / "runtime"
        FilesystemProjectRepository.initialize(self.project, project_id="attention-project",
                                               initial_state={"project_id": "attention-project", "version": 0})
        environment = patch.dict(os.environ, {
            "ACP_FIXTURE_ROOT": str(self.root), "CODEX_HOME": str(self.root / "codex"),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.cli, self.agent = self.root / "cli.py", self.root / "agent.py"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")
        self.agent.write_text(HUB_AGENT, encoding="utf-8")
        self.commands = {"codex": (sys.executable, str(self.cli), str(self.root / "cli.jsonl"))}
        self.store = self.open_store()
        self.addCleanup(self.close_store)

    def open_store(self):
        return chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands,
                              acp_command=(sys.executable, "-u", str(self.agent)), timeout_s=15)

    def close_store(self):
        for row in self.store.list():
            self.store.stop(row.id)
        self.store.shutdown()

    def create(self):
        return self.store.create(ChatCreateRequest(projectDir=str(self.project), provider="codex"))

    def post(self, session, text):
        return self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content=text))

    def summary(self, session):
        return next(row for row in self.store.list() if row.id == session.id)

    def waiting(self, session):
        row = wait_for(lambda: self.summary(session), lambda row: row.attention == "permission" or row.status != "running")
        self.assertEqual((row.status, row.attention), ("running", "permission"), row.error)
        return row

    def finished(self, session):
        row = wait_for(lambda: self.summary(session), lambda row: row.status != "running")
        self.assertEqual(row.status, "idle", row.error)
        return row

    def answer(self, session, option_id="allow"):
        detail = self.store.get(session.id)
        permission = next(message.permission for message in detail.messages if message.permission)
        return self.store.resolve_permission(session.id, permission.id,
                                             ChatPermissionRequest(projectId=session.projectId, optionId=option_id))

    def test_a_waiting_permission_is_flagged_until_it_is_answered(self):
        session, other = self.create(), self.create()
        self.assertIsNone(self.summary(session).attention)
        self.post(session, "permission")
        self.waiting(session)
        self.assertIsNone(self.summary(other).attention, "only the conversation that asked waits")
        self.assertEqual(self.store.get(session.id).attention, "permission", "the open conversation agrees")
        answered = self.answer(session)
        self.assertIsNone(answered.attention)
        self.assertIsNone(self.summary(session).attention, "answering clears the flag at once, before the turn ends")
        self.assertIsNone(self.finished(session).attention)

    def test_each_request_and_its_answer_move_the_summary(self):
        # Two requests in a row must read as two moments, so a client that
        # keys a notice on (chat, kind, updatedAt) never folds them into one.
        session = self.create()
        self.post(session, "permission")
        first = self.waiting(session)
        asked = next(message for message in self.store.get(session.id).messages if message.permission)
        self.assertGreaterEqual(_moment(first.updatedAt), _moment(asked.createdAt),
                                "the request itself moves updatedAt")
        answered = self.answer(session)
        self.assertGreater(_moment(answered.updatedAt), _moment(first.updatedAt), "so does its answer")
        self.finished(session)
        self.post(session, "permission")
        second = self.waiting(session)
        self.assertGreater(_moment(second.updatedAt), _moment(first.updatedAt))
        self.answer(session, None)
        self.finished(session)

    def test_stopping_the_turn_withdraws_the_flag(self):
        session = self.create()
        self.post(session, "permission")
        self.waiting(session)
        stopped = self.store.stop(session.id)
        self.assertIsNone(stopped.attention)
        self.assertEqual(self.summary(session).status, "interrupted")
        self.assertIsNone(self.summary(session).attention)

    def test_the_flag_is_never_stored_and_a_restart_clears_it(self):
        session = self.create()
        self.post(session, "permission")
        self.waiting(session)
        record = self.runtime / "chats" / f"{session.id}.json"
        # What a Hub that stopped abruptly leaves behind: the turn still running
        # and its request still in the transcript.
        crashed = record.read_text(encoding="utf-8")
        saved = json.loads(crashed)
        self.assertNotIn("attention", saved)
        self.assertEqual(saved["status"], "running")
        self.assertTrue(any(message.get("permission") for message in saved["messages"]))
        self.close_store()
        record.write_text(crashed, encoding="utf-8")
        self.store = self.open_store()
        reopened = self.summary(session)
        self.assertEqual(reopened.status, "interrupted")
        self.assertIsNone(reopened.attention, "no request survives the Hub that asked it")
        self.assertIsNone(self.store.get(session.id).attention)

    def test_the_session_list_says_which_chats_wait(self):
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            session, other = self.create(), self.create()
            self.post(session, "permission")
            self.waiting(session)
            listed = client.get("/api/chat/sessions")
            self.assertEqual(listed.status_code, 200, listed.text)
            rows = {row["id"]: row for row in listed.json()}
            self.assertEqual(rows[session.id]["attention"], "permission")
            self.assertIsNone(rows[other.id]["attention"])
            self.assertNotIn("messages", rows[session.id], "a summary never carries the transcript")
            by_project = client.get("/api/chat/sessions", params={"projectId": session.projectId}).json()
            self.assertEqual({row["id"]: row["attention"] for row in by_project}, {session.id: "permission", other.id: None})
            self.assertEqual(client.get(f"/api/chat/sessions/{session.id}").json()["attention"], "permission")
            self.store.stop(session.id)
            self.assertIsNone(next(row for row in client.get("/api/chat/sessions").json() if row["id"] == session.id)["attention"])

    def test_listing_reads_no_file(self):
        # Every open Hub view polls the list, so answering it stays in memory.
        session = self.create()
        self.post(session, "permission")
        self.waiting(session)

        def refused(*_args, **_kwargs):
            raise AssertionError("the session list touched the disk")

        with patch.object(chat.Path, "read_text", refused), patch.object(chat.Path, "read_bytes", refused), \
             patch.object(chat.Path, "open", refused), patch.object(chat.Path, "iterdir", refused), \
             patch.object(chat, "read_application_settings", refused):
            rows = self.store.list()
        self.assertEqual(next(row for row in rows if row.id == session.id).attention, "permission")


if __name__ == "__main__":
    unittest.main()
