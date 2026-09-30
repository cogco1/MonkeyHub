"""Real subprocess chat turns with a local fake CLI; no model or paid call."""

import base64
import json
import os
from pathlib import Path
from pathlib import Path as _Path
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from unittest.mock import patch
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.chat import skill_plugins, store as chat
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import (
    AppStatus, ChatCreateRequest, ChatDesignContext, ChatMessage, ChatPostRequest, HubFailure,
)
from monkeyhub_api.settings.models import ApplicationSettingsDto


FAKE_CLI = r'''
import json, os, subprocess, sys, time, tomllib
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
input_message = None
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
        elif request.get("type") == "user":
            input_message = request
            break
    if input_message is None:
        sys.exit(0)
if "mcp" in args and "list" in args:
    print(json.dumps([{"name": "unrelated", "enabled": True, "transport": {"type": "stdio"}},
                      {"name": "remote-unrelated", "enabled": True, "transport": {"type": "streamable_http"}}]))
    sys.exit(0)
prompt = input_message["message"]["content"][0]["text"] if input_message else sys.stdin.read()
if "scratch-computation-test" in prompt:
    added = [args[index + 1] for index, value in enumerate(args) if value == "--add-dir"]
    scratch = Path(added[-1])
    script = scratch / "calculation.py"
    script.write_text("print(6 * 7)\n", encoding="utf-8")
    result = subprocess.check_output([sys.executable, str(script)], cwd=scratch, text=True)
    (scratch / "result.txt").write_text(result, encoding="utf-8")
config_path = Path(os.environ["CODEX_HOME"]) / "config.toml"
config = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
profile = config.get("profiles", {}).get(config.get("profile"), {})
with Path(sys.argv[1]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "prompt": prompt, "input_message": input_message, "cwd": os.getcwd(),
        "model_provider": profile.get("model_provider", config.get("model_provider"))}) + "\n")
def emit(value):
    print(json.dumps(value), flush=True)
if "fail-test" in prompt:
    print("Cannot authenticate: " + os.environ["CHAT_TEST_SECRET"], file=sys.stderr)
    sys.exit(3)
if "--output-format" in args:
    flag = "--resume" if "--resume" in args else "--session-id"
    native = args[args.index(flag) + 1]
    if "--setting-sources" in args and "--disable-slash-commands" not in args:
        # A library chat's init lists the skills it can load (#463).
        emit({"type": "system", "subtype": "init", "session_id": native, "claude_code_version": "9.9.9",
              "skills": ["monkeyhub-library:hatch-review", "design", "newthing"]})
    else:
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
             {"path": "/api/proposals", "method": "POST", "summary": "Create Proposal",
              "body": {"filler": "s" * 4000}, "components": {"schemas": {"ProposalRequest": {"filler": "c" * 4000}}},
              "note": "Request inputs only; each $ref names an entry of components.schemas."})
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


STALLED_EXIT = r'''
"""Stop a turn whose prepared read is stuck in the binding checks, then exit.

The whole point is the exit: nothing here may be joined on the way out, so a
Studio that stops answering cannot keep the Hub process alive. Run as its own
interpreter because that is the only place interpreter shutdown can be observed.
"""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

root, runtime, project, fake, log = (Path(value) for value in sys.argv[1:6])
sys.path.insert(0, str(root))
from tools.dev import source_roots
source_roots.put_first(root)

from monkeyhub_api.chat import store as chat
from monkeyhub_api.models import ChatCreateRequest, ChatDesignContext, ChatPostRequest

commands = {name: (sys.executable, str(fake), str(log)) for name in ("codex", "claude")}
store = chat.ChatStore(runtime, "http://127.0.0.1:1", commands=commands)
session = store.create(ChatCreateRequest(projectDir=str(project), provider="codex"))
arrived = threading.Event()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if urlsplit(self.path).path == "/api/chat/sessions/" + session.id:
            body = store.get(session.id).model_dump_json().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        # /api/apps and /api/health: the binding checks themselves stop
        # answering, so the stalled read is inside the parallel check and not
        # only on the context request after it.
        arrived.set()
        threading.Event().wait(600)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
store.hub_url = "http://127.0.0.1:%d" % server.server_address[1]

store.post(session.id, ChatPostRequest(
    projectId=session.projectId, content="Raise it to 0.5 m.",
    designContext=ChatDesignContext(sourceRunId="run-001", stateDigest="a" * 64,
                                    targetComponentId="portico", elementId="portico-cornice")))
if not arrived.wait(30):
    print("the binding checks were never reached", flush=True)
    sys.exit(2)
