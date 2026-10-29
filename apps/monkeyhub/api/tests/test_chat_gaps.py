"""The Hub conversation's agent gaps: any folder, no turn cap and resuming, external MCP, permission levels, subagents."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_chat import FAKE_CLI, wait_for  # noqa: E402 - the same fake CLI, sys.path set by that module

from archflow.project.repository import FilesystemProjectRepository  # noqa: E402
from monkeyhub_api import projects  # noqa: E402
from monkeyhub_api.chat import providers, store as chat  # noqa: E402
from monkeyhub_api.models import ChatCreateRequest, ChatPostRequest, HubFailure  # noqa: E402


class _Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub folder 测试 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.folder = self.root / "notes and code"
        self.folder.mkdir()
        (self.folder / "readme.txt").write_text("hello", encoding="utf-8")
        self.project = self.root / "project"
        FilesystemProjectRepository.initialize(self.project, project_id="chat-project",
                                               initial_state={"project_id": "chat-project", "version": 0})
        environment = {"CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                       "APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local"),
                       "CHAT_TEST_SECRET": "fake-private-token-12345"}
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fake = self.root / "fake cli.py"
        self.fake.write_text(FAKE_CLI, encoding="utf-8")
        self.log = self.root / "calls.jsonl"
        self.commands = {name: (sys.executable, str(self.fake), str(self.log)) for name in ("codex", "claude")}
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.addCleanup(self.close)

    def close(self):
        for row in self.store.list():
            self.store.stop(row.id)
        self.store.shutdown()

    def create(self, path, kind="folder", provider="claude"):
        return self.store.create(ChatCreateRequest(projectDir=str(path), provider=provider, kind=kind))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def finished(self, session, timeout=15):
        return wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running", timeout=timeout)


class FolderChatTests(_Fixture):
    """Conversations in any folder: they read and write it, and nothing else of the Hub's opens to them."""

    def test_a_folder_conversation_is_bound_to_the_folder_with_an_id_no_project_can_have(self):
        session = self.create(self.folder)
        self.assertEqual(session.kind, "folder")
        self.assertTrue(session.projectId.startswith("folder:"))
        self.assertEqual(Path(session.projectDir), self.folder.resolve())
        again = self.create(self.folder)
        self.assertEqual(again.projectId, session.projectId, "one folder, one id however often it is opened")
        listed = [row for row in self.store.projects() if row.kind == "folder"]
        self.assertEqual([(row.projectId, row.name, row.chatCount) for row in listed],
                         [(session.projectId, self.folder.name, 2)])

    def test_auto_opens_a_project_as_a_project_and_any_other_folder_as_itself(self):
        self.assertEqual(self.create(self.project, kind="auto").kind, "project")
        self.assertEqual(self.create(self.folder, kind="auto").kind, "folder")

    def test_a_project_a_drive_or_a_relative_path_is_not_a_folder_conversation(self):
        for path, code in ((self.project, "CHAT_FOLDER_IS_PROJECT"), (Path(self.folder.anchor), "CHAT_FOLDER_INVALID"),
                           (Path("relative"), "CHAT_FOLDER_INVALID"), (self.root / "missing", "CHAT_FOLDER_INVALID")):
            with self.subTest(path=str(path)), self.assertRaises(HubFailure) as refused:
                self.create(path)
            self.assertEqual(refused.exception.error.code, code)

    def test_a_damaged_project_is_never_opened_as_a_writable_folder(self):
        damaged = self.root / "damaged"
        damaged.mkdir()
        (damaged / "project.json").write_text("{not json", encoding="utf-8")
        for kind in ("folder", "auto"):
            with self.subTest(kind=kind), self.assertRaises(HubFailure) as refused:
                self.create(damaged, kind=kind)
            self.assertEqual(refused.exception.error.code, "CHAT_FOLDER_IS_PROJECT")

    def test_claude_starts_in_the_folder_and_may_write_it_but_not_the_hubs_own_places(self):
        session = self.create(self.folder)
        with self.store._lock:
            command, _ = self.store._command(self.store._sessions[session.id])
        at = command.index("--disallowedTools")
        end = next(index for index in range(at + 1, len(command)) if command[index].startswith("--"))
        denied = command[at + 1:end]
        self.assertEqual(tuple(denied), providers._claude_denied(None, self.runtime))
        folder_rule = providers._edit_rule(str(self.folder.resolve()), "/**")
        self.assertNotIn(folder_rule, denied, "the folder itself is writable")
        self.assertTrue(denied, "the Hub's records and Claude's own configuration stay closed")
        added = [command[index + 1] for index, value in enumerate(command) if value == "--add-dir"]
        self.assertEqual(added, [str(self.store._scratch_path(session.id))], "the folder is the workspace, not an extra dir")
        mcp = json.loads(command[command.index("--mcp-config") + 1])["mcpServers"]["monkeyhub"]
        self.assertIn("--folder", mcp["args"], "no design tools without a project")

    def test_a_folder_turn_runs_in_the_folder_with_its_own_envelope(self):
        session = self.create(self.folder)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="hello"))
        finished = wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running")
        self.assertEqual(finished.status, "idle", finished.error)
        call = self.calls()[-1]
        self.assertEqual(Path(call["cwd"]).resolve(), self.folder.resolve())
        self.assertIn("working in the folder " + session.projectDir, call["prompt"])
        self.assertNotIn("reversible candidates", call["prompt"])

    def test_a_folder_turn_takes_no_design_context(self):
        from monkeyhub_api.models import ChatDesignContext

        session = self.create(self.folder)
        with self.assertRaises(HubFailure) as refused:
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="raise it",
                designContext=ChatDesignContext(sourceRunId="run-001", stateDigest="a" * 64)))
        self.assertEqual(refused.exception.error.code, "CHAT_FOLDER_NO_DESIGN")

    def test_a_running_folder_turn_never_holds_the_active_project(self):
        session = self.create(self.folder)
        with self.store._lock:
            self.store._running[session.id] = chat._Running()
        try:
            with self.store.project_configuration(str(self.project)):
                pass
            with self.store.application_lifecycle("monkeyarch", stopping=True, project_dir=str(self.project)):
                pass
        finally:
            with self.store._lock:
                self.store._running.pop(session.id, None)

    def test_a_moved_folder_refuses_further_messages(self):
        session = self.create(self.folder)
        moved = self.root / "moved"
        os.replace(self.folder, moved)
        with self.assertRaises(HubFailure) as refused:
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="hello"))
        self.assertEqual(refused.exception.error.code, "CHAT_FOLDER_INVALID")
        self.assertNotEqual(projects._folder(str(moved))[0], session.projectId, "another place is another folder")


