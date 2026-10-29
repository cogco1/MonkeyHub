"""Standing instructions and notes, wake-ups, and messages between conversations; fake CLI, no model call."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from archflow.project.repository import FilesystemProjectRepository  # noqa: E402
from monkeyhub_api.app.composition import HubSettings, create_app  # noqa: E402
from monkeyhub_api.chat import hub_tools, mcp_server, notes, providers, relay, store as chat, transport, wakeups  # noqa: E402
from monkeyhub_api.models import ChatCreateRequest, ChatMessage, ChatMessageOrigin, ChatPostRequest, HubFailure  # noqa: E402
from test_chat import FAKE_CLI, wait_for  # noqa: E402


class _Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


class StandingNotesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Hub notes ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = self.root / "runtime"

    def test_scopes_are_kept_apart_and_a_removed_note_can_come_back(self):
        notes.set_instructions(self.runtime, None, "删除文件一律进回收站。")
        notes.set_instructions(self.runtime, str(self.root / "p"), "本项目单位用毫米。")
        kept = notes.add_note(self.runtime, str(self.root / "p"), "  墙厚   默认 240  ", source="agent", chat_id="c")
        again = notes.add_note(self.runtime, str(self.root / "p"), "墙厚 默认 240", source="person")
        self.assertEqual(kept.id, again.id)
        self.assertEqual(kept.text, "墙厚 默认 240")
        block = notes.turn_block(self.runtime, str(self.root / "p"))
        self.assertIn("删除文件一律进回收站。", block)
        self.assertIn("本项目单位用毫米。", block)
        self.assertIn(f"墙厚 默认 240 (id {kept.id[:8]}, project)", block)
        self.assertNotIn("本项目单位用毫米", notes.turn_block(self.runtime, str(self.root / "other")))

        notes.remove_note(self.runtime, str(self.root / "p"), kept.id[:8])
        self.assertNotIn("墙厚", notes.turn_block(self.runtime, str(self.root / "p")))
        scope = notes.restore_note(self.runtime, str(self.root / "p"), kept.id)
        self.assertIsNone(scope.memories[0].removedAt)
        self.assertIn("墙厚", notes.turn_block(self.runtime, str(self.root / "p")))

    def test_nothing_saved_gives_a_turn_nothing(self):
        self.assertEqual(notes.turn_block(self.runtime, str(self.root / "p")), "")

    def test_an_unreadable_file_is_said_and_never_overwritten(self):
        path = self.runtime / notes.NOTES_DIR / "global.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertIn("could not be read", notes.turn_block(self.runtime, None))
        with self.assertRaises(HubFailure) as caught:
            notes.set_instructions(self.runtime, None, "x")
        self.assertEqual(caught.exception.error.code, "NOTES_UNREADABLE")
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")


class _FakeChats:
    """What wake-ups ask of the chat store: a conversation's state, and sending it a message."""

    def __init__(self):
        self.status, self.archived, self.posted = "idle", False, []

    def get(self, chat_id):
        if chat_id != "chat-1":
            raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.")
        return type("Detail", (), {"id": chat_id, "projectId": "p", "projectDir": "D:/p", "status": self.status,
                                   "archived": self.archived, "sourceSessionId": None})()

    def post(self, chat_id, request, *, origin=None):
        self.posted.append((chat_id, request.content, origin))


class WakeupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Hub wakeups ")
        self.addCleanup(temp.cleanup)
        self.clock = _Clock()
        self.chats = _FakeChats()
        self.wakeups = wakeups.Wakeups(Path(temp.name), self.chats, now=self.clock)

    def test_once_runs_when_due_and_waits_for_a_running_turn(self):
        made = self.wakeups.add("chat-1", wakeups.WakeupRequest(prompt="看看渲染好了没有", inMinutes=10), created_by="agent")
        self.assertEqual(self.wakeups.tick(), [])
        self.clock.now += timedelta(minutes=11)
        self.chats.status = "running"
        self.assertEqual(self.wakeups.tick(), [])
        self.chats.status = "idle"
        self.assertEqual(self.wakeups.tick(), [made.id])
        chat_id, content, origin = self.chats.posted[0]
        self.assertEqual((chat_id, content, origin.kind, origin.scheduleId), ("chat-1", "看看渲染好了没有", "schedule", made.id))
        self.assertEqual(self.wakeups.list("chat-1"), [])
        self.assertEqual(self.wakeups.list("chat-1", active_only=False)[0].status, "done")

    def test_a_repeat_counts_on_from_its_run_and_survives_a_restart(self):
        made = self.wakeups.add("chat-1", wakeups.WakeupRequest(prompt="巡检", everyMinutes=60), created_by="person")
        self.clock.now += timedelta(hours=5)  # the Hub was closed through four of them: it runs once
        self.assertEqual(self.wakeups.tick(), [made.id])
        self.assertEqual(self.wakeups.tick(), [])
        reopened = wakeups.Wakeups(self.wakeups.path.parents[1], self.chats, now=self.clock)
        row = reopened.list("chat-1")[0]
        self.assertEqual(datetime.fromisoformat(row.nextAt), self.clock.now + timedelta(hours=1))
        self.assertEqual((row.runs, row.label), (1, "every 1 h"))

    def test_an_archived_conversation_pauses_and_resumes(self):
        made = self.wakeups.add("chat-1", wakeups.WakeupRequest(prompt="x", inMinutes=1), created_by="agent")
        self.clock.now += timedelta(minutes=2)
        self.chats.archived = True
        self.assertEqual(self.wakeups.tick(), [])
        self.assertEqual(self.wakeups.list("chat-1")[0].status, "paused")
        self.chats.archived = False
        self.assertEqual(self.wakeups.tick(), [made.id])

    def test_cancel_and_timing_rules(self):
        made = self.wakeups.add("chat-1", wakeups.WakeupRequest(prompt="x", dailyAt="09:00", weekdays=[0]), created_by="agent")
        local = datetime.fromisoformat(made.nextAt).astimezone(wakeups._local_zone())
        self.assertEqual((local.weekday(), local.hour, local.minute), (0, 9, 0))
        self.assertEqual(self.wakeups.cancel(made.id[:8], "chat-1").status, "cancelled")
        self.assertEqual(self.wakeups.list("chat-1"), [])
        for bad in ({"prompt": "x"}, {"prompt": "x", "inMinutes": 5, "everyMinutes": 10}, {"prompt": "x", "everyMinutes": 1},
                    {"prompt": "x", "afterJob": {"path": "/api/project"}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                wakeups.WakeupRequest.model_validate(bad)
        with self.assertRaises(HubFailure):
            self.wakeups.add("chat-2", wakeups.WakeupRequest(prompt="x", inMinutes=5), created_by="agent")

    def test_a_job_wait_gives_up_and_says_so(self):
        made = self.wakeups.add("chat-1", wakeups.WakeupRequest(prompt="继续出图", afterJob={"path": "/api/skills/runs/run-1"}),
                                created_by="agent")
        self.clock.now += timedelta(minutes=1)
        self.assertEqual(self.wakeups.tick(), [])  # no Hub to read the job from: nothing is guessed
        self.clock.now += timedelta(hours=5)
        self.assertEqual(self.wakeups.tick(), [made.id])
        self.assertIn("Stopped waiting for /api/skills/runs/run-1", self.chats.posted[0][1])


class ConversationTurnTests(unittest.TestCase):
    """The hooks in the chat store: every turn reads the notes, and a message knows who sent it."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Hub B line ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = self.root / "runtime"
        self.project = self.root / "project"
        FilesystemProjectRepository.initialize(self.project, project_id="chat-project",
                                               initial_state={"project_id": "chat-project", "version": 0})
        environment = {"CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                       "APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local"),
                       "CHAT_TEST_SECRET": "fake-private-token-12345"}
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        fake = self.root / "fake cli.py"
        fake.write_text(FAKE_CLI, encoding="utf-8")
        self.log = self.root / "calls.jsonl"
        self.commands = {name: (sys.executable, str(fake), str(self.log)) for name in ("codex", "claude")}
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:1", commands=self.commands)
        self.addCleanup(self.store.shutdown)

    def create(self, title="New chat"):
        return self.store.create(ChatCreateRequest(projectDir=str(self.project), provider="codex", title=title))

    def finished(self, session):
        return wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running")

    def prompts(self):
        return [json.loads(line)["prompt"] for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_every_turn_reads_the_instructions_and_notes_before_the_request(self):
        notes.set_instructions(self.runtime, None, "删除文件一律进回收站。")
        note = notes.add_note(self.runtime, str(self.project), "立面统一用浅灰。", source="agent")
        session = self.create()
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="出一张南立面"))
        self.assertEqual(self.finished(session).status, "idle")
        prompt = self.prompts()[-1]
        self.assertLess(prompt.index("删除文件一律进回收站。"), prompt.index("出一张南立面"))
        self.assertIn("立面统一用浅灰。", prompt)
        notes.remove_note(self.runtime, str(self.project), note.id)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="再出北立面"))
        self.finished(session)
        self.assertNotIn("立面统一用浅灰。", self.prompts()[-1])

    def test_a_wakeup_is_marked_in_the_transcript_and_introduced_to_the_agent(self):
        session = self.create()
        origin = ChatMessageOrigin(kind="schedule", label="daily at 09:00", scheduleId="w1")
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="巡检渲染队列"), origin=origin)
        detail = self.finished(session)
        message = next(row for row in detail.messages if row.role == "user")
        self.assertEqual((message.content, message.origin.kind), ("巡检渲染队列", "schedule"))
        self.assertIn("[Scheduled wake-up (daily at 09:00)", self.prompts()[-1])
        reopened = chat.ChatStore(self.runtime, "http://127.0.0.1:1", commands=self.commands)
        self.assertEqual(next(row for row in reopened.get(session.id).messages if row.role == "user").origin, origin)

    def test_messages_between_conversations_and_their_chain_limit(self):
        sender, receiver = self.create("总协调"), self.create("立面")
        result = relay.send(self.store, sender.id, relay.ChatRelayRequest(to=receiver.id, text="请把南立面改成浅灰"))
        self.assertTrue(result.started)
        detail = self.finished(receiver)
        message = next(row for row in detail.messages if row.id == result.messageId)
        self.assertEqual((message.origin.kind, message.origin.chatId, message.origin.depth), ("chat", sender.id, 1))
        self.assertIn("\u00ab总协调\u00bb", self.prompts()[-1])
        # The receiver answering back is the second message of the chain; the fourth is refused.
        held = self.store._sessions[receiver.id]
        held.messages.append(ChatMessage(id="m-x", role="user", content="x", createdAt=chat._now(),
                                         origin=ChatMessageOrigin(kind="chat", chatId=sender.id, depth=relay.RELAY_DEPTH)))
        with self.assertRaises(HubFailure) as caught:
            relay.send(self.store, receiver.id, relay.ChatRelayRequest(to=sender.id, text="好"))
        self.assertEqual(caught.exception.error.code, "CHAT_RELAY_DEPTH")
        with self.assertRaises(HubFailure):
            relay.send(self.store, sender.id, relay.ChatRelayRequest(to=sender.id, text="me"))

    def test_search_reads_titles_and_what_was_said_never_tool_rows(self):
        first, second = self.create("南立面"), self.create("场地")
        self.store._sessions[first.id].messages += [
            ChatMessage(id="a1", role="user", content="把窗台降到 900", createdAt=chat._now()),
            ChatMessage(id="t1", role="tool", content="studio_request 场地 窗台", createdAt=chat._now())]
        self.store._sessions[second.id].messages.append(
            ChatMessage(id="b1", role="assistant", content="场地的等高线已经导入", createdAt=chat._now()))
        self.assertEqual([hit.chatId for hit in relay.search(self.store, "窗台")], [first.id])
        self.assertEqual([hit.chatId for hit in relay.search(self.store, "场地 等高线")], [second.id])
        self.assertEqual(relay.search(self.store, "南立面 900")[0].matches[0].messageId, "a1")
        self.assertEqual(len(relay.search(self.store, "")), 2)


class HubRouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Hub B routes ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        FilesystemProjectRepository.initialize(self.project, project_id="route-project",
                                               initial_state={"project_id": "route-project", "version": 0})

    def test_notes_page_and_agent_routes(self):
        app = create_app(HubSettings(runtime_root=self.root / "runtime"))
        chats = app.state.chats
        session = chat._SavedChat(id="0b9f1c2e-0000-4000-8000-000000000001", projectId="route-project",
                                  projectDir=str(self.project), title="t", provider="codex",
                                  createdAt=chat._now(), updatedAt=chat._now())
        chats._loaded = True
        chats._sessions[session.id] = session
        with patch.object(app.state.applications, "start"), patch.object(app.state.wakeups, "start"), \
                TestClient(app, base_url="http://127.0.0.1:8790") as client:
            saved = client.put("/api/notes/instructions", json={"projectDir": str(self.project), "text": "单位毫米"})
            self.assertEqual(saved.status_code, 200, saved.text)
            refused = client.post(f"/api/chat/sessions/{session.id}/notes", json={"action": "add", "text": "墙厚240"})
            self.assertEqual(refused.status_code, 403)
            token = {"Authorization": f"Bearer {chats.presentation_token(session.id)}"}
            kept = client.post(f"/api/chat/sessions/{session.id}/notes", json={"action": "add", "text": "墙厚240"}, headers=token)
            self.assertEqual(kept.status_code, 200, kept.text)
            view = client.get("/api/notes", params={"projectDir": str(self.project)}).json()
            self.assertEqual(view["project"]["instructions"], "单位毫米")
            self.assertEqual([row["text"] for row in view["project"]["memories"]], ["墙厚240"])
            self.assertEqual(view["global"]["memories"], [])
            note_id = view["project"]["memories"][0]["id"]
            removed = client.post(f"/api/notes/memories/{note_id}/remove", json={"projectDir": str(self.project)})
            self.assertIsNotNone(removed.json()["memories"][0]["removedAt"])
            made = client.post(f"/api/chat/sessions/{session.id}/wakeups", json={"prompt": "巡检", "everyMinutes": 30},
                               headers=token)
            self.assertEqual((made.status_code, made.json()["createdBy"]), (201, "agent"))
            listed = client.get(f"/api/chat/sessions/{session.id}/wakeups").json()
            self.assertEqual([row["id"] for row in listed], [made.json()["id"]])
            cancelled = client.post(f"/api/chat/sessions/{session.id}/wakeups/{made.json()['id']}/cancel")
            self.assertEqual(cancelled.json()["status"], "cancelled")
            self.assertEqual(client.get("/api/chat/search", params={"q": "t"}).status_code, 200)


class ToolSideTests(unittest.TestCase):
    def test_every_tool_is_approved_and_speaks_with_the_conversation_token(self):
        for name in hub_tools.TOOL_NAMES:
            self.assertIn(f"mcp__monkeyhub__{name}", providers._CLAUDE_APPROVED)
        self.assertEqual({row["name"] for row in hub_tools.tool_definitions()}, set(hub_tools.TOOL_NAMES))
        self.assertTrue(hub_tools.HUB_OWN <= mcp_server._HUB_OWN)
        sent = []

        def answer(base, path, method="GET", body=None, timeout=180, *, headers=None, png=False):
            sent.append((path, method, body, headers))
            return []

        chat_id = "0b9f1c2e-0000-4000-8000-000000000001"
        with patch.dict(os.environ, {"MONKEYHUB_PRESENTATION_TOKEN": "tok"}), patch.object(transport, "_request_json", answer):
            hub_tools.call("http://h", chat_id, "memory_note", {"action": "add", "text": "x"})
            hub_tools.call("http://h", chat_id, "chat_schedule", {"action": "add", "prompt": "p", "inMinutes": 5})
            hub_tools.call("http://h", chat_id, "chat_find", {"query": "南 立面"})
            hub_tools.call("http://h", chat_id, "chat_send", {"chatId": chat_id.replace("1", "2"), "text": "hi"})
        self.assertEqual(sent[0][:2], (f"/api/chat/sessions/{chat_id}/notes", "POST"))
        self.assertEqual(sent[0][3], {"Authorization": "Bearer tok"})
        self.assertEqual(sent[1][2], {"prompt": "p", "inMinutes": 5})
        self.assertTrue(sent[2][0].startswith("/api/chat/search?q="))
        self.assertEqual(sent[3][2]["to"], chat_id.replace("1", "2"))
        with self.assertRaises(HubFailure):
            hub_tools.call("http://h", chat_id, "chat_send", {"chatId": "not-a-uuid", "text": "hi"})


if __name__ == "__main__":
    unittest.main()
