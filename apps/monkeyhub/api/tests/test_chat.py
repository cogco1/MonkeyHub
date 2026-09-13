"""Real subprocess chat turns with a local fake CLI; no model or paid call."""

import json
import os
from pathlib import Path
from pathlib import Path as _Path
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.transport.settings import ApplicationSettingsDto
from monkeyhub_api import chat
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import AppStatus, ChatCreateRequest, ChatPostRequest, HubFailure


FAKE_CLI = r'''
import json, os, sys, time, tomllib
from pathlib import Path
from uuid import uuid4
sys.stdin.reconfigure(encoding="utf-8")
args = sys.argv[2:]
# The read-only questions Hub asks a connection: status and catalogue. They are
# answered without touching the call log, which records conversation turns.
if args[:2] == ["login", "status"]:
    print("Logged in using ChatGPT")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "fixture"}))
    sys.exit(0)
if args[:1] == ["app-server"]:
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if request.get("method") == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"userAgent": "fake"}}), flush=True)
        elif request.get("method") == "model/list":
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"data": [
                {"id": "fixture-model-a", "displayName": "Fixture A", "hidden": False},
                {"id": "fixture-model-b", "displayName": "Fixture B", "hidden": False},
                {"id": "fixture-hidden", "displayName": "Hidden", "hidden": True}]}}), flush=True)
    sys.exit(0)
if "--input-format" in args:
    # The SDK control protocol: the model list rides on the initialize answer.
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if request.get("type") == "control_request" and request["request"].get("subtype") == "initialize":
            print(json.dumps({"type": "control_response", "response": {
                "subtype": "success", "request_id": request.get("request_id"),
                "response": {"models": [{"value": "fixture-claude-a", "displayName": "Fixture A"},
                                        {"value": "fixture-claude-b", "displayName": "Fixture B"}]}}}), flush=True)
    sys.exit(0)
if "mcp" in args and "list" in args:
    print(json.dumps([{"name": "unrelated", "enabled": True, "transport": {"type": "stdio"}},
                      {"name": "remote-unrelated", "enabled": True, "transport": {"type": "streamable_http"}}]))
    sys.exit(0)
prompt = sys.stdin.read()
config_path = Path(os.environ["CODEX_HOME"]) / "config.toml"
config = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
profile = config.get("profiles", {}).get(config.get("profile"), {})
with Path(sys.argv[1]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "prompt": prompt, "cwd": os.getcwd(),
        "model_provider": profile.get("model_provider", config.get("model_provider"))}) + "\n")
def emit(value):
    print(json.dumps(value), flush=True)
if "fail-test" in prompt:
    print("Cannot authenticate: " + os.environ["CHAT_TEST_SECRET"], file=sys.stderr)
    sys.exit(3)
if "--output-format" in args:
    flag = "--resume" if "--resume" in args else "--session-id"
    native = args[args.index(flag) + 1]
    emit({"type": "system", "session_id": native})
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
        "delta": {"type": "text_delta", "text": "hello "}}})
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
        "delta": {"type": "text_delta", "text": "world"}}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello world"}]}})
    if "tool-test" in prompt:
        # The shapes the installed Claude CLI emits: a tool_use block in an
        # assistant message, answered by a tool_result in a user message.
        def use(identifier, tool, arguments, text, is_error=False, repeat=False):
            block = {"type": "tool_use", "id": identifier, "name": "mcp__monkeyhub__" + tool, "input": arguments}
            emit({"type": "assistant", "message": {"id": "msg_" + identifier, "content": [block]}, "session_id": native})
            if repeat:
                emit({"type": "assistant", "message": {"id": "msg_" + identifier, "content": [block]}, "session_id": native})
            result = {"type": "tool_result", "tool_use_id": identifier,
                      "content": [{"type": "text", "text": text}], "is_error": is_error}
            emit({"type": "user", "message": {"content": [result]}, "session_id": native})
            if repeat:
                emit({"type": "user", "message": {"content": [result]}, "session_id": native})
        use("toolu_01", "studio_request", {"method": "POST", "path": "/api/proposals/p-1/candidate"},
            json.dumps({"jobId": "job-1", "candidateId": "studio-cand-1", "status": "queued"}))
        use("toolu_02", "studio_request", {"method": "GET", "path": "/api/jobs/job-1"},
            json.dumps({"jobId": "job-1", "status": "succeeded", "candidateId": "studio-cand-1"}), repeat=True)
        use("toolu_03", "studio_request", {"method": "GET", "path": "/api/state?run=studio-cand-0"},
            json.dumps({"projectId": "chat-project", "referenceRun": {"runId": "studio-cand-0"}}))
        use("toolu_04", "studio_request", {"method": "POST", "path": "/api/issue"},
            "HubFailure(422): This action is not exposed to the chat.", is_error=True)
        use("toolu_05", "studio_request", {"method": "GET", "path": "/api/state?run=studio-cand-missing"},
            "HubFailure(404): CHAT_TOOL_FAILED: that run is not in this project.", is_error=True)
        emit({"type": "assistant", "message": {"id": "msg_read", "content": [
            {"type": "tool_use", "id": "toolu_read", "name": "Read",
             "input": {"file_path": "runs/studio-cand-9/records/candidate.json"}}]}, "session_id": native})
        emit({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_read", "is_error": False, "content": [
                {"type": "text", "text": json.dumps({"candidateId": "studio-cand-9", "status": "succeeded"})}]}]},
            "session_id": native})
        use("toolu_06", "studio_schema", {"method": "GET", "path": "/api/state"},
            json.dumps({"path": "/api/state", "method": "GET", "operation": {"summary": "Read State",
                        "responses": {"200": {"schema": {"filler": "s" * 3000}}}}}))
        emit({"type": "assistant", "message": {"id": "msg_final", "content": [
            {"type": "text", "text": "the candidate studio-cand-1 is ready"}]}, "session_id": native})
    emit({"type": "result", "session_id": native, "result": "hello world", "is_error": False})
else:
    native = args[args.index("resume") + 1] if "resume" in args else str(uuid4())
    emit({"type": "thread.started", "thread_id": native})
    emit({"type": "item.completed", "item": {"type": "agent_message", "id": "item_0",
        "text": "ready" if "pause-test" in prompt else "response to the conversation"}})
    if "tool-test" in prompt:
        # The shapes the installed Codex CLI emits for this adapter's MCP calls.
        def call(item_id, tool, arguments, result=None, error=None, status="completed"):
            base = {"type": "mcp_tool_call", "id": item_id, "server": "monkeyhub", "tool": tool,
                    "arguments": arguments, "result": None, "error": None, "status": "in_progress"}
            emit({"type": "item.started", "item": dict(base)})
            emit({"type": "item.started", "item": dict(base)})
            emit({"type": "item.completed", "item": {**base, "status": status, "error": error,
                  "result": None if result is None else {"content": [{"type": "text", "text": json.dumps(result)}],
                                                         "structured_content": None}}})
        call("item_1", "studio_schema", {"method": "POST", "path": "/api/proposals"},
             {"path": "/api/proposals", "method": "POST", "operation": {"summary": "Create Proposal",
              "requestBody": {"content": {"application/json": {"schema": {"filler": "s" * 4000}}}}},
              "components": {"schemas": {"ProposalRequest": {"filler": "c" * 4000}}}})
        call("item_2", "studio_request", {"method": "POST", "path": "/api/proposals/p-1/candidate"},
             {"jobId": "job-1", "candidateId": "studio-cand-1", "status": "queued"})
        call("item_3", "studio_request", {"method": "POST", "path": "/api/issue"},
             error="HubFailure(422): This action is not exposed to the chat.", status="failed")
        call("item_4", "studio_request", {"method": "GET", "path": "/api/jobs/job-1"},
             {"jobId": "job-1", "status": "succeeded", "candidateId": "studio-cand-1", "proposalId": "p-1",
              "events": ["s" * 2000]})
        call("item_5", "studio_request", {"method": "GET", "path": "/api/state?run=studio-cand-0"},
             {"projectId": "chat-project", "referenceRun": {"runId": "studio-cand-0", "baseVersion": 0},
              "stateDigest": "d" * 64})
        call("item_6", "studio_request", {"method": "GET", "path": "/api/state"},
             {"projectId": "chat-project", "referenceRun": {"runId": "run-001", "baseVersion": 0},
              "stateDigest": "d" * 64})
        emit({"type": "item.completed", "item": {"type": "agent_message", "id": "item_7",
            "text": "the candidate studio-cand-1 is ready"}})
    if "model-refused-test" in prompt:
        # Recorded from `codex exec -m not-a-real-model-xyz`: the CLI wraps the
        # service's own JSON body in its error event, and repeats it in
        # turn.failed as a mapping.
        body = json.dumps({"type": "error", "status": 400, "error": {
            "type": "invalid_request_error",
            "message": "The 'not-a-real-model-xyz' model is not supported when using Codex with a ChatGPT account."}})
        emit({"type": "turn.failed", "error": {"message": body}})
        sys.exit(0)
    if "pause-test" in prompt:
        time.sleep(20)
    if "incomplete-test" not in prompt:
        emit({"type": "turn.completed", "usage": {}})
'''


def wait_for(read, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.03)
    raise AssertionError("The fake CLI did not reach the expected state.")



def _tools_of(module):
    """The tools the stdio server advertises, read from its own listing."""

    import io, json as _json
    from unittest.mock import patch as _patch
    lines = [_json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})]
    out = io.StringIO()
    class _Reader(io.StringIO):
        def reconfigure(self, **kwargs):
            return None
    class _Writer(io.StringIO):
        def reconfigure(self, **kwargs):
            return None
    reader, writer = _Reader(chr(10).join(lines) + chr(10)), _Writer()
    with _patch.object(module.sys, "stdin", reader), _patch.object(module.sys, "stdout", writer):
        module._mcp("http://127.0.0.1:1", "00000000-0000-4000-8000-000000000000")
    answer = _json.loads(writer.getvalue().strip().splitlines()[-1])
    return answer["result"]["tools"]


class ChatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub chat 测试 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.project = self.root / "project"
        self.other = self.root / "other"
        for path, name in ((self.project, "chat-project"), (self.other, "other-project")):
            FilesystemProjectRepository.initialize(
                path, project_id=name, initial_state={"project_id": name, "version": 0},
            )
        environment = {
            "CODEX_HOME": str(self.root / "codex"),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "APPDATA": str(self.root / "roaming"),
            "LOCALAPPDATA": str(self.root / "local"),
            "CHAT_TEST_SECRET": "fake-private-token-12345",
        }
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.fake = self.root / "fake cli.py"
        self.fake.write_text(FAKE_CLI, encoding="utf-8")
        self.log = self.root / "calls.jsonl"
        self.commands = {name: (sys.executable, str(self.fake), str(self.log)) for name in ("codex", "claude")}
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.addCleanup(self.close_store)

    def close_store(self):
        for row in self.store.list():
            self.store.stop(row.id)
        self.store.shutdown()

    def create(self, project=None, provider="codex"):
        return self.store.create(ChatCreateRequest(projectDir=str(project or self.project), provider=provider))

    def post(self, session, content="hello"):
        return self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content=content))

    def finished(self, session):
        return wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running")

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_turn_envelope_reuses_connected_action_contract(self):
        tools = {tool["name"]: tool for tool in _tools_of(chat)}
        modelling = tools["studio_request"]["description"]
        for contract in ("/api/proposals/sketch", "/api/capabilities", "sourceRunId",
                         "keep", "against=<runId>", "never send the request again"):
            self.assertIn(contract, modelling)
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                session = self.create(provider=provider)
                for content in ("Change the main height; keep the porch unchanged.",
                                "Continue the same candidate with a different height."):
                    self.post(session, content)
                    self.assertEqual(self.finished(session).status, "idle")
                    prompt = self.calls()[-1]["prompt"]
                    self.assertTrue(prompt.endswith("\n\n" + content))
                    envelope = prompt[:-(len(content) + 2)]
                    self.assertIn("studio_request", envelope)
                    self.assertIn(f"project {session.projectId} at {session.projectDir}", envelope)
                    # One action contract, rather than endpoint recipes repeated
                    # in the prompt sent to the CLI on every native-session turn.
                    for duplicate in ("/api/proposals/sketch", "/api/capabilities", "awaitSeconds"):
                        self.assertNotIn(duplicate, envelope)
                    for boundary in ("Project files are read-only", "Do not call another model",
                                     "do not claim approval, issuance or printer upload",
                                     "Do not switch Hub configuration"):
                        self.assertIn(boundary, envelope)

    def test_codex_continues_native_session_after_hub_reopen(self):
        before = {str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        session = self.create()
        self.post(session, "first question")
        first = self.finished(session)
        self.assertEqual(first.status, "idle")
        self.assertEqual([row.role for row in first.messages], ["user", "assistant"])
        self.assertNotIn("nativeSessionId", first.model_dump())
        saved = json.loads((self.runtime / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
        native = saved["nativeSessionId"]
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.post(session, "continue that answer")
        final = self.finished(session)
        self.assertEqual(final.status, "idle")
        self.assertEqual(len(final.messages), 4)
        call = self.calls()[-1]
        self.assertEqual(call["args"][call["args"].index("resume") + 1], native)
        self.assertNotIn("--ignore-user-config", call["args"])
        # A normal writable workspace: the assistant works, rather than reads.
        self.assertEqual(call["args"][call["args"].index("-s") + 1], "workspace-write")
        # The turn runs in the source checkout it is working on; the bound
        # project travels as the writable root beside it.
        self.assertEqual(Path(call["cwd"]), chat._source_checkout())
        self.assertEqual(call["args"][call["args"].index("--add-dir") + 1], str(self.project))
        after = {str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_claude_stream_is_not_duplicated_and_resumes(self):
        session = self.create(provider="claude")
        for _ in range(2):
            self.post(session)
            finished = self.finished(session)
            self.assertEqual(finished.status, "idle")
        self.assertEqual([row.content for row in finished.messages if row.role == "assistant"], ["hello world", "hello world"])
        args = self.calls()[-1]["args"]
        self.assertEqual(args[args.index("--resume") + 1], session.id)
        # Its own built-in tools, so it can edit and run what it is working on.
        self.assertEqual(args[args.index("--tools") + 1], "default")
        self.assertIn("--strict-mcp-config", args)

    def test_usage_sources_keep_project_and_archive_binding_without_transcripts(self):
        native = self.create()
        acp = self.create(self.other)
        self.create()  # A conversation without a native connection has no usage source yet.
        claude = self.create(provider="claude")
        for session, changes in (
            (native, {"transport": "cli", "nativeSessionId": str(uuid4())}),
            (acp, {"transport": "acp", "acpSessionId": "fixture/session:not-a-uuid",
                   "nativeSessionId": "obsolete-cli-identity"}),
            (claude, {"nativeSessionId": str(uuid4())}),
        ):
            row = self.store._sessions[session.id]
            for key, value in changes.items():
                setattr(row, key, value)
            row.messages.append(chat.ChatMessage(id=str(uuid4()), role="user",
                content="private conversation must not reach Monitor", createdAt=row.createdAt))
            self.store._save(row)
        expected = [
            {"projectId": native.projectId, "sessionId": self.store._sessions[native.id].nativeSessionId},
            {"projectId": acp.projectId, "sessionId": "fixture/session:not-a-uuid"},
        ]
        self.store.set_archived(native.id, True)
        self.assertCountEqual([row.model_dump() for row in self.store.usage_sources()], expected)
        saved = {path: path.read_bytes() for path in (self.runtime / "chats").glob("*.json")}
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            response = client.get("/api/chat/usage-sources")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertCountEqual(response.json(), expected)
            self.assertNotIn("private conversation", response.text)
            command, _ = app.state.applications._command("monitor", ApplicationSettingsDto())
            self.assertEqual(command[command.index("--codex-bindings-url") + 1],
                             "http://127.0.0.1:8790/api/chat/usage-sources")
        self.assertEqual({path: path.read_bytes() for path in saved}, saved)
        self.assertFalse(self.log.exists(), "Reading usage sources never calls the CLI")

    def test_archive_keeps_transcript_and_native_session_after_restart(self):
        session = self.create()
        other = self.create(self.other)
        self.post(session, "tool-test")
        finished = self.finished(session)
        saved_path = self.runtime / "chats" / f"{session.id}.json"
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            path = f"/api/chat/sessions/{session.id}"
            response = client.put(path + "/archive", json={"archived": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["archived"])
            self.assertEqual(response.json()["messages"], finished.model_dump()["messages"])
            self.assertEqual([row["id"] for row in client.get("/api/chat/sessions").json()], [other.id])
            self.assertEqual([row["id"] for row in client.get("/api/chat/sessions?archived=true&projectId=chat-project").json()], [session.id])
            self.assertEqual(client.get("/api/chat/sessions?archived=true&projectId=other-project").json(), [])
            self.assertEqual(client.get(path).json(), response.json())
            self.assertEqual(client.put(path + "/archive", json={"archived": True}).json(), response.json())
            self.assertEqual(client.put(path + "/archive", json={"archived": "false"}).status_code, 422)
            blocked = client.post(path + "/messages", json={"projectId": session.projectId, "content": "must restore first"})
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json()["code"], "CHAT_ARCHIVED")
            self.assertEqual(len(self.calls()), 1, "archiving and reading never invoke the CLI")
        retained = json.loads(saved_path.read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in retained.items() if k not in {"archived", "updatedAt"}},
                         {k: v for k, v in saved.items() if k not in {"archived", "updatedAt"}})
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.assertTrue(self.store.get(session.id).archived)
        self.assertEqual([row.id for row in self.store.list(archived=True)], [session.id])
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            restored = client.put(path + "/archive", json={"archived": False})
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertFalse(restored.json()["archived"])
            self.assertEqual(restored.json()["messages"], finished.model_dump()["messages"])
            self.assertEqual(client.get("/api/chat/sessions?archived=true").json(), [])
            self.assertEqual(client.get("/api/chat/sessions").json()[0]["id"], session.id)
            self.post(session, "continue after restoring")
            self.assertEqual(self.finished(session).status, "idle")
            args = self.calls()[-1]["args"]
            self.assertEqual(args[args.index("resume") + 1], saved["nativeSessionId"])

    def test_running_chat_cannot_be_archived_or_cancelled_by_archiving(self):
        session = self.create()
        self.post(session, "pause-test")
        wait_for(lambda: self.store.get(session.id), lambda row: any(m.content == "ready" for m in row.messages))
        running = self.store._running[session.id]
        with self.assertRaises(HubFailure) as failure:
            self.store.set_archived(session.id, True)
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(failure.exception.error.code, "CHAT_RUNNING")
        self.assertFalse(running.stop.is_set())
        self.assertIsNone(running.process.poll())
        self.assertEqual(self.store.get(session.id).status, "running")
        self.assertFalse(self.store.get(session.id).archived)
        self.store.stop(session.id)
        self.finished(session)
        self.assertTrue(self.store.set_archived(session.id, True).archived)

    def test_old_chat_without_archived_field_remains_active(self):
        session = self.create()
        path = self.runtime / "chats" / f"{session.id}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved.pop("archived")
        saved.update(transport="acp", acpSessionId=str(uuid4()), acpDefaultModel="fixture-model-a")
        path.write_text(json.dumps(saved), encoding="utf-8")
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.assertFalse(self.store.get(session.id).archived)
        self.assertEqual([row.id for row in self.store.list()], [session.id])
        self.assertEqual(self.store.list(archived=True), [])
        self.store.set_archived(session.id, True)
        self.store.set_archived(session.id, False)
        retained = json.loads(path.read_text(encoding="utf-8"))
        for field in ("transport", "acpSessionId", "acpDefaultModel", "messages"):
            self.assertEqual(retained[field], saved[field])

    def test_existing_codex_profile_keeps_provider_route(self):
        config = Path(os.environ["CODEX_HOME"])
        config.mkdir()
        (config / "config.toml").write_text(
            'profile = "paid"\nmodel = "fallback"\n'
            '[profiles.paid]\nmodel = "paid-model"\nmodel_provider = "paid-endpoint"\n'
            '[model_providers.paid-endpoint]\nname = "Existing plan"\n'
            'base_url = "https://configured.example/v1"\nwire_api = "responses"\nenv_key = "PLAN_KEY"\n'
            'experimental_bearer_token = "direct-private-credential"\n'
            '[mcp_servers.unrelated]\ncommand = "do-not-launch"\n', encoding="utf-8",
        )
        session = self.create()
        self.post(session)
        self.assertEqual(self.finished(session).status, "idle")
        call = self.calls()[0]
        args = call["args"]
        self.assertEqual(call["model_provider"], "paid-endpoint")
        self.assertNotIn("--ignore-user-config", args)
        self.assertNotIn("-m", args)
        self.assertFalse(any(value in json.dumps(args) for value in ("direct-private-credential", "configured.example", "do-not-launch")))
        override = tomllib.loads(next(value for value in args if value.startswith("mcp_servers=")))["mcp_servers"]
        self.assertFalse(override["unrelated"]["enabled"])
        self.assertFalse(override["remote-unrelated"]["enabled"])
        self.assertTrue(override["monkeyhub"]["enabled"])
        self.assertEqual(set(override["monkeyhub"]["enabled_tools"]),
                         {"studio_schema", "studio_request", "fab_request"})
        self.assertEqual(override["monkeyhub"]["tools"], {
            name: {"approval_mode": "approve"}
            for name in ("studio_schema", "studio_request", "fab_request")
        })

    def test_process_failure_redacts_credentials_and_keeps_user_message(self):
        session = self.create()
        self.post(session, "fail-test")
        failed = self.finished(session)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error.code, "CHAT_PROCESS_FAILED")
        self.assertIn("[redacted]", failed.error.detail)
        self.assertNotIn(os.environ["CHAT_TEST_SECRET"], failed.model_dump_json())
        self.post(session, "try again")
        self.assertEqual(self.finished(session).status, "idle")
        self.post(session, "incomplete-test")
        self.assertEqual(self.finished(session).error.code, "CHAT_INCOMPLETE")

    def test_a_refused_model_is_reported_in_the_providers_own_words(self):
        session = self.create()
        self.post(session, "model-refused-test")
        failed = self.finished(session)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error.code, "CHAT_PROVIDER_FAILED")
        # The provider's body, not this process's rendering of a dict.
        self.assertNotIn("{'message'", failed.error.detail)
        self.assertIn("invalid_request_error", failed.error.detail)
        self.assertIn("model is not supported", failed.error.detail)
        # The conversation continues; nothing about the model was changed for it.
        self.assertIsNone(failed.model)
        self.post(session, "try again")
        self.assertEqual(self.finished(session).status, "idle")

    def test_cross_project_chats_run_together_and_stop_is_scoped(self):
        first, second, other = self.create(), self.create(), self.create(self.other)
        self.post(first, "pause-test")
        self.post(second, "pause-test")
        wait_for(lambda: self.store.get(first.id), lambda row: len(row.messages) == 2)
        with self.assertRaises(HubFailure) as wrong:
            self.store.post(first.id, ChatPostRequest(projectId=other.projectId, content="wrong project"))
        self.assertEqual(wrong.exception.error.code, "CHAT_PROJECT_MISMATCH")
        self.post(other, "pause-test")
        self.assertEqual(self.store.get(other.id).status, "running")
        with self.assertRaises(HubFailure):
            with self.store.project_configuration(str(self.other)):
                self.fail("A running chat must retain its project.")
        with self.assertRaises(HubFailure):
            with self.store.application_lifecycle("monkeydiagram", stopping=True, project_dir=str(self.project)):
                self.fail("A running chat must retain its shared Studio.")
        self.assertEqual(self.store.stop(first.id).status, "interrupted")
        self.assertEqual(self.store.get(second.id).status, "running")
        self.store.stop(second.id)
        self.assertEqual(self.store.get(other.id).status, "running")
        with self.store.application_lifecycle("monkeydiagram", stopping=True, project_dir=str(self.project)):
            pass  # B remains admitted while A can close.
        self.store.stop(other.id)

    def test_interrupted_record_is_readable_and_can_continue(self):
        session = self.create()
        self.post(session)
        self.finished(session)
        self.store.shutdown()
        path = self.runtime / "chats" / f"{session.id}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["status"] = "running"
        saved["messages"][-1]["status"] = "streaming"
        path.write_text(json.dumps(saved), encoding="utf-8")
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        recovered = self.store.get(session.id)
        self.assertEqual(recovered.status, "interrupted")
        self.assertEqual(recovered.messages[-1].status, "interrupted")
        self.post(session)
        self.assertEqual(self.finished(session).status, "idle")

    def test_http_roundtrip_invalid_project_and_old_settings(self):
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            response = client.post("/api/chat/sessions", json={"projectDir": str(self.project), "provider": "codex"})
            self.assertEqual(response.status_code, 201, response.text)
            session = response.json()
            posted = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"projectId": "chat-project", "content": "hello"})
            self.assertEqual(posted.status_code, 202, posted.text)
            finished = wait_for(lambda: client.get(f"/api/chat/sessions/{session['id']}").json(), lambda row: row["status"] != "running")
            self.assertEqual(finished["status"], "idle")
            settings = client.get("/api/settings/apps").json()
            settings["projectDir"] = str(self.root / "missing-project")
            self.assertEqual(client.put("/api/settings/apps", json=settings).status_code, 200)
            projects = client.get("/api/chat/projects")
            self.assertEqual(projects.status_code, 200, projects.text)
            self.assertEqual(projects.json()[0]["chatCount"], 1)
            self.assertEqual(client.get("/api/chat/sessions?projectId=chat-project").json()[0]["id"], session["id"])
            invalid = client.post("/api/chat/sessions", json={"projectDir": "relative", "provider": "codex"})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()["code"], "CHAT_PROJECT_INVALID")

    def test_both_clis_start_in_a_normal_writable_workspace_on_the_source(self):
        """What the CLIs are actually started with: work, not a reading room."""

        from pathlib import Path as _Path
        codex = self.create()
        claude = self.create(provider="claude")
        source = chat._source_checkout()
        self.assertIsNotNone(source, "this checkout is where the assistant works")

        with self.store._lock:
            codex_command, _ = self.store._command(self.store._sessions[codex.id])
            claude_command, _ = self.store._command(self.store._sessions[claude.id])

        # Codex: its normal writable sandbox, rooted in the source, with the
        # bound project writable beside it.
        self.assertIn("workspace-write", codex_command)
        self.assertNotIn("read-only", codex_command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex_command)
        self.assertEqual(codex_command[codex_command.index("-C") + 1], str(source))
        self.assertEqual(codex_command[codex_command.index("--add-dir") + 1], str(self.project))

        # Claude: its own built-in tools back, and the same two roots.
        self.assertEqual(claude_command[claude_command.index("--tools") + 1], "default")
        # Available is not approved. With nobody to answer a prompt, the tools a
        # headless turn may actually use have to be named, and editing and
        # running are among them.
        approved = claude_command[claude_command.index("--allowedTools") + 1].split(",")
        for name in ("Read", "Glob", "Grep", "Write", "Edit", "Bash"):
            self.assertIn(name, approved, name)
        for name in ("studio_request", "studio_schema", "fab_request"):
            self.assertIn(f"mcp__monkeyhub__{name}", approved, name)
        self.assertEqual(claude_command[claude_command.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("Read,Grep,Glob", claude_command)
        self.assertEqual(claude_command[claude_command.index("--add-dir") + 1], str(self.project))
        self.assertNotIn("--dangerously-skip-permissions", claude_command)
        # The MCP servers it may reach are still only this adapter's.
        self.assertIn("--strict-mcp-config", claude_command)

        # The native session is still the same one, resumed rather than restarted.
        with self.store._lock:
            self.store._sessions[codex.id].nativeSessionId = "thread-1"
            self.store._sessions[claude.id].nativeSessionId = "session-1"
            resumed_codex, _ = self.store._command(self.store._sessions[codex.id])
            resumed_claude, _ = self.store._command(self.store._sessions[claude.id])
        self.assertEqual(resumed_codex[resumed_codex.index("resume") + 1], "thread-1")
        self.assertEqual(resumed_claude[resumed_claude.index("--resume") + 1], "session-1")
        self.assertIn("workspace-write", resumed_codex)

    def test_a_turn_runs_where_the_source_is_and_says_what_it_may_change(self):
        session = self.create()
        self.post(session, "hello")
        self.finished(session)
        [call] = [row for row in self.calls() if "hello" in row.get("prompt", "")]
        self.assertEqual(_Path(call["cwd"]).resolve(), chat._source_checkout().resolve(),
                         "the turn runs in the source checkout, not in the project's data folder")
        self.assertIn("AGENTS.md", call["prompt"])
        self.assertIn(str(self.project), call["prompt"])
        self.assertIn("P036", call["prompt"])
        # Per-turn guidance keeps the edit principle; endpoint recipes live in
        # the connected action contract, checked by the tests below.
        self.assertIn("existing numeric control", call["prompt"])
        self.assertIn("documented modification flow instead of drawing it again", call["prompt"])

    def _studio_tool_path(self, base, path, method, headers, session):
        """Verify the Hub admission boundary before routing its fake Studio call."""

        if method in {"POST", "PUT"} and not path.startswith("/api/fab/"):
            self.assertEqual(base, self.store.hub_url)
            runtime_id = uuid5(NAMESPACE_URL, f"{session.projectId}:{os.path.normcase(str(Path(session.projectDir).resolve()))}")
            prefix = f"/api/runtime/projects/{runtime_id}/studio"
            self.assertTrue(path.startswith(prefix + "/api/"), path)
            self.assertEqual(set(headers or {}), {"Idempotency-Key", "X-Monkey-Chat"})
            self.assertEqual(headers["X-Monkey-Chat"], session.id)
            self.assertEqual(str(UUID(headers["Idempotency-Key"])), headers["Idempotency-Key"])
            return path.removeprefix(prefix)
        self.assertIsNone(headers, "Readback and service identity checks do not admit a mutation")
        if path.startswith(("/api/proposals/", "/api/jobs/", "/api/candidates/", "/api/state")) or path == "/api/project":
            self.assertEqual(base, "http://127.0.0.1:8791")
        return path

    def test_the_drawing_action_is_findable_and_callable_without_exploring(self):
        """What a request to make a form actually needs: the described path works."""

        session = self.create()
        session.status = "running"
        described = [tool for tool in _tools_of(chat)]
        request_tool = next(tool for tool in described if tool["name"] == "studio_request")
        schema_tool = next(tool for tool in described if tool["name"] == "studio_schema")
        # The action, its fields and its units are stated where the CLI reads
        # them, so making a massing needs no schema round trip at all.
        for stated in ("/api/proposals/sketch", "stateDigest", "componentId", "elementId",
                       "profile", "height", "baseLevel", "metres", "[x, z]",
                       "/api/proposals/{id}/candidate", "GET /api/state/frame", "/api/project/modeling",
                       "GET /api/documents?runId=", "MonkeyDiagram's documents list automatically",
                       "PUT /api/document-annotations", "baseRevisionSha256",
                       "GET /api/drawings/styles", "POST /api/drawings/sheets",
                       "Do not use PUT /api/board to save a generated drawing"):
            self.assertIn(stated, request_tool["description"], stated)
        self.assertIn("only for an action", schema_tool["description"])

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            if path == "/openapi.json":
                return {"paths": {"/api/project/modeling": {"post": {"summary": "initialize"}},
                                  "/api/documents": {"get": {"summary": "list drawings"}},
                                  "/api/proposals/sketch": {"post": {"summary": "draw"}},
                                  "/api/options/{option_id}/select": {"post": {"summary": "select"}}},
                        "components": {"schemas": {}}}
            return {"method": method, "body": body, "path": path}

        with patch.object(chat, "_request_json", side_effect=request):
            initialized = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/project/modeling", "body": {"projectId": session.projectId},
            })
            self.assertEqual(initialized["body"], {"projectId": session.projectId})
            with self.assertRaises(HubFailure) as wrong_project:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                    "method": "POST", "path": "/api/project/modeling", "body": {"projectId": "other"},
                })
            self.assertEqual(wrong_project.exception.error.code, "CHAT_PROJECT_MISMATCH")
            drawn = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals/sketch",
                "body": {"stateDigest": "a" * 64, "componentId": "portico", "elementId": "drawn-1",
                         "profile": [[0, 0], [6, 0], [6, 4], [0, 4]], "height": 3.2,
                         "baseLevel": "level-ground"},
            })
            self.assertEqual(drawn["path"], "/api/proposals/sketch")
            self.assertEqual(drawn["body"]["height"], 3.2)
            documents = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": "/api/documents?runId=studio-drawing-1",
            })
            self.assertEqual(documents["path"], "/api/documents?runId=studio-drawing-1")
            styles = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": "/api/drawings/styles"})
            self.assertEqual(styles["path"], "/api/drawings/styles")
            sheet_body = {"projectId": session.projectId, "styleId": "arch400-white", "scaleDenominator": 5,
                          "modelSource": {"runId": "studio-candidate", "stateDigest": "d" * 64, "assetSha256": "e" * 64}}
            sheet = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/drawings/sheets", "body": sheet_body})
            self.assertEqual(sheet["body"], sheet_body)
            annotation_body = {"projectId": session.projectId, "runId": "studio-drawing-1",
                               "assetSha256": "b" * 64, "pageIndex": 0,
                               "drawingRevisionRef": "retained-drawing", "baseRevisionSha256": "c" * 64,
                               "annotations": [{"id": "width", "kind": "ruler", "label": "1600 mm (assumed)",
                                                "points": [[0.1, 0.8], [0.9, 0.8]], "color": "#000000", "lineWidth": 0.001}]}
            page = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": "/api/document-annotations?runId=studio-drawing-1&assetSha256=" + "b" * 64 + "&pageIndex=0"})
            self.assertTrue(page["path"].startswith("/api/document-annotations?"))
            annotated = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "PUT", "path": "/api/document-annotations", "body": annotation_body})
            self.assertEqual(annotated["body"], annotation_body)
            with self.assertRaises(HubFailure) as wrong_page_project:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                    "method": "PUT", "path": "/api/document-annotations", "body": {**annotation_body, "projectId": "other"}})
            self.assertEqual(wrong_page_project.exception.error.code, "CHAT_PROJECT_MISMATCH")
            schema = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {
                "method": "GET", "path": "/api/documents",
            })
            self.assertEqual(schema["operation"]["summary"], "list drawings")
            for method, path in (("POST", "/api/documents"), ("GET", "/api/documents/asset-1/bytes")):
                with self.assertRaises(HubFailure) as refused:
                    chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": method, "path": path})
                self.assertEqual(refused.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
            # A documented template can be read as a schema, which is what an
            # exploring turn used to fail on.
            for template in ("/api/options/{option_id}/select", "/api/proposals/sketch", "/api/project/modeling"):
                answer = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                        {"method": "POST", "path": template})
                self.assertEqual(answer["method"], "POST")
                self.assertIn("summary", answer["operation"])
            # Reading a schema still cannot reach what calling cannot reach.
            with self.assertRaises(HubFailure):
                chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                               {"method": "POST", "path": "/api/issue"})

    def test_structured_edit_preserves_binding_and_checked_impact_without_duplicate_payloads(self):
        session = self.create()
        session.status = "running"
        body = {
            "stateDigest": "a" * 64, "sourceRunId": "studio-cand-base", "sourceStageRef": "stage-base",
            "sourceProposalId": "studio-previous", "keep": ["entity:main"],
            "semanticEdit": {"summary": "Widen and lift the canopy", "parameters": [
                {"key": "width", "value": 4.2}, {"key": "elevation", "value": 3.1}]},
        }
        impact = {"direct": ["parameter:width", "parameter:elevation"],
                  "propagated": ["entity:canopy", "entity:column-left", "entity:column-right"],
                  "protected": ["entity:main"], "conflicts": [], "locks": [],
                  "unknownCoverage": {"count": 1, "componentIds": ["unrelated"]},
                  "honesty": ["Only declared dependencies are covered."]}
        complete = {
            "proposalId": "studio-next", "status": "proposed", "baseStateDigest": "a" * 64,
            "sourceRunId": "studio-cand-base", "sourceStageRef": "stage-base",
            "change": {"kind": "edit_components", "summary": body["semanticEdit"]["summary"],
                       "changes": [{"entityId": "parameter:width", "action": "update"}],
                       "kept": ["Main remains"], "edits": body["semanticEdit"]},
            "decisionOperator": {"parameters": body["semanticEdit"]}, "impact": impact,
        }
        calls = []
        operation_ids = []
        explicit_operation_id = str(uuid4())

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if headers:
                operation_ids.append(headers["Idempotency-Key"])
            calls.append((method, path, body))
            return complete

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=request):
            concise = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals", "body": body,
                "operationId": explicit_operation_id})
            self.assertEqual(calls, [("POST", "/api/proposals", {**body, "projectId": session.projectId})])
            self.assertEqual(operation_ids, [explicit_operation_id])
            self.assertEqual(concise["impact"], impact)
            self.assertEqual(concise["baseStateDigest"], body["stateDigest"])
            self.assertEqual(concise["sourceStageRef"], "stage-base")
            self.assertEqual(concise["change"]["changes"], complete["change"]["changes"])
            self.assertNotIn("edits", concise["change"])
            self.assertNotIn("decisionOperator", concise)
            detailed = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": "/api/proposals/studio-next"})
            self.assertEqual(detailed, complete)
            self.assertIn("edits", complete["change"], "shortening the tool reply must not mutate the stored proposal")

            # Thousands of derived coordinates remain available through GET,
            # without forcing every continuation to reread the entire list.
            complete["change"]["changes"] = [{"entityId": f"parameter:coordinate-{i}", "action": "update"} for i in range(2783)]
            impact["direct"] = [f"parameter:coordinate-{i}" for i in range(2783)]
            impact["conflicts"] = [{"target": "entity:main", "reason": "Kept"}]
            impact["locks"] = ["entity:main"]
            large = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals", "body": body})
            self.assertEqual(len(set(operation_ids)), 2, "A new logical mutation receives its own operation id")
            for section, field in (("change", "changes"), ("impact", "direct")):
                self.assertLess(len(large[section][field]), len(complete[section][field]))
                self.assertEqual(large[section][f"{field}Count"], 2783)
                self.assertEqual(len(large[section][field]) + large[section][f"{field}Omitted"], 2783)
            self.assertEqual(large["change"]["kept"], complete["change"]["kept"])
            for field in ("propagated", "protected", "conflicts", "locks", "unknownCoverage", "honesty"):
                self.assertEqual(large["impact"][field], impact[field])
            retained = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": large["detailsPath"]})
            self.assertEqual(retained, complete)
            self.assertEqual(len(retained["change"]["changes"]), 2783)
            self.assertEqual(len(retained["impact"]["direct"]), 2783)

    def test_schema_query_selects_the_actual_producer_inputs(self):
        session = self.create()
        variants = [{"properties": {"producer": {"enum": [name]},
                                     "params": {"properties": {field: {"type": "number"}}}}}
                    for name, field in (("prism", "height"), ("loft", "profile_size"))]
        document = {
            "paths": {"/api/proposals": {"post": {
                "summary": "Author an edit", "requestBody": {"$ref": "#/components/schemas/ProposalRequestDto"},
                "responses": {"201": {"$ref": "#/components/schemas/ProposalDto"}}}}},
            "components": {"schemas": {
                "ProposalRequestDto": {"properties": {"semanticEdit": {"$ref": "#/components/schemas/SemanticEditRequestDto"}}},
                "SemanticEditRequestDto": {"properties": {
                    "entities": {"items": {"anyOf": [{"properties": {"fields": {"anyOf": variants}}}]}},
                    "parameters": {"type": "array"}}},
                "ProposalDto": {"description": "The independently queryable complete response"},
            }},
        }
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=lambda *a, **k: json.loads(json.dumps(document))):
            answer = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {
                "method": "POST", "path": "/api/proposals", "producer": "loft"})
            schema = answer["components"]["schemas"]["SemanticEditRequestDto"]
            selected = schema["properties"]["entities"]["items"]["anyOf"][0]["properties"]["fields"]["anyOf"]
            self.assertEqual(selected, [variants[1]])
            self.assertEqual(schema["properties"]["parameters"], {"type": "array"})
            self.assertNotIn("ProposalDto", answer["components"]["schemas"])
            full = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {
                "method": "POST", "path": "/api/proposals"})
            self.assertIn("ProposalDto", full["components"]["schemas"])
            self.assertEqual(full["components"]["schemas"]["SemanticEditRequestDto"], document["components"]["schemas"]["SemanticEditRequestDto"])
            with self.assertRaises(HubFailure) as unavailable:
                chat.call_tool(self.store.hub_url, session.id, "studio_schema", {
                    "method": "POST", "path": "/api/proposals", "producer": "missing"})
            self.assertIn("loft", unavailable.exception.error.detail)
            self.assertIn("prism", unavailable.exception.error.detail)

    def test_changing_something_starts_at_the_capability_index_not_at_a_guess(self):
        """One short pointer, and the bound path behind it — not a second hand-written contract."""

        session = self.create()
        session.status = "running"
        description = next(tool for tool in _tools_of(chat) if tool["name"] == "studio_request")["description"]
        for stated in ("GET /api/capabilities/candidate.modify_existing?target=",
                       "&elementId=<the element>", "GET /api/capabilities?goal=",
                       "POST /api/capabilities/{capabilityId}/run", "awaitSeconds: 60",
                       # The waiting rule the tool's own description carries, which is
                       # what a new stdio bridge reads without the Hub being restarted.
                       "omit yield_time_ms so it keeps its 30000 ms default",
                       "do not loop short functions.wait calls",
                       "keep is a list", "never send the request again",
                       "No match in the index is not a verdict"):
            self.assertIn(stated, description, stated)
        # A change to something already known must not be made to walk the
        # index and the state again first.
        self.assertIn("do not read the index", description)
        self.assertNotIn("ask the capability index first", description)
        # The modify path is described once. The old hand-written body for it
        # is gone, so there are not two editable descriptions of one action.
        self.assertNotIn("CHANGE ONE EXISTING NUMBER", description)
        self.assertNotIn("targetComponentId, optional elementId", description)
        # What the fixed massing chain and the compare parameter say is kept.
        self.assertIn("/api/proposals/sketch", description)
        self.assertIn("compare?against=<runId>", description)

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            return {"method": method, "body": body, "path": path}

        with patch.object(chat, "_request_json", side_effect=request):
            index = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                   {"method": "GET", "path": "/api/capabilities?goal=%E6%94%B9%E9%AB%98%E5%BA%A6"})
            self.assertEqual(index["method"], "GET")
            described = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": "/api/capabilities/candidate.modify_existing?target=portico"})
            self.assertIn("candidate.modify_existing", described["path"])
            ran = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/capabilities/candidate.modify_existing/run",
                "body": {"stateDigest": "a" * 64, "targetComponentId": "portico",
                         "elementId": "portico-base", "utterance": "set height to 4.2",
                         "keep": ["entity:portico-cornice"]}})
            self.assertEqual(ran["body"]["utterance"], "set height to 4.2")
            self.assertEqual(ran["body"]["keep"], ["entity:portico-cornice"])
            # The projection grants nothing the API does not already expose.
            for method, path in (("POST", "/api/capabilities"),
                                 ("POST", "/api/capabilities/candidate.modify_existing/accept"),
                                 ("PUT", "/api/capabilities/candidate.modify_existing/run")):
                with self.assertRaises(HubFailure):
                    chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                   {"method": method, "path": path})

    # ---- one tool call that sees one action through

    def _finishing_service(self, session, *, job_states, candidate=None, compare=None,
                           readback_error=None, barriers=None):
        """A stand-in Hub and Studio that records what was actually sent.

        It answers the same shapes the real services answer and nothing more.
        ``barriers`` lets a test hold each call of a group until every call of
        that group has arrived, so an implementation that asked for them one
        after another would never get past it.
        """

        sent = []

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            sent.append({"method": method, "path": path, "body": body})
            if barriers:
                for belongs, barrier in barriers:
                    if belongs(base, path):
                        barrier.wait()
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running",
                         "url": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            if path == "/api/capabilities/candidate.modify_existing/run":
                return {"capabilityId": "candidate.modify_existing", "proposalId": "studio-p1",
                        "jobId": "job-1", "candidateId": "studio-cand-2", "status": "queued",
                        "change": {"old": 3.3, "new": 4.2}}
            if path == "/api/jobs/job-1":
                return job_states.pop(0) if len(job_states) > 1 else job_states[0]
            if path == "/api/candidates/studio-cand-2":
                if readback_error:
                    raise HubFailure(503, "CHAT_TOOL_FAILED", readback_error)
                return candidate
            if path.startswith("/api/candidates/studio-cand-2/compare"):
                return compare
            raise AssertionError(f"unexpected call: {method} {path}")

        return request, sent

    def _run_with_wait(self, request, session, wait=60, body=None):
        with patch.object(chat, "_request_json", side_effect=request):
            return chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/capabilities/candidate.modify_existing/run",
                "body": body if body is not None else {
                    "stateDigest": "a" * 64, "targetComponentId": "portico",
                    "elementId": "small-house-main", "utterance": "set height to 4.2",
                    "sourceRunId": "studio-cand-1", "keep": ["entity:portico-base"]},
                "awaitSeconds": wait,
            })

    def test_one_call_posts_once_and_reads_the_finished_run_back(self):
        """The whole of a known change in one call: post, watch, read, compare."""

        session = self.create()
        session.status = "running"
        # The run reports work it did not check. That has to travel as it is.
        candidate = {"candidateId": "studio-cand-2", "stateDigest": "b" * 64, "changedVsProjection": True,
                     "seatExecutionComplete": True, "harness": "Unaccepted candidate",
                     "relationChecks": {"held": 1, "violated": 0, "unchecked": 2,
                                        "heldFlag": False, "fullyChecked": False},
                     "honesty": ["one relation was not checked"],
                     "artifacts": [{"runId": "studio-cand-2", "fileName": "seat.3dm", "sha256": "c" * 64,
                                    "relativePath": "runs/studio-cand-2/seat.3dm", "objectCount": 6,
                                    "readbackVerified": True, "representation": "composed",
                                    "lengthUnit": "meters", "available": True},
                                   {"runId": "studio-cand-2", "fileName": "gone.3dm", "available": False,
                                    "unavailableReason": "the file is not on disk"}]}
        compare = {"candidateId": "studio-cand-2", "against": "studio-cand-1", "tolerance": 1e-9,
                   "changed": 1, "unchanged": 1, "added": 0, "removed": 0, "honesty": [],
                   "objects": [
                       {"name": "obj-small-house-main", "componentId": "small-house", "producerOp": "prism",
                        "status": "changed", "before": {"min": [0, 0, 0], "max": [3, 2, 3.3]},
                        "after": {"min": [0, 0, 0], "max": [3, 2, 4.2]}},
                       {"name": "obj-portico-base", "componentId": "portico", "producerOp": "prism",
                        "status": "unchanged", "before": {"min": [0, 0, 0], "max": [4, 2, 0.6]},
                        "after": {"min": [0, 0, 0], "max": [4, 2, 0.6]}}]}
        request, sent = self._finishing_service(
            session,
            job_states=[{"jobId": "job-1", "status": "queued"},
                        {"jobId": "job-1", "status": "running"},
                        {"jobId": "job-1", "status": "succeeded", "candidateId": "studio-cand-2"}],
            candidate=candidate, compare=compare)
        answer = self._run_with_wait(request, session)

        posts = [row for row in sent if row["method"] == "POST"]
        self.assertEqual(len(posts), 1, f"one action, one POST; sent {posts}")
        self.assertEqual(posts[0]["path"], "/api/capabilities/candidate.modify_existing/run")
        self.assertEqual(answer["status"], "succeeded")
        self.assertEqual(answer["readback"], "ok")
        self.assertEqual(answer["candidateId"], "studio-cand-2")
        self.assertEqual(answer["candidate"]["relationChecks"], candidate["relationChecks"],
                         "unchecked relations must not come back as held")
        self.assertEqual(answer["candidate"]["honesty"], candidate["honesty"])
        saved = {row["fileName"]: row for row in answer["artifacts"]}
        self.assertEqual(saved["seat.3dm"]["relativePath"], "runs/studio-cand-2/seat.3dm")
        self.assertIs(saved["seat.3dm"]["readbackVerified"], True)
        self.assertIs(saved["gone.3dm"]["available"], False,
                      "an export that is not there says so rather than being dropped")
        # The comparison is against the run the change was submitted from.
        self.assertEqual(answer["compare"]["against"], "studio-cand-1")
        self.assertTrue(any(row["path"].endswith("compare?against=studio-cand-1") for row in sent))
        self.assertEqual(answer["compare"]["objects"][0]["after"], {"min": [0, 0, 0], "max": [3, 2, 4.2]},
                         "the reader needs the box that changed, not a count of changes")
        self.assertEqual([row["name"] for row in answer["compare"]["objects"] if row["status"] == "unchanged"],
                         ["obj-portico-base"])
        self.assertEqual(answer["next"], [],
                         "a finished answer must not invite the reads it already made")

    def test_checkpoint_wait_uses_the_proposals_base_and_posts_only_once(self):
        session = self.create()
        session.status = "running"
        request, sent = self._finishing_service(
            session, job_states=[{"jobId": "job-1", "status": "succeeded"}],
            candidate={"candidateId": "studio-cand-2", "artifacts": []},
            compare={"against": "studio-cand-1", "changed": []})

        def checkpoint(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == "/api/proposals/studio-final":
                return {"proposalId": "studio-final", "sourceRunId": "studio-cand-1"}
            if path == "/api/proposals/studio-final/candidate":
                sent.append({"method": method, "path": path, "body": body})
                return {"jobId": "job-1", "candidateId": "studio-cand-2", "status": "queued"}
            return request(base, path, method, body, timeout)

        with patch.object(chat, "_request_json", side_effect=checkpoint):
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals/studio-final/candidate", "awaitSeconds": 30})
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["compare"]["against"], "studio-cand-1")
        self.assertEqual([row for row in sent if row["method"] == "POST"], [
            {"method": "POST", "path": "/api/proposals/studio-final/candidate", "body": None}])

    def test_first_checkpoint_reads_candidate_without_a_fictitious_comparison_run(self):
        session = self.create()
        session.status = "running"
        objects = [{"name": "canopy", "componentId": "entry", "producerOp": "prism",
                    "bbox": {"min": [-1.5, -1.8, 2.8], "max": [1.5, 0, 2.98]},
                    "lengthUnit": "meter", "upAxis": "Z-up"}]
        request, sent = self._finishing_service(
            session, job_states=[{"jobId": "job-1", "status": "succeeded"}],
            candidate={"candidateId": "studio-cand-2", "artifacts": [],
                       "objects": objects, "objectReadbackError": None})

        def first_checkpoint(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == "/api/proposals/studio-first":
                return {"proposalId": "studio-first", "sourceRunId": None}
            if path == "/api/proposals/studio-first/candidate":
                sent.append({"method": method, "path": path, "body": body})
                return {"jobId": "job-1", "candidateId": "studio-cand-2", "status": "queued"}
            if "/compare" in path:
                self.fail("The first candidate has no retained before-run to compare against")
            return request(base, path, method, body, timeout)

        with patch.object(chat, "_request_json", side_effect=first_checkpoint):
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals/studio-first/candidate", "awaitSeconds": 30})
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["readback"], "ok")
        self.assertEqual(result["objects"], objects)
        self.assertIsNone(result["objectReadbackError"])
        self.assertIsNone(result["compare"])
        self.assertEqual(result["next"], [])
        self.assertEqual(len([row for row in sent if row["method"] == "POST"]), 1)

    def test_the_binding_is_settled_before_anything_is_posted(self):
        session = self.create()
        session.status = "running"
        request, sent = self._finishing_service(session, job_states=[{"jobId": "job-1", "status": "succeeded"}])

        def refuse(base, path, method="GET", body=None, timeout=None):
            if path.startswith("/api/apps?"):
                sent.append({"method": method, "path": path, "body": body})
                return [{"appId": "monkeyarch", "state": "stopped", "url": None, "processId": None}]
            return request(base, path, method, body, timeout)

        with self.assertRaises(HubFailure) as refused:
            self._run_with_wait(refuse, session)
        self.assertEqual(refused.exception.error.code, "CHAT_STUDIO_UNAVAILABLE")
        self.assertEqual([row for row in sent if row["method"] == "POST"], [],
                         "nothing may be posted until every binding check has passed")

    def test_the_independent_reads_of_one_call_really_overlap(self):
        """Not "a pool exists": each group is held until all of it has arrived."""

        import threading

        session = self.create()
        session.status = "running"
        first = threading.Barrier(2, timeout=8)
        second = threading.Barrier(2, timeout=8)
        readback = threading.Barrier(2, timeout=8)
        request, sent = self._finishing_service(
            session,
            job_states=[{"jobId": "job-1", "status": "succeeded", "candidateId": "studio-cand-2"}],
            candidate={"candidateId": "studio-cand-2", "artifacts": []},
            compare={"changed": [], "added": [], "removed": [], "unchanged": []},
            barriers=[
                # The Hub and the Studio both answer /api/health, so a group is
                # which service is being asked as well as what is being asked.
                (lambda base, path: base == self.store.hub_url and (path.startswith("/api/apps?") or path == "/api/health"),
                 first),
                (lambda base, path: base != self.store.hub_url and path in {"/api/health", "/api/project"},
                 second),
                (lambda base, path: path.startswith("/api/candidates/studio-cand-2"), readback),
            ])
        # Every group holds each of its calls until all of them have arrived: an
        # implementation that asked for them one after another would sit here
        # until the barrier gave up, and this would fail rather than pass slowly.
        answer = self._run_with_wait(request, session)
        self.assertEqual(answer["status"], "succeeded")
        self.assertEqual(answer["readback"], "ok")

    def test_a_wait_that_runs_out_names_the_job_and_never_posts_again(self):
        session = self.create()
        session.status = "running"
        request, sent = self._finishing_service(
            session, job_states=[{"jobId": "job-1", "status": "running"}])
        answer = self._run_with_wait(request, session, wait=1)
        self.assertEqual(answer["status"], "running")
        self.assertTrue(answer["waitedOut"])
        self.assertEqual(answer["jobId"], "job-1")
        self.assertEqual(answer["candidateId"], "studio-cand-2")
        self.assertIn("GET /api/jobs/job-1", answer["next"])
        self.assertIn("not sent again", answer["detail"])
        self.assertEqual(len([row for row in sent if row["method"] == "POST"]), 1,
                         "running out of time must never repeat the action")

    def test_a_failed_job_keeps_its_own_words(self):
        session = self.create()
        session.status = "running"
        failure = {"jobId": "job-1", "status": "failed", "error": "COMPONENT_NOT_BUILT: no seat built it"}
        request, sent = self._finishing_service(session, job_states=[failure])
        answer = self._run_with_wait(request, session)
        self.assertEqual(answer["status"], "failed")
        self.assertEqual(answer["job"], failure)
        self.assertNotIn("candidate", answer, "a failed run has nothing to read back")
        self.assertEqual(len([row for row in sent if row["method"] == "POST"]), 1)

    def test_a_readback_that_fails_is_not_reported_as_a_read_candidate(self):
        session = self.create()
        session.status = "running"
        request, _ = self._finishing_service(
            session,
            job_states=[{"jobId": "job-1", "status": "succeeded", "candidateId": "studio-cand-2"}],
            readback_error="the application refused the request")
        answer = self._run_with_wait(request, session)
        self.assertEqual(answer["status"], "succeeded", "the job did finish, and says so")
        self.assertEqual(answer["readback"], "failed")
        self.assertIn("refused", answer["detail"])
        self.assertNotIn("candidate", answer, "nothing may stand in for the answer that was not read")
        self.assertIn("GET /api/candidates/studio-cand-2", answer["next"])

    def test_the_wait_belongs_to_the_one_action_that_can_be_seen_through(self):
        session = self.create()
        session.status = "running"
        request, sent = self._finishing_service(session, job_states=[{"jobId": "job-1", "status": "succeeded"}])
        with patch.object(chat, "_request_json", side_effect=request):
            for arguments in (
                {"method": "GET", "path": "/api/state", "awaitSeconds": 30},
                {"method": "POST", "path": "/api/proposals/sketch", "awaitSeconds": 30},
                {"method": "POST", "path": "/api/capabilities/candidate.modify_existing/run",
                 "awaitSeconds": 0},
                {"method": "POST", "path": "/api/capabilities/candidate.modify_existing/run",
                 "awaitSeconds": 4000},
                {"method": "POST", "path": "/api/capabilities/candidate.modify_existing/run",
                 "awaitSeconds": "120"},
            ):
                with self.assertRaises(HubFailure, msg=str(arguments)) as refused:
                    chat.call_tool(self.store.hub_url, session.id, "studio_request", arguments)
                self.assertEqual(refused.exception.error.code, "CHAT_TOOL_INVALID", str(arguments))
        self.assertEqual([row for row in sent if row["method"] == "POST"], [],
                         "a refused argument must not have sent anything")

    def test_bound_tool_checks_project_and_does_not_expose_issue_or_upload(self):
        session = self.create()
        session.status = "running"
        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            return {"method": method, "body": body, "path": path}
        with patch.object(chat, "_request_json", side_effect=request):
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "POST", "path": "/api/proposals", "body": {"utterance": "set height to 1.2"}})
            self.assertEqual(result["body"]["projectId"], "chat-project")
            closure_body = {"refs": ["entity:wall"]}
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "POST", "path": "/api/state/closure", "body": closure_body})
            self.assertEqual(result["method"], "POST")
            self.assertEqual(result["body"], closure_body)
            with self.assertRaises(HubFailure) as wrong_method:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/state/closure"})
            self.assertEqual(wrong_method.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
            for path in ("/api/intents", "/api/issue", "/api/settings/apps", "https://remote.example/api/state"):
                with self.assertRaises(HubFailure):
                    chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "POST", "path": path})
            with self.assertRaises(HubFailure):
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "POST", "path": "/api/proposals", "body": {"projectId": "other"}})
            with self.assertRaises(HubFailure):
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/artifacts?projectId=other"})
            result = chat.call_tool(self.store.hub_url, session.id, "fab_request", {"method": "POST", "path": "/api/fab/send", "body": {"source": "job", "dryRun": False, "accessCode": "secret"}})
            self.assertTrue(result["body"]["dryRun"])
            self.assertNotIn("accessCode", result["body"])
            session.projectId = "other"
            with self.assertRaises(HubFailure) as changed:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/state"})
            self.assertEqual(changed.exception.error.code, "CHAT_PROJECT_MISMATCH")

    def test_tools_route_each_chat_to_its_project_while_both_are_running(self):
        first, second = self.create(), self.create(self.other)
        self.post(first, "pause-test")
        self.post(second, "pause-test")
        services = {first.projectDir: ("http://127.0.0.1:18791", 123, first),
                    second.projectDir: ("http://127.0.0.1:18792", 456, second)}
        calls = []
        wrong_binding = False

        def request(base, path, method="GET", body=None, timeout=None):
            calls.append((base, path, method))
            if base == self.store.hub_url:
                if path.startswith("/api/chat/sessions/"):
                    return self.store.get(path.rsplit("/", 1)[-1]).model_dump()
                if path.startswith("/api/apps?"):
                    target = parse_qs(urlsplit(path).query)["projectDir"][0]
                    address, pid, _ = services[target]
                    return [{"appId": "monkeyarch", "state": "running", "url": address + "/", "processId": pid}]
                self.assertEqual(path, "/api/health", "binding must not depend on global project settings")
                return {"sourceRevision": "revision"}
            _, pid, session = next(row for row in services.values() if row[0] == base)
            if path == "/api/health":
                return {"sourceRevision": "revision", "processId": pid}
            if path == "/api/project":
                bound = second if wrong_binding else session
                return {"projectId": bound.projectId, "projectDir": bound.projectDir}
            self.assertEqual(path, "/api/state")
            return {"projectId": session.projectId}

        with patch.object(chat, "_request_json", side_effect=request):
            for session in (first, second):
                result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/state"})
                self.assertEqual(result["projectId"], session.projectId)
            wrong_binding = True
            before = sum(path == "/api/state" for _, path, _ in calls)
            with self.assertRaises(HubFailure) as refused:
                chat.call_tool(self.store.hub_url, first.id, "studio_request", {"method": "GET", "path": "/api/state"})
            self.assertEqual(refused.exception.error.code, "CHAT_PROJECT_MISMATCH")
            self.assertEqual(sum(path == "/api/state" for _, path, _ in calls), before)
        self.store.stop(first.id)
        self.assertEqual(self.store.get(second.id).status, "running")
        self.store.stop(second.id)

    def test_modeling_preparation_proxies_only_the_verified_current_studio(self):
        sent = []
        studio_pid = 123

        def request(base, path, method="GET", body=None, timeout=None):
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": studio_pid, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            self.assertEqual((base, method, path), ("http://127.0.0.1:8791", "POST", "/api/project/modeling"))
            sent.append(body)
            return {"projectId": "chat-project", "initialized": True}

        app = create_app(HubSettings(self.runtime))
        ready = AppStatus(appId="monkeyarch", title="MonkeyArch", serviceId="studio", state="running", url="http://127.0.0.1:8791/", processId=123)
        with patch.object(app.state.applications, "start", return_value=ready), TestClient(app, base_url="http://127.0.0.1:8790") as client, patch.object(chat, "_request_json", side_effect=request):
            prepared = client.post("/api/project/modeling", params={"projectDir": str(self.project)}, json={"projectId": "chat-project"})
            self.assertEqual(prepared.status_code, 200, prepared.text)
            self.assertEqual(prepared.json(), {"projectId": "chat-project", "initialized": True})
            wrong = client.post("/api/project/modeling", params={"projectDir": str(self.project)}, json={"projectId": "other-project"})
            self.assertEqual(wrong.status_code, 409)
            self.assertEqual(wrong.json()["code"], "CHAT_PROJECT_MISMATCH")
            studio_pid = 999
            replaced = client.post("/api/project/modeling", params={"projectDir": str(self.project)}, json={"projectId": "chat-project"})
            self.assertEqual(replaced.status_code, 409)
            self.assertEqual(replaced.json()["code"], "CHAT_SERVICE_CHANGED")
        self.assertEqual(sent, [{"projectId": "chat-project"}])

    def test_fab_tools_do_not_require_or_rebind_studio(self):
        session = self.create()
        session.status = "running"
        sent = []

        def request(base, path, method="GET", body=None, timeout=None):
            self.assertEqual(base, self.store.hub_url)
            sent.append(path)
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/fab/profiles":
                return {"fixture": {"label": "Installed profile"}}
            if path == "/api/fab/send":
                return body
            self.fail(f"Independent Fab tried to use Studio: {path}")

        with patch.object(chat, "_request_json", side_effect=request):
            profiles = chat.call_tool(self.store.hub_url, session.id, "fab_request", {"path": "/api/fab/profiles"})
            self.assertIn("fixture", profiles)
            checked = chat.call_tool(self.store.hub_url, session.id, "fab_request", {
                "method": "POST", "path": "/api/fab/send",
                "body": {"source": "job", "dryRun": False, "accessCode": "secret"},
            })
            self.assertTrue(checked["dryRun"])
            self.assertNotIn("accessCode", checked)
            for status, project_id, expected in (("idle", "chat-project", "CHAT_NOT_RUNNING"),
                                                   ("running", "other", "CHAT_PROJECT_MISMATCH")):
                session.status, session.projectId = status, project_id
                with self.assertRaises(HubFailure) as refused:
                    chat.call_tool(self.store.hub_url, session.id, "fab_request", {"path": "/api/fab/profiles"})
                self.assertEqual(refused.exception.error.code, expected)
        self.assertEqual(sent.count("/api/fab/profiles"), 1)

    def test_tool_activity_is_summarised_and_survives_a_reopen(self):
        session = self.create()
        self.post(session, "tool-test please")
        finished = self.finished(session)
        self.assertEqual(finished.status, "idle")
        activity = [row for row in finished.messages if row.role == "tool"]
        self.assertEqual([row.status for row in activity],
                         ["complete", "complete", "failed", "complete", "complete", "complete"])
        self.assertEqual(len(activity), len({row.id for row in activity}), "one row per MCP call")
        self.assertEqual([row.role for row in finished.messages][:2], ["user", "assistant"])
        schema, started, refused, job, earlier, reference = activity
        self.assertEqual(schema.content.splitlines()[0], "studio_schema · POST /api/proposals · completed")
        self.assertIn("read the schema of POST /api/proposals", schema.content)
        self.assertNotIn("s" * 100, finished.model_dump_json(), "no schema or state document is copied in")
        self.assertLess(len(schema.content), 400)
        self.assertIn("candidateId: studio-cand-1", started.content)
        self.assertIsNone(started.candidateId, "a queued job is not a finished candidate")
        self.assertEqual(refused.content.splitlines()[0], "studio_request · POST /api/issue · failed")
        self.assertIn("not exposed to the chat", refused.content)
        self.assertIsNone(refused.candidateId)
        self.assertEqual(job.candidateId, "studio-cand-1")
        # A read of one exact earlier run stays openable from its own row; a
        # read of the project's own reference run is not a candidate at all.
        self.assertEqual(earlier.candidateId, "studio-cand-0")
        self.assertIsNone(reference.candidateId)
        self.assertEqual(finished.messages[-1].content, "the candidate studio-cand-1 is ready")
        # The same rows are read back after Hub reopens, and a second turn
        # keeps them rather than replacing them.
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        reopened = self.store.get(session.id)
        self.assertEqual([row.model_dump() for row in reopened.messages], [row.model_dump() for row in finished.messages])
        self.post(session, "tool-test again")
        continued = self.finished(session)
        self.assertEqual([row.content for row in continued.messages[:len(finished.messages)]],
                         [row.content for row in finished.messages])
        self.assertEqual(len([row for row in continued.messages if row.role == "tool"]), 12)

    def test_claude_tool_use_and_result_become_one_activity_row(self):
        session = self.create(provider="claude")
        self.post(session, "tool-test please")
        finished = self.finished(session)
        self.assertEqual(finished.status, "idle")
        activity = [row for row in finished.messages if row.role == "tool"]
        # Six calls, one row each: the two halves are joined by tool_use_id and
        # a repeated pair does not open a second row or reopen a finished one.
        self.assertEqual(len(activity), 7)
        self.assertEqual(len({row.id for row in activity}), 7)
        started, job, earlier, refused, missing, read, schema = activity
        self.assertEqual([row.status for row in activity],
                         ["complete", "complete", "complete", "failed", "failed", "complete", "complete"])
        self.assertEqual(started.content.splitlines()[0],
                         "studio_request · POST /api/proposals/p-1/candidate · completed")
        self.assertIn("candidateId: studio-cand-1", started.content)
        self.assertIsNone(started.candidateId, "a queued job is not a finished candidate")
        self.assertEqual(job.candidateId, "studio-cand-1")
        self.assertEqual(earlier.candidateId, "studio-cand-0", "an exact run read stays openable")
        self.assertEqual(refused.content.splitlines()[0], "studio_request · POST /api/issue · failed")
        self.assertIn("not exposed to the chat", refused.content)
        self.assertIsNone(refused.candidateId)
        # A failed read of a named run is not a candidate, however it was asked.
        self.assertEqual(missing.content.splitlines()[0],
                         "studio_request · GET /api/state?run=studio-cand-missing · failed")
        self.assertIsNone(missing.candidateId)
        # The CLI's own file tools are shown as activity, but reading a record
        # file is not a finished run and offers no candidate.
        self.assertEqual(read.content.splitlines()[0], "Read · completed")
        self.assertIsNone(read.candidateId)
        self.assertIn("read the schema of GET /api/state", schema.content)
        self.assertNotIn("s" * 100, finished.model_dump_json(), "no schema document is copied in")
        self.assertEqual([row.content for row in finished.messages if row.role == "assistant"], ["hello world"])
        # The same rows come back after Hub reopens.
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        reopened = self.store.get(session.id)
        self.assertEqual([row.model_dump() for row in reopened.messages],
                         [row.model_dump() for row in finished.messages])

    def test_an_interrupted_claude_tool_call_does_not_stay_pending(self):
        session = self.create(provider="claude")
        saved = chat._SavedChat.model_validate(session.model_dump())
        saved.messages.append(chat.ChatMessage(id="u-1", role="user", content="ask", createdAt="2026-09-11"))
        opened = chat._claude_call({"type": "tool_use", "id": "toolu_9", "name": "mcp__monkeyhub__studio_request",
                                    "input": {"method": "GET", "path": "/api/state"}}, None)
        self.store._tool_message(saved, opened, "item.started", {})
        pending = saved.messages[-1]
        self.assertEqual(pending.status, "streaming")
        self.assertEqual(pending.content, "studio_request · GET /api/state · in_progress")
        self.store._save(saved)
        self.store._sessions[saved.id] = saved
        saved.status = "running"
        self.store._save(saved)
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        recovered = self.store.get(session.id)
        self.assertEqual(recovered.status, "interrupted")
        self.assertEqual(recovered.messages[-1].status, "interrupted")

    def test_an_older_chat_without_tool_activity_still_opens(self):
        session = self.create()
        self.post(session)
        self.finished(session)
        self.store.shutdown()
        path = self.runtime / "chats" / f"{session.id}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        for message in saved["messages"]:
            message.pop("candidateId", None)
        path.write_text(json.dumps(saved), encoding="utf-8")
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        older = self.store.get(session.id)
        self.assertEqual([row.candidateId for row in older.messages], [None, None])
        self.post(session, "tool-test continues an older chat")
        self.assertEqual(self.finished(session).status, "idle")
        self.assertEqual([row.candidateId for row in self.store.get(session.id).messages if row.role == "tool"],
                         [None, None, None, "studio-cand-1", "studio-cand-0", None])

    def test_an_interrupted_tool_call_does_not_stay_pending(self):
        session = self.create()
        self.post(session, "tool-test and pause-test")
        wait_for(lambda: self.store.get(session.id),
                 lambda row: any(item.role == "tool" for item in row.messages))
        self.store.stop(session.id)
        stopped = self.store.get(session.id)
        self.assertEqual(stopped.status, "interrupted")
        self.assertNotIn("streaming", [row.status for row in stopped.messages])

    def test_projects_report_the_published_version_and_stage(self):
        session = self.create()
        listed = next(row for row in self.store.projects() if row.projectId == session.projectId)
        self.assertEqual(listed.version, 0, "a new project publishes version 0")
        self.assertIsNone(listed.stage, "no Stage is claimed before one is accepted")
        self.assertEqual(listed.projectDir, str(self.project))
        # An unreadable project is reported as unknown rather than as a version.
        self.assertEqual(chat._position(str(self.root / "missing")), (None, None))

    def test_a_chat_keeps_its_own_model_and_the_next_call_uses_it(self):
        session = self.create()
        self.post(session, "first question")
        self.assertEqual(self.finished(session).status, "idle")
        self.assertNotIn("-m", self.calls()[0]["args"], "no model is passed when none was chosen")
        changed = self.store.set_model(session.id, "  fixture-model-b  ")
        self.assertEqual(changed.model, "fixture-model-b")
        self.assertEqual(changed.provider, session.provider, "the connection is untouched")
        saved = json.loads((self.runtime / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
        native = saved["nativeSessionId"]
        self.assertEqual(saved["model"], "fixture-model-b")
        # The choice survives a Hub restart and reaches the next real CLI start,
        # still resuming the same native session.
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.assertEqual(self.store.get(session.id).model, "fixture-model-b")
        self.post(session, "second question")
        self.assertEqual(self.finished(session).status, "idle")
        call = self.calls()[-1]["args"]
        self.assertEqual(call[call.index("-m") + 1], "fixture-model-b")
        self.assertEqual(call[call.index("resume") + 1], native)
        reopened = json.loads((self.runtime / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
        self.assertEqual(reopened["nativeSessionId"], native)
        self.assertEqual(reopened["projectId"], session.projectId)
        # Back to the CLI's own default, and never while a turn is running.
        self.assertIsNone(self.store.set_model(session.id, None).model)
        self.post(session, "pause-test")
        wait_for(lambda: self.store.get(session.id), lambda row: row.status == "running")
        with self.assertRaises(HubFailure) as running:
            self.store.set_model(session.id, "fixture-model-a")
        self.assertEqual(running.exception.error.code, "CHAT_RUNNING")
        self.store.stop(session.id)

    def test_connections_are_checked_once_and_report_what_was_found(self):
        checks = []

        def fake_check(commands, environment):
            time.sleep(0.4)
            checks.append(sorted(commands))
            return {"codex": {"signedIn": True, "models": ["fixture-model-a"], "modelDetail": "Listed by the fake CLI."},
                    "claude": {"signedIn": False, "models": [], "modelDetail": "This CLI offers no model list."}}

        with patch.object(chat, "_check_providers", side_effect=fake_check):
            first = {row.id: row for row in self.store.providers()}
            self.assertEqual(first["codex"].modelCatalog, "checking", "the first read never waits for a CLI")
            self.assertEqual(first["codex"].models, [])
            rows = wait_for(lambda: {row.id: row for row in self.store.providers()},
                            lambda value: value["codex"].modelCatalog != "checking")
            self.assertEqual(rows["codex"].models, ["fixture-model-a"])
            self.assertEqual(rows["codex"].modelCatalog, "ready")
            self.assertTrue(rows["codex"].signedIn)
            self.assertTrue(rows["codex"].installed)
            self.assertEqual(rows["claude"].modelCatalog, "unavailable")
            self.assertEqual(rows["claude"].models, [])
            self.assertFalse(rows["claude"].signedIn)
            self.assertIn("no model list", rows["claude"].modelDetail)
            self.assertFalse(rows["coding-plan"].available, "no endpoint is configured in this environment")
            for _ in range(5):
                self.store.providers()
            self.assertEqual(len(checks), 1, "reading the list again does not start a CLI")
            self.store.providers(refresh=True)
            wait_for(lambda: len(checks), lambda value: value == 2)
        # No credential or account detail is carried on the wire.
        body = json.dumps([row.model_dump() for row in self.store.providers()])
        self.assertNotIn(os.environ["CHAT_TEST_SECRET"], body)
        self.assertNotIn("@", body)

    def test_a_new_project_is_created_in_the_workspace_and_can_be_chatted_in(self):
        from monkeyhub_api.models import ChatProjectRequest

        workspace = self.store.workspace()
        self.assertEqual(Path(workspace.workspaceDir), self.runtime / "workspace" / "projects")
        self.assertFalse(workspace.configured, "no workspace was saved, so the runtime root answers")
        created = self.store.create_project(ChatProjectRequest(name="harbour-study"))
        root = Path(created.projectDir)
        self.assertEqual(created.projectId, "harbour-study")
        self.assertEqual(root.parent, self.runtime / "workspace" / "projects")
        self.assertEqual(created.version, 0)
        self.assertIsNone(created.stage)
        # A real P036 project: its own identity, head and empty authored record.
        repository = FilesystemProjectRepository.open(root)
        self.assertEqual(repository.layout.project_id, "harbour-study")
        self.assertEqual(repository.read_head().version, 0)
        self.assertEqual(json.loads((root / "project.json").read_text(encoding="utf-8"))["project_id"], "harbour-study")
        self.assertEqual(self.store.workspace().projects, ["harbour-study"])
        # It can be chatted in, and both survive a Hub restart.
        session = self.create(root)
        self.post(session, "hello")
        self.assertEqual(self.finished(session).status, "idle")
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.assertIn("harbour-study", [row.projectId for row in self.store.projects()])
        self.assertEqual(self.store.get(session.id).projectId, "harbour-study")

    def test_a_new_project_never_overwrites_or_escapes_its_workspace(self):
        from monkeyhub_api.models import ChatProjectRequest

        self.store.create_project(ChatProjectRequest(name="harbour-study"))
        marker = self.runtime / "workspace" / "projects" / "harbour-study" / "project.json"
        before = marker.read_bytes()
        for name, code in (("harbour-study", "PROJECT_EXISTS"), ("has space", "PROJECT_NAME_INVALID"),
                           ("../escape", "PROJECT_NAME_INVALID"), ("   ", "PROJECT_NAME_REQUIRED")):
            with self.assertRaises(HubFailure) as refused:
                self.store.create_project(ChatProjectRequest(name=name))
            self.assertEqual(refused.exception.error.code, code, name)
        self.assertEqual(marker.read_bytes(), before, "the existing project was left exactly as it was")
        # A folder that already holds something is never initialized over.
        occupied = self.runtime / "workspace" / "projects" / "occupied"
        occupied.mkdir(parents=True)
        (occupied / "notes.txt").write_text("mine", encoding="utf-8")
        with self.assertRaises(HubFailure) as taken:
            self.store.create_project(ChatProjectRequest(name="occupied"))
        self.assertEqual(taken.exception.error.code, "PROJECT_EXISTS")
        self.assertEqual((occupied / "notes.txt").read_text(encoding="utf-8"), "mine")
        self.assertFalse((occupied / "project.json").exists())

    def test_created_project_remains_listed_after_chat_refusal_and_hub_restart(self):
        from monkeyhub_api.models import ChatProvider

        unavailable = ChatProvider(id="codex", label="Codex", installed=False, available=False, detail="Unavailable fixture")
        app = create_app(HubSettings(self.runtime))
        with patch.object(app.state.applications, "start"), patch.object(app.state.chats, "providers", return_value=[unavailable]), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            created = client.post("/api/chat/projects", json={"name": "waiting-project"})
            self.assertEqual(created.status_code, 201, created.text)
            project = created.json()
            refused = client.post("/api/chat/sessions", json={"projectDir": project["projectDir"], "provider": "codex"})
            self.assertEqual(refused.status_code, 503, refused.text)
            self.assertEqual(refused.json()["code"], "CHAT_PROVIDER_UNAVAILABLE")
            self.assertEqual(client.get("/api/chat/projects").json(), [project])
        root = Path(project["projectDir"])
        before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        # Broken immediate children are ignored; existing projects outside the
        # configured workspace are not discovered merely by being nearby.
        broken = root.parent / "broken-project"
        broken.mkdir()
        (broken / "project.json").write_text("invalid", encoding="utf-8")
        reopened = create_app(HubSettings(self.runtime))
        with patch.object(reopened.state.applications, "start"), TestClient(reopened, base_url="http://127.0.0.1:8790") as client:
            self.assertEqual(client.get("/api/chat/projects").json(), [project])
            self.assertEqual(client.get("/api/chat/sessions").json(), [])
        self.assertEqual(before, {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_stdio_bridge_boots_without_installing_another_service(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        process = subprocess.run(
            [sys.executable, str(Path(chat.__file__)), "--mcp", "--hub-url", self.store.hub_url, "--chat-id", str(uuid4())],
            input="".join(json.dumps(row) + "\n" for row in requests), capture_output=True, text=True,
            encoding="utf-8", timeout=10, cwd=self.project,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(row) for row in process.stdout.splitlines()]
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual({tool["name"] for tool in replies[1]["result"]["tools"]}, {"studio_schema", "studio_request", "fab_request"})


class AcpCommandTests(unittest.TestCase):
    def test_packaged_node_works_without_path_and_source_keeps_path_node(self):
        with tempfile.TemporaryDirectory(prefix="Hub ACP 路径 ") as temporary:
            root = Path(temporary)
            hub = root / "apps/monkeyhub"
            adapter = hub / "node_modules/@agentclientprotocol/codex-acp/dist/index.js"
            adapter.parent.mkdir(parents=True)
            adapter.touch()
            bundled_node = root / "_runtime/node/node.exe"
            bundled_node.parent.mkdir(parents=True)
            bundled_node.touch()
            with patch.object(chat, "__file__", str(hub / "api/monkeyhub_api/chat.py")), \
                    patch.object(chat.importlib.util, "find_spec", return_value=object()), \
                    patch.object(chat.shutil, "which", return_value=None) as lookup:
                self.assertEqual(chat._codex_acp_command(), (str(bundled_node), str(adapter)))
                lookup.assert_not_called()
                bundled_node.unlink()
                self.assertIsNone(chat._codex_acp_command())
                lookup.return_value = "source-node.exe"
                self.assertEqual(chat._codex_acp_command(), ("source-node.exe", str(adapter)))

    def test_missing_adapter_or_python_sdk_does_not_offer_acp(self):
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary) / "apps/monkeyhub"
            adapter = hub / "node_modules/@agentclientprotocol/codex-acp/dist/index.js"
            with patch.object(chat, "__file__", str(hub / "api/monkeyhub_api/chat.py")), \
                    patch.object(chat.shutil, "which", return_value="node.exe"), \
                    patch.object(chat.importlib.util, "find_spec", return_value=object()) as sdk:
                self.assertIsNone(chat._codex_acp_command())
                adapter.parent.mkdir(parents=True)
                adapter.touch()
                sdk.return_value = None
                self.assertIsNone(chat._codex_acp_command())


if __name__ == "__main__":
    unittest.main()