print("stalled in binding checks", flush=True)
store.stop(session.id)
print("turn stopped", flush=True)
store.shutdown()
# The read is still waiting on a socket that will not answer. Returning from
# here has to be enough; nothing may wait for it.
sys.exit(0)
'''


def wait_for(read, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.03)
    raise AssertionError("The fake CLI did not reach the expected state.")



def _tools_of(module, external=None):
    """The tools the stdio server advertises, read from its own listing.

    ``external`` lists them as an external presentation connection does.
    """

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
        module._mcp("http://127.0.0.1:1", "00000000-0000-4000-8000-000000000000", external)
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

    def test_attachment_only_message_download_and_reopen(self):
        original = {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            session = self.create()
            other = self.create(project=self.other)
            data = "合成附件，供测试读取。".encode("utf-8")
            encoded = base64.b64encode(data).decode("ascii")
            posted = client.post(f"/api/chat/sessions/{session.id}/messages", json={
                "projectId": session.projectId,
                "attachments": [{"name": "参考 材料.txt", "mimeType": "text/plain", "data": encoded}],
            })
            self.assertEqual(posted.status_code, 202, posted.text)
            detail = self.finished(session)
            self.assertEqual(detail.status, "idle")
            self.assertEqual(detail.title, "参考 材料.txt")
            message = detail.messages[0]
            self.assertEqual(message.content, "")
            attachment = message.attachments[0]
            self.assertEqual((attachment.name, attachment.size), ("参考 材料.txt", len(data)))
            url = f"/api/chat/sessions/{session.id}/attachments/{attachment.id}"
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, data)
            self.assertEqual(response.headers["content-type"], "application/octet-stream")
            self.assertIn("attachment;", response.headers["content-disposition"])
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(client.get(f"/api/chat/sessions/{other.id}/attachments/{attachment.id}").status_code, 404)
            self.assertNotIn(encoded, (self.runtime / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
            metadata, path = self.store.attachment(session.id, attachment.id)
            self.assertTrue(path.is_relative_to(self.runtime / "chats" / session.id / "attachments"))
            references = json.loads(self.calls()[-1]["prompt"].split("Files attached to this message", 1)[1].split("\n", 1)[1])
            self.assertEqual(references[0]["id"], attachment.id)
            self.assertEqual(references[0]["path"], str(path))
            self.assertIn("prefer attachment_read", self.calls()[-1]["prompt"])
            client.put(f"/api/chat/sessions/{session.id}/archive", json={"archived": True})
            self.assertEqual(client.get(url).content, data)
            reopened = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
            self.addCleanup(reopened.shutdown)
            self.assertTrue(reopened.get(session.id).archived)
            retained, retained_path = reopened.attachment(session.id, attachment.id)
            self.assertEqual(retained, metadata)
            self.assertEqual(retained_path.read_bytes(), data)
        self.assertEqual(original, {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob("*") if p.is_file()})

    def test_attachment_read_text_and_binary_pages_are_bounded_and_read_only(self):
        session, other = self.create(), self.create(project=self.other)
        text = "甲🙂é\n尾" * 20000
        binary = b"\x00\xff\x80abc"
        with patch.object(self.store, "_run"):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, attachments=[
                {"name": "reference.txt", "data": base64.b64encode(text.encode("utf-8")).decode("ascii")},
                {"name": "reference.bin", "data": base64.b64encode(binary).decode("ascii")},
            ]))
        text_file, binary_file = self.store.get(session.id).messages[0].attachments
        before = {p.relative_to(self.root): p.read_bytes() for root in (self.project, self.other, self.runtime)
                  for p in root.rglob("*") if p.is_file()}
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start") as start, TestClient(app, base_url=self.store.hub_url) as client:
            start.reset_mock()
            prefix = f"/api/chat/sessions/{session.id}/attachments/"
            url = prefix + text_file.id + "/read"
            offset, pieces = 0, []
            while offset is not None:
                response = client.get(url, params={"offset": offset, "limit": 32768})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual((result["id"], result["name"], result["mimeType"], result["size"]),
                                 (text_file.id, text_file.name, text_file.mimeType, text_file.size))
                self.assertEqual((result["format"], result["offset"], result["total"], result["page"], result["totalPages"]),
                                 ("text", offset, len(text), 1, 1))
                self.assertLessEqual(len(result["content"]), 32768)
                pieces.append(result["content"])
                offset = result["nextOffset"]
            self.assertEqual("".join(pieces), text)
            binary_url = prefix + binary_file.id + "/read"
            results = [client.get(binary_url, params={"offset": offset, "limit": 4}).json() for offset in (0, 4)]
            self.assertEqual([result["format"] for result in results], ["base64", "base64"])
            self.assertEqual([result["total"] for result in results], [len(binary)] * 2)
            self.assertEqual([result["nextOffset"] for result in results], [4, None])
            self.assertEqual(b"".join(base64.b64decode(result["content"]) for result in results), binary)
            self.assertEqual(client.get(url, params={"offset": len(text) + 1}).json()["content"], "")
            for query in ({"offset": -1}, {"offset": "bad"}, {"limit": 0}, {"limit": 65537}, {"page": 0}, {"page": 2}):
                with self.subTest(query=query):
                    self.assertEqual(client.get(url, params=query).status_code, 422)
            self.assertEqual(client.get(f"/api/chat/sessions/{other.id}/attachments/{text_file.id}/read").status_code, 404)
            self.assertEqual(client.get(prefix + str(uuid4()) + "/read").status_code, 404)
            start.assert_not_called()
            self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for root in (self.project, self.other, self.runtime)
                                      for p in root.rglob("*") if p.is_file()})

    def test_attachment_read_extracts_only_requested_pdf_page_and_reports_invalid_pdf(self):
        import io
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        for content in ("First reference page", "Second reference page", ""):
            page = writer.add_blank_page(width=200, height=200)
            if content:
                font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                         NameObject("/Subtype"): NameObject("/Type1"),
                                         NameObject("/BaseFont"): NameObject("/Helvetica")})
                page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
                stream = DecodedStreamObject()
                stream.set_data(f"BT /F1 12 Tf 10 20 Td ({content}) Tj ET".encode("ascii"))
                page[NameObject("/Contents")] = writer._add_object(stream)
        pdf = io.BytesIO()
        writer.write(pdf)
        session = self.create()
        with patch.object(self.store, "_run"):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, attachments=[
                {"name": "reference.pdf", "mimeType": "application/pdf", "data": base64.b64encode(pdf.getvalue()).decode("ascii")},
                {"name": "broken.pdf", "data": base64.b64encode(b"not a PDF").decode("ascii")},
            ]))
        attachment, broken = self.store.get(session.id).messages[0].attachments
        _, path = self.store.attachment(session.id, attachment.id)
        first = self.store.read_attachment(session.id, attachment.id, page=2, limit=6)
        rest = self.store.read_attachment(session.id, attachment.id, page=2, offset=first["nextOffset"])
        self.assertEqual((first["format"], first["page"], first["totalPages"]), ("text", 2, 3))
        self.assertEqual(first["content"] + rest["content"], "Second reference page")
        self.assertIsNone(rest["nextOffset"])
        empty = self.store.read_attachment(session.id, attachment.id, page=3)
        self.assertEqual((empty["content"], empty["total"], empty["nextOffset"]), ("", 0, None))
        self.assertEqual(path.read_bytes(), pdf.getvalue())
        for identifier, page, code in ((attachment.id, 4, "CHAT_ATTACHMENT_PAGE_INVALID"), (broken.id, 1, "CHAT_ATTACHMENT_PDF_INVALID")):
            with self.subTest(identifier=identifier, page=page), self.assertRaises(HubFailure) as failure:
                self.store.read_attachment(session.id, identifier, page=page)
            self.assertEqual(failure.exception.error.code, code)

    def test_attachment_tool_reads_only_running_chat_without_studio_or_path_arguments(self):
        session, other = self.create(), self.create(project=self.other)
        with patch.object(self.store, "_run"):
            for current, data in ((session, b"local reference"), (other, b"other reference")):
                self.store.post(current.id, ChatPostRequest(projectId=current.projectId,
                    attachments=[{"name": "reference.txt", "data": base64.b64encode(data).decode("ascii")}]))
        attachment = self.store.get(session.id).messages[0].attachments[0]
        foreign = self.store.get(other.id).messages[0].attachments[0]
        calls = []

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            self.assertEqual(base, self.store.hub_url)
            self.assertEqual(method, "GET")
            self.assertIsNone(body)
            calls.append(path)
            prefix = f"/api/chat/sessions/{session.id}"
            if path == prefix:
                return self.store.get(session.id).model_dump()
            parsed = urlsplit(path)
            self.assertTrue(parsed.path.startswith(prefix + "/attachments/"), path)
            self.assertTrue(parsed.path.endswith("/read"), path)
            options = {key: int(values[0]) for key, values in parse_qs(parsed.query).items()}
            return self.store.read_attachment(session.id, parsed.path.split("/")[-2], **options)

        described = next(tool for tool in _tools_of(chat) if tool["name"] == "attachment_read")
        self.assertEqual(set(described["inputSchema"]["properties"]), {"attachmentId", "offset", "limit", "page"})
        self.assertIn("Empty PDF text does not mean", described["description"])
        before = {p.relative_to(self.root): p.read_bytes() for root in (self.project, self.other, self.runtime)
                  for p in root.rglob("*") if p.is_file()}
        with patch.object(chat, "_request_json", side_effect=request), patch.object(chat, "_bound_studio") as studio:
            result = chat.call_tool(self.store.hub_url, session.id, "attachment_read", {"attachmentId": attachment.id, "limit": 5})
            self.assertEqual((result["content"], result["nextOffset"]), ("local", 5))
            with self.assertRaises(HubFailure) as cross_chat:
                chat.call_tool(self.store.hub_url, session.id, "attachment_read", {"attachmentId": foreign.id})
            self.assertEqual(cross_chat.exception.error.code, "CHAT_ATTACHMENT_NOT_FOUND")
            for options in ({"path": str(self.project)}, {"chatId": other.id}, {"method": "POST"}, {"offset": -1},
                            {"offset": True}, {"offset": 1.5}, {"limit": 0}, {"limit": 65537}, {"page": 0},
                            {"limit": "5"}, {"attachmentId": "../reference.txt"}, {"attachmentId": 3}):
                count = len(calls)
                with self.subTest(options=options), self.assertRaises(HubFailure):
                    chat.call_tool(self.store.hub_url, session.id, "attachment_read", {"attachmentId": attachment.id, **options})
                self.assertEqual(len(calls), count)
            with patch.object(chat, "_project", return_value=("replaced-project", session.projectDir)):
                with self.assertRaises(HubFailure) as mismatch:
                    chat.call_tool(self.store.hub_url, session.id, "attachment_read", {"attachmentId": attachment.id})
                self.assertEqual(mismatch.exception.error.code, "CHAT_PROJECT_MISMATCH")
            self.store._sessions[session.id].status = "idle"
            with self.assertRaises(HubFailure) as stopped:
                chat.call_tool(self.store.hub_url, session.id, "attachment_read", {"attachmentId": attachment.id})
            self.assertEqual(stopped.exception.error.code, "CHAT_NOT_RUNNING")
            studio.assert_not_called()
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for root in (self.project, self.other, self.runtime)
                                  for p in root.rglob("*") if p.is_file()})

    def test_invalid_attachments_never_write_a_message_or_file(self):
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            session = self.create()
            transcript = self.runtime / "chats" / f"{session.id}.json"
            saved = transcript.read_bytes()
            for attachment in (
                {"name": "../escape.txt", "data": "YQ=="},
                {"name": "C:\\escape.txt", "data": "YQ=="},
                {"name": "file.txt", "data": "not valid base64"},
            ):
                with self.subTest(attachment=attachment):
                    response = client.post(f"/api/chat/sessions/{session.id}/messages", json={
                        "projectId": session.projectId, "attachments": [attachment],
                    })
                    self.assertEqual(response.status_code, 422, response.text)
            response = client.post(f"/api/chat/sessions/{session.id}/messages", json={
                "projectId": session.projectId, "attachments": [{"name": "a", "data": "YQ=="}] * 9,
            })
            self.assertEqual(response.status_code, 422)
            self.assertEqual(transcript.read_bytes(), saved)
            self.assertFalse((self.runtime / "chats" / session.id).exists())
            self.assertEqual(self.store.get(session.id).messages, [])

    def test_oversize_attachments_are_refused_before_persistence(self):
        session = self.create()
        for sizes in ((20 * 1024 * 1024 + 1,), (15 * 1024 * 1024,) * 3):
            with self.subTest(sizes=sizes):
                request = ChatPostRequest(projectId=session.projectId, attachments=[
                    {"name": f"file-{index}.bin", "data": base64.b64encode(b"x" * size).decode("ascii")}
                    for index, size in enumerate(sizes)
                ])
                with self.assertRaises(HubFailure) as error:
                    self.store.post(session.id, request)
                self.assertEqual(error.exception.error.code, "CHAT_ATTACHMENT_TOO_LARGE")
                self.assertEqual(self.store.get(session.id).messages, [])
                self.assertFalse((self.runtime / "chats" / session.id).exists())

    def test_attachment_write_is_rolled_back_when_transcript_save_fails(self):
        session = self.create()
        request = ChatPostRequest(projectId=session.projectId, attachments=[{"name": "keep.txt", "data": "YQ=="}])
        self.store.post(session.id, request)
        retained = self.finished(session).messages[0].attachments[0]
        _, retained_path = self.store.attachment(session.id, retained.id)
        transcript = self.runtime / "chats" / f"{session.id}.json"
        saved = transcript.read_bytes()
        with patch.object(chat.os, "replace", side_effect=OSError("fixture disk failure")), self.assertRaises(OSError):
            self.store.post(session.id, request)
        self.assertEqual(transcript.read_bytes(), saved)
        self.assertEqual(retained_path.read_bytes(), b"a")
        self.assertEqual(list(retained_path.parent.iterdir()), [retained_path])
        self.assertEqual(len(self.store.get(session.id).messages), 2)

    def test_legacy_codex_images_reach_first_and_resumed_turn(self):
        session = self.create()
        for index in range(2):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="Read the image.",
                attachments=[{"name": "test.png", "mimeType": "image/png", "data": "cGljdHVyZQ=="}]))
            detail = self.finished(session)
            self.assertEqual(detail.status, "idle")
            args = self.calls()[-1]["args"]
            image_index = args.index("--image")
            self.assertEqual(Path(args[image_index + 1]).read_bytes(), b"picture")
            self.assertEqual(args[-1], "-")
            self.assertNotIn("cGljdHVyZQ==", args)
            if index:
                self.assertLess(args.index("resume"), image_index)
                self.assertEqual(args[args.index("resume") + 1], self.store._sessions[session.id].nativeSessionId)
        self.post(session)
        self.assertEqual(self.finished(session).status, "idle")
        self.assertNotIn("--image", self.calls()[-1]["args"])
        self.assertNotIn("test.png", self.calls()[-1]["prompt"])

    def test_claude_attachments_use_stdin_and_preserve_resume(self):
        for provider in ("claude", "coding-plan"):
            with self.subTest(provider=provider), patch.dict(os.environ, {
                "ANTHROPIC_BASE_URL": "https://fixture.example.invalid", "ANTHROPIC_AUTH_TOKEN": "fixture-plan-token",
            }):
                session = self.create(provider=provider)
                native = None
                for index in range(2):
                    self.store.post(session.id, ChatPostRequest(projectId=session.projectId,
                        attachments=[{"name": "view.png", "mimeType": "image/png", "data": "cGljdHVyZQ=="},
                                     {"name": "note.pdf", "mimeType": "application/pdf", "data": "JVBERg=="}]))
                    detail = self.finished(session)
                    self.assertEqual(detail.status, "idle", detail.error)
                    call = self.calls()[-1]
                    args, envelope = call["args"], call["input_message"]
                    self.assertEqual(args[args.index("--input-format") + 1], "stream-json")
                    self.assertNotIn("cGljdHVyZQ==", args)
                    self.assertNotIn(str(self.runtime), args)
                    self.assertEqual(envelope["session_id"], self.store._sessions[session.id].nativeSessionId)
                    self.assertIsNone(envelope["parent_tool_use_id"])
                    blocks = envelope["message"]["content"]
                    self.assertEqual([block["type"] for block in blocks], ["text", "image"])
                    self.assertIn("note.pdf", blocks[0]["text"])
                    self.assertEqual(blocks[1]["source"], {"type": "base64", "media_type": "image/png", "data": "cGljdHVyZQ=="})
                    if index:
                        self.assertEqual(self.store._sessions[session.id].nativeSessionId, native)
                        self.assertEqual(args[args.index("--resume") + 1], native)
                    native = self.store._sessions[session.id].nativeSessionId
                self.post(session)
                self.assertEqual(self.finished(session).status, "idle")
                # A turn without files reads the same stream-json stdin (#301),
                # with its prompt as the only block.
                plain = self.calls()[-1]["input_message"]["message"]["content"]
                self.assertEqual([block["type"] for block in plain], ["text"])
                self.assertIn("--replay-user-messages", self.calls()[-1]["args"])
                self.assertNotIn("view.png", self.calls()[-1]["prompt"])

    def test_turn_envelope_reuses_connected_action_contract(self):
        tools = {tool["name"]: tool for tool in _tools_of(chat)}
        modelling = tools["studio_request"]["description"]
        for contract in ("/api/proposals/construction", "/api/construction/model", "/api/capabilities",
                         "sourceRunId", "keep", "against=<runId>", "never send the request again"):
            self.assertIn(contract, modelling)
        # #419: meaning is added later, to the same id, and only when the user states it.
        self.assertIn("MEANING: only when the user says what a part is, POST /api/proposals/facets", modelling)
        self.assertNotIn("GET /api/semantics supplies", modelling)
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
                    for duplicate in ("/api/proposals/construction", "/api/capabilities", "awaitSeconds"):
                        self.assertNotIn(duplicate, envelope)
                    for boundary in ("Project files are read-only", "the user's keep conditions",
                                     "do not claim approval, issuance or printer upload",
                                     "Do not switch Hub configuration"):
                        self.assertIn(boundary, envelope)
                    self.assertIn("generate and inspect a candidate", envelope)
                    self.assertIn("then revise as needed", envelope)
                    for restriction in ("Compose the whole requested modeling chain", "Ask at most one",
                                        "Do not export a candidate after every form"):
                        self.assertNotIn(restriction, envelope)

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
        # #404 F7: this adapter's tools arrive with the prompt. Deferred, every
        # turn began with a ToolSearch call before any design work.
        config = json.loads(args[args.index("--mcp-config") + 1])
        self.assertEqual(list(config["mcpServers"]), ["monkeyhub"])
        self.assertIs(config["mcpServers"]["monkeyhub"]["alwaysLoad"], True)

    def test_usage_sources_preserve_prior_sessions_after_reset_and_reload_from_legacy_records(self):
        sessions = [(self.create(), "cli", str(uuid4()), str(uuid4())),
                    (self.create(self.other), "acp", "fixture/old-session", "fixture/new-session")]
        self.create()  # No provider session has started.
        claude = self.create(provider="claude")
        self.store._sessions[claude.id].nativeSessionId = str(uuid4())
        self.store._fresh_provider_session(claude.id)
        expected = []
        for session, transport, old_id, _ in sessions:
            row = self.store._sessions[session.id]
            row.transport = transport
            setattr(row, "acpSessionId" if transport == "acp" else "nativeSessionId", old_id)
            self.store._save(row)
            path = self.store.root / f"{session.id}.json"
            legacy = json.loads(path.read_text(encoding="utf-8"))
            legacy.pop("priorProviderSessionIds")
            path.write_text(json.dumps(legacy), encoding="utf-8")
            expected.append({"projectId": session.projectId, "sessionId": old_id})
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, self.store.hub_url, commands=self.commands)
        self.assertCountEqual([row.model_dump() for row in self.store.usage_sources()], expected)
        for session, transport, old_id, new_id in sessions:
            row = self.store._fresh_provider_session(session.id)
            self.assertEqual(row.priorProviderSessionIds, [old_id])
            self.assertIsNone(row.nativeSessionId)
            self.assertIsNone(row.acpSessionId)
            # A reset without a new connection adds no identity. Reobserving
            # the same ID is also one usage source, never duplicate accounting.
            row = self.store._fresh_provider_session(session.id)
            self.assertEqual(row.priorProviderSessionIds, [old_id])
            setattr(row, "acpSessionId" if transport == "acp" else "nativeSessionId", old_id)
            row = self.store._fresh_provider_session(session.id)
            self.assertEqual(row.priorProviderSessionIds, [old_id])
            setattr(row, "acpSessionId" if transport == "acp" else "nativeSessionId", new_id)
            self.store._save(row)
            expected.append({"projectId": session.projectId, "sessionId": new_id})
        self.assertCountEqual([row.model_dump() for row in self.store.usage_sources()], expected)
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, self.store.hub_url, commands=self.commands)
        self.assertCountEqual([row.model_dump() for row in self.store.usage_sources()], expected)
        for session, _, old_id, _ in sessions:
            self.assertEqual(self.store._sessions[session.id].priorProviderSessionIds, [old_id])
            self.assertNotIn("priorProviderSessionIds", self.store.get(session.id).model_dump())

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

    def test_archiving_clears_the_chats_scratch_and_restoring_starts_empty(self):
        """#404 item 6: scratch is temporary computation, cleared with the chat's archive."""
        session, kept = self.create(provider="claude"), self.create(provider="claude")
        scratch = self.store._scratch_path(session.id)
        (scratch / "work").mkdir(parents=True)
        (scratch / "work" / "trial.py").write_text("print('volume')", encoding="utf-8")
        (self.store._scratch_path(kept.id) / "kept.txt").write_text("still in use", encoding="utf-8")
        attachments = self.runtime / "chats" / session.id / "attachments"
        attachments.mkdir(parents=True)
        (attachments / "sketch.png").write_bytes(b"not scratch")
        self.assertTrue(self.store.set_archived(session.id, True).archived)
        self.assertFalse(scratch.exists())
        self.assertTrue((attachments / "sketch.png").exists(), "attachments are the chat's record, not scratch")
        self.assertTrue((self.store._scratch_path(kept.id) / "kept.txt").exists(), "another chat's scratch stays")
        # Something left in an archived chat's scratch is cleared when Hub starts again.
        scratch.mkdir(parents=True)
        (scratch / "late.txt").write_text("written after archiving", encoding="utf-8")
        self.store.shutdown()
        self.store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=self.commands)
        self.assertTrue(self.store.get(session.id).archived)
        self.assertFalse(scratch.exists())
        self.assertTrue((self.store._scratch_path(kept.id) / "kept.txt").exists())
        restored = self.store.set_archived(session.id, False)
        self.assertFalse(restored.archived)
        self.assertTrue(scratch.is_dir())
        self.assertEqual(list(scratch.iterdir()), [], "a restored chat starts with an empty scratch")

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
        # Computer use is named here like the rest; whether it may actually run
        # is the policy file's answer, given by the route the tool calls.
        exposed = ("studio_schema", "studio_request", "visual_review", "fab_request", "attachment_read", "chat_present",
                   "computer_inspect", "computer_action", "computer_record")
        self.assertEqual(set(override["monkeyhub"]["enabled_tools"]), set(exposed))
        self.assertEqual(override["monkeyhub"]["tools"], {
            name: {"approval_mode": "approve"} for name in exposed
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
            with self.store.application_lifecycle("monkeyboard", stopping=True, project_dir=str(self.project)):
                self.fail("A running chat must retain its shared Studio.")
        self.assertEqual(self.store.stop(first.id).status, "interrupted")
        self.assertEqual(self.store.get(second.id).status, "running")
        self.store.stop(second.id)
        self.assertEqual(self.store.get(other.id).status, "running")
        with self.store.application_lifecycle("monkeyboard", stopping=True, project_dir=str(self.project)):
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
        self.assertNotIn("alwaysLoad", " ".join(codex_command), "a Claude CLI setting, not Codex's")
        self.assertNotIn("read-only", codex_command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex_command)
        self.assertEqual(codex_command[codex_command.index("-C") + 1], str(source))
        self.assertEqual(codex_command[codex_command.index("--add-dir") + 1], str(self.project))

        # Claude: its own built-in tools, the source and project, and chat scratch.
        self.assertEqual(claude_command[claude_command.index("--tools") + 1], "default")
        # Available is not approved. With nobody to answer a prompt, the tools a
        # headless turn may actually use have to be named, and editing and
        # running are among them.
        approved = claude_command[claude_command.index("--allowedTools") + 1].split(",")
        for name in ("Read", "Glob", "Grep", "Write", "Edit", "Bash"):
            self.assertIn(name, approved, name)
        for name in ("studio_request", "studio_schema", "visual_review", "fab_request", "attachment_read"):
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

    def test_claude_can_compute_in_separate_chat_scratch_in_bundle_and_checkout(self):
        original = {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        scratch_paths = []
        for source in (None, chat._source_checkout()):
            with self.subTest(source=source), patch.object(chat, "_source_checkout", return_value=source):
                session = self.create(provider="claude")
                self.post(session, "scratch-computation-test")
                self.assertEqual(self.finished(session).status, "idle")
                call = self.calls()[-1]
                scratch = self.runtime / "chats" / session.id / "scratch"
                scratch_paths.append(scratch)
                added = [call["args"][index + 1] for index, value in enumerate(call["args"]) if value == "--add-dir"]
                self.assertEqual(added, ([str(self.project)] if source is not None else []) + [str(scratch)])
                self.assertEqual(Path(call["cwd"]).resolve(), (source or self.project).resolve())
                self.assertIn(str(scratch), call["prompt"])
                self.assertIn("temporary calculation scripts", call["prompt"])
                self.assertIn("save design results through the connected tools and P036", call["prompt"])
                self.assertEqual((scratch / "result.txt").read_text(encoding="utf-8").strip(), "42")
                self.assertFalse(scratch.resolve().is_relative_to(self.project.resolve()))
                self.assertNotIn(".claude", scratch.parts)
        self.assertNotEqual(*scratch_paths)
        self.assertEqual(original, {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob("*") if p.is_file()})

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
        self.assertIn("existing controls", call["prompt"])
        self.assertIn("dependencies for linked edits", call["prompt"])
        # Looking is bounded: a spatial result through visual_review, a
        # deterministic edit by readback alone.
        self.assertIn("Judge a spatial or formal result with visual_review", call["prompt"])
        self.assertIn("check a deterministic edit by readback without looking", call["prompt"])
        self.assertIn("delivers exact images for you to inspect", call["prompt"])
        self.assertIn("do not query or create a model admission for an unchanged source", call["prompt"])

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
        if path.startswith(("/api/proposals/", "/api/jobs/", "/api/candidates/", "/api/state", "/api/construction",
                            "/api/domains")) or path == "/api/project":
            self.assertEqual(base, "http://127.0.0.1:8791")
        return path

    def test_program_and_massing_routes_remain_bound_agent_capabilities(self):
        """Retiring a UI panel must not remove the Agent's real runtime contract."""

        session = self.create()
        session.status = "running"
        source = {"stateDigest": "a" * 64, "sourceRunId": "candidate-a",
                  "modelSource": {"runId": "candidate-a", "stateDigest": "a" * 64, "assetSha256": "b" * 64}}
        routes = [
            ("GET", "/api/program?run=candidate-a", None),
            ("GET", "/api/options?run=candidate-a", None),
            ("GET", "/api/semantics", None),
            ("POST", "/api/program", {**source, "sheet": {"departments": []}}),
            ("POST", "/api/options", {**source, "transform": "add_floor"}),
            ("POST", "/api/options/option-1/select", None),
        ]
        forwarded = []
        description = next(tool for tool in _tools_of(chat) if tool["name"] == "studio_request")["description"]
        # GET /api/semantics stays callable below; the guide no longer names it,
        # because meaning is added with facets rather than a semantic kind (#419).
        for path in ("/api/program", "/api/options", "/api/options/{id}/select"):
            self.assertIn(path, description)

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": session.projectId, "projectDir": str(self.project)}
            if method == "GET":
                self.assertEqual(base, "http://127.0.0.1:8791")
            forwarded.append((method, path, body))
            return {"method": method, "path": path, "body": body}

        with patch.object(chat, "_request_json", side_effect=request):
            for method, path, body in routes:
                with self.subTest(method=method, path=path):
                    result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                        "method": method, "path": path, **({"body": body} if body is not None else {}),
                    })
                    self.assertEqual((result["method"], result["path"], result["body"]), (method, path, body))
        self.assertEqual(forwarded, routes, "Each request forwards once; reads preserve run and writes preserve exact source.")

    def test_action_discovery_reads_real_runtime_contracts_without_prior_path_knowledge(self):
        from project_runtime.main import create_app as studio_app
        from project_runtime.settings import StudioSettings

        runtime = studio_app(StudioSettings(project_dir=self.project, cad_export="off"))
        self.addCleanup(runtime.state.jobs.shutdown)
        self.addCleanup(runtime.state.render_jobs.shutdown)
        document = runtime.openapi()
        session = self.create()
        calls = []
        advertised = next(tool for tool in _tools_of(chat) if tool["name"] == "studio_schema")["inputSchema"]
        self.assertNotIn("path", advertised.get("required", []))
        self.assertNotIn("method", advertised.get("required", []))
        self.assertIn("pathPrefix", advertised["properties"])

        def request(base, path, method="GET", body=None, **kwargs):
            calls.append((method, path))
            self.assertEqual((base, method, path), ("http://127.0.0.1:8791", "GET", "/openapi.json"))
            return json.loads(json.dumps(document))

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=request):
            listing = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {})
            self.assertEqual((listing["offset"], listing["limit"]), (0, 30))
            self.assertLessEqual(len(listing["actions"]), 30)
            drawings = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"pathPrefix": "/api/drawings"})
            actions = {(row["method"], row["path"]) for row in drawings["actions"]}
            for path in ("elevations", "sheets", "section-perspectives", "plans"):
                self.assertIn(("POST", f"/api/drawings/{path}"), actions)
            self.assertIn(("GET", "/api/drawings/model-view"), actions)
            self.assertNotIn(("POST", "/api/drawings/plans/dimension-proposal"), actions)
            self.assertTrue(all(set(row) == {"method", "path", "summary"} for row in drawings["actions"]),
                            "Discovery returns concise actions; contracts are read only after selecting one.")
            selected = next(row for row in drawings["actions"] if row["path"].endswith("/sheets"))
            schema = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                    {"method": selected["method"], "path": selected["path"]})
            self.assertIn("modelSource", schema["components"]["schemas"]["SheetRequestDto"]["properties"])
            model_source = schema["components"]["schemas"]["ModelSourceDto"]["properties"]
            self.assertTrue({"runId", "stateDigest", "assetSha256"}.issubset(model_source))
            assets = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"pathPrefix": "/api/model-assets"})
            asset = next(row for row in assets["actions"] if row["path"].endswith("/index"))
            self.assertEqual(asset["method"], "GET")
            self.assertIn("{asset_sha256}", asset["path"])
            asset_schema = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                          {"method": asset["method"], "path": asset["path"]})
            self.assertEqual(asset_schema["path"], asset["path"])
        self.assertTrue(calls)

    def test_action_discovery_pages_and_reflects_new_allowed_runtime_routes(self):
        session = self.create()
        document = {"paths": {
            "/api/drawings/model-view": {"get": {"summary": "Inspect an exact model"}},
            "/api/drawings/sheets": {"post": {"summary": "Make a registered sheet"}},
            "/api/documents": {"get": {"summary": "Read documents"}, "post": {"summary": "Register any document"}},
            "/api/visual-reviews": {"post": {"summary": "Separate allowance"}},
        }, "components": {"schemas": {}}}
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=lambda *a, **k: json.loads(json.dumps(document))):
            first = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"limit": 2})
            second = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"offset": 2, "limit": 2})
            self.assertEqual((first["total"], second["total"]), (3, 3))
            self.assertEqual(len(first["actions"]), 2)
            self.assertEqual(len(second["actions"]), 1)
            self.assertTrue(first.get("next"))
            self.assertFalse(second.get("next"))
            actions = {(row["method"], row["path"]) for row in first["actions"] + second["actions"]}
            self.assertEqual(actions, {("GET", "/api/documents"), ("GET", "/api/drawings/model-view"),
                                       ("POST", "/api/drawings/sheets")})
            writes = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"method": "POST"})
            self.assertEqual([(row["method"], row["path"]) for row in writes["actions"]],
                             [("POST", "/api/drawings/sheets")])
            # A route absent from this Runtime must not be advertised merely
            # because Hub permits it. Once Runtime supplies it, no second
            # hand-maintained capability list is needed.
            document["paths"]["/api/drawings/elevations"] = {"post": {"summary": "Make elevations"}}
            updated = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"method": "POST"})
            self.assertEqual({row["path"] for row in updated["actions"]},
                             {"/api/drawings/sheets", "/api/drawings/elevations"})
            empty = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"offset": 100})
            self.assertEqual(empty["actions"], [])
            self.assertFalse(empty.get("next"))

    def test_action_discovery_rejects_invalid_paging_and_preserves_project_binding(self):
        session = self.create()
        bound = ("http://127.0.0.1:8791", session.model_dump())
        with patch.object(chat, "_bound_studio", return_value=bound), patch.object(chat, "_request_json") as request:
            for arguments in ({"offset": -1}, {"offset": True}, {"offset": "0"},
                              {"limit": 0}, {"limit": 51}, {"limit": False}, {"limit": "2"}):
                with self.subTest(arguments=arguments), self.assertRaises(HubFailure) as failure:
                    chat.call_tool(self.store.hub_url, session.id, "studio_schema", arguments)
                self.assertEqual(failure.exception.error.code, "CHAT_TOOL_INVALID")
            request.assert_not_called()
        with patch.object(chat, "_bound_studio", side_effect=HubFailure(409, "CHAT_PROJECT_MISMATCH", "Wrong project")), \
                patch.object(chat, "_request_json") as request:
            with self.assertRaises(HubFailure) as failure:
                chat.call_tool(self.store.hub_url, session.id, "studio_schema", {})
            self.assertEqual(failure.exception.error.code, "CHAT_PROJECT_MISMATCH")
            request.assert_not_called()

    def test_unknown_drawing_action_recovers_without_automatically_writing(self):
        session = self.create()
        document = {"paths": {
            "/api/drawings/elevations": {"post": {"summary": "Make elevations"}},
            "/api/drawings/sheets": {"post": {"summary": "Make a registered sheet"}},
            "/api/drawings/model-view": {"get": {"summary": "Inspect a model"}},
            "/api/documents": {"get": {"summary": "List drawings"}, "post": {"summary": "Register a document"}},
            "/api/visual-reviews": {"post": {"summary": "Separate allowance"}},
        }, "components": {"schemas": {}}}
        calls = []

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            calls.append((method, path, body))
            if path == "/openapi.json":
                return json.loads(json.dumps(document))
            return {"method": method, "path": path, "body": body}

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=request):
            for name in ("studio_schema", "studio_request"):
                for path in ("/api/drawings/projection", "/api/drawings/sheet"):
                    with self.subTest(name=name, path=path), self.assertRaises(HubFailure) as failure:
                        chat.call_tool(self.store.hub_url, session.id, name, {"method": "POST", "path": path})
                    self.assertEqual(failure.exception.error.code, "CHAT_ACTION_UNKNOWN")
                    self.assertIn("studio_schema", failure.exception.error.detail)
                    self.assertIn("/api/drawings/sheets", failure.exception.error.detail)
                for path in ("/api/documents", "/api/visual-reviews"):
                    with self.subTest(name=name, path=path), self.assertRaises(HubFailure) as failure:
                        chat.call_tool(self.store.hub_url, session.id, name, {"method": "POST", "path": path})
                    self.assertEqual(failure.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
                    self.assertIn("studio_schema", failure.exception.error.detail)
            with self.assertRaises(HubFailure) as absent:
                chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                               {"method": "POST", "path": "/api/drawings/section-perspectives"})
            self.assertEqual(absent.exception.error.code, "CHAT_ACTION_UNSUPPORTED")
            self.assertIn("studio_schema", absent.exception.error.detail)
            for arguments in ({"method": "POST", "path": "https://remote.example/api/drawings/sheets"},
                              {"method": "DELETE", "path": "/api/drawings/sheets"}):
                with self.assertRaises(HubFailure) as invalid:
                    chat.call_tool(self.store.hub_url, session.id, "studio_request", arguments)
                self.assertEqual(invalid.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
            self.assertTrue(all(method == "GET" and path == "/openapi.json" for method, path, _ in calls))
            listing = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                     {"method": "POST", "pathPrefix": "/api/drawings"})
            selected = next(row for row in listing["actions"] if row["path"].endswith("/sheets"))
            body = {"projectId": session.projectId, "modelSource": {
                "runId": "candidate-exact", "stateDigest": "a" * 64, "assetSha256": "b" * 64}}
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                    {"method": selected["method"], "path": selected["path"], "body": body})
            self.assertEqual(result["body"], body)
        self.assertEqual([row for row in calls if row[0] != "GET"], [("POST", "/api/drawings/sheets", body)])

    def test_registered_capability_matches_always_point_to_complete_action_discovery(self):
        session = self.create()
        index = {"capabilities": [{"capabilityId": "candidate.modify_existing"}], "registered": 2, "note": None}
        seen = []

        def request(base, path, method="GET", body=None, **kwargs):
            seen.append((method, path))
            if path == "/openapi.json":
                return {"paths": {"/api/drawings/sheets": {"post": {"summary": "Make a sheet"}}},
                        "components": {"schemas": {}}}
            self.assertEqual(path, "/api/capabilities?goal=drawings%20for%20candidate")
            return dict(index)

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=request):
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                    {"method": "GET", "path": "/api/capabilities?goal=drawings%20for%20candidate"})
            self.assertEqual({key: result[key] for key in index}, index)
            discovery = result["actionDiscovery"]
            self.assertEqual((discovery["tool"], discovery["arguments"]), ("studio_schema", {}))
            listing = chat.call_tool(self.store.hub_url, session.id, discovery["tool"], discovery["arguments"])
            self.assertIn(("POST", "/api/drawings/sheets"), {(row["method"], row["path"]) for row in listing["actions"]})
        self.assertTrue(all(method == "GET" for method, _ in seen))

    def test_capability_query_encodes_chinese_and_spaces_without_encoding_percent_twice(self):
        import io

        paths = ("/api/capabilities?goal=重出 立面&run=candidate-1",
                 "/api/capabilities?goal=%E9%87%8D%E5%87%BA%20%E7%AB%8B%E9%9D%A2&run=candidate-1")
        seen = []

        def open_request(request, **kwargs):
            seen.append(request.full_url)
            return io.BytesIO(b'{"capabilities": []}')

        with patch.object(chat, "_SERVICE_OPENER") as opener:
            opener.open.side_effect = open_request
            for path in paths:
                self.assertEqual(chat._request_json("http://127.0.0.1:8791", path), {"capabilities": []})
        self.assertEqual(seen, ["http://127.0.0.1:8791" + paths[1]] * 2)
        query = parse_qs(urlsplit(seen[0]).query)
        self.assertEqual(query, {"goal": ["重出 立面"], "run": ["candidate-1"]})

    def test_the_drawing_action_is_findable_and_callable_without_exploring(self):
        """What a request to make a form actually needs: the described path works."""

        session = self.create()
        session.status = "running"
        described = [tool for tool in _tools_of(chat)]
        request_tool = next(tool for tool in described if tool["name"] == "studio_request")
        schema_tool = next(tool for tool in described if tool["name"] == "studio_schema")
        # The action, its fields and its units are stated where the CLI reads
        # them, so making a massing needs no schema round trip at all.
        for stated in ("POST /api/proposals/construction", "stateDigest", "script", "rect(", "extrude(",
                       "at=top(", "cut(", "metres", "(x, z)", "Y up",
                       "/api/proposals/{id}/candidate", "GET /api/construction/model", "/api/project/modeling",
                       "剖透视", "pathPrefix /api/drawings", "pathPrefix /api/board"):
            self.assertIn(stated, request_tool["description"], stated)
        # Drawing work is one line upfront; its full text is the guide the
        # drawing and Board prefixes answer with.
        drawings, board = chat._guide("/api/drawings/"), chat._guide("/api/documents")
        for stated in ("GET /api/documents?runId=", "MonkeyDiagram's documents list",
                       "GET /api/drawings/styles", "POST /api/drawings/sheets",
                       "POST /api/drawings/section-perspectives", "剖透视",
                       "keep: 'left'|'right'", "POST /api/board/export",
                       # Entourage on a cut plan (#244): the typed edit, its
                       # symbols and the reads that show where each one landed.
                       "POST /api/drawings/plans", "previousRevisionRef", "dressingOperations",
                       "'person-plan'|'tree-plan'", "GET /api/drawings/plans/vector",
                       "POST /api/drawings/plans/status", "GET /api/drawings/plans/dimensions"):
            self.assertIn(stated, drawings, stated)
            self.assertNotIn(stated, request_tool["description"].replace("剖透视 section perspectives", ""), stated)
        for stated in ("/api/document-annotations", "baseRevisionSha256", "POST /api/board/export"):
            self.assertIn(stated, board, stated)
        self.assertIn("clarify a field or correct a request", schema_tool["description"])
        plans = []

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path == "/api/drawings/plans/status":
                # A POST that only reads goes to the bound Studio unadmitted.
                self.assertEqual((base, method, headers), ("http://127.0.0.1:8791", "POST", None))
                return {"method": method, "body": body, "path": path}
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == "/api/drawings/plans":
                plans.append(body)
                if any(row.get("id") == "missing" for row in body.get("dressingOperations") or ()):
                    raise HubFailure(422, "DRAWING_DRESSING_MISSING", "No dressing object missing exists in this drawing revision.")
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            if path == "/openapi.json":
                return {"paths": {"/api/project/modeling": {"post": {"summary": "initialize"}},
                                   "/api/documents": {"get": {"summary": "list drawings"},
                                                      "post": {"summary": "register documents outside chat"}},
                                   "/api/documents/{asset_sha256}/bytes": {"get": {"summary": "read document bytes"}},
                                   "/api/visual-reviews": {"post": {"summary": "separate review allowance"}},
                                   "/api/drawings/plans/dimension-proposal": {"post": {"summary": "propose plan dimensions"}},
                                   "/api/proposals/construction": {"post": {"summary": "construct"}},
                                   "/api/proposals/facets": {"post": {"summary": "add facets"}},
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
            script = "mass = extrude(rect(0, 0, 6, 4), 3.2)"
            drawn = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals/construction",
                "body": {"stateDigest": "a" * 64, "script": script},
            })
            self.assertEqual((drawn["path"], drawn["body"]),
                             ("/api/proposals/construction", {"stateDigest": "a" * 64, "script": script}))
            # A later script continues from a candidate and an unexecuted chain,
            # with the same source fields every write carries.
            continued_body = {"stateDigest": "a" * 64, "sourceRunId": "candidate-before",
                              "sourceStageRef": "stage-base", "sourceProposalId": "proposal-before",
                              "script": 'set_base(get("mass"), 2)', "keep": ["entity:block-1"]}
            raised = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals/construction", "body": continued_body,
            })
            self.assertEqual((raised["path"], raised["body"]), ("/api/proposals/construction", continued_body))
            with self.assertRaises(HubFailure) as wrong_construction_project:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                    "method": "POST", "path": "/api/proposals/construction",
                    "body": {**continued_body, "projectId": "other"},
                })
            self.assertEqual(wrong_construction_project.exception.error.code, "CHAT_PROJECT_MISMATCH")
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
            section_body = {"projectId": session.projectId, "section": {"line": [[0, 2], [6, 2]], "keep": "left"},
                            "modelSource": {"runId": "studio-candidate", "stateDigest": "d" * 64, "assetSha256": "e" * 64}}
            section = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/drawings/section-perspectives", "body": section_body})
            self.assertEqual((section["path"], section["body"]), ("/api/drawings/section-perspectives", section_body))
            with self.assertRaises(HubFailure) as other_project_section:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                    "method": "POST", "path": "/api/drawings/section-perspectives",
                    "body": {**section_body, "projectId": "other"}})
            self.assertEqual(other_project_section.exception.error.code, "CHAT_PROJECT_MISMATCH")
            # Three entourage objects in one typed batch, admitted like any
            # drawing write and forwarded as asked, marked as the Agent's; the
            # reads go straight to the Studio, and a refused batch comes back
            # once, in its own words.
            placing = {"projectId": session.projectId, "sourceStageRef": "stage-base", "drawingId": "room-plan",
                       "previousRevisionRef": "retained-plan-1", "dressingOperations": [
                           {"op": "insert", "id": name, "object": {"id": name, "assetId": asset, "positionUv": [u, 1],
                                                                   "size": 0.6}}
                           for name, asset, u in (("person-a", "person-plan", 1), ("person-b", "person-plan", 2),
                                                  ("tree-a", "tree-plan", 3))]}
            placed = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/drawings/plans", "body": placing})
            self.assertEqual((placed["path"], placed["body"]), ("/api/drawings/plans", {**placing, "sourceKind": "agent"}))
            retained = "runId=studio-drawing-1&assetSha256=" + "b" * 64 + "&revisionRef=retained-plan-2"
            vector = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": "/api/drawings/plans/vector?" + retained})
            self.assertEqual((vector["method"], vector["path"]), ("GET", "/api/drawings/plans/vector?" + retained))
            status_body = {"runId": "studio-drawing-1", "assetSha256": "b" * 64, "revisionRef": "retained-plan-2"}
            status = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/drawings/plans/status", "body": status_body})
            self.assertEqual(status["body"], status_body)
            with self.assertRaises(HubFailure) as admitted_read:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                    "method": "POST", "path": "/api/drawings/plans/status", "body": status_body,
                    "operationId": str(uuid4())})
            self.assertEqual(admitted_read.exception.error.code, "CHAT_TOOL_INVALID")
            choices = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "GET", "path": "/api/drawings/plans/dimensions?sourceRunId=studio-candidate&stateDigest="
                + "d" * 64 + "&assetSha256=" + "e" * 64})
            self.assertTrue(choices["path"].startswith("/api/drawings/plans/dimensions?"))
            refused_batch = {**placing, "previousRevisionRef": "retained-plan-2", "dressingOperations": [
                {"op": "move", "id": "person-a", "positionUv": [2, 2]}, {"op": "delete", "id": "missing"}]}
            with self.assertRaises(HubFailure) as missing:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                    "method": "POST", "path": "/api/drawings/plans", "body": refused_batch})
            self.assertEqual((missing.exception.status, missing.exception.error.code), (422, "DRAWING_DRESSING_MISSING"))
            self.assertEqual(plans, [{**placing, "sourceKind": "agent"}, {**refused_batch, "sourceKind": "agent"}],
                             "each batch is sent once, and a refusal is not retried")
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
            self.assertEqual(schema["summary"], "list drawings")
            # The visual review route is reached only through its own tool, which
            # holds the allowance; a request or schema path would go around it.
            for method, path in (("POST", "/api/documents"), ("GET", "/api/documents/asset-1/bytes"),
                                 ("POST", "/api/drawings/plans/dimension-proposal"), ("POST", "/api/visual-reviews")):
                for tool in ("studio_request", "studio_schema"):
                    with self.subTest(tool=tool, path=path), self.assertRaises(HubFailure) as refused:
                        chat.call_tool(self.store.hub_url, session.id, tool, {"method": method, "path": path})
                    self.assertEqual(refused.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
            # A documented template can be read as a schema, which is what an
            # exploring turn used to fail on.
            for template in ("/api/options/{option_id}/select", "/api/proposals/construction", "/api/project/modeling",
                             "/api/proposals/facets"):
                answer = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                        {"method": "POST", "path": template})
                self.assertEqual(answer["method"], "POST")
                self.assertIn("summary", answer)
                self.assertNotIn("responses", json.dumps(answer), "request inputs only")
            # Reading a schema still cannot reach what calling cannot reach.
            with self.assertRaises(HubFailure):
                chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                               {"method": "POST", "path": "/api/issue"})

    def test_the_agents_cut_plans_always_say_the_agent_asked(self):
        """A plan the Agent asks for is its reading of the user, never the architect's own edit (05 §5)."""

        session = self.create()
        session.status = "running"

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path != "/api/drawings/plans/status":
                path = self._studio_tool_path(base, path, method, headers, session)
            return {"path": path, "body": body}

        plan = {"projectId": session.projectId, "sourceStageRef": "stage-base", "drawingId": "room-plan",
                "previousRevisionRef": "retained-plan-1", "hatchSpacingMm": 3}
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=request):
            for asked, sent in (
                (plan, {**plan, "sourceKind": "agent"}),
                ({**plan, "sourceKind": None}, {**plan, "sourceKind": "agent"}),
                ({**plan, "source_kind": None}, {**plan, "sourceKind": "agent"}),
                ({**plan, "sourceKind": "agent"}, {**plan, "sourceKind": "agent"}),
            ):
                with self.subTest(asked=asked):
                    answer = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                            {"method": "POST", "path": "/api/drawings/plans", "body": asked})
                    self.assertEqual((answer["path"], answer["body"]), ("/api/drawings/plans", sent))
            # The Agent cannot claim a person asked: suggestions count only a person's own corrections.
            for claimed in ({**plan, "sourceKind": "human"}, {**plan, "source_kind": "human"}):
                with self.subTest(claimed=claimed), self.assertRaises(HubFailure) as refused:
                    chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                   {"method": "POST", "path": "/api/drawings/plans", "body": claimed})
                self.assertEqual((refused.exception.status, refused.exception.error.code), (422, "CHAT_TOOL_INVALID"))
            self.assertNotIn("sourceKind", plan, "the Agent's own arguments are not rewritten")
            # Only a cut plan's request says who asked: its status read and the other drawings forward as asked.
            model = {"runId": "studio-candidate", "stateDigest": "d" * 64, "assetSha256": "e" * 64}
            for path, body in (
                ("/api/drawings/plans/status", {"runId": "studio-drawing-1", "assetSha256": "b" * 64,
                                                "revisionRef": "retained-plan-2"}),
                ("/api/drawings/section-perspectives", {"projectId": session.projectId, "modelSource": model,
                                                        "section": {"line": [[0, 2], [6, 2]], "keep": "left"}}),
                ("/api/drawings/elevations", {"projectId": session.projectId, "modelSource": model, "view": "front"}),
            ):
                with self.subTest(path=path):
                    answer = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                            {"method": "POST", "path": path, "body": body})
                    self.assertEqual((answer["path"], answer["body"]), (path, body))

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

    # ---- #419: one construction script makes geometry; producers and meaning stay out of it

    def test_studio_schema_answers_the_proposal_contract_and_takes_no_producer(self):
        """No producer index: the action's own schema, and a producer is an unknown argument."""

        session = self.create()
        document = {
            "paths": {"/api/proposals": {"post": {
                "summary": "Author an edit", "requestBody": {"$ref": "#/components/schemas/ProposalRequestDto"},
                "responses": {"201": {"description": "Created",
                                      "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProposalDto"}}}},
                              "422": {"description": "Refused"}}}},
                      "/api/construction/model": {"get": {
                "summary": "Read the model",
                "responses": {"200": {"description": "The model",
                                      "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ModelDto"}}}}}}}},
            "components": {"schemas": {
                "ProposalRequestDto": {"properties": {"semanticEdit": {"$ref": "#/components/schemas/SemanticEditRequestDto"}}},
                "SemanticEditRequestDto": {"properties": {"parameters": {"type": "array"}}},
                "ProposalDto": {"description": "The complete response"},
                "ModelDto": {"description": "What a read answers"},
            }},
        }
        tools = {tool["name"]: tool for tool in _tools_of(chat)}
        self.assertNotIn("producer", tools["studio_schema"]["inputSchema"]["properties"])
        # Each bound tool is held to exactly the arguments it advertises.
        for name in ("studio_schema", "studio_request", "fab_request"):
            self.assertEqual(set(tools[name]["inputSchema"]["properties"]), chat._TOOL_ARGUMENTS[name], name)
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=lambda *a, **k: json.loads(json.dumps(document))):
            answer = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {
                "method": "POST", "path": "/api/proposals"})
            read = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {
                "method": "GET", "path": "/api/construction/model"})
        self.assertEqual(set(answer), {"path", "method", "summary", "body", "components", "note"})
        # Request inputs only (#404 F7): the answer arrives when the call is sent.
        self.assertEqual(set(answer["components"]["schemas"]), {"ProposalRequestDto", "SemanticEditRequestDto"})
        self.assertNotIn("ProposalDto", json.dumps(answer))
        self.assertNotIn("ModelDto", json.dumps(read))
        # A retired option is refused by name, before any runtime is resolved or
        # any request made, rather than ignored as if it had taken effect.
        with patch.object(chat, "_bound_studio") as studio, patch.object(chat, "_request_json") as request:
            for name, arguments in (
                ("studio_schema", {"method": "POST", "path": "/api/proposals", "producer": "prism"}),
                ("studio_schema", {"producer": "prism"}),
                ("studio_request", {"method": "POST", "path": "/api/proposals", "body": {}, "producer": "prism"}),
                ("fab_request", {"method": "GET", "path": "/api/fab/profiles", "producer": "prism"}),
                ("studio_request", {"method": "GET", "path": "/api/construction/model", "run": "candidate-1"}),
            ):
                with self.subTest(name=name, arguments=arguments), self.assertRaises(HubFailure) as refused:
                    chat.call_tool(self.store.hub_url, session.id, name, arguments)
                self.assertEqual((refused.exception.status, refused.exception.error.code), (422, "CHAT_TOOL_INVALID"))
                unknown = next(key for key in arguments if key not in {"method", "path", "body"})
                self.assertIn(f"{name} has no argument {unknown}", refused.exception.error.detail)
            studio.assert_not_called()
            request.assert_not_called()

    def test_the_agent_contract_names_no_producer_classification_or_backend(self):
        """The layer rule for every tool text, domain guide and prompt note; "wall" appears only as a facet value in MEANING/CAPABILITIES."""

        from monkeyarch.authoring.construction.vocabulary import layer_rule_violations
        from monkeyhub_api.models import ChatPresentationBindRequest

        external = ChatPresentationBindRequest(projectDir=str(self.project), sourceSessionId="external-session")
        for listed in (_tools_of(chat), _tools_of(chat, external)):
            texts, names = [], []

            def collect(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "description" and isinstance(item, str):
                            texts.append(item)
                            continue
                        if key == "properties" and isinstance(item, dict):
                            names.extend(item)
                        collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)

            for tool in listed:
                if tool["name"] != "studio_request":
                    collect(tool)
                    continue
                lines = tool["description"].splitlines()
                start = next(i for i, line in enumerate(lines) if line.startswith("MEANING:"))
                end = next(i for i, line in enumerate(lines) if line.startswith("DOMAINS:"))
                # Stage C: the user has said what a part is, and a facet value may name it.
                self.assertEqual(layer_rule_violations("\n".join(lines[start:end])), ("wall",))
                texts.append("\n".join(lines[:start] + lines[end:]))
                collect(tool["inputSchema"])
            self.assertGreater(len(texts), len(listed))
            for text in texts:
                self.assertEqual(layer_rule_violations(text), (), text[:160])
            for name in names:
                self.assertEqual(layer_rule_violations(name), (), name)
        # What studio_schema answers beside a domain's actions, and the notes a turn's prompt carries.
        read = {**{f"guide {prefix}": text for prefix, text in chat._GUIDES.items()},
                "context note": chat._CONTEXT_NOTE, "memory note": chat._MEMORY_NOTE, "render note": chat._RENDER_NOTE}
        self.assertIn("guide /api/render", read)
        for name, text in read.items():
            self.assertEqual(layer_rule_violations(text), (), name)

    def test_the_inline_script_and_verbs_are_the_interpreters_own(self):
        """The guide's example runs as written and names the ids it promises; its verbs exist."""

        from archflow.project.refs import ProjectVersionRef
        from archflow.state.state_record import Entity, StateRecord
        from monkeyarch.authoring.construction.lowering import compile_construction_script
        from monkeyarch.authoring.construction.vocabulary import vocabulary

        modelling = next(tool for tool in _tools_of(chat) if tool["name"] == "studio_request")["description"]
        lines = modelling.splitlines()
        first = next(i for i, line in enumerate(lines) if line.startswith("The script is a small Python-like program")) + 1
        example = []
        for line in lines[first:]:
            if not line.startswith("  "):
                break
            example.append(line[2:])
        # What POST /api/project/modeling seeds: a modelling root and a ground level at zero.
        evidence = "input:monkeyarch-modeling-setup"
        record = StateRecord(
            project_id="guide", run_id="authored",
            entities=(Entity("model", "Component@1", {"intent": "Root for candidate modeling", "source_refs": [evidence]}),
                      Entity("ground", "Level@1", {"role": "ground", "elevation": 0.0}, basis_refs=(evidence,))),
            evidence_refs=(evidence,), option={"option_id": "modeling"}, base=ProjectVersionRef("guide", 0, "0" * 64))
        result = compile_construction_script("\n".join(example), record, root_component_id="model")
        made = sorted(row["entity_id"] for row in result.entities if row["schema"] == "Component@1")
        # "w in a loop gives w-1..w-4": the ids the guide promises are the ones the script makes.
        self.assertEqual(made, ["mass", "upper", "w-1", "w-2", "w-3", "w-4"])
        at = next(i for i, line in enumerate(lines) if line.startswith("Verbs:"))
        listed = " ".join(lines[at:at + 2]).removeprefix("Verbs:").strip().rstrip(".").replace("|", " ").split()
        contract = vocabulary()
        known = {verb["name"] for verb in contract["verbs"]} | set(contract["language"]["builtins"])
        self.assertEqual([name for name in listed if name not in known], [])
        self.assertEqual(len(listed), len(set(listed)))
        for stated in ("GET /api/construction lists every verb and argument",
                       "POST /api/proposals/construction {stateDigest, script",
                       "CURRENT MODEL: GET /api/construction/model gives stateDigest",
                       "Without ?run=<candidateId> it reads the default",
                       "POST /api/proposals/facets", "POST /api/proposals/hosted-opening",
                       "GET /api/domains/{structure|envelope}/readiness", "semanticEdit never carries geometry"):
            self.assertIn(stated, modelling)

    def test_semantic_edit_carries_no_geometry_or_meaning(self):
        """Element/Type rows point to the construction route, meaning to facets; nothing is sent (#419)."""

        session = self.create()
        session.status = "running"
        geometry = [
            {"entity_id": "block-1-body", "schema": "Element@1", "parent_id": None,
             "fields": {"component_id": "block-1", "producer": "prism", "references": {"base": {"level": "ground"}},
                        "params": {"profile": [[0, 0], [4, 0], [4, 3], [0, 3]], "height": 3}}},
            {"entity_id": "frame-type", "schema": "Type@1",
             "fields": {"producer": "prism", "references": {}, "params": {"height": 2}}},
            # An upsert may leave out schema; it still rewrites that element's geometry.
            {"entity_id": "block-1-body", "fields": {"params": {"height": 4}}},
            {"entity_id": "block-1-body", "fields": {"references": {"base": {"datum": "mass-top"}}}},
        ]
        meaning = [
            {"entity_id": "block-1", "schema": "Component@1", "fields": {"intent": "a block", "semantic_kind": "slab"}},
            {"entity_id": "block-1", "fields": {"semanticKind": "slab"}},
            {"entity_id": "block-1", "schema": "Component@1", "fields": {"facets": {"architectural.role": "slab"}}},
            {"entity_id": "block-1", "schema": "Component@1", "fields": {"roles": ["role.load_bearing"]}},
        ]
        source = {"stateDigest": "a" * 64, "sourceRunId": "candidate-a"}
        with patch.object(chat, "_bound_studio") as studio, patch.object(chat, "_request_json") as request:
            for rows, said in ((geometry, "Geometry is authored with POST /api/proposals/construction; semanticEdit "
                                          "carries parameters, relations, readings and component intents."),
                               (meaning, "Meaning is added with POST /api/proposals/facets.")):
                for row in rows:
                    for key in ("semanticEdit", "semantic_edit"):
                        body = {**source, key: {"summary": "an edit", "entities": [
                            {"entity_id": "block-1", "schema": "Component@1", "fields": {"intent": "a block"}}, row]}}
                        with self.subTest(row=row, key=key), self.assertRaises(HubFailure) as refused:
                            chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                           {"method": "POST", "path": "/api/proposals", "body": body})
                        self.assertEqual((refused.exception.status, refused.exception.error.code, refused.exception.error.detail),
                                         (422, "CHAT_TOOL_INVALID", said))
            studio.assert_not_called()
            request.assert_not_called()

        sent = []

        def forward(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            sent.append((method, path, body))
            return {"proposalId": "proposal-1", "status": "proposed"}

        accepted = [
            {"summary": "Storey height", "parameters": [{"key": "storey", "value": 3.2, "unit": "m"}]},
            {"summary": "Say what the block is for and what was assumed", "entities": [
                {"entity_id": "block-1", "schema": "Component@1", "fields": {"intent": "the first study block"}},
                {"entity_id": "reading-1", "schema": "Reading@1",
                 "fields": {"note": "The site edge is assumed straight.", "subject_refs": ["entity:block-1"]}}]},
        ]
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=forward):
            for edit in accepted:
                with self.subTest(edit=edit["summary"]):
                    answer = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                        "method": "POST", "path": "/api/proposals", "body": {**source, "semanticEdit": edit}})
                    self.assertEqual(answer["proposalId"], "proposal-1")
        self.assertEqual(sent, [("POST", "/api/proposals", {**source, "semanticEdit": edit, "projectId": session.projectId})
                                for edit in accepted])

    def test_the_construction_routes_replace_the_producer_routes(self):
        """The allow-lists take the construction contract and refuse the routes it replaced, naming the new one."""

        added = [("GET", "/api/construction"), ("GET", "/api/construction/model"),
                 ("GET", "/api/construction/model?run=candidate-1"),
                 ("GET", "/api/domains/structure/readiness"), ("GET", "/api/domains/envelope/readiness?run=candidate-1"),
                 ("POST", "/api/proposals/construction"), ("POST", "/api/proposals/facets"),
                 ("POST", "/api/proposals/hosted-opening")]
        kept = [("GET", "/api/state/frame"), ("GET", "/api/state/volumes?run=candidate-1"), ("POST", "/api/state/closure"),
                ("POST", "/api/proposals"), ("POST", "/api/proposals/proposal-1/candidate"), ("GET", "/api/semantics")]
        replaced = {("GET", "/api/state"): "GET /api/construction/model",
                    ("GET", "/api/state?run=candidate-1"): "GET /api/construction/model",
                    **{("POST", f"/api/proposals/{name}"): "POST /api/proposals/construction"
                       for name in ("sketch", "transform", "push-pull", "delete", "elevation")}}
        patterns = {"GET": chat._READ, "POST": chat._POST}
        for method, path in added + kept:
            self.assertTrue(patterns[method].fullmatch(urlsplit(path).path), (method, path))
        for method, path in replaced:
            self.assertIsNone(patterns[method].fullmatch(urlsplit(path).path), (method, path))
        self.assertIsNone(chat._READ.fullmatch("/api/domains/Structure/readiness"))

        session = self.create()
        session.status = "running"
        # A replaced route is refused with the route that replaced it, before
        # any runtime is resolved: nothing is started or sent to explain it.
        with patch.object(chat, "_bound_studio") as studio, patch.object(chat, "_request_json") as request:
            for (method, path), replacement in replaced.items():
                for tool in ("studio_request", "studio_schema"):
                    with self.subTest(tool=tool, method=method, path=path), self.assertRaises(HubFailure) as refused:
                        chat.call_tool(self.store.hub_url, session.id, tool, {"method": method, "path": path, "body": {}})
                    self.assertEqual((refused.exception.status, refused.exception.error.code), (422, "CHAT_TOOL_UNAVAILABLE"))
                    self.assertIn(f"use {replacement}", refused.exception.error.detail)
                    self.assertIn("Nothing was executed", refused.exception.error.detail)
            studio.assert_not_called()
            request.assert_not_called()

        sent = []
        document = {"paths": {
            "/api/construction": {"get": {"summary": "The construction vocabulary"}},
            "/api/construction/model": {"get": {"summary": "The model in construction terms"}},
            "/api/domains/{domain}/readiness": {"get": {"summary": "What a domain reads"}},
            "/api/proposals/construction": {"post": {"summary": "Run a construction script"}},
            "/api/proposals/facets": {"post": {"summary": "Add meaning"}},
            "/api/proposals/hosted-opening": {"post": {"summary": "Host a door or window"}},
            # The web client's own drawing routes stay in the Runtime.
            "/api/state": {"get": {"summary": "The state record"}},
            "/api/proposals/sketch": {"post": {"summary": "Draw"}},
            "/api/proposals/push-pull": {"post": {"summary": "Push-pull"}},
        }, "components": {"schemas": {}}}

        def forward(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == "/openapi.json":
                return json.loads(json.dumps(document))
            sent.append((method, path, body))
            return {"method": method, "path": path, "body": body}

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=forward):
            for method, path in added:
                body = {"stateDigest": "a" * 64} if method == "POST" else None
                with self.subTest(method=method, path=path):
                    answer = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                        "method": method, "path": path, **({"body": body} if body else {})})
                    self.assertEqual((answer["method"], answer["path"], answer["body"]), (method, path, body))
            self.assertEqual(sent, [(method, path, {"stateDigest": "a" * 64} if method == "POST" else None)
                                    for method, path in added])
            listing = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {})
            self.assertEqual({(row["method"], row["path"]) for row in listing["actions"]},
                             {("GET", "/api/construction"), ("GET", "/api/construction/model"),
                              ("GET", "/api/domains/{domain}/readiness"), ("POST", "/api/proposals/construction"),
                              ("POST", "/api/proposals/facets"), ("POST", "/api/proposals/hosted-opening")})
            for method, template in (("GET", "/api/domains/{domain}/readiness"), ("POST", "/api/proposals/construction")):
                schema = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                        {"method": method, "path": template})
                self.assertEqual((schema["method"], schema["path"]), (method, template))

    def test_the_domain_index_is_a_chat_read_beside_each_domains_readiness(self):
        """#419: an agent learns which domains there are (GET /api/domains) as well as what one reads."""

        for path in ("/api/domains", "/api/domains/structure/readiness", "/api/domains/envelope/readiness"):
            self.assertIsNotNone(chat._READ.fullmatch(path), path)
        for path in ("/api/domains/", "/api/domains/structure", "/api/domains/structure/readiness/more"):
            self.assertIsNone(chat._READ.fullmatch(path), path)

        session = self.create()
        session.status = "running"
        sent = []
        document = {"paths": {
            "/api/domains": {"get": {"summary": "Every known domain"}},
            "/api/domains/{domain}/readiness": {"get": {"summary": "What a domain reads"}},
        }, "components": {"schemas": {}}}

        def forward(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == "/openapi.json":
                return json.loads(json.dumps(document))
            sent.append((method, path))
            return {"domains": [{"id": "structure"}, {"id": "envelope"}]}

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=forward):
            answer = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                    {"method": "GET", "path": "/api/domains"})
            listing = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"pathPrefix": "/api/domains"})
        self.assertEqual(answer, {"domains": [{"id": "structure"}, {"id": "envelope"}]})
        self.assertEqual(sent, [("GET", "/api/domains")])
        self.assertEqual({(row["method"], row["path"]) for row in listing["actions"]},
                         {("GET", "/api/domains"), ("GET", "/api/domains/{domain}/readiness")})

    def test_studio_schema_finds_literal_routes_before_templated_ones(self):
        """A templated route listed earlier in the OpenAPI document must not shadow a literal
        route that also matches it (#419): /api/proposals/{proposal_id} (GET only) must not hide
        the POST-only /api/proposals/construction and /api/proposals/facets literal routes."""

        session = self.create()
        document = {"paths": {
            "/api/proposals/{proposal_id}": {"get": {"summary": "Read a proposal"}},
            "/api/proposals/construction": {"post": {"summary": "Run a construction script"}},
            "/api/proposals/facets": {"post": {"summary": "Add meaning"}},
        }, "components": {"schemas": {}}}
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=lambda *a, **k: json.loads(json.dumps(document))):
            for path in ("/api/proposals/construction", "/api/proposals/facets"):
                with self.subTest(path=path):
                    answer = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                            {"method": "POST", "path": path})
                    self.assertEqual((answer["path"], answer["summary"]), (path, document["paths"][path]["post"]["summary"]))
            # A concrete id still resolves through the templated route: nothing
            # about preferring literal matches may break normal template lookup.
            schema = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                    {"method": "GET", "path": "/api/proposals/abc123"})
            self.assertEqual((schema["path"], schema["summary"]), ("/api/proposals/{proposal_id}", "Read a proposal"))

    def test_studio_schema_reads_back_every_action_its_discovery_lists(self):
        """Against the Studio's real OpenAPI, each action discovery lists reads back as that action (#419).

        The Studio lists GET /api/proposals/{proposal_id} before the POST-only construction,
        facets and hosted-opening routes that template also matches; a route registered later
        in the same position is covered here without being named.
        """

        from project_runtime.main import create_app as studio_app
        from project_runtime.settings import StudioSettings

        project = self.root / "discovery-round-trip"
        FilesystemProjectRepository.initialize(project, project_id="discovery-round-trip",
                                               initial_state={"project_id": "discovery-round-trip", "version": 0})
        with TestClient(studio_app(StudioSettings(project_dir=project, cad_export="off"))) as client:
            document = client.get("/openapi.json").json()
        session = self.create()
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=lambda *a, **k: json.loads(json.dumps(document))):
            listed, arguments = [], {"limit": 50}
            while arguments:
                page = chat.call_tool(self.store.hub_url, session.id, "studio_schema", arguments)
                listed += [(row["method"], row["path"]) for row in page["actions"]]
                arguments = page.get("next", {}).get("arguments")
            self.assertEqual(len(listed), page["total"])
            self.assertLessEqual({("POST", "/api/proposals/construction"), ("POST", "/api/proposals/facets"),
                                  ("POST", "/api/proposals/hosted-opening")}, set(listed))
            for method, path in listed:
                with self.subTest(method=method, path=path):
                    answer = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                            {"method": method, "path": path})
                    self.assertEqual((answer["path"], answer["summary"]),
                                     (path, document["paths"][path][method.lower()]["summary"]))

    def test_construction_proposals_answer_without_the_rows_they_generated(self):
        """Like a semantic edit, a script's proposal comes back without its edits and operator; its report stays."""

        session = self.create()
        session.status = "running"
        report = {"report": [{"id": "mass", "form": "solid", "status": "created"}], "log": ["done"]}

        def answer(base, path, method="GET", body=None, timeout=None, *, headers=None):
            self._studio_tool_path(base, path, method, headers, session)
            return {"proposalId": "proposal-1", "status": "proposed", "decisionOperator": {"entities": ["rows"]},
                    "change": {"kind": "edit_components", "changes": [{"entityId": "entity:mass", "action": "create"}],
                               "edits": {"entities": ["generated rows"]}}, "construction": report}

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=answer):
            for path, body in (("/api/proposals/construction", {"stateDigest": "a" * 64, "script": "mass = extrude(rect(0, 0, 4, 3), 3)"}),
                               ("/api/proposals/facets", {"stateDigest": "a" * 64, "targets": [{"id": "mass", "set": {"material.name": "brick"}}]}),
                               ("/api/proposals/hosted-opening", {"stateDigest": "a" * 64, "host": "mass", "kind": "door"})):
                with self.subTest(path=path):
                    result = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                            {"method": "POST", "path": path, "body": body})
                    self.assertNotIn("decisionOperator", result)
                    self.assertEqual(result["change"], {"kind": "edit_components",
                                                        "changes": [{"entityId": "entity:mass", "action": "create"}]})
                    self.assertEqual(result["construction"], report)

    def test_the_agent_guide_does_not_grow(self):
        """#404 F7 moved every domain but modeling behind one line each: the CLI
        loads this description before every turn, and it was 15,972 characters.
        #419 states the construction script there instead of a producer index.
        """

        tools = {tool["name"]: tool for tool in _tools_of(chat)}
        modelling = tools["studio_request"]["description"]
        self.assertLessEqual(len(modelling.splitlines()), 106)
        self.assertLessEqual(len(modelling), 10000)
        self.assertLessEqual(len(json.dumps(list(tools.values()), ensure_ascii=False)), 28000)
        for prefix in chat._GUIDES:
            self.assertIn(f"pathPrefix {prefix}", modelling, "each domain is named with where its text is")
            self.assertNotIn(chat._GUIDES[prefix].splitlines()[-1], modelling)

    def test_discovery_answers_a_domain_guide_and_refuses_bad_arguments_before_preparing(self):
        """#404 F7: the domain text moved out of the guide; item 5: a refusal starts no runtime."""

        session = self.create()
        session.status = "running"
        document = {"paths": {"/api/drawings/sheets": {"post": {"summary": "Make a sheet"}},
                              "/api/documents": {"get": {"summary": "Read documents"}},
                              "/api/exports": {"post": {"summary": "Export"}},
                              "/api/state": {"get": {"summary": "Read state"}}}, "components": {"schemas": {}}}
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())) as bound, \
                patch.object(chat, "_request_json", side_effect=lambda *a, **k: json.loads(json.dumps(document))):
            for prefix, words in (("/api/drawings", "SECTION PERSPECTIVE"), ("/api/documents", "PUT /api/document-annotations"),
                                  ("/api/intents/", "RETAINED FEEDBACK"), ("/api/exports", "MODEL CONVERSION")):
                answer = chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"pathPrefix": prefix})
                self.assertIn(words, answer["guide"], prefix)
            self.assertNotIn("guide", chat.call_tool(self.store.hub_url, session.id, "studio_schema", {"pathPrefix": "/api/state"}))
            self.assertNotIn("guide", chat.call_tool(self.store.hub_url, session.id, "studio_schema", {}))
            bound.reset_mock()
            for arguments in ({"limit": 500}, {"producer": "wall"}, {"body": {}}, {"pathPrefix": "drawings"},
                              {"offset": -1}, {"method": "DELETE"}):
                with self.subTest(arguments=arguments), self.assertRaises(HubFailure) as refused:
                    chat.call_tool(self.store.hub_url, session.id, "studio_schema", arguments)
                self.assertEqual(refused.exception.error.code, "CHAT_TOOL_INVALID")
            bound.assert_not_called()

    def test_schema_answers_are_request_inputs_with_repeats_named_once(self):
        """#404 F7: a schema answer was 9-35k characters of titles, responses and repeated subtrees."""

        from project_runtime.main import create_app as studio_app
        from project_runtime.settings import StudioSettings

        project = self.root / "compact-schema"
        FilesystemProjectRepository.initialize(project, project_id="compact-schema",
                                               initial_state={"project_id": "compact-schema", "version": 0})
        with TestClient(studio_app(StudioSettings(project_dir=project, cad_export="off"))) as client:
            document = client.get("/openapi.json").json()
        session = self.create()

        def expand(value, schemas):
            if isinstance(value, dict):
                reference = value.get("$ref", "")
                if reference.startswith("#/components/schemas/Shared"):
                    return expand(schemas[reference.rsplit("/", 1)[-1]], schemas)
                return {key: expand(item, schemas) for key, item in value.items()}
            if isinstance(value, list):
                return [expand(item, schemas) for item in value]
            return value

        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", session.model_dump())), \
                patch.object(chat, "_request_json", side_effect=lambda *a, **k: json.loads(json.dumps(document))):
            answers = {}
            for label, arguments, most in (("construction", {"method": "POST", "path": "/api/proposals/construction"}, 6000),
                                           ("admissions", {"method": "POST", "path": "/api/admissions"}, 4000),
                                           ("proposals", {"method": "POST", "path": "/api/proposals"}, 12000)):
                answer = answers[label] = chat.call_tool(self.store.hub_url, session.id, "studio_schema", arguments)
                text = json.dumps(answer, ensure_ascii=False)
                self.assertLessEqual(len(text), most, label)
                self.assertNotIn('"responses"', text)
                self.assertNotIn('"title"', text)
                self.assertNotIn("x-monkey", text, "headers are the adapter's to send")
                self.assertTrue(answer["note"].startswith("Request inputs only"))
            # Naming a repeat loses nothing: expanded, the proposal answer is the
            # compacted contract, and its references stay where they were.
            schemas = answers["proposals"]["components"]["schemas"]
            edit = expand(schemas["SemanticEditRequestDto"], schemas)
            self.assertEqual(edit, expand(chat._compact(document["components"]["schemas"]["SemanticEditRequestDto"]), schemas))
            # A nullable field reads as its type, marked, and a property named
            # like a keyword is still a property.
            script = answers["construction"]["components"]["schemas"]["ConstructionRequestDto"]["properties"]
            self.assertEqual((script["summary"]["type"], script["summary"]["nullable"]), ("string", True))
            self.assertEqual(chat._compact({"properties": {"title": {"type": "string", "title": "Title"}}}),
                             {"properties": {"title": {"type": "string"}}})

    def test_tool_rows_name_what_a_schema_or_discovery_answer_read(self):
        """#404 comment item 1: a schema answer showed as a 320-character JSON preview."""

        contract = {"path": "/api/proposals/construction", "method": "POST", "summary": "Create", "body": {"$ref": "x" * 400},
                    "note": "Request inputs only; each $ref names an entry of components.schemas."}
        self.assertEqual(chat._tool_values(json.dumps(contract)),
                         (["read the schema of POST /api/proposals/construction"], None))
        listing = {"actions": [], "total": 12, "offset": 0, "limit": 30, "guide": "DRAWINGS", "note": "..."}
        self.assertEqual(chat._tool_values(json.dumps(listing)), (["listed 12 actions with their guide"], None))
        # A request answer that happens to carry a path and a method is not a schema read.
        self.assertEqual(chat._tool_values(json.dumps({"path": "/a", "method": "GET", "status": "ok"}))[0], ["status: ok"])
        row, _, _ = chat._tool_activity({"tool": "studio_schema", "server": "monkeyhub", "status": "completed",
                                         "arguments": {"method": "POST", "path": "/api/proposals/construction"},
                                         "result": {"content": [{"type": "text", "text": json.dumps(contract)}]}})
        self.assertEqual(row.splitlines()[1], "read the schema of POST /api/proposals/construction")
        self.assertLess(len(row), 200)

    def test_a_tool_call_before_the_first_turn_is_not_told_the_chat_stopped(self):
        """#404 F12: CHAT_NOT_RUNNING said "no longer running" to a chat that had not started."""

        for messages, external, words in (([], False, "has not started a turn yet"),
                                          ([], True, "chat_present kind=user"),
                                          ([{"role": "user", "content": "hi"}], False, "turn has ended")):
            with self.subTest(messages=messages, external=external):
                session = {"status": "idle", "messages": messages, "sourceSessionId": "source" if external else None}
                failure = chat._not_running(session)
                self.assertEqual(failure.error.code, "CHAT_NOT_RUNNING")
                self.assertIn(words, failure.error.detail)
                self.assertNotIn("no longer running", failure.error.detail)
        session = self.create()
        with patch.object(chat, "_request_json", return_value=session.model_dump()), \
                self.assertRaises(HubFailure) as refused:
            chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/construction/model"})
        self.assertEqual(refused.exception.error.code, "CHAT_NOT_RUNNING")
        self.assertIn("has not started a turn yet", refused.exception.error.detail)

    def test_existing_controls_and_candidate_continuation_are_discoverable(self):
        """One short pointer, and the bound path behind it — not a second hand-written contract."""

        session = self.create()
        session.status = "running"
        description = next(tool for tool in _tools_of(chat) if tool["name"] == "studio_request")["description"]
        # A geometry id is the component the capability targets; its element is
        # the runtime's, so naming one is optional (#419).
        for stated in ("GET /api/capabilities/candidate.modify_existing?target=<id>&run=<candidateId>",
                       "(elementId is optional)", "GET /api/capabilities?goal=",
                       "POST /api/capabilities/{capabilityId}/run", "awaitSeconds: 60",
                       "keep is a list", "never send the request again",
                       "GET /api/construction/model?run=<candidateId>", "original baseStateDigest",
                       "Multiple observation and revision cycles"):
            self.assertIn(stated, description, stated)
        for restriction in ("Never generate an intermediate", "READ ONCE", "do not read the index",
                            "yield_time_ms", "functions.wait", "&elementId=<the element>"):
            self.assertNotIn(restriction, description)
        # The construction route and exact-source comparison remain discoverable.
        self.assertIn("/api/proposals/construction", description)
        self.assertIn("compare?against=<runId>", description)

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            path = self._studio_tool_path(base, path, method, headers, session)
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/settings/apps":
                return {"projectDir": str(self.project)}
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
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
                           readback_error=None, compare_error=None, barriers=None):
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
                         "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
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
                if compare_error:
                    raise HubFailure(404, "INSPECTION_NOT_FOUND", compare_error)
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
                                    "lengthUnit": "meters", "available": True,
                                    "modelSource": {"runId": "studio-cand-2", "stateDigest": "d" * 64,
                                                    "assetSha256": "e" * 64},
                                    "sourceStageRef": "source-stage"},
                                   {"runId": "studio-cand-2", "fileName": "gone.3dm", "available": False,
                                    "modelSource": None, "sourceStageRef": None,
                                    "unavailableReason": "the file is not on disk"},
                                   {"runId": "studio-cand-2", "fileName": "legacy.3dm", "sha256": "f" * 64}]}
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
        self.assertEqual(saved["seat.3dm"]["modelSource"], candidate["artifacts"][0]["modelSource"],
                         "reuse the exact observation source; do not rebuild it from candidate or artifact hashes")
        self.assertEqual(saved["seat.3dm"]["sourceStageRef"], "source-stage")
        self.assertIsNone(saved["gone.3dm"]["modelSource"])
        self.assertIsNone(saved["gone.3dm"]["sourceStageRef"])
        self.assertNotIn("modelSource", saved["legacy.3dm"])
        self.assertNotIn("sourceStageRef", saved["legacy.3dm"])
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
            if path == "/api/runtime/projects/open":
                # The Hub's own refusal to prepare the project travels as itself.
                raise HubFailure(409, "PROJECT_RUNTIME_REFUSED", "The Hub could not open this project.")
            return request(base, path, method, body, timeout)

        with self.assertRaises(HubFailure) as refused:
            self._run_with_wait(refuse, session)
        self.assertEqual(refused.exception.error.code, "PROJECT_RUNTIME_REFUSED")
        self.assertEqual([row for row in sent if row["method"] == "POST"], [],
                         "nothing may be posted until every binding check has passed")

    def _preparing_hub(self, session, *, worker=None, attached_id=None, starts_after=1, opened_delay=0.0):
        """A stand-in Hub whose project Studio is not running until it is prepared.

        It answers the Hub routes the web client's ensureProject uses, counts
        each open and start, and lets the Studio run only after a start and
        ``starts_after`` status reads (``None``: never).
        """

        state = {"opens": 0, "starts": 0, "reads": 0, "running": False}
        runtime_id = str(uuid5(NAMESPACE_URL, f"{session.projectId}:{os.path.normcase(str(Path(session.projectDir).resolve()))}"))
        guard = threading.Lock()

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if timeout is not None:
                self.assertGreater(timeout, 0)
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            if path == "/api/runtime/projects/open":
                self.assertEqual((base, method), (self.store.hub_url, "POST"))
                self.assertEqual(body, {"projectDir": session.projectDir, "projectId": session.projectId})
                time.sleep(opened_delay)
                with guard:
                    state["opens"] += 1
                return {"runtimeId": runtime_id, "projectId": attached_id or session.projectId,
                        "projectDir": session.projectDir, "workers": []}
            if path == "/api/runtime":
                studio = worker or {"serviceId": "studio", "state": "stopped", "healthy": False, "processId": None}
                return {"serverId": "hub", "sequence": 1, "workers": [], "projects": [{
                    "runtimeId": runtime_id, "projectId": attached_id or session.projectId,
                    "projectDir": session.projectDir, "workers": [studio]}]}
            if path.startswith("/api/apps/monkeyrender/start?"):
                self.assertEqual(parse_qs(urlsplit(path).query)["projectDir"], [session.projectDir])
                with guard:
                    state["starts"] += 1
                return {"appId": "monkeyrender", "state": "starting", "url": None}
            if path.startswith("/api/apps?"):
                self.assertEqual(parse_qs(urlsplit(path).query)["projectDir"], [session.projectDir])
                with guard:
                    if state["starts"]:
                        state["reads"] += 1
                        state["running"] = starts_after is not None and state["reads"] >= starts_after
                    running = state["running"]
                if not running:
                    return [{"appId": "monkeyarch", "state": "stopped", "url": None, "processId": None},
                            {"appId": "monkeyrender", "state": "starting" if state["starts"] else "stopped", "url": None}]
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch",
                         "apiUrl": "http://127.0.0.1:8791/", "processId": 123},
                        {"appId": "monkeyrender", "state": "running", "url": "http://127.0.0.1:8790/?view=render"}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": session.projectId, "projectDir": session.projectDir}
            if path == "/api/construction/model":
                self.assertEqual(base, "http://127.0.0.1:8791")
                return {"stateDigest": "a" * 64}
            raise AssertionError(f"unexpected call: {method} {path}")

        return request, state

    def test_a_design_tool_prepares_the_project_runtime_itself(self):
        """#414: no page has to be opened before an agent's first design call."""

        session = self.create()
        session.status = "running"
        request, state = self._preparing_hub(session, starts_after=2)
        with patch.object(chat, "_request_json", side_effect=request), patch.object(chat, "_PREPARE_POLL_S", 0.01):
            answer = chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/construction/model"})
        self.assertEqual(answer, {"stateDigest": "a" * 64})
        self.assertEqual((state["opens"], state["starts"]), (1, 1))
        description = next(tool for tool in _tools_of(chat) if tool["name"] == "studio_request")["description"]
        self.assertIn("prepare the project's runtime themselves", description)

    def test_a_worker_that_needs_recovery_is_refused_with_its_own_error(self):
        session = self.create()
        session.status = "running"
        crashed = {"serviceId": "studio", "state": "crashed", "healthy": False, "processId": 77,
                   "error": {"code": "WORKER_EXITED", "detail": "The project service exited with code 3."}}
        request, state = self._preparing_hub(session, worker=crashed)
        with patch.object(chat, "_request_json", side_effect=request), self.assertRaises(HubFailure) as refused:
            chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/construction/model"})
        self.assertEqual(refused.exception.error.code, "WORKER_EXITED")
        # The Hub's own words first, then what the agent can do about it: tell the user.
        self.assertTrue(refused.exception.error.detail.startswith("The project service exited with code 3. "))
        self.assertIn("tell the user", refused.exception.error.detail)
        self.assertEqual(state["starts"], 0, "recovery stays an explicit act; nothing is started")

    def test_a_request_the_chat_may_not_make_never_prepares_a_runtime(self):
        session = self.create()
        session.status = "running"
        request, state = self._preparing_hub(session)
        with patch.object(chat, "_request_json", side_effect=request), self.assertRaises(HubFailure) as refused:
            chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "DELETE", "path": "/api/project"})
        self.assertEqual(refused.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
        self.assertEqual(state["starts"], 0, "the allow-list is checked before the Studio is resolved")
        # An allowed method on a path the chat may not use is explained from a
        # Studio that is already running, never by opening one (#405).
        with patch.object(chat, "_request_json", side_effect=request), self.assertRaises(HubFailure) as refused:
            chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/not-exposed"})
        self.assertEqual(refused.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
        self.assertEqual((state["opens"], state["starts"]), (0, 0))

    def test_a_runtime_attached_to_another_project_is_refused(self):
        session = self.create()
        session.status = "running"
        request, state = self._preparing_hub(session, attached_id="other-project")
        with patch.object(chat, "_request_json", side_effect=request), self.assertRaises(HubFailure) as refused:
            chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/construction/model"})
        self.assertEqual(refused.exception.error.code, "CHAT_PROJECT_MISMATCH")
        self.assertEqual(state["starts"], 0)

    def test_a_preparation_that_does_not_finish_in_time_is_refused_clearly(self):
        session = self.create()
        session.status = "running"
        request, state = self._preparing_hub(session, starts_after=None)
        began = time.monotonic()
        with patch.object(chat, "_request_json", side_effect=request), patch.object(chat, "_PREPARE_POLL_S", 0.05), \
                self.assertRaises(HubFailure) as refused:
            chat._bound_studio(self.store.hub_url, session.id, timeout=0.6)
        self.assertLess(time.monotonic() - began, 3)
        self.assertEqual(refused.exception.error.code, "CHAT_STUDIO_UNAVAILABLE")
        self.assertIn("did not become ready in time", refused.exception.error.detail)
        self.assertEqual(state["starts"], 1)

    def test_concurrent_design_calls_prepare_the_project_once(self):
        session = self.create()
        session.status = "running"
        request, state = self._preparing_hub(session, starts_after=2, opened_delay=0.2)
        answers, failures = [], []

        def call():
            try:
                answers.append(chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                              {"method": "GET", "path": "/api/construction/model"}))
            except BaseException as exc:  # noqa: BLE001 - reported below
                failures.append(exc)

        with patch.object(chat, "_request_json", side_effect=request), patch.object(chat, "_PREPARE_POLL_S", 0.01):
            threads = [threading.Thread(target=call) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(failures, [])
        self.assertEqual(len(answers), 2)
        self.assertEqual((state["opens"], state["starts"]), (1, 1))

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

    def test_partial_readback_keeps_each_success_and_only_follows_missing_reads(self):
        session = self.create()
        session.status = "running"
        candidate = {"candidateId": "studio-cand-2", "stateDigest": "b" * 64,
                     "seatExecutionComplete": True,
                     "artifacts": [{"modelSource": {"runId": "studio-cand-2", "stateDigest": "b" * 64,
                                                    "assetSha256": "c" * 64}, "sourceStageRef": None}],
                     "objects": [{"name": "cornice", "bbox": {"min": [0, 0, 0.6], "max": [4, 2, 1.1]}}],
                     "relationChecks": {"held": 1, "unchecked": 2},
                     "honesty": ["two relations were not checked"]}
        compare = {"against": "studio-cand-1", "changed": 1, "unchanged": 1, "objects": []}
        for missing in (("compare",), ("candidate",), ("candidate", "compare")):
            with self.subTest(missing=missing):
                request, sent = self._finishing_service(
                    session, job_states=[{"jobId": "job-1", "status": "succeeded"}],
                    candidate=candidate, compare=compare,
                    readback_error="candidate unavailable" if "candidate" in missing else None,
                    compare_error="source inspection missing" if "compare" in missing else None)
                answer = self._run_with_wait(request, session)
                self.assertEqual(answer["status"], "succeeded")
                self.assertEqual(answer["readback"], "failed", "partial evidence is never full verification")
                self.assertEqual(set(answer["readbackErrors"]), set(missing))
                expected_next = []
                if "candidate" in missing:
                    expected_next.append("GET /api/candidates/studio-cand-2")
                    for key in ("candidate", "objects", "artifacts", "objectReadbackError"):
                        self.assertNotIn(key, answer)
                else:
                    self.assertEqual(answer["objects"], candidate["objects"])
                    self.assertEqual(answer["artifacts"], candidate["artifacts"],
                                     "a missing comparison must not force a second candidate read for its observation source")
                    self.assertEqual(answer["candidate"]["relationChecks"], candidate["relationChecks"])
                    self.assertEqual(answer["candidate"]["honesty"], candidate["honesty"])
                if "compare" in missing:
                    expected_next.append("GET /api/candidates/studio-cand-2/compare?against=studio-cand-1")
                    self.assertNotIn("compare", answer)
                else:
                    self.assertEqual(answer["compare"]["against"], "studio-cand-1")
                    self.assertEqual(answer["compare"]["unchanged"], 1)
                self.assertEqual(answer["next"], expected_next)
                self.assertEqual(len([row for row in sent if row["method"] == "POST"]), 1)
                self.assertIsNone(chat._tool_values(json.dumps(answer))[1],
                                  "partial evidence must not become a fully read-back candidate card")

    def test_readback_deadline_preserves_completed_sibling_without_waiting_for_stalled_read(self):
        release = threading.Event()
        candidate = {"candidateId": "candidate", "objects": [{"name": "cornice"}], "artifacts": []}

        def request(base, path, **kwargs):
            if path == "/api/jobs/job":
                return {"status": "succeeded"}
            if path == "/api/candidates/candidate":
                return candidate
            release.wait(5)
            return {"changed": 1}

        try:
            with patch.object(chat, "_request_json", side_effect=request):
                answer = chat._finish("http://127.0.0.1:8791", {"jobId": "job", "candidateId": "candidate"},
                                      {"sourceRunId": "before"}, time.monotonic() + 0.2)
            self.assertFalse(release.is_set())
            self.assertEqual(answer["objects"], candidate["objects"])
            self.assertEqual(answer["readback"], "failed")
            self.assertIn("compare", answer["readbackErrors"])
            self.assertEqual(answer["next"], ["GET /api/candidates/candidate/compare?against=before"])
        finally:
            release.set()

    def test_partial_readback_does_not_swallow_unexpected_errors(self):
        def request(base, path, **kwargs):
            if path.startswith("/api/jobs/"):
                return {"status": "succeeded"}
            raise AssertionError("unexpected readback defect")

        with patch.object(chat, "_request_json", side_effect=request), self.assertRaisesRegex(AssertionError, "defect"):
            chat._finish("http://127.0.0.1:8791", {"jobId": "job", "candidateId": "candidate"},
                         {"sourceRunId": "before"}, time.monotonic() + 1)

    def test_benchmark_can_inspect_one_admitted_candidate_without_a_complete_readback_card(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("turn_benchmark", ROOT / "tools/benchmarks/run_turn_benchmark.py")
        benchmark = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(benchmark)
        detail = {"id": "this-chat", "messages": [{"role": "tool", "candidateId": None}]}
        operation = {"sessionId": "this-chat", "candidateId": "candidate-1", "jobId": "job-1"}
        runtime = {"operations": [operation, {"sessionId": "another-chat", "candidateId": "other", "jobId": "other-job"},
                                  {"sessionId": "this-chat", "candidateId": "refused", "status": "failed"}]}
        self.assertEqual(benchmark.candidate_for_readback(detail, runtime), "candidate-1")
        self.assertIsNone(benchmark.candidate_for_readback(detail, {"operations": []}))
        runtime["operations"].append({**operation, "candidateId": "candidate-2"})
        self.assertIsNone(benchmark.candidate_for_readback(detail, runtime), "an ambiguous run must not be guessed")

    def test_the_wait_belongs_to_the_one_action_that_can_be_seen_through(self):
        session = self.create()
        session.status = "running"
        request, sent = self._finishing_service(session, job_states=[{"jobId": "job-1", "status": "succeeded"}])
        with patch.object(chat, "_request_json", side_effect=request):
            for arguments in (
                {"method": "GET", "path": "/api/construction/model", "awaitSeconds": 30},
                {"method": "POST", "path": "/api/proposals/construction", "awaitSeconds": 30},
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
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            if path == "/openapi.json":
                return {"paths": {"/api/state/closure": {"post": {"summary": "Read a declared closure"}}},
                        "components": {"schemas": {}}}
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
            self.assertEqual(wrong_method.exception.error.code, "CHAT_ACTION_UNKNOWN")
            index_path = "/api/model-assets/" + "a" * 64 + "/index?runId=source&stateDigest=" + "b" * 64 + "&limit=20"
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": index_path})
            self.assertEqual(result["path"], index_path)
            self.assertEqual(result["method"], "GET")
            with self.assertRaises(HubFailure):
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "POST", "path": index_path})
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
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/construction/model"})
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
                    return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": address + "/", "processId": pid}]
                self.assertEqual(path, "/api/health", "binding must not depend on global project settings")
                return {"sourceRevision": "revision"}
            _, pid, session = next(row for row in services.values() if row[0] == base)
            if path == "/api/health":
                return {"sourceRevision": "revision", "processId": pid}
            if path == "/api/project":
                bound = second if wrong_binding else session
                return {"projectId": bound.projectId, "projectDir": bound.projectDir}
            self.assertEqual(path, "/api/construction/model")
            return {"projectId": session.projectId}

        with patch.object(chat, "_request_json", side_effect=request):
            for session in (first, second):
                result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": "/api/construction/model"})
                self.assertEqual(result["projectId"], session.projectId)
            wrong_binding = True
            before = sum(path == "/api/construction/model" for _, path, _ in calls)
            with self.assertRaises(HubFailure) as refused:
                chat.call_tool(self.store.hub_url, first.id, "studio_request", {"method": "GET", "path": "/api/construction/model"})
            self.assertEqual(refused.exception.error.code, "CHAT_PROJECT_MISMATCH")
            self.assertEqual(sum(path == "/api/construction/model" for _, path, _ in calls), before)
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
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": studio_pid, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": "chat-project", "projectDir": str(self.project)}
            self.assertEqual((base, method, path), ("http://127.0.0.1:8791", "POST", "/api/project/modeling"))
            sent.append(body)
            return {"projectId": "chat-project", "initialized": True}

        app = create_app(HubSettings(self.runtime))
        ready = AppStatus(appId="monkeyarch", title="MonkeyArch", serviceId="studio", state="running", url="http://127.0.0.1:8790/?view=arch", apiUrl="http://127.0.0.1:8791/", processId=123)
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

    def test_the_chat_list_never_waits_for_the_project_list_and_an_unchanged_project_is_not_read_again(self):
        # #449: the Hub asks for both together on every refresh.
        session = self.create()
        self.store.projects()
        with patch.object(chat, "_position", wraps=chat._position) as positions:
            self.store.projects()
            positions.assert_not_called()
            head = self.project / "HEAD"
            stamp = head.stat()
            os.utime(head, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
            self.assertEqual(next(row for row in self.store.projects() if row.projectId == session.projectId).version, 0)
            self.assertEqual(positions.call_count, 1, "a moved HEAD is read again")
        reading, release = threading.Event(), threading.Event()

        def slow_position(root):
            reading.set()
            release.wait(10)
            return 0, None

        head_stamp = head.stat()
        os.utime(head, ns=(head_stamp.st_atime_ns, head_stamp.st_mtime_ns + 1_000_000_000))
        with patch.object(chat, "_position", side_effect=slow_position):
            listing = threading.Thread(target=self.store.projects)
            listing.start()
            self.assertTrue(reading.wait(10))
            try:
                listed = []
                answer = threading.Thread(target=lambda: listed.append(self.store.list()))
                answer.start()
                answer.join(5)
                self.assertEqual([row.id for row in listed[0]], [session.id], "the chat list answered while a project was read")
            finally:
                release.set()
                listing.join(10)

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
        self.assertEqual({tool["name"] for tool in replies[1]["result"]["tools"]},
                         {"studio_schema", "studio_request", "visual_review", "fab_request", "attachment_read",
                          "chat_present", "computer_inspect", "computer_action", "computer_record"})

    # ---- a turn that names its own source and focus

    PACK = {"contextPack": "ContextPack@1",
            "source": {"projectId": "chat-project", "runId": "run-001", "stateDigest": "a" * 64,
                       "writeWith": 'sourceRunId="run-001"'},
            "target": {"componentId": "portico", "elementId": "portico-cornice",
                       "editable": [{"field": "height", "value": 0.3, "unit": None}], "notEditable": []},
            "contextTier": "design", "escalation": ["architectural_or_extended_scope"],
            "context": {"elements": []}, "preflight": None, "honesty": []}

    def selected(self, **overrides):
        values = {"sourceRunId": "run-001", "stateDigest": "a" * 64,
                  "targetComponentId": "portico", "elementId": "portico-cornice"}
        return ChatDesignContext(**{**values, **overrides})

    def studio(self, session, packs, refusal=None, memory=None):
        """A stand-in Hub and Studio for the one read a prepared turn makes."""

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path == f"/api/chat/sessions/{session.id}":
                return self.store.get(session.id).model_dump()
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running",
                         "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
            if path == "/api/health":
                return {"processId": 123, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": session.projectId, "projectDir": session.projectDir}
            if path == "/api/intents/context":
                packs.append({"method": method, "base": base, "body": body})
                if refusal is not None:
                    raise refusal
                return self.PACK
            if path == "/api/memory/about" and (memory is not None or refusal is not None):
                packs.append({"method": method, "base": base, "path": path, "body": body})
                if refusal is not None:
                    raise refusal
                return {"projectId": session.projectId, "memory": memory}
            raise AssertionError(f"a prepared turn asked for something unexpected: {method} {path}")

        return request

    def turns(self):
        return [] if not self.log.exists() else self.calls()

    def test_project_context_starts_fresh_cli_then_resumes_and_keeps_visible_history(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                session = self.create(provider=provider)
                self.post(session, "OLD_CHAT_ONLY_185")
                self.assertEqual(self.finished(session).status, "idle")
                old_id = self.store._sessions[session.id].nativeSessionId
                with patch.object(chat, "_request_json", side_effect=self.studio(session, [])):
                    self.store.post(session.id, ChatPostRequest(
                        projectId=session.projectId, content="Continue from retained geometry",
                        contextMode="project", designContext=self.selected()))
                    result = self.finished(session)
                self.assertEqual(result.status, "idle", result.error)
                fresh = self.calls()[-1]
                self.assertNotIn(old_id, fresh["args"])
                self.assertNotIn("OLD_CHAT_ONLY_185", fresh["prompt"])
                self.assertIn('"stateDigest": "' + "a" * 64, fresh["prompt"])
                new_id = self.store._sessions[session.id].nativeSessionId
                self.assertNotEqual(new_id, old_id)
                self.assertEqual([(m.content, m.contextMode) for m in result.messages if m.role == "user"],
                                 [("OLD_CHAT_ONLY_185", "continue"), ("Continue from retained geometry", "project")])
                self.store.shutdown()
                self.store = chat.ChatStore(self.runtime, self.store.hub_url, commands=self.commands)
                self.post(session, "Next detail")
                self.assertEqual(self.finished(session).status, "idle")
                self.assertIn(new_id, self.calls()[-1]["args"])
                self.assertNotIn(old_id, self.calls()[-1]["args"])

    def test_refused_project_context_preserves_existing_cli_continuation(self):
        session = self.create(provider="claude")
        self.post(session)
        self.finished(session)
        old_id = self.store._sessions[session.id].nativeSessionId
        count = len(self.calls())
        with patch.object(chat, "_request_json", side_effect=self.studio(
                session, [], HubFailure(409, "STALE_BASE", "Selected source changed"))):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="Continue",
                contextMode="project", designContext=self.selected()))
            self.assertEqual(self.finished(session).error.code, "STALE_BASE")
        self.assertEqual(len(self.calls()), count)
        self.assertEqual(self.store._sessions[session.id].nativeSessionId, old_id)
        self.post(session, "Keep talking")
        self.finished(session)
        self.assertIn(old_id, self.calls()[-1]["args"])

    def test_whole_project_and_multiple_focus_forward_without_inventing_an_element(self):
        session = self.create()
        packs = []
        for extra in ({}, {"elementIds": ["wall-a", "wall-b"], "contextRefs": ["entity:roof"], "contextOffset": 64},
                      {"studyEvidence": [{"studyId": "courtyard", "ledgerRef": "project:exact-retained-study"}]}):
            with patch.object(chat, "_request_json", side_effect=self.studio(session, packs)):
                self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="Design the facade",
                    designContext=ChatDesignContext(sourceRunId="run-001", stateDigest="a" * 64, **extra)))
                self.assertEqual(self.finished(session).status, "idle")
            self.assertEqual(packs[-1]["body"], {"projectId": session.projectId, "utterance": "Design the facade",
                "sourceRunId": "run-001", "stateDigest": "a" * 64, **extra})

    def test_project_context_requires_a_source_before_posting(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ChatPostRequest(projectId="chat-project", content="Continue", contextMode="project")

    def test_fresh_context_carries_selected_study_conditions_without_old_chat(self):
        session = self.create(provider="claude")
        self.post(session, "OLD_PRECEDENT_CHAT")
        self.finished(session)
        old_id = self.store._sessions[session.id].nativeSessionId
        selected = {"studyId": "passage", "ledgerRef": "project:exact-study-revision"}
        evidence = {**selected, "designPrior": {"conditions": ["ONLY_WITH_VERIFIED_ACCESS"],
                    "changedContext": {"decision": "reject"}}, "completeness": {"complete": True}}
        pack = {**self.PACK, "studyEvidence": [evidence]}
        calls = []
        with patch.object(self, "PACK", pack), patch.object(chat, "_request_json", side_effect=self.studio(session, calls)):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId,
                content="Continue with the selected precedent", contextMode="project",
                designContext=self.selected(studyEvidence=[selected])))
            result = self.finished(session)
        self.assertEqual(result.status, "idle", result.error)
        prompt = self.calls()[-1]["prompt"]
        self.assertIn("ONLY_WITH_VERIFIED_ACCESS", prompt)
        self.assertIn('"decision": "reject"', prompt)
        self.assertNotIn("OLD_PRECEDENT_CHAT", prompt)
        self.assertNotIn(old_id, self.calls()[-1]["args"])
        self.assertEqual(calls[0]["body"]["studyEvidence"], [selected])

    def test_study_reopen_uses_project_bound_read_without_mutation_admission(self):
        session = self.create()
        self.store._sessions[session.id].status = "running"
        path = "/api/studies/passage.v1?ledgerRef=project%3Aexact-study"
        def request(base, requested, method="GET", body=None, **kwargs):
            if requested == path:
                self.assertEqual(base, "http://127.0.0.1:8791")
                self.assertEqual(method, "GET")
                return {"ledgerRef": "project:exact-study", "studyId": "passage.v1"}
            return self.studio(session, [])(base, requested, method, body, **kwargs)
        with patch.object(chat, "_request_json", side_effect=request):
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request", {"method": "GET", "path": path})
        self.assertEqual(result["ledgerRef"], "project:exact-study")
        self.assertIsNone(chat._POST.fullmatch("/api/studies"))

    def test_model_view_reaches_mcp_as_image_with_exact_metadata(self):
        import io
        session = self.create()
        path = "/api/drawings/model-view?runId=run-001&stateDigest=" + "a" * 64 + "&assetSha256=" + "b" * 64 + "&view=top"
        picture = {"source": {"runId": "run-001", "stateDigest": "a" * 64, "assetSha256": "b" * 64},
                   "view": "top", "mimeType": "image/png", "data": "iVBORw0KGgo=", "width": 800, "height": 500,
                   "representation": "orthographic-line-projection"}
        def request(base, requested, method="GET", body=None, **kwargs):
            if requested == path:
                self.assertEqual(method, "GET")
                return picture
            return self.studio(session, [])(base, requested, method, body, **kwargs)
        class Stream(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        line = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "studio_request", "arguments": {"method": "GET", "path": path}}}
        reader, writer = Stream(json.dumps(line) + "\n"), Stream()
        self.store._sessions[session.id].status = "running"
        with patch.object(chat, "_request_json", side_effect=request), \
             patch.object(chat.sys, "stdin", reader), patch.object(chat.sys, "stdout", writer):
            chat._mcp(self.store.hub_url, session.id)
        result = json.loads(writer.getvalue())["result"]
        self.assertNotIn("isError", result)
        metadata, image = result["content"]
        self.assertEqual(json.loads(metadata["text"]), {k: v for k, v in picture.items() if k != "data"})
        self.assertEqual(image, {"type": "image", "mimeType": "image/png", "data": picture["data"]})
        self.assertNotIn(picture["data"], metadata["text"])

    def test_context_supplement_is_exposed_as_a_read_without_mutation_admission(self):
        session = self.create()
        self.store._sessions[session.id].status = "running"
        packs = []
        body = {"projectId": session.projectId, "sourceRunId": "run-001", "stateDigest": "a" * 64,
                "utterance": "Design the facade", "contextRefs": ["entity:roof"], "contextOffset": 64}
        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs)):
            result = chat.call_tool(self.store.hub_url, session.id, "studio_request",
                                   {"method": "POST", "path": "/api/intents/context", "body": body})
        self.assertEqual(result, self.PACK)
        self.assertEqual(packs, [{"method": "POST", "base": "http://127.0.0.1:8791", "body": body}])

    def test_registered_pdf_page_reaches_mcp_as_image_without_mutation_admission(self):
        import io
        import fitz
        from PIL import Image
        from urllib.error import HTTPError
        from project_runtime.main import create_app as studio_app
        from project_runtime.settings import StudioSettings

        project = self.root / "chat-project"
        FilesystemProjectRepository.initialize(project, project_id="chat-project", initial_state={"project_id": "chat-project", "version": 0})
        session = self.create(project=project)
        self.store._sessions[session.id].status = "running"
        client = TestClient(studio_app(StudioSettings(project_dir=project, cad_export="off")))
        self.addCleanup(client.close)
        with fitz.open() as pdf:
            pdf.new_page(width=400, height=300)
            page = pdf.new_page(width=800, height=600)
            page.draw_rect(fitz.Rect(200, 200, 400, 400), color=(0, 0, 1), fill=(0, 0, 1))
            page.set_cropbox(fitz.Rect(100, 50, 700, 550))
            page.set_rotation(90)
            data = pdf.tobytes()
        uploaded = client.post("/api/documents", json={"projectId": session.projectId, "fileName": "sheet.pdf",
            "mimeType": "application/pdf", "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document = uploaded.json()
        source = {key: document[key] for key in ("runId", "assetSha256", "revisionRef")}
        source["pageIndex"] = 1
        body = {"projectId": session.projectId, "pages": [source], "format": "png", "zip": False, "maxEdge": 600}
        before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
        http_request, requests = chat._request_json, []

        def open_request(request, **kwargs):
            requests.append((request.full_url, request.method, json.loads(request.data)))
            reply = client.post("/api/board/export", json=json.loads(request.data))
            stream = io.BytesIO(reply.content)
            stream.headers = reply.headers
            if reply.status_code >= 400:
                raise HTTPError(request.full_url, reply.status_code, "refused", reply.headers, stream)
            return stream

        def request(base, path, method="GET", body=None, **kwargs):
            if path == "/api/board/export":
                return http_request(base, path, method, body, **kwargs)
            if path == "/openapi.json":
                return client.get(path).json()
            return self.studio(session, [])(base, path, method, body, **kwargs)

        class Stream(io.StringIO):
            def reconfigure(self, **kwargs):
                pass

        arguments = {"method": "POST", "path": "/api/board/export", "body": body}
        missing = {**arguments, "body": {**body, "pages": [{**source, "pageIndex": 2}]}}
        lines = [{"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {
            "name": "studio_request", "arguments": arg}} for index, arg in enumerate((arguments, missing), 1)]
        writer = Stream()
        with patch.object(chat, "_request_json", side_effect=request), \
             patch.object(chat, "_SERVICE_OPENER") as opener, \
             patch.object(chat.sys, "stdin", Stream("\n".join(json.dumps(line) for line in lines) + "\n")), \
             patch.object(chat.sys, "stdout", writer):
            opener.open.side_effect = open_request
            schema = chat.call_tool(self.store.hub_url, session.id, "studio_schema",
                                    {"method": "POST", "path": "/api/board/export"})
            self.assertIn("maxEdge", schema["components"]["schemas"]["BoardExportRequestDto"]["properties"])
            chat._mcp(self.store.hub_url, session.id)
        success, failure = [json.loads(line)["result"] for line in writer.getvalue().splitlines()]
        self.assertNotIn("isError", success)
        text, image = success["content"]
        metadata = json.loads(text["text"])
        self.assertEqual(metadata["source"], {"projectId": session.projectId, **source})
        self.assertEqual((metadata["width"], metadata["height"]), (500, 600))
        self.assertFalse(metadata["annotationsIncluded"])
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertNotIn("data", metadata)
        with Image.open(io.BytesIO(base64.b64decode(image["data"]))) as picture:
            self.assertEqual(picture.size, (500, 600))
            self.assertIn((0, 0, 255), {color for _, color in picture.getcolors(picture.width * picture.height)})
        self.assertTrue(failure["isError"])
        self.assertEqual(json.loads(failure["content"][0]["text"])["code"], "DOCUMENT_PAGE_NOT_FOUND")
        self.assertEqual(requests, [("http://127.0.0.1:8791/api/board/export", "POST", arg["body"])
                                   for arg in (arguments, missing)])
        self.assertEqual({p: p.read_bytes() for p in project.rglob("*") if p.is_file()}, before)
        self.assertFalse((self.runtime / "operations").exists())

    def test_drawing_page_tool_rejects_unbound_and_non_image_requests_before_export(self):
        body = {"projectId": "chat-project", "pages": [{"runId": "run-001", "assetSha256": "a" * 64,
                "revisionRef": None, "pageIndex": 0}], "format": "png", "zip": False}
        arguments = {"method": "POST", "path": "/api/board/export", "body": body}
        invalid = [{**arguments, "body": {**body, **change}} for change in (
            {"projectId": "other"}, {"format": "merged-pdf"}, {"zip": True}, {"zip": 0},
            {"pages": body["pages"] * 2}, {"pages": []}, {"pages": [{"runId": "run-001"}]},
            {"maxEdge": True}, {"maxEdge": 2049}, {"maxEdge": None},
        )]
        invalid += [{**arguments, **change} for change in (
            {"operationId": str(uuid4())}, {"awaitSeconds": 5}, {"path": "/api/board/export?path=private.pdf"},
            {"method": "GET"}, {"path": "/api/documents/" + "a" * 64 + "/bytes"},
        )]
        with patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:8791", {"projectId": "chat-project"})), \
             patch.object(chat, "_request_json", return_value={"paths": {}}) as request:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(HubFailure):
                    chat.call_tool(self.store.hub_url, "chat", "studio_request", value)
            self.assertTrue(all(call.args == ("http://127.0.0.1:8791", "/openapi.json")
                                and not call.kwargs for call in request.call_args_list),
                            "A refused path may discover alternatives, but it must never export a page.")
            request.reset_mock()
            request.return_value = {"data": "png", "mimeType": "image/png"}
            chat.call_tool(self.store.hub_url, "chat", "studio_request", arguments)
            self.assertEqual(request.call_args.kwargs, {"png": True})
            self.assertEqual(request.call_args.args[3]["maxEdge"], 2048)

    def test_drawing_page_transport_rejects_invalid_or_unbounded_png(self):
        import io
        from PIL import Image
        oversized = io.BytesIO()
        Image.new("RGB", (2049, 1)).save(oversized, format="PNG")
        for mime, data, code in (
            ("application/zip", b"zip", "CHAT_IMAGE_INVALID"),
            ("image/png", b"not a png", "CHAT_IMAGE_INVALID"),
            ("image/png", oversized.getvalue(), "CHAT_IMAGE_INVALID"),
            ("image/png", b"x" * (4 * 1024 * 1024 + 1), "CHAT_IMAGE_TOO_LARGE"),
        ):
            with self.subTest(mime=mime, code=code), patch.object(chat, "_SERVICE_OPENER") as opener:
                response = io.BytesIO(data)
                response.headers = {"Content-Type": mime}
                opener.open.return_value = response
                with self.assertRaises(HubFailure) as failure:
                    chat._request_json("http://127.0.0.1:8791", "/api/board/export", "POST", {}, png=True)
                self.assertEqual(failure.exception.error.code, code)

    # ---- the Agent's bounded look (#303)

    def looking(self, *answers):
        """The Studio's own visual review route over this chat's project, with one registered page.

        The route, its allowance policy and the page export owner are the real
        ones, and Hub reaches them through its real transport. Only the provider
        seam is a stand-in, answering these observations in turn. Returns the
        session, the page and what the route and the provider were each sent.
        """
        import io
        from urllib.error import HTTPError
        from PIL import Image
        from project_runtime.application.visual_observation import (
            ObservationUsage, ProviderAnswer, ProviderCapability,
        )
        from project_runtime.main import create_app as studio_app
        from project_runtime.settings import StudioSettings

        project = self.root / "chat-project"
        FilesystemProjectRepository.initialize(project, project_id="chat-project",
                                               initial_state={"project_id": "chat-project", "version": 0})
        session = self.create(project=project)
        self.store._sessions[session.id].status = "running"
        client = TestClient(studio_app(StudioSettings(project_dir=project, cad_export="off")))
        self.addCleanup(client.close)
        sheet = io.BytesIO()
        Image.new("RGB", (160, 120), "white").save(sheet, format="PNG")
        uploaded = client.post("/api/documents", json={
            "projectId": session.projectId, "fileName": "sheet.png", "mimeType": "image/png",
            "contentBase64": base64.b64encode(sheet.getvalue()).decode("ascii")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document = uploaded.json()
        page = {"kind": "page", "runId": document["runId"], "assetSha256": document["assetSha256"],
                "revisionRef": document["revisionRef"], "pageIndex": 0}
        sent = {"route": [], "provider": []}

        class Provider:
            def capability(self):
                return ProviderCapability("codex", "stand-in", 4, 4 * 1024 * 1024, ("image/png",), True)

            def observe(self, request, frames):
                sent["provider"].append((request, tuple(frames)))
                usage = ObservationUsage("codex", "stand-in", 1, len(frames), sum(len(frame.png) for frame in frames),
                                         1200, None, 80, None, 900, None)
                return ProviderAnswer(answers[len(sent["provider"]) - 1], usage)

        transport = chat._request_json

        def request(base, path, method="GET", body=None, **kwargs):
            if path == "/api/visual-reviews":
                sent["route"].append(body)
                return transport(base, path, method, body, **kwargs)
            return self.studio(session, [])(base, path, method, body, **kwargs)

        def open_request(request, **kwargs):
            reply = client.post(urlsplit(request.full_url).path, json=json.loads(request.data))
            stream = io.BytesIO(reply.content)
            stream.headers = reply.headers
            if reply.status_code >= 400:
                raise HTTPError(request.full_url, reply.status_code, "refused", reply.headers, stream)
            return stream

        self.enterContext(patch.object(chat, "_request_json", side_effect=request))
        self.enterContext(patch.object(chat, "_SERVICE_OPENER")).open.side_effect = open_request
        self.enterContext(patch("project_runtime.api.routes.intents.visual_provider", return_value=Provider()))
        return session, page, sent

    def say(self, session, content):
        """The next user message the Agent is given, and so the one it answers."""
        self.store._sessions[session.id].messages.append(
            ChatMessage(id=str(uuid4()), role="user", content=content, createdAt=chat._now()))

    def look(self, page, **changes):
        return {"delivery": "observation", "taskClass": "spatial_formal", "reason": "first_bundle", "domain": "board", "sourceRefs": [page],
                "viewRecipe": ["page-0"], "task": "Check that the sheet reads in the intended order.",
                "criteria": [{"criterionId": "hierarchy", "text": "One drawing leads the sheet."}],
                "preserve": ["Keep the drawn content as it is."], **changes}

    def agent(self, session, *calls, raw=False):
        """Each visual_review call as the Agent receives its answer, through the stdio adapter."""
        import io

        class Stream(io.StringIO):
            def reconfigure(self, **kwargs):
                pass

        lines = [{"jsonrpc": "2.0", "id": index, "method": "tools/call",
                  "params": {"name": "visual_review", "arguments": arguments}}
                 for index, arguments in enumerate(calls, 1)]
        writer = Stream()
        with patch.object(chat.sys, "stdin", Stream("".join(json.dumps(line) + "\n" for line in lines))), \
                patch.object(chat.sys, "stdout", writer):
            chat._mcp(self.store.hub_url, session.id)
        results = [json.loads(line)["result"] for line in writer.getvalue().splitlines()]
        if raw:
            return results
        for result in results:
            self.assertEqual({row["type"] for row in result["content"]}, {"text"}, "findings, never images")
        return [(result.get("isError", False), json.loads(result["content"][0]["text"])) for result in results]

    NOTHING_SEEN = {"observations": [], "unresolved_questions": [], "suggested_checks": []}

    def test_visual_review_is_its_own_tool_and_hub_fills_the_project_and_the_allowance(self):
        tool = next(tool for tool in _tools_of(chat) if tool["name"] == "visual_review")
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["required"]),
                         {"taskClass", "reason", "domain", "sourceRefs", "viewRecipe", "task", "criteria"})
        self.assertFalse({"projectId", "budgetState"} & set(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])
        for stated in ("native images", "deterministic edit", "GET /api/drawings/model-view", "escalate",
                       "after_repair", "继续优化", "VISUAL_BUDGET_EXHAUSTED", "axon", "page-<pageIndex>"):
            self.assertIn(stated, tool["description"], stated)
        page = {"kind": "page", "runId": "run-001", "assetSha256": "a" * 64, "revisionRef": None, "pageIndex": 0}
        with patch.object(chat, "_request_json", side_effect=AssertionError("refused before anything is read")):
            for supplied in ({"budgetState": {"taskClass": "polish", "allowed": 4, "used": 0}},
                             {"projectId": "other-project"}, {"frames": ["png"]}):
                with self.subTest(supplied=supplied), self.assertRaises(HubFailure) as refused:
                    chat.call_tool(self.store.hub_url, str(uuid4()), "visual_review", {**self.look(page), **supplied})
                self.assertEqual(refused.exception.error.code, "CHAT_TOOL_INVALID")

    def test_default_visual_review_delivers_native_images_without_a_second_provider(self):
        import io
        from hashlib import sha256
        from PIL import Image

        session, page, sent = self.looking()
        self.say(session, "Look at this sheet's hierarchy.")
        arguments = self.look(page)
        arguments.pop("delivery")
        first, = self.agent(session, arguments, raw=True)
        self.assertFalse(first.get("isError"), first)
        self.assertEqual([row["type"] for row in first["content"]], ["text", "text", "image"])
        metadata = json.loads(first["content"][0]["text"])
        frame = json.loads(first["content"][1]["text"])
        png = base64.b64decode(first["content"][2]["data"], validate=True)
        self.assertEqual(frame["sourceRef"], page)
        self.assertEqual(frame["frameSha256"], sha256(png).hexdigest())
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            self.assertEqual(image.size, (frame["width"], frame["height"]))
        self.assertIsNone(metadata["observation"])
        self.assertIsNone(metadata["usage"])
        self.assertEqual(metadata["allowance"]["used"], 1)
        self.assertEqual(sent["provider"], [], "the current agent sees the images; no second provider is called")
        refused, = self.agent(session, {**arguments, "reason": "after_repair", "addressedFindingIds": ["f1"]}, raw=True)
        self.assertTrue(refused["isError"], refused)
        self.assertEqual(json.loads(refused["content"][0]["text"])["code"], "VISUAL_REVIEW_NOT_WARRANTED")
        self.assertEqual(sent["route"][-1]["budgetState"]["used"], 1)

    def test_visual_frames_invalid_payload_cannot_reset_budget_or_reach_the_agent(self):
        import io
        from copy import deepcopy
        from hashlib import sha256
        from PIL import Image

        session = self.create()
        self.say(session, "Inspect the exact page.")
        page = {"kind": "page", "runId": "run-001", "assetSha256": "a" * 64, "revisionRef": None, "pageIndex": 0}
        stream = io.BytesIO()
        Image.new("RGB", (2, 2)).save(stream, format="PNG")
        payload = stream.getvalue()
        answer = {"delivery": "frames", "observation": None, "usage": None,
                  "budgetState": {"taskClass": "spatial_formal", "allowed": 2, "used": 1, "lastFindingIds": []},
                  "frames": [{"sourceRef": page, "viewRef": "page-0", "representation": "registered-document-page",
                              "frameSha256": sha256(payload).hexdigest(), "mimeType": "image/png", "width": 2, "height": 2,
                              "data": base64.b64encode(payload).decode("ascii")}]}
        for change in ("source", "digest", "dimension", "budget", "finding"):
            bad = deepcopy(answer)
            if change == "source":
                bad["frames"][0]["sourceRef"]["assetSha256"] = "b" * 64
            elif change == "digest":
                bad["frames"][0]["data"] = base64.b64encode(b"wrong").decode("ascii")
            elif change == "dimension":
                bad["frames"][0]["width"] = 3
            elif change == "budget":
                bad["budgetState"]["used"] = 0
            else:
                bad["budgetState"]["lastFindingIds"] = ["f1"]
            self.say(session, "Inspect the exact page again.")
            with self.subTest(change=change), patch.object(chat, "_bound_studio", return_value=("runtime", self.store._sessions[session.id].model_dump())), \
                    patch.object(chat, "_request_json", return_value=bad), self.assertRaises(HubFailure) as failure:
                chat._visual_review(self.store.hub_url, session.id, self.look(page, delivery="frames"))
            self.assertEqual(failure.exception.error.code, "CHAT_TOOL_FAILED")
            self.assertEqual(chat._visual_allowances[session.id][1]["used"], 1)

    def test_visual_frame_renderer_refusal_leaves_budget_available(self):
        session = self.create()
        self.say(session, "Inspect the exact page.")
        page = {"kind": "page", "runId": "run-001", "assetSha256": "a" * 64, "revisionRef": None, "pageIndex": 0}
        with patch.object(chat, "_bound_studio", return_value=("runtime", self.store._sessions[session.id].model_dump())), \
                patch.object(chat, "_request_json", side_effect=HubFailure(502, "DRAWING_RENDER_FAILED", "Renderer failed.")), \
                self.assertRaises(HubFailure) as failure:
            chat._visual_review(self.store.hub_url, session.id, self.look(page, delivery="frames"))
        self.assertEqual(failure.exception.error.code, "DRAWING_RENDER_FAILED")
        self.assertEqual(chat._visual_allowances[session.id][1]["used"], 0)

    def test_a_spatial_look_gets_two_reviews_and_the_third_reaches_the_agent_as_exhausted(self):
        drawn, overlap = ({"type": kind, "target_refs": [target], "description": text, "confidence": 0.8,
                           "severity": severity, "evidence_region": None}
                          for kind, target, text, severity in (
                              ("composition", "criterion:hierarchy", "Two drawings carry equal weight.", "minor"),
                              ("preserve", "preserve:1", "The title block now covers part of the drawing.", "major")))
        session, page, sent = self.looking({**self.NOTHING_SEEN, "observations": [drawn, overlap]},
                                           {**self.NOTHING_SEEN, "observations": [drawn]})
        self.say(session, "Make the sheet read more clearly.")
        (failed, first), (_, second), (refused, third) = self.agent(
            session, self.look(page), self.look(page, reason="after_repair", addressedFindingIds=["f1"]),
            self.look(page, reason="after_repair", addressedFindingIds=["f1"]))
        self.assertFalse(failed)
        # A finding on a preserve condition is the architect's question, not another review's.
        self.assertEqual([(row["findingId"], row["targetRefs"], row["escalate"]) for row in first["observation"]["observations"]],
                         [("f1", ["criterion:hierarchy"], False), ("f2", ["preserve:1"], True)])
        self.assertEqual((first["allowance"], first["usage"]["imageInputs"]),
                         ({"taskClass": "spatial_formal", "allowed": 2, "used": 1}, 1))
        self.assertEqual(second["allowance"]["used"], 2)
        self.assertTrue(refused)
        self.assertEqual((third["code"], third["httpStatus"]), ("VISUAL_BUDGET_EXHAUSTED", 409))
        self.assertIn("2 of 2 reviews used", third["detail"])
        self.assertEqual(len(sent["provider"]), 2, "the third look reached no provider")
        # Hub filled the project and carried the allowance each answer handed back.
        self.assertEqual({body["projectId"] for body in sent["route"]}, {session.projectId})
        self.assertEqual([body["budgetState"] for body in sent["route"]], [
            {"taskClass": "spatial_formal", "allowed": 2, "used": 0, "lastFindingIds": []},
            {"taskClass": "spatial_formal", "allowed": 2, "used": 1, "lastFindingIds": ["f1", "f2"]},
            {"taskClass": "spatial_formal", "allowed": 2, "used": 2, "lastFindingIds": ["f1"]},
        ])

    def test_the_allowance_belongs_to_the_answered_message_and_keeps_its_class_once_spent(self):
        session, page, sent = self.looking(self.NOTHING_SEEN, self.NOTHING_SEEN)
        self.say(session, "Set the door to 1.2 m, then check how the sheet reads.")
        (_, unwarranted), (_, looked), (_, switched) = self.agent(
            session, self.look(page, taskClass="deterministic_edit"), self.look(page),
            self.look(page, taskClass="polish", polishRounds=2, reason="polish_round"))
        self.assertEqual(unwarranted["code"], "VISUAL_REVIEW_NOT_WARRANTED")
        # Nothing was spent, so the Agent could still declare the look it needed.
        self.assertEqual(looked["allowance"], {"taskClass": "spatial_formal", "allowed": 2, "used": 1})
        self.assertEqual(switched["code"], "VISUAL_TASK_CLASS_FIXED")
        self.assertEqual(len(sent["route"]), 2, "a class the message no longer takes never reaches the Studio")
        self.say(session, "Now check the revised sheet.")
        (_, fresh), = self.agent(session, self.look(page))
        self.assertEqual(fresh["allowance"], {"taskClass": "spatial_formal", "allowed": 2, "used": 1})
        self.assertEqual(sent["route"][-1]["budgetState"]["used"], 0, "the next message starts its own allowance")
        self.assertEqual(len(sent["provider"]), 2)

    def test_more_than_two_polish_rounds_need_the_users_own_words(self):
        session, page, sent = self.looking(self.NOTHING_SEEN, self.NOTHING_SEEN)

        def polish(rounds):
            return self.look(page, taskClass="polish", polishRounds=rounds, reason="polish_round")

        self.say(session, "Make the entrance wider.")
        (_, unasked), (_, modest) = self.agent(session, polish(4), polish(2))
        self.assertEqual(unasked["code"], "VISUAL_POLISH_NOT_ASKED")
        self.assertEqual(modest["allowance"], {"taskClass": "polish", "allowed": 2, "used": 1})
        self.say(session, "继续打磨这张图的层次。")
        (_, asked), = self.agent(session, polish(4))
        self.assertEqual(asked["allowance"], {"taskClass": "polish", "allowed": 4, "used": 1})
        self.assertEqual([body["budgetState"]["allowed"] for body in sent["route"]], [2, 4])

    def test_the_named_source_is_read_once_and_never_carried_into_the_next_turn(self):
        session = self.create()
        packs = []
        words = "Raise portico-cornice to 0.5 m and keep portico-base as it is."
        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs)):
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content=words, designContext=self.selected()))
            self.assertEqual(self.finished(session).status, "idle")
            # The whole message travels, unedited, with exactly the named focus.
            self.assertEqual(len(packs), 1)
            self.assertEqual(packs[0]["method"], "POST")
            self.assertEqual(packs[0]["base"], "http://127.0.0.1:8791")
            self.assertEqual(packs[0]["body"], {
                "utterance": words, "projectId": session.projectId, "sourceRunId": "run-001",
                "stateDigest": "a" * 64, "targetComponentId": "portico",
                "elementId": "portico-cornice"})
            prompt = self.calls()[-1]["prompt"]
            # The request is still the request: the pack follows it as data.
            self.assertIn("\n\n" + words + "\n\n" + chat._CONTEXT_NOTE + "\n", prompt)
            # How an accepted drawing recipe reaches a new drawing, in its order.
            self.assertIn("an explicit value in the drawing request wins, then the drawing's own previous revision, "
                          "then the project recipe, then the default", prompt)
            self.assertIn('"contextPack": "ContextPack@1"', prompt)
            self.assertIn('"stateDigest": "' + "a" * 64, prompt)
            # A later message that names nothing is the turn it always was.
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="and what did that change?"))
            self.assertEqual(self.finished(session).status, "idle")
        self.assertEqual(len(packs), 1, "a turn with no context of its own must inherit none")
        plain = self.calls()[-1]["prompt"]
        self.assertTrue(plain.endswith("\n\nand what did that change?"))
        self.assertNotIn(chat._CONTEXT_NOTE, plain)

    # ---- project memory reaches a turn with no design context (#252)

    LOCATOR = {"memory": {"memoryId": "mem-frame", "kind": "locator",
                          "value": {"label": "项目图框", "target": {"kind": "document", "runId": "documents",
                                                                  "assetSha256": "b" * 64, "pageIndex": 0}}},
               "status": "current", "staleReason": None, "matchedTerms": ["图框"]}

    def test_a_turn_with_no_design_context_is_handed_the_memory_its_words_are_about(self):
        session = self.create()
        packs = []
        words = "项目图框在哪？"
        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs, memory=[self.LOCATOR])):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content=words))
            self.assertEqual(self.finished(session).status, "idle")
        # The whole message, once, to the bound Studio's read-only route.
        self.assertEqual(packs, [{"method": "POST", "base": "http://127.0.0.1:8791", "path": "/api/memory/about",
                                  "body": {"projectId": session.projectId, "utterance": words}}])
        prompt = self.calls()[-1]["prompt"]
        self.assertIn("\n\n" + words + "\n\n" + chat._MEMORY_NOTE + "\n", prompt)
        self.assertTrue(prompt.endswith(json.dumps([self.LOCATOR], ensure_ascii=False, separators=(",", ":"))))
        self.assertEqual(chat._MEMORY_NOTE.count("\n"), 1, "the note is two lines")
        self.assertNotIn(chat._CONTEXT_NOTE, prompt)

    def test_words_that_match_no_memory_add_no_block(self):
        session = self.create()
        packs = []
        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs, memory=[])):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="接着往下调"))
            self.assertEqual(self.finished(session).status, "idle")
        self.assertEqual([row["path"] for row in packs], ["/api/memory/about"])
        prompt = self.calls()[-1]["prompt"]
        self.assertTrue(prompt.endswith("\n\n接着往下调"))
        self.assertNotIn(chat._MEMORY_NOTE, prompt)
        self.assertNotIn("Project memory could not be read", prompt)

    def test_a_refused_memory_read_is_one_line_and_the_provider_still_starts(self):
        session = self.create()
        packs = []
        before = len(self.turns())
        refusal = HubFailure(403, "ACTION_FORBIDDEN", "The authenticated actor is not granted this action.")
        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs, refusal=refusal)):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="项目图框在哪？"))
            finished = self.finished(session)
        self.assertEqual(finished.status, "idle", finished.error)
        self.assertEqual(len(packs), 1)
        self.assertEqual(len(self.turns()), before + 1, "the provider starts without memory")
        prompt = self.calls()[-1]["prompt"]
        # One line, before the request, which is still the last thing said.
        self.assertTrue(prompt.endswith("\n\nProject memory could not be read for this turn: "
                                        "The authenticated actor is not granted this action.\n\n项目图框在哪？"))
        self.assertNotIn(chat._MEMORY_NOTE, prompt)

    def test_a_turn_with_design_context_reads_no_separate_memory(self):
        session = self.create()
        packs = []
        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs, memory=[self.LOCATOR])):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="项目图框在哪？",
                                                        designContext=self.selected()))
            self.assertEqual(self.finished(session).status, "idle")
        # The prepared context already carries ContextPack.memory.
        self.assertEqual([row.get("path", "/api/intents/context") for row in packs], ["/api/intents/context"])
        prompt = self.calls()[-1]["prompt"]
        self.assertIn(chat._CONTEXT_NOTE, prompt)
        self.assertNotIn(chat._MEMORY_NOTE, prompt)

    def test_the_memory_read_is_a_post_read_the_agent_may_make_and_binds_no_words(self):
        self.assertTrue(chat._POST.fullmatch("/api/memory/about"))
        self.assertIn("/api/memory/about", chat._POST_READS)
        self.assertFalse(chat._binds_words("POST", "/api/memory/about"))
        self.assertTrue(chat._binds_words("POST", "/api/memory"))
        self.assertIn("POST /api/memory/about", chat._GUIDES["/api/memory"])

    # ---- a recipe says which library skill to load, and whether its version is current (#252 3c)

    RECIPE = {"memory": {"memoryId": "mem-hatch", "kind": "recipe",
                         "value": {"task": "出平面图前检查填充", "skill": "skill:hatch-review@1", "note": None}},
              "status": "current", "staleReason": None, "matchedTerms": ["平面", "填充"]}

    def library(self, *versions):
        """The configured library's current index, as the Hub reads it through the library Runtime."""
        rows = [{"id": "skill:hatch-review", "version": version, "name": "hatch-review",
                 "description": "Review a plan's hatching."} for version in versions]
        return skill_plugins.Library({"projectId": "skill-library", "skills": rows},
                                     lambda skill_id, version: {**rows[-1], "body": "ZX7.\n"})

    def recipe_turn(self, session, words, memory, library):
        reads, packs = [], []

        def configured(runtime_root, hub_url):
            reads.append(hub_url)
            return library

        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs, memory=memory)), \
                patch.object(skill_plugins, "configured_library", side_effect=configured):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content=words))
            finished = self.finished(session)
        self.assertEqual(finished.status, "idle", finished.error)
        return self.calls()[-1], reads

    def carried(self, prompt):
        block = prompt.rsplit("\n", 1)[-1]
        return [row for row in json.loads(block) if row["memory"]["kind"] == "recipe"]

    def test_a_turn_about_its_task_carries_the_recipe_with_the_skill_to_load(self):
        session = self.create(provider="claude")
        call, reads = self.recipe_turn(session, "帮我出平面图，检查一下填充", [self.RECIPE], self.library(1))
        [recipe] = self.carried(call["prompt"])
        self.assertEqual(recipe["skill"], {
            "load": "monkeyhub-library:hatch-review", "pinned": "skill:hatch-review@1", "libraryVersion": 1,
            "note": "pinned 1 is the library's current version."})
        # One read of the library serves the recipe and the plugin the CLI loads.
        self.assertEqual(len(reads), 1)
        plugin = Path(call["args"][call["args"].index("--plugin-dir") + 1])
        self.assertTrue((plugin / "skills/hatch-review/SKILL.md").is_file())
        self.assertIn("load its skill", chat._MEMORY_NOTE)

        # Words about something else carry no recipe.
        call, _ = self.recipe_turn(session, "把檐口压低一点", [], self.library(1))
        self.assertNotIn("hatch-review", call["prompt"])
        self.assertNotIn(chat._MEMORY_NOTE, call["prompt"])

    def test_a_recipe_says_when_the_library_moved_or_lost_its_skill(self):
        session = self.create(provider="claude")
        call, _ = self.recipe_turn(session, "出平面图", [self.RECIPE], self.library(2))
        [moved] = self.carried(call["prompt"])
        self.assertEqual((moved["skill"]["pinned"], moved["skill"]["libraryVersion"]), ("skill:hatch-review@1", 2))
        self.assertTrue(moved["skill"]["note"].startswith("pinned 1, library now 2"))
        # Nothing is swapped: the stored recipe still pins 1.
        self.assertEqual(moved["memory"]["value"]["skill"], "skill:hatch-review@1")

        call, _ = self.recipe_turn(session, "出平面图", [self.RECIPE], self.library())
        [gone] = self.carried(call["prompt"])
        self.assertEqual((gone["skill"]["load"], gone["skill"]["libraryVersion"]),
                         ("monkeyhub-library:hatch-review", None))
        self.assertTrue(gone["skill"]["note"].startswith("not in the library"))
        self.assertNotIn("--plugin-dir", self.calls()[-1]["args"], "an empty library has nothing to load")

        call, _ = self.recipe_turn(session, "出平面图", [self.RECIPE], None)
        [unset] = self.carried(call["prompt"])
        self.assertTrue(unset["skill"]["note"].startswith("not in the library (no skill library is set)"))

    def test_a_library_chat_learns_its_leftover_skills_and_turns_them_off_next_turn(self):
        for provider in ("claude", "coding-plan"):
            with self.subTest(provider=provider), \
                    patch.object(chat, "_coding_plan_env", return_value={
                        "ANTHROPIC_BASE_URL": "https://fixture.example.invalid",
                        "ANTHROPIC_AUTH_TOKEN": "fixture-plan-token"}):
                leftovers = skill_plugins.leftovers_path(self.runtime)
                leftovers.unlink(missing_ok=True)
                session = self.create(provider=provider)

                def overrides(call):
                    return json.loads(call["args"][call["args"].index("--settings") + 1])["skillOverrides"]

                with self.assertLogs(skill_plugins.__name__, "WARNING") as logged:
                    call, _ = self.recipe_turn(session, "把檐口压低一点", [], self.library(1))
                self.assertEqual(overrides(call), {"design": "off", "doctor": "off"})
                # The library's own skill and a skill already off are not leftovers.
                self.assertEqual(json.loads(leftovers.read_text(encoding="utf-8")), {"9.9.9": ["newthing"]})
                self.assertEqual(len(logged.records), 1)
                self.assertIn("'newthing'", logged.output[0])

                call, _ = self.recipe_turn(session, "把檐口压低一点", [], self.library(1))
                self.assertEqual(overrides(call), {"design": "off", "doctor": "off", "newthing": "off"})
                self.assertEqual(json.loads(leftovers.read_text(encoding="utf-8")), {"9.9.9": ["newthing"]})

                # A chat with no library has no Skill tool and learns nothing.
                call, _ = self.recipe_turn(session, "把檐口压低一点", [], None)
                self.assertIn("--disable-slash-commands", call["args"])
                self.assertNotIn("--settings", call["args"])

    def test_an_unreadable_library_is_said_on_the_recipe_and_a_codex_turn_still_runs(self):
        session = self.create()
        refused = HubFailure(503, "CHAT_SKILL_LIBRARY_UNAVAILABLE", "The skill library could not be read: Not ready.")
        packs = []
        with patch.object(chat, "_request_json", side_effect=self.studio(session, packs, memory=[self.RECIPE])), \
                patch.object(skill_plugins, "configured_library", side_effect=refused):
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="出平面图"))
            finished = self.finished(session)
        self.assertEqual(finished.status, "idle", finished.error)
        [recipe] = self.carried(self.calls()[-1]["prompt"])
        self.assertEqual(recipe["skill"]["note"], "The skill library could not be read: Not ready. Tell the user.")
        # A provider the plugin is not handed to is told it cannot load the skill.
        call, _ = self.recipe_turn(session, "出平面图", [self.RECIPE], self.library(1))
        [recipe] = self.carried(call["prompt"])
        self.assertIsNone(recipe["skill"]["load"])
        self.assertIn("loads no library skills", recipe["skill"]["note"])

    def test_a_refused_preparation_ends_the_turn_in_its_own_words_and_starts_no_cli(self):
        session = self.create()
        packs = []
        before = len(self.turns())
        refusal = HubFailure(409, "CHAT_TOOL_FAILED",
                             "the request names state 0000, but chat-project is at abcd.")
        with patch.object(chat, "_request_json",
                          side_effect=self.studio(session, packs, refusal=refusal)):
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="Raise it to 0.5 m.",
                designContext=self.selected(stateDigest="0" * 64)))
            finished = self.finished(session)
        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.error.code, "CHAT_TOOL_FAILED")
        self.assertIn("is at abcd", finished.error.detail)
        self.assertEqual(len(packs), 1, "a refusal is not retried against a guessed source")
        self.assertEqual(len(self.turns()), before, "no provider may be started by a refused turn")

    def test_a_turn_stopped_while_it_is_prepared_starts_no_cli(self):
        session = self.create()
        packs = []
        before = len(self.turns())

        def stop_then_answer(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path == "/api/intents/context":
                # Stopped between the preparation and the provider, which is
                # exactly where the second cancellation check stands.
                self.store._running[session.id].stop.set()
            return self.studio(session, packs)(base, path, method, body, timeout, headers=headers)

        with patch.object(chat, "_request_json", side_effect=stop_then_answer):
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="Raise it to 0.5 m.",
                designContext=self.selected()))
            self.assertEqual(self.finished(session).status, "interrupted")
        self.assertEqual(len(packs), 1)
        self.assertEqual(len(self.turns()), before, "a stopped turn starts no provider")

    def test_a_turn_already_stopped_is_never_prepared_at_all(self):
        session = self.create()
        running = chat._Running(design_context=self.selected())
        running.stop.set()
        with patch.object(chat, "_context_pack",
                          side_effect=AssertionError("a stopped turn must not be prepared")):
            self.store._run(session.id, "Raise it to 0.5 m.", running)
        self.assertEqual(self.store.get(session.id).status, "interrupted")

    def test_the_turns_trace_headers_do_not_outlive_its_preparation(self):
        session = self.create()
        session.status = "running"
        session.messages.append(chat.ChatMessage(
            id="turn-1", role="user", content="Raise it to 0.5 m.", createdAt=chat._now()))
        packs = []

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path == f"/api/chat/sessions/{session.id}":
                return session.model_dump()
            return self.studio(session, packs)(base, path, method, body, timeout, headers=headers)

        with patch.object(chat, "_request_json", side_effect=request):
            appended = chat._context_pack(self.store.hub_url, session.id, "Raise it to 0.5 m.",
                                          self.selected(), time.monotonic() + 30)
        self.assertEqual(appended, self.PACK)
        # _bound_studio binds this turn's headers; nothing after it may inherit
        # them, so a second turn cannot be correlated to the first one's span.
        self.assertEqual(chat._trace_headers.get(), {})

    # ---- a preparation that stops answering

    def stalling_studio(self, session):
        """A real loopback service that answers the bound checks, then stalls.

        The turn's own urllib makes these calls, so what the preparation waits on
        here is a socket that will not answer — the case a shorter HTTP deadline
        cannot end, because the wait has already begun. Returns the event that
        says the stalled read has been entered.
        """

        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        arrived, release, store = threading.Event(), threading.Event(), self.store

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def answer(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                saved = store.get(session.id)
                if path == f"/api/chat/sessions/{session.id}":
                    self.answer(json.loads(saved.model_dump_json()))
                elif path == "/api/apps":
                    self.answer([{"appId": "monkeyarch", "state": "running",
                                  "url": store.hub_url, "apiUrl": store.hub_url, "processId": 123}])
                elif path == "/api/health":
                    self.answer({"processId": 123, "sourceRevision": "same-revision"})
                elif path == "/api/project":
                    self.answer({"projectId": saved.projectId, "projectDir": saved.projectDir})
                else:
                    self.send_error(404)

            def do_POST(self):
                if urlsplit(self.path).path != "/api/intents/context":
                    return self.send_error(404)
                # Accepted, read, and then not answered.
                arrived.set()
                release.wait(20)
                self.answer(ChatTests.PACK)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.store.hub_url = f"http://127.0.0.1:{server.server_address[1]}"
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        return arrived

    def test_a_stop_during_a_stalled_preparation_ends_the_turn_and_starts_no_cli(self):
        session = self.create()
        before = len(self.turns())
        arrived = self.stalling_studio(session)
        self.store.post(session.id, ChatPostRequest(
            projectId=session.projectId, content="Raise it to 0.5 m.",
            designContext=self.selected()))
        self.assertTrue(arrived.wait(10), "the preparation never reached the stalled read")
        began = time.monotonic()
        stopped = self.store.stop(session.id)
        elapsed = time.monotonic() - began
        # The turn is over when it is stopped, not when the socket gives up: the
        # answer the abandoned read may still produce belongs to nothing.
        self.assertLess(elapsed, 5, "stop waited on the preparation instead of ending the turn")
        self.assertEqual(stopped.status, "interrupted")
        self.assertEqual(stopped.error.code, "CHAT_STOPPED")
        self.assertEqual(len(self.turns()), before, "a stopped turn starts no provider")

    def test_a_preparation_that_outlasts_the_turn_ends_it_at_the_turns_own_limit(self):
        session = self.create()
        before = len(self.turns())
        arrived = self.stalling_studio(session)
        self.store.timeout_s = 1.0
        began = time.monotonic()
        self.store.post(session.id, ChatPostRequest(
            projectId=session.projectId, content="Raise it to 0.5 m.",
            designContext=self.selected()))
        self.assertTrue(arrived.wait(10), "the preparation never reached the stalled read")
        finished = self.finished(session)
        # One limit for the turn, so the stalled read cannot hold it open past
        # the limit the conversation was told about.
        self.assertLess(time.monotonic() - began, 15)
        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.error.code, "CHAT_TIMEOUT")
        self.assertIn("prepared", finished.error.detail)
        self.assertEqual(len(self.turns()), before, "a turn that ran out starts no provider")

    def slow_studio(self, session, packs, seconds):
        """The same prepared read, taking a known part of the turn to answer."""

        answer = self.studio(session, packs)

        def request(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path == "/api/intents/context":
                time.sleep(seconds)
            return answer(base, path, method, body, timeout, headers=headers)

        return request

    def paused_turn(self, session, preparing):
        """One real CLI turn held open until its own limit ends it, and its cost."""

        packs, before = [], len(self.turns())
        began = time.monotonic()
        with patch.object(chat, "_request_json",
                          side_effect=self.slow_studio(session, packs, preparing)):
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="pause-test with a named source",
                designContext=self.selected()))
            finished = self.finished(session)
        elapsed = time.monotonic() - began
        self.assertEqual(len(packs), 1)
        self.assertEqual(finished.error.code, "CHAT_TIMEOUT")
        # The CLI really ran on what preparing left, rather than being skipped.
        self.assertEqual(len(self.turns()), before + 1)
        self.assertIn(chat._CONTEXT_NOTE, self.calls()[-1]["prompt"])
        return elapsed

    def test_preparing_the_context_is_taken_out_of_the_cli_turns_own_limit(self):
        session = self.create()
        self.store.timeout_s = 3.0
        # The same turn twice against the real fake CLI, which holds until it is
        # stopped: only how long preparing took differs.
        quick = self.paused_turn(session, 0.05)
        slow = self.paused_turn(session, 1.5)
        # The CLI's share is what preparing left. Were the two limits separate,
        # the slower preparation would push this turn out by its whole 1.5s.
        self.assertLess(slow - quick, 1.0,
                        f"the CLI was given a fresh limit beside the preparation ({quick=:.2f} {slow=:.2f})")

    def test_the_acp_adapter_keeps_its_own_inactivity_interval(self):
        from monkeyhub_api.chat import acp_session as adapter

        session = self.create()
        self.store._sessions[session.id].transport = "acp"
        self.store.timeout_s = 6.0
        packs, sent = [], []

        class Recorder:
            """Stands in for the adapter only, at the boundary it is called on."""

            def __init__(self, **arguments):
                self.default_model = "fixture-model-a"

            def prompt(self, text, session_id, model, on_session, timeout_s, *, images=()):
                on_session("fixture/session:recorded")
                sent.append({"prompt": text, "timeout_s": timeout_s})

            def close(self):
                pass

        with patch.object(chat, "_request_json", side_effect=self.slow_studio(session, packs, 0.5)), \
             patch.object(adapter, "CodexAcpSession", Recorder):
            self.store.post(session.id, ChatPostRequest(
                projectId=session.projectId, content="Raise it to 0.5 m.",
                designContext=self.selected()))
            self.assertEqual(self.finished(session).status, "idle")
        self.assertEqual(len(sent), 1)
        # This adapter's limit is an inactivity interval its own updates
        # reschedule, so preparing must not shorten it into a turn total.
        self.assertEqual(sent[0]["timeout_s"], self.store.timeout_s)
        # It still receives the prepared context itself.
        self.assertEqual(len(packs), 1)
        self.assertIn(chat._CONTEXT_NOTE, sent[0]["prompt"])

    def test_the_hub_exits_while_a_prepared_read_is_stalled_in_its_binding_checks(self):
        script = self.root / "stalled exit.py"
        script.write_text(STALLED_EXIT, encoding="utf-8")
        began = time.monotonic()
        # A separate interpreter, because what is being checked is its exit: a
        # read nobody is waiting on must not be joined on the way out.
        finished = subprocess.run(
            [sys.executable, str(script), str(ROOT), str(self.root / "exit runtime"),
             str(self.project), str(self.fake), str(self.log)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertIn("stalled in binding checks", finished.stdout)
        self.assertIn("turn stopped", finished.stdout)
        self.assertLess(time.monotonic() - began, 60,
                        "the stalled read held the interpreter open on the way out")


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
            with patch.object(chat, "__file__", str(hub / "api/monkeyhub_api/chat/store.py")), \
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
            with patch.object(chat, "__file__", str(hub / "api/monkeyhub_api/chat/store.py")), \
                    patch.object(chat.shutil, "which", return_value="node.exe"), \
                    patch.object(chat.importlib.util, "find_spec", return_value=object()) as sdk:
                self.assertIsNone(chat._codex_acp_command())
                adapter.parent.mkdir(parents=True)
                adapter.touch()
                sdk.return_value = None
                self.assertIsNone(chat._codex_acp_command())


# A Claude-shaped CLI that keeps reading stream-json user messages while its
# turn runs, as the installed CLI does with --input-format stream-json: a message
# read at the next step is echoed back with isReplay, and after its result the CLI
# answers what it already read and exits when stdin closes (#301).
STEER_CLI = r'''
import json, sys, time
from pathlib import Path
sys.stdin.reconfigure(encoding="utf-8")
log_path, args = Path(sys.argv[1]), sys.argv[2:]
def log(**fields):
    with log_path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(fields) + "\n")
