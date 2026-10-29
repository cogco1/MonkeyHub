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


if __name__ == "__main__":
    unittest.main()
