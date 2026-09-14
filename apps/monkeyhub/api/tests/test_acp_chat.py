"""Hub persistence and HTTP decisions through the real ACP SDK, without a model."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_chat import FAKE_CLI, wait_for
from test_acp_session import FAKE_AGENT, PNG_IMAGE
from fastapi.testclient import TestClient
from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api import chat
from monkeyhub_api.main import HubSettings, _windows_runtime_root, create_app
from monkeyhub_api.models import ChatCreateRequest, ChatPostRequest


HUB_AGENT = FAKE_AGENT.replace("asyncio.run(main())", "") + r'''
from acp.schema import ToolCallProgress
BaseAgent = FakeAgent
class FakeAgent(BaseAgent):
    async def initialize(self, **kwargs):
        log("config", codex=os.environ["CODEX_PATH"], config=json.loads(os.environ["CODEX_CONFIG"]),
            mode=os.environ["INITIAL_AGENT_MODE"])
        return await super().initialize(**kwargs)

    async def prompt(self, session_id, prompt, **kwargs):
        log("hub_prompt", sessionId=session_id,
            blocks=[block.model_dump(by_alias=True, exclude_none=True) for block in prompt])
        text = prompt[0].text.rsplit("\n\n", 1)[-1]
        if text != "candidate":
            return await super().prompt(session_id, [prompt[0].model_copy(update={"text": text}), *prompt[1:]], **kwargs)
        for identifier, server, readback in (("bound", "monkeyhub", "ok"), ("failed", "monkeyhub", "failed"),
                                             ("other", "other-server", "ok"), ("file", None, "ok")):
            await self.client.session_update(session_id=session_id, update=ToolCallStart(
                session_update="tool_call", tool_call_id=identifier, title="Studio call", kind="execute",
                status="in_progress", raw_input={"server": server, "tool": "studio_request",
                "arguments": {"method": "GET", "path": "/api/jobs/job-1"}},
                _meta={"is_mcp_tool_call": server is not None},
            ))
            result = {"content": [{"type": "text", "text": json.dumps({
                "status": "succeeded", "candidateId": "candidate-" + identifier, "readback": readback,
            })}]}
            await self.client.session_update(session_id=session_id, update=ToolCallProgress(
                session_update="tool_call_update", tool_call_id=identifier,
                status="completed", raw_output={"result": result, "error": None},
            ))
        for _ in range(30):
            await self.emit("x")
        return PromptResponse(stop_reason="end_turn")

asyncio.run(main())
'''


class RuntimeRootTests(unittest.TestCase):
    def test_short_windows_verbatim_paths_use_equivalent_ordinary_spelling(self):
        self.assertEqual(_windows_runtime_root(r"\\?\E:\MonkeyHub 数据", windows=True), r"E:\MonkeyHub 数据")
        self.assertEqual(_windows_runtime_root(r"\\?\UNC\server\share\MonkeyHub", windows=True), r"\\server\share\MonkeyHub")
        self.assertEqual(_windows_runtime_root(r"E:\MonkeyHub 数据", windows=True), r"E:\MonkeyHub 数据")
        self.assertEqual(_windows_runtime_root(r"\\server\share\MonkeyHub", windows=True), r"\\server\share\MonkeyHub")

    def test_device_long_and_non_windows_paths_are_not_rewritten(self):
        self.assertEqual(_windows_runtime_root(r"\\.\PIPE\MonkeyHub", windows=True), r"\\.\PIPE\MonkeyHub")
        long_path = "\\\\?\\E:\\" + ("deep\\" * 60) + "MonkeyHub"
        self.assertEqual(_windows_runtime_root(long_path, windows=True), long_path)
        self.assertEqual(_windows_runtime_root(r"\\?\E:\MonkeyHub 数据", windows=False), r"\\?\E:\MonkeyHub 数据")


@unittest.skipUnless(os.name == "nt", "Windows desktop source paths")
class AcpCommandTests(unittest.TestCase):
    def test_adapter_entrypoint_preserves_drive_and_unc_locations(self):
        for root, expected in (
            (r"\\?\E:\MonkeyHub 安装", r"E:\MonkeyHub 安装"),
            (r"\\?\UNC\server\share\MonkeyHub", r"\\server\share\MonkeyHub"),
            (r"E:\MonkeyHub 安装", r"E:\MonkeyHub 安装"),
            (r"\\server\share\MonkeyHub", r"\\server\share\MonkeyHub"),
        ):
            with self.subTest(root=root):
                source = Path(root) / "apps/monkeyhub/api/monkeyhub_api/chat.py"
                with patch.object(chat.Path, "resolve", return_value=source), \
                     patch.object(chat.Path, "is_file", return_value=True), \
                     patch.object(chat.importlib.util, "find_spec", return_value=object()):
                    command = chat._codex_acp_command()
                self.assertEqual(command, (
                    str(Path(root) / "_runtime/node/node.exe"),
                    str(Path(expected) / "apps/monkeyhub/node_modules/@agentclientprotocol/codex-acp/dist/index.js"),
                ))

    @unittest.skipUnless(shutil.which("node"), "Node is required for entrypoint resolution")
    def test_node_loads_adapter_from_verbatim_desktop_source(self):
        with tempfile.TemporaryDirectory(prefix="Hub ACP 入口 ") as directory:
            root = Path(directory).resolve()
            hub = root / "apps/monkeyhub"
            source = hub / "api/monkeyhub_api/chat.py"
            source.parent.mkdir(parents=True)
            source.touch()
            adapter = hub / "node_modules/@agentclientprotocol/codex-acp/dist/index.js"
            adapter.parent.mkdir(parents=True)
            adapter.write_text("console.log('adapter entrypoint loaded');", encoding="utf-8")
            with patch.object(chat, "__file__", "\\\\?\\" + str(source)):
                command = chat._codex_acp_command()
            self.assertIsNotNone(command)
            checked = subprocess.run([command[0], "--check", command[1]], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            loaded = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(loaded.stdout.strip(), "adapter entrypoint loaded")


class AcpChatTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Hub ACP 测试 ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project, self.runtime = self.root / "project", self.root / "runtime"
        FilesystemProjectRepository.initialize(self.project, project_id="acp-project", initial_state={"project_id": "acp-project", "version": 0})
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

    def finished(self, session):
        result = wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running")
        self.assertEqual(result.status, "idle", result.error)
        return result

    def permission(self, session):
        result = wait_for(lambda: self.store.get(session.id), lambda row: any(m.permission for m in row.messages) or row.status != "running")
        self.assertEqual(result.status, "running", result.error)
        return next(m.permission for m in result.messages if m.permission)

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_two_turns_reuse_process_and_reopen_restores_without_replay(self):
        before = {str(p): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        session = self.create()
        with patch.object(self.store, "_codex_mcp", wraps=self.store._codex_mcp) as configuration:
            for text in ("first", "second"):
                self.post(session, text)
                self.finished(session)
            self.assertEqual(configuration.call_count, 1)
        saved = json.loads((self.runtime / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["transport"], "acp")
        self.assertEqual(saved["acpSessionId"], "fixture/session:not-a-uuid")
        self.assertIsNone(saved["nativeSessionId"])
        calls = self.calls()
        self.assertEqual(len({r["pid"] for r in calls}), 1)
        config = next(r for r in calls if r["event"] == "config")
        self.assertEqual(config["mode"], "read-only")
        self.assertEqual(config["codex"], str(Path(sys.executable).resolve()))
        self.assertFalse(config["config"]["mcp_servers"]["unrelated"]["enabled"])
        self.assertFalse(config["config"]["mcp_servers"]["remote-unrelated"]["enabled"])
        self.assertTrue(config["config"]["mcp_servers"]["monkeyhub"]["required"])
        self.assertEqual(next(r for r in calls if r["event"] == "new")["mcp"], [])
        self.store.shutdown()
        self.store = self.open_store()
        self.post(session, "third")
        result = self.finished(session)
        self.assertEqual([m.content for m in result.messages if m.role == "assistant"], ["answer: first", "answer: second", "answer: third"])
        self.assertEqual(len([r for r in self.calls() if r["event"] == "new"]), 1)
        self.assertEqual(len([r for r in self.calls() if r["event"] == "load"]), 1)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.project.rglob("*") if p.is_file()})

    def test_http_permission_requires_current_project_option_and_single_decision(self):
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            session = self.create()
            self.post(session, "permission")
            permission = self.permission(session)
            endpoint = f"/api/chat/sessions/{session.id}/permissions/{permission.id}"
            self.assertFalse(any(r["event"] == "permission" for r in self.calls()))
            self.assertEqual(client.post(endpoint, json={"projectId": "wrong", "optionId": "allow"}).status_code, 409)
            self.assertEqual(client.post(endpoint, json={"projectId": session.projectId, "optionId": "unknown"}).status_code, 422)
            answer = client.post(endpoint, json={"projectId": session.projectId, "optionId": "allow"})
            self.assertEqual(answer.status_code, 200, answer.text)
            self.finished(session)
            self.assertEqual(client.post(endpoint, json={"projectId": session.projectId, "optionId": "allow"}).status_code, 409)
            self.post(session, "permission")
            permission = self.permission(session)
            answer = client.post(f"/api/chat/sessions/{session.id}/permissions/{permission.id}", json={"projectId": session.projectId, "optionId": None})
            self.assertEqual(answer.status_code, 200, answer.text)
            self.finished(session)
            outcomes = [{k: v for k, v in r["outcome"].items() if v is not None}
                        for r in self.calls() if r["event"] == "permission"]
            self.assertEqual(outcomes, [{"outcome": "selected", "optionId": "allow"}, {"outcome": "cancelled"}])

    def test_attachments_reach_acp_once_and_remain_downloadable_after_reopen(self):
        before = {str(path): path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        session = self.create()
        files = [
            ("参考 图.png", "image/png", base64.b64decode(PNG_IMAGE)),
            ("brief.pdf", "application/pdf", b"%PDF-1.4\n% fixture attachment\n%%EOF\n"),
            ("说明.txt", "text/plain", "保留这份参考。\n".encode("utf-8")),
        ]
        self.store.post(session.id, ChatPostRequest(
            projectId=session.projectId, content="inspect attached references",
            attachments=[{"name": name, "mimeType": mime, "data": base64.b64encode(data).decode("ascii")}
                         for name, mime, data in files],
        ))
        first = self.finished(session)
        metadata = next(message.attachments for message in first.messages if message.role == "user")
        self.assertEqual([(item.name, item.mimeType, item.size) for item in metadata],
                         [(name, mime, len(data)) for name, mime, data in files])
        first_turn = next(call for call in self.calls() if call["event"] == "hub_prompt")
        self.assertEqual(first_turn["blocks"][1:], [{"type": "image", "mimeType": "image/png", "data": PNG_IMAGE}])
        prompt = first_turn["blocks"][0]["text"]
        self.assertIn("inspect attached references", prompt)
        references = json.loads(prompt.rsplit("\n", 1)[-1])
        self.assertEqual([(item["name"], item["mimeType"]) for item in references],
                         [(name, mime) for name, mime, _ in files])
        for reference, attachment, (_, _, data) in zip(references, metadata, files, strict=True):
            path = Path(reference["path"])
            self.assertEqual(path.parent, self.runtime / "chats" / session.id / "attachments")
            self.assertEqual(path.stem, attachment.id)
            self.assertEqual(path.read_bytes(), data)
        saved = json.loads((self.runtime / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["messages"][0]["attachments"], [item.model_dump() for item in metadata])
        self.assertNotIn(PNG_IMAGE, json.dumps(saved))

        self.post(session, "plain follow-up")
        self.finished(session)
        self.store.shutdown()
        self.store = self.open_store()
        restored = self.store.get(session.id)
        self.assertEqual(restored.messages[0].attachments, metadata)
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            other = self.create()
            for attachment, (_, _, data) in zip(metadata, files, strict=True):
                suffix = f"/attachments/{attachment.id}"
                response = client.get(f"/api/chat/sessions/{session.id}" + suffix)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, data)
                self.assertEqual(client.get(f"/api/chat/sessions/{other.id}" + suffix).status_code, 404)
            self.post(session, "continue after reopen")
            result = self.finished(session)
            self.assertEqual(result.messages[0].attachments, metadata)
        turns = [call for call in self.calls() if call["event"] == "hub_prompt"]
        self.assertEqual(len(turns), 3)
        self.assertEqual([turn["sessionId"] for turn in turns], ["fixture/session:not-a-uuid"] * 3)
        for turn in turns[1:]:
            self.assertEqual([block["type"] for block in turn["blocks"]], ["text"])
            self.assertTrue(all(reference["name"] not in turn["blocks"][0]["text"] for reference in references))
        self.assertEqual(len([call for call in self.calls() if call["event"] == "new"]), 1)
        self.assertEqual(len([call for call in self.calls() if call["event"] == "load"]), 1)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.project.rglob("*") if path.is_file()})

    def test_image_without_agent_capability_fails_without_cli_fallback(self):
        session = self.create()
        with patch.dict(os.environ, {"ACP_IMAGE_CAPABILITY": "missing"}):
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="inspect image",
                attachments=[{"name": "reference.png", "mimeType": "image/png", "data": PNG_IMAGE}],
            ))
            result = wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "CHAT_ACP_FAILED")
        self.assertIn("does not support image attachments", result.error.detail)
        self.assertFalse(any(call["event"] in {"new", "load", "prompt", "hub_prompt"} for call in self.calls()))
        self.assertFalse(any(message.role == "assistant" for message in result.messages))
        self.assertFalse((self.root / "cli.jsonl").exists())
        attachment = result.messages[0].attachments[0]
        self.assertEqual(self.store.attachment(session.id, attachment.id)[1].read_bytes(), base64.b64decode(PNG_IMAGE))

    def test_stop_dismisses_permission_and_next_turn_can_continue(self):
        session = self.create()
        self.post(session, "permission")
        self.permission(session)
        self.store.stop(session.id)
        stopped = wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running")
        self.assertEqual(stopped.status, "interrupted")
        self.assertFalse(any(m.permission for m in stopped.messages))
        self.post(session, "after stop")
        self.finished(session)
        self.store.shutdown()
        self.store = self.open_store()
        self.assertFalse(any(m.permission for m in self.store.get(session.id).messages))

    def test_only_bound_successful_tool_readback_exposes_candidate(self):
        session = self.create()
        with patch.object(self.store, "_save", wraps=self.store._save) as save:
            self.post(session, "candidate")
            result = self.finished(session)
        self.assertEqual([m.candidateId for m in result.messages if m.candidateId], ["candidate-bound"])
        self.assertEqual(len([m for m in result.messages if m.role == "tool"]), 4)
        self.assertEqual([m.content for m in result.messages if m.role == "assistant"], ["x" * 30])
        self.assertLess(save.call_count, 15, "streaming must not fsync on every chunk")
        self.store.shutdown()
        self.store = self.open_store()
        self.assertEqual(self.store.get(session.id).messages[-1].content, "x" * 30)

    def test_runtime_close_cancels_only_its_project_permission_and_agent(self):
        other_project = self.root / "second-project"
        FilesystemProjectRepository.initialize(other_project, project_id="other-project",
                                              initial_state={"project_id": "other-project", "version": 0})
        before = {str(p): p.read_bytes() for root in (self.project, other_project)
                  for p in root.rglob("*") if p.is_file()}
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            session = self.create()
            other = self.store.create(ChatCreateRequest(projectDir=str(other_project), provider="codex"))
            for row in (session, other):
                self.post(row, "permission")
                self.permission(row)
            first_agent = self.store._acp_sessions[session.id]
            other_agent = self.store._acp_sessions[other.id]
            opened = client.post("/api/runtime/projects/open", json={
                "projectId": session.projectId, "projectDir": session.projectDir})
            self.assertEqual(opened.status_code, 200, opened.text)
            with patch.object(first_agent, "close", wraps=first_agent.close) as closed_agent:
                closed = client.post(f"/api/runtime/projects/{opened.json()['runtimeId']}/close",
                                     json={"projectId": session.projectId})
                self.assertEqual(closed.status_code, 202, closed.text)
                self.assertEqual(closed.json()["state"], "closed")
                closed_agent.assert_called_once()
            recovery = client.post(f"/api/runtime/projects/{opened.json()['runtimeId']}/recover",
                                   json={"projectId": session.projectId})
            self.assertEqual(recovery.status_code, 409, recovery.text)
            self.assertEqual(recovery.json()["code"], "RUNTIME_CLOSED")
            stopped = self.store.get(session.id)
            self.assertEqual(stopped.status, "interrupted")
            self.assertFalse(any(m.permission for m in stopped.messages))
            self.assertNotIn(session.id, self.store._acp_sessions)
            self.assertIs(self.store._acp_sessions[other.id], other_agent)
            self.assertEqual(self.store.get(other.id).status, "running")
            self.assertTrue(any(m.permission for m in self.store.get(other.id).messages))
            self.store.stop(other.id)
        self.assertEqual(before, {str(p): p.read_bytes() for root in (self.project, other_project)
                                 for p in root.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