class ResumeTests(_Fixture):
    """No turn cap, and a turn a Hub restart cut off goes on by itself, once."""

    def reopen(self, resume=True):
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands, resume=resume)

    def cut_off(self, session):
        """What a Hub that died mid-turn leaves behind: the record says running, the last answer streaming."""
        path = self.runtime / "chats" / f"{session.id}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["status"] = "running"
        saved["messages"][-1]["status"] = "streaming"
        path.write_text(json.dumps(saved), encoding="utf-8")

    def test_a_cut_off_turn_resumes_on_its_own_native_session_once(self):
        session = self.create(self.folder)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="hello"))
        self.finished(session)
        native = self.store._sessions[session.id].nativeSessionId
        self.assertTrue(native)
        self.store.shutdown()
        self.cut_off(session)
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands, resume=True)
        self.store.list()  # loading is what resumes
        resumed = self.finished(session)
        self.assertEqual(resumed.status, "idle", resumed.error)
        call = self.calls()[-1]
        self.assertEqual(call["args"][call["args"].index("--resume") + 1], native)
        self.assertIn("MonkeyHub restarted", call["prompt"])
        self.assertIn("interrupted", [row.status for row in resumed.messages], "what was streaming says it was cut off")
        # Cut off again during the same user message: the person decides, nothing loops.
        self.store.shutdown()
        self.cut_off(session)
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands, resume=True)
        again = self.store.get(session.id)
        self.assertEqual(again.status, "interrupted")
        self.assertEqual(again.error.code, "CHAT_INTERRUPTED")

    def test_without_resume_a_cut_off_turn_waits_for_the_person(self):
        session = self.create(self.folder)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="hello"))
        self.finished(session)
        self.store.shutdown()
        self.cut_off(session)
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.assertEqual(self.store.get(session.id).status, "interrupted")

    def test_closing_cuts_a_long_turn_off_and_the_next_start_resumes_it(self):
        session = self.create(self.folder, provider="codex")
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="pause-test"))
        wait_for(lambda: self.store.get(session.id), lambda row: any(item.role == "assistant" for item in row.messages))
        began = time.monotonic()
        with patch.object(chat, "_SHUTDOWN_GRACE_S", 0.5):
            self.store.shutdown()
        self.assertLess(time.monotonic() - began, 15, "closing waited for the turn instead of cutting it off")
        saved = json.loads((self.runtime / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "running", "left for the next start to resume")
        self.assertIsNone(saved["error"])
        native = saved["nativeSessionId"]
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands, resume=True)
        self.store.list()
        wait_for(lambda: self.calls(), lambda rows: len(rows) >= 2)
        call = self.calls()[-1]
        self.assertEqual(call["args"][call["args"].index("resume") + 1], native)
        self.assertIn("MonkeyHub restarted", call["prompt"])
        self.store.stop(session.id)