def emit(value):
    print(json.dumps(value), flush=True)
def read_user():
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("type") == "user":
            return message
    return None
if args[args.index("--input-format") + 1] != "stream-json" or "--replay-user-messages" not in args:
    sys.exit(2)
first = read_user()
prompt = first["message"]["content"][0]["text"]
native = args[args.index("--resume" if "--resume" in args else "--session-id") + 1]
log(event="turn", args=args, prompt=prompt)
emit({"type": "system", "subtype": "init", "session_id": native})
def tool(result=None):
    if result is None:
        emit({"type": "assistant", "session_id": native, "message": {"content": [
            {"type": "tool_use", "id": "toolu_before", "name": "mcp__monkeyhub__studio_request",
             "input": {"method": "GET", "path": "/api/jobs/job-1"}}]}})
    else:
        emit({"type": "user", "session_id": native, "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_before", "content": [{"type": "text", "text": json.dumps(result)}]}]}})
heard = []
if "steer-wait" in prompt or "steer-hold" in prompt:
    expected = 2 if "steer-wait-2" in prompt else 1
    tool()
    while len(heard) < expected:
        message = read_user()
        if message is None:
            break
        text = message["message"]["content"][0]["text"]
        log(event="stdin", uuid=message.get("uuid"), text=text)
        if "steer-hold" in prompt:
            continue
        emit({"type": "user", "isReplay": True, "uuid": message.get("uuid"), "session_id": native,
              "message": message["message"]})
        if not heard:
            # The call opened before the interjection finishes after it was read.
            tool({"status": "succeeded", "candidateId": "cand-before", "jobId": "job-1"})
        heard.append(text)
    answer = "heard: " + " | ".join(heard)
    emit({"type": "assistant", "session_id": native, "message": {"content": [{"type": "text", "text": answer}]}})
    emit({"type": "result", "session_id": native, "result": answer, "is_error": False})
else:
    emit({"type": "result", "session_id": native, "result": "answer: " + prompt.rsplit("\n\n", 1)[-1], "is_error": False})
rest = read_user()
log(event="closed", after=None if rest is None else rest.get("uuid"))
if "steer-late" in prompt:
    time.sleep(3)
'''


class InterjectionTests(unittest.TestCase):
    """#301: a message sent while a turn runs reaches it, or becomes the next prompt."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub 插话 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        FilesystemProjectRepository.initialize(
            self.project, project_id="chat-project", initial_state={"project_id": "chat-project", "version": 0},
        )
        environment = patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "CHAT_TEST_SECRET": "fake-private-token-12345",
        })
        environment.start()
        self.addCleanup(environment.stop)
        (self.root / "codex.py").write_text(FAKE_CLI, encoding="utf-8")
        (self.root / "claude.py").write_text(STEER_CLI, encoding="utf-8")
        self.codex_log, self.claude_log = self.root / "codex.jsonl", self.root / "claude.jsonl"
        self.store = chat.ChatStore(self.root / "runtime", "http://127.0.0.1:8790", commands={
            "codex": (sys.executable, str(self.root / "codex.py"), str(self.codex_log)),
            "claude": (sys.executable, str(self.root / "claude.py"), str(self.claude_log)),
        })
        self.addCleanup(self.close_store)

    def close_store(self):
        for row in self.store.list():
            self.store.stop(row.id)
        self.store.shutdown()

    def start(self, provider, content):
        session = self.store.create(ChatCreateRequest(projectDir=str(self.project), provider=provider))
        self.say(session, content)
        return session

    def say(self, session, content):
        return self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content=content))

    def finished(self, session):
        return wait_for(lambda: self.store.get(session.id), lambda row: row.status != "running", timeout=90)

    def calls(self, path, event=None):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [row for row in rows if event is None or row.get("event") == event]

    def test_claude_interjections_reach_the_running_turn_in_order(self):
        session = self.start("claude", "steer-wait-2")
        wait_for(lambda: self.store.get(session.id), lambda row: any(m.role == "tool" for m in row.messages), timeout=60)
        first = self.say(session, "Make the courtyard square")
        # The message is in the conversation at once, marked, before the Agent has it.
        self.assertEqual([(m.content, m.interjection) for m in first.messages if m.role == "user"][-1],
                         ("Make the courtyard square", "pending"))
        self.assertEqual(first.status, "running")
        self.say(session, "and keep the porch")
        detail = self.finished(session)
        self.assertEqual((detail.status, detail.error), ("idle", None))
        users = [m for m in detail.messages if m.role == "user"]
        self.assertEqual([(m.content, m.interjection) for m in users], [
            ("steer-wait-2", None), ("Make the courtyard square", "delivered"), ("and keep the porch", "delivered")])
        # One process, no restart: both arrived on its stdin, in order, as themselves.
        self.assertEqual(len(self.calls(self.claude_log, "turn")), 1)
        self.assertEqual([(row["uuid"], row["text"]) for row in self.calls(self.claude_log, "stdin")],
                         [(users[1].id, users[1].content), (users[2].id, users[2].content)])
        # The call opened before the interjection keeps its one row and its candidate.
        calls = [m for m in detail.messages if m.role == "tool"]
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].id, calls[0].status, calls[0].candidateId),
                         (f"{users[0].id}:toolu_before", "complete", "cand-before"))
        answer = next(m for m in detail.messages if m.role == "assistant")
        self.assertEqual(answer.content, "heard: Make the courtyard square | and keep the porch")
        self.assertTrue(answer.id.startswith(users[2].id + ":"), "the answer follows the message it answers")
        saved = json.loads((self.root / "runtime" / "chats" / f"{session.id}.json").read_text(encoding="utf-8"))
        self.assertEqual([row.get("interjection") for row in saved["messages"] if row["role"] == "user"],
                         [None, "delivered", "delivered"])

    def test_claude_interjection_after_the_result_becomes_the_next_prompt(self):
        session = self.start("claude", "steer-late")
        wait_for(lambda: self.store.get(session.id),
                 lambda row: any(m.role == "assistant" and m.content == "answer: steer-late" for m in row.messages), timeout=60)
        running = self.store._running[session.id]
        wait_for(lambda: running.stdin, lambda channel: channel is None)
        late = self.say(session, "one more thing")
        self.assertEqual([m.interjection for m in late.messages if m.role == "user"], [None, "pending"])
        wait_for(lambda: self.calls(self.claude_log, "turn"), lambda rows: len(rows) == 2, timeout=60)
        detail = self.finished(session)
        self.assertEqual((detail.status, detail.error), ("idle", None))
        turns = self.calls(self.claude_log, "turn")
        native = self.store._sessions[session.id].nativeSessionId
        self.assertEqual(turns[1]["args"][turns[1]["args"].index("--resume") + 1], native)
        self.assertTrue(turns[1]["prompt"].endswith("\n\none more thing"))
        message = [m for m in detail.messages if m.role == "user"][-1]
        self.assertEqual(message.interjection, "delivered")
        answer = next(m for m in detail.messages if m.content == "answer: one more thing")
        self.assertTrue(answer.id.startswith(message.id + ":"))

    def test_stop_still_stops_a_turn_with_an_interjection_waiting(self):
        session = self.start("claude", "steer-hold")
        wait_for(lambda: self.store.get(session.id), lambda row: any(m.role == "tool" for m in row.messages), timeout=60)
        self.say(session, "change direction")
        wait_for(lambda: self.calls(self.claude_log, "stdin"), lambda rows: len(rows) == 1, timeout=60)
        self.store.stop(session.id)
        detail = self.finished(session)
        self.assertEqual((detail.status, detail.error.code), ("interrupted", "CHAT_STOPPED"))
        self.assertEqual([m.interjection for m in detail.messages if m.role == "user"], [None, "undelivered"])
        self.assertEqual([m.status for m in detail.messages if m.role == "tool"], ["interrupted"])
        time.sleep(0.5)
        self.assertEqual(len(self.calls(self.claude_log, "turn")), 1, "a stopped turn starts nothing after it")
        self.assertNotIn(session.id, self.store._running)

    def test_codex_cli_turn_is_stopped_and_continued_with_the_interjection(self):
        session = self.start("codex", "pause-test")
        wait_for(lambda: self.store.get(session.id), lambda row: any(m.content == "ready" for m in row.messages), timeout=60)
        posted = self.say(session, "switch to plan B")
        self.assertEqual([m.interjection for m in posted.messages if m.role == "user"], [None, "restarted"])
        detail = self.finished(session)
        self.assertEqual((detail.status, detail.error), ("idle", None))
        turns = self.calls(self.codex_log)
        self.assertEqual(len(turns), 2)
        native = self.store._sessions[session.id].nativeSessionId
        self.assertEqual(turns[1]["args"][turns[1]["args"].index("resume") + 1], native)
        self.assertTrue(turns[1]["prompt"].rstrip().endswith("switch to plan B"))
        self.assertIn("stopped your previous step", turns[1]["prompt"])
        # What the stopped step already said stays; the continuation answers after it.
        self.assertTrue(any(m.content == "ready" for m in detail.messages))
        self.assertEqual([m.interjection for m in detail.messages if m.role == "user"], [None, "restarted"])
        self.assertTrue(any(m.content == "response to the conversation" for m in detail.messages))

    def test_interjection_is_refused_only_for_files(self):
        session = self.start("claude", "steer-hold")
        wait_for(lambda: self.store.get(session.id), lambda row: any(m.role == "tool" for m in row.messages), timeout=60)
        with self.assertRaises(HubFailure) as refused:
            self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="see this",
                            attachments=[{"name": "a.txt", "mimeType": "text/plain", "data": "YQ=="}]))
        self.assertEqual(refused.exception.error.code, "CHAT_INTERJECTION_FILES")
        self.assertEqual([m.content for m in self.store.get(session.id).messages if m.role == "user"], ["steer-hold"])
        self.store.stop(session.id)


if __name__ == "__main__":
    unittest.main()