class PermissionLevelTests(_Fixture):
    """Read only, ask before changing anything, or anything; and the "ask" questions as cards."""

    def command(self, session):
        with self.store._lock:
            return self.store._command(self.store._sessions[session.id])[0]

    @staticmethod
    def approved(command):
        return command[command.index("--allowedTools") + 1].split(",")

    def test_a_folder_starts_at_ask_and_a_project_at_open(self):
        self.assertEqual(self.create(self.folder).permissionMode, "ask")
        self.assertEqual(self.create(self.project, kind="project").permissionMode, "open")

    def test_each_level_names_what_a_turn_may_use_unasked(self):
        session = self.create(self.folder)
        self.store.set_permission_mode(session.id, "read")
        read = self.command(session)
        self.assertEqual(read[read.index("--permission-mode") + 1], "dontAsk")
        for name in ("Write", "Edit", "Bash", "PowerShell"):
            self.assertNotIn(name, self.approved(read), name)
        for name in ("Read", "Grep", "WebFetch", "WebSearch", "Task", "Agent"):
            self.assertIn(name, self.approved(read), name)
        self.store.set_permission_mode(session.id, "ask")
        ask = self.command(session)
        self.assertEqual(ask[ask.index("--permission-mode") + 1], "manual")
        self.assertEqual(ask[ask.index("--permission-prompt-tool") + 1], "stdio")
        self.assertEqual(ask[ask.index("--permission-prompts") + 1], "host")
        self.assertNotIn("Bash", self.approved(ask))
        self.assertIn(providers._edit_rule(self.store._scratch_path(session.id), "/**"), self.approved(ask),
                      "the chat's own scratch stays writable unasked")
        self.store.set_permission_mode(session.id, "open")
        opened = self.command(session)
        self.assertEqual(opened[opened.index("--permission-prompts") + 1], "none")
        for name in ("Write", "Edit", "Bash", "PowerShell", "WebFetch", "WebSearch", "Agent"):
            self.assertIn(name, self.approved(opened), name)

    def test_the_level_cannot_change_under_a_running_turn_or_to_something_else(self):
        session = self.create(self.folder)
        with self.assertRaises(HubFailure) as refused:
            self.store.set_permission_mode(session.id, "everything")
        self.assertEqual(refused.exception.error.code, "CHAT_PERMISSION_MODE_INVALID")
        with self.store._lock:
            self.store._running[session.id] = chat._Running()
        try:
            with self.assertRaises(HubFailure) as running:
                self.store.set_permission_mode(session.id, "open")
            self.assertEqual(running.exception.error.code, "CHAT_RUNNING")
        finally:
            with self.store._lock:
                self.store._running.pop(session.id, None)

    def asked(self, session):
        return wait_for(lambda: self.store.get(session.id), lambda row: row.attention == "permission")

    def answer_file(self):
        return json.loads((self.root / "ask-answer.json").read_text(encoding="utf-8"))

    def test_an_ask_level_question_is_a_card_and_its_answer_goes_back_to_the_cli(self):
        from monkeyhub_api.models import ChatPermissionRequest

        session = self.create(self.folder)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="ask-test"))
        card = next(row for row in self.asked(session).messages if row.permission is not None)
        self.assertIn("echo hi", card.permission.title)
        self.assertEqual([option.kind for option in card.permission.options], ["allow_once", "allow_always", "reject_once"])
        self.store.resolve_permission(session.id, card.permission.id,
                                      ChatPermissionRequest(projectId=session.projectId, optionId="allow_once"))
        self.assertEqual(self.finished(session).status, "idle")
        answer = self.answer_file()["response"]
        self.assertEqual((answer["subtype"], answer["request_id"]), ("success", "req-ask-1"))
        self.assertEqual(answer["response"], {"behavior": "allow", "updatedInput": {"command": "echo hi"}})

    def test_no_is_a_deny_and_always_answers_the_next_question_here_by_itself(self):
        from monkeyhub_api.chat import acp_grants
        from monkeyhub_api.models import ChatPermissionRequest
        from monkeyhub_api.settings import tool_grants

        session = self.create(self.folder)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="ask-test"))
        card = next(row for row in self.asked(session).messages if row.permission is not None)
        self.store.resolve_permission(session.id, card.permission.id,
                                      ChatPermissionRequest(projectId=session.projectId, optionId="reject_once"))
        self.finished(session)
        self.assertEqual(self.answer_file()["response"]["response"]["behavior"], "deny")
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="ask-test again"))
        card = [row for row in self.asked(session).messages if row.permission is not None][-1]
        self.store.resolve_permission(session.id, card.permission.id,
                                      ChatPermissionRequest(projectId=session.projectId, optionId=acp_grants.HUB_PROJECT))
        self.finished(session)
        self.assertTrue(tool_grants.covers(self.runtime, session.projectId, "run"))
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="ask-test third"))
        finished = self.finished(session)
        self.assertEqual(self.answer_file()["response"]["response"]["behavior"], "allow")
        self.assertTrue(any("allowed by your earlier answer here" in row.content for row in finished.messages))

    def test_edits_can_be_allowed_for_a_folder_and_never_for_a_project(self):
        from monkeyhub_api.settings import tool_grants

        tool_grants.allow(self.runtime, "folder:abc", "edit")
        with self.assertRaises(HubFailure):
            tool_grants.allow(self.runtime, "chat-project", "edit")

    def test_a_folders_always_answer_keeps_the_folder_settings_shows(self):
        from monkeyhub_api.chat import acp_grants
        from monkeyhub_api.models import ChatPermissionRequest
        from monkeyhub_api.settings import tool_grants

        session = self.create(self.folder)
        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="ask-test"))
        card = next(row for row in self.asked(session).messages if row.permission is not None)
        self.store.resolve_permission(session.id, card.permission.id,
                                      ChatPermissionRequest(projectId=session.projectId, optionId=acp_grants.HUB_PROJECT))
        self.finished(session)
        [grant] = tool_grants.read(self.runtime).grants
        self.assertEqual((grant.project_id, grant.place), (session.projectId, session.projectDir))

    def test_a_new_codex_conversation_keeps_asking_as_it_always_did(self):
        self.assertEqual(self.create(self.project, kind="project", provider="codex").permissionMode, "ask")
        self.assertEqual(self.create(self.project, kind="project", provider="claude").permissionMode, "open")

    def codex_acp(self, mode):
        """One Codex ACP turn at this level, through a stand-in adapter that records what it was started with."""
        from monkeyhub_api.chat import acp_session as adapter

        started = []

        class Recorder:
            def __init__(self, **arguments):
                started.append(arguments)
                self.default_model = "fixture-model-a"

            def prompt(self, text, session_id, model, on_session, timeout_s, *, images=()):
                on_session("fixture/session:recorded")

            def close(self):
                pass

        session = self.create(self.folder, provider="codex")
        self.store._sessions[session.id].transport = "acp"
        self.store.set_permission_mode(session.id, mode)
        with patch.object(adapter, "CodexAcpSession", Recorder):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="hello"))
            self.finished(session)
        return session, started[0]["environment"]["INITIAL_AGENT_MODE"]

    def test_codex_takes_the_level_as_its_adapter_mode(self):
        self.assertEqual(self.codex_acp("open")[1], "agent")
        self.assertEqual(self.codex_acp("ask")[1], "read-only")
        session, mode = self.codex_acp("read")
        self.assertEqual(mode, "read-only")

    def test_changing_the_level_starts_codex_on_a_new_adapter(self):
        closed = []

        class Cached:
            def close(self):
                closed.append(True)

        session = self.create(self.folder, provider="codex")
        self.store._acp_sessions[session.id] = Cached()
        self.store.set_permission_mode(session.id, "open")
        self.assertEqual(closed, [True])
        self.assertNotIn(session.id, self.store._acp_sessions)

    def test_a_read_only_codex_question_is_answered_no_and_says_so(self):
        session = self.create(self.folder, provider="codex")
        self.store.set_permission_mode(session.id, "read")
        with self.store._lock:
            saved = self.store._sessions[session.id]
            saved.transport, saved.acpSessionId = "acp", "fixture/session:read"
            saved.messages.append(chat.ChatMessage(id="u-1", role="user", content="look", createdAt="2026-10-29"))
            self.store._running[session.id] = chat._Running(turns=["u-1"])
        try:
            future = self.store._acp_permission(session.id, {
                "sessionId": "fixture/session:read",
                "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                            {"optionId": "no", "name": "Reject", "kind": "reject_once"}],
                "toolCall": {"toolCallId": "t1", "title": "Run command?", "rawInput": {"command": "del x"}}})
            self.assertEqual(future.result(timeout=1), "no")
            self.assertIn("refused: this conversation is read only", self.store.get(session.id).messages[-1].content)
        finally:
            with self.store._lock:
                self.store._running.pop(session.id, None)


class ExternalMcpTests(_Fixture):
    """The person's other MCP servers: chosen in the Hub, given to the turn, their keys never shown."""

    SECRET = "Bearer drive-secret-token-123456"

    def setUp(self):
        super().setUp()
        claude = self.root / "claude"
        claude.mkdir(exist_ok=True)
        (claude / ".claude.json").write_text(json.dumps({"mcpServers": {
            "blender": {"command": "uvx", "args": ["blender-mcp"]},
            "drive": {"type": "http", "url": "https://drive.example/mcp", "headers": {"Authorization": self.SECRET}},
            "monkeyhub": {"command": "evil"},
            "bad name": {"command": "x"},
        }}), encoding="utf-8")

    def test_claude_codes_servers_are_listed_without_their_keys_and_used_only_when_switched_on(self):
        from monkeyhub_api.chat import mcp_servers

        view = mcp_servers.public(self.runtime)
        self.assertFalse(view.fromClaude)
        self.assertEqual([(row.name, row.enabled) for row in view.servers], [("blender", False), ("drive", False)])
        self.assertEqual(mcp_servers.effective(self.runtime), {})
        view = mcp_servers.update(self.runtime, mcp_servers.McpServersUpdate(fromClaude=True, disabled=["blender"]))
        self.assertEqual([(row.name, row.enabled) for row in view.servers], [("blender", False), ("drive", True)])
        self.assertNotIn(self.SECRET, view.model_dump_json())
        self.assertEqual(view.servers[1].envKeys, ["Authorization"])
        self.assertEqual(list(mcp_servers.effective(self.runtime)), ["drive"])

    def test_a_turn_reads_the_servers_from_a_file_and_may_use_them_at_open_only(self):
        from monkeyhub_api.chat import mcp_servers

        mcp_servers.update(self.runtime, mcp_servers.McpServersUpdate(fromClaude=True))
        session = self.create(self.folder)
        self.store.set_permission_mode(session.id, "open")
        with self.store._lock:
            command, _ = self.store._command(self.store._sessions[session.id])
        self.assertNotIn(self.SECRET, " ".join(command), "a key is never on a command line")
        at = command.index("--mcp-config")
        self.assertEqual(list(json.loads(command[at + 1])["mcpServers"]), ["monkeyhub"])
        given = json.loads(Path(command[at + 2]).read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(sorted(given), ["blender", "drive"])
        self.assertTrue(Path(command[at + 2]).resolve().is_relative_to((self.runtime / "config").resolve()))
        approved = command[command.index("--allowedTools") + 1].split(",")
        self.assertIn("mcp__blender", approved)
        self.assertIn("mcp__drive", approved)
        self.assertEqual(providers._redact("header " + self.SECRET), "header [redacted]")
        self.store.set_permission_mode(session.id, "read")
        with self.store._lock:
            read, _ = self.store._command(self.store._sessions[session.id])
        self.assertEqual(read.count("--mcp-config"), 1)
        self.assertTrue(read[read.index("--mcp-config") + 2].startswith("--"), "read gives no other server")

    def test_codex_gets_a_keyed_server_only_through_its_environment(self):
        from monkeyhub_api.chat import mcp_servers

        mcp_servers.update(self.runtime, mcp_servers.McpServersUpdate(fromClaude=True))
        session = self.create(self.folder, provider="codex")
        with self.store._lock:
            saved = self.store._sessions[session.id]
            shown = self.store._codex_mcp(saved, self.commands["codex"], dict(os.environ))
            private = self.store._codex_mcp(saved, self.commands["codex"], dict(os.environ), private=True)
        self.assertTrue(shown["blender"]["enabled"])
        self.assertNotIn("drive", shown, "a server with keys never goes on a command line")
        self.assertEqual(private["drive"]["http_headers"], {"Authorization": self.SECRET})
        self.assertTrue(private["monkeyhub"]["enabled"])

    def test_claude_codes_servers_for_the_folder_join_its_turns(self):
        from monkeyhub_api.chat import mcp_servers

        config = self.root / "claude" / ".claude.json"
        saved = json.loads(config.read_text(encoding="utf-8"))
        saved["projects"] = {str(self.folder).replace("\\", "/") + "/": {
            "mcpServers": {"rhino": {"command": "rhino-mcp"}},
            "enabledMcpjsonServers": ["approved"], "disabledMcpjsonServers": ["refused"]}}
        config.write_text(json.dumps(saved), encoding="utf-8")
        (self.folder / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "approved": {"command": "ok-mcp"}, "refused": {"command": "no-mcp"}, "unasked": {"command": "maybe-mcp"}}}),
            encoding="utf-8")
        mcp_servers.update(self.runtime, mcp_servers.McpServersUpdate(fromClaude=True))
        self.assertEqual(sorted(mcp_servers.effective(self.runtime, str(self.folder))),
                         ["approved", "blender", "drive", "rhino"])
        self.assertEqual(sorted(mcp_servers.effective(self.runtime, str(self.project))), ["blender", "drive"])

    def test_a_server_is_added_to_and_taken_off_the_hubs_own_list(self):
        from monkeyhub_api.chat import mcp_servers

        view = mcp_servers.update(self.runtime, mcp_servers.McpServersUpdate(add={
            "pdf": {"command": "pdf-mcp", "args": ["--port", "0"], "env": {"PDF_KEY": "pdf-secret-123456"}}}))
        [row] = [item for item in view.servers if item.source == "hub"]
        self.assertEqual((row.name, row.command, row.args, row.envKeys, row.enabled),
                         ("pdf", "pdf-mcp", ["--port", "0"], ["PDF_KEY"], True))
        self.assertNotIn("pdf-secret-123456", view.model_dump_json())
        self.assertIn("pdf", mcp_servers.effective(self.runtime))
        with self.assertRaises(HubFailure):
            mcp_servers.update(self.runtime, mcp_servers.McpServersUpdate(add={"monkeyhub": {"command": "x"}}))
        view = mcp_servers.update(self.runtime, mcp_servers.McpServersUpdate(remove=["pdf"]))
        self.assertEqual([item for item in view.servers if item.source == "hub"], [])

    def test_the_settings_routes_read_and_change_the_list(self):
        from fastapi.testclient import TestClient
        from monkeyhub_api.app.composition import HubSettings, create_app

        with patch("monkeyhub_api.app.composition.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            listed = client.get("/api/chat/mcp-servers")
            self.assertEqual(listed.status_code, 200, listed.text)
            changed = client.put("/api/chat/mcp-servers", json={"fromClaude": True})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertTrue(changed.json()["fromClaude"])
            self.assertNotIn(self.SECRET, changed.text)
            session = self.create(self.folder)
            level = client.put(f"/api/chat/sessions/{session.id}/permission-mode", json={"permissionMode": "read"})
            self.assertEqual(level.status_code, 200, level.text)
            self.assertEqual(level.json()["permissionMode"], "read")


class SubagentTests(_Fixture):
    """Subagents are allowed at every level; their calls are rows under the call that started them, their words
    never this turn's answer."""

    def test_subagents_are_allowed_at_every_level(self):
        session = self.create(self.folder)
        for mode in ("read", "ask", "open"):
            self.store.set_permission_mode(session.id, mode)
            with self.store._lock:
                command, _ = self.store._command(self.store._sessions[session.id])
            approved = command[command.index("--allowedTools") + 1].split(",")
            self.assertIn("Agent", approved, mode)
            self.assertIn("Task", approved, mode)

    def test_a_subagents_calls_are_marked_rows_and_its_words_stay_out_of_the_answer(self):
        from monkeyhub_api.chat import activity

        session = self.create(self.folder)
        saved = chat._SavedChat.model_validate(session.model_dump())
        saved.messages.append(chat.ChatMessage(id="u-1", role="user", content="survey", createdAt="2026-10-29"))
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "I'll ask a helper."},
             {"type": "tool_use", "id": "toolu_task", "name": "Agent",
              "input": {"description": "survey the folder", "prompt": "list it", "subagent_type": "Explore"}}]}},
            {"type": "assistant", "parent_tool_use_id": "toolu_task", "message": {"content": [
                {"type": "text", "text": "subagent thinking aloud"},
                {"type": "tool_use", "id": "toolu_sub", "name": "Read", "input": {"file_path": "readme.txt"}}]}},
            {"type": "stream_event", "parent_tool_use_id": "toolu_task", "event": {
                "type": "content_block_delta", "delta": {"type": "text_delta", "text": "subagent delta"}}},
            {"type": "user", "parent_tool_use_id": "toolu_task", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_sub", "content": "hello"}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_task", "content": "the folder holds readme.txt"}]}},
        ]
        with self.store._lock:
            for event in events:
                self.store._event(saved, event, {})
        rows = {row.id.split(":", 1)[1]: row for row in saved.messages if row.role == "tool"}
        self.assertTrue(rows["toolu_task"].content.startswith("Agent · survey the folder · completed"))
        self.assertTrue(rows["toolu_sub"].content.startswith(activity.SUBAGENT_MARK + "Read · completed"))
        self.assertEqual(rows["toolu_sub"].parentCallId, "u-1:toolu_task", "it folds under the call that started it")
        self.assertIsNone(rows["toolu_task"].parentCallId)
        answer = "".join(row.content for row in saved.messages if row.role == "assistant")
        self.assertEqual(answer, "I'll ask a helper.")
        order = [row.id for row in saved.messages]
        self.assertLess(order.index("u-1:toolu_task"), order.index("u-1:toolu_sub"), "the helper's row follows its call")


if __name__ == "__main__":
    unittest.main()
