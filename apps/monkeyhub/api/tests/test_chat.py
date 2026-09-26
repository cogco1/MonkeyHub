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
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.transport.settings import ApplicationSettingsDto
from monkeyhub_api import chat
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import (
    AppStatus, ChatCreateRequest, ChatDesignContext, ChatPostRequest, HubFailure,
)


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
for directory in (root, root / "apps/archflow-studio/api", root / "apps/monkeyhub/api"):
    sys.path.insert(0, str(directory))

from monkeyhub_api import chat
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
        exposed = ("studio_schema", "studio_request", "fab_request", "attachment_read", "chat_present",
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
        for name in ("studio_request", "studio_schema", "fab_request", "attachment_read"):
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
        self.assertIn("existing controls", call["prompt"])
        self.assertIn("dependencies for linked edits", call["prompt"])

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
        for path in ("/api/program", "/api/options", "/api/semantics", "/api/options/{id}/select"):
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
                       "GET /api/documents?runId=", "MonkeyDiagram's documents list",
                        "/api/document-annotations", "baseRevisionSha256",
                        "GET /api/drawings/styles", "POST /api/drawings/sheets",
                        "/api/proposals/elevation", "POST /api/drawings/section-perspectives", "剖透视",
                        "keep: 'left'|'right'", "POST /api/board/export",
                        # Entourage on a cut plan (#244): the typed edit, its
                        # symbols and the reads that show where each one landed.
                        "POST /api/drawings/plans", "previousRevisionRef", "dressingOperations",
                        "'person-plan'|'tree-plan'", "GET /api/drawings/plans/vector",
                        "POST /api/drawings/plans/status", "GET /api/drawings/plans/dimensions"):
            self.assertIn(stated, request_tool["description"], stated)
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
                                   "/api/documents": {"get": {"summary": "list drawings"}},
                                   "/api/proposals/sketch": {"post": {"summary": "draw"}},
                                   "/api/proposals/elevation": {"post": {"summary": "edit elevation"}},
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
            elevation_body = {"stateDigest": "a" * 64, "sourceRunId": "candidate-before",
                              "sourceStageRef": "stage-base", "sourceProposalId": "proposal-before",
                              "elementId": "drawn-1", "action": "set-base", "value": 2,
                              "keep": ["entity:porch"]}
            raised = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/proposals/elevation", "body": elevation_body,
            })
            self.assertEqual((raised["path"], raised["body"]), ("/api/proposals/elevation", elevation_body))
            with self.assertRaises(HubFailure) as wrong_elevation_project:
                chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                    "method": "POST", "path": "/api/proposals/elevation",
                    "body": {**elevation_body, "projectId": "other"},
                })
            self.assertEqual(wrong_elevation_project.exception.error.code, "CHAT_PROJECT_MISMATCH")
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
            # drawing write and forwarded as asked; the reads go straight to
            # the Studio, and a refused batch comes back once, in its own words.
            placing = {"projectId": session.projectId, "sourceStageRef": "stage-base", "drawingId": "room-plan",
                       "previousRevisionRef": "retained-plan-1", "dressingOperations": [
                           {"op": "insert", "id": name, "object": {"id": name, "assetId": asset, "positionUv": [u, 1],
                                                                   "size": 0.6}}
                           for name, asset, u in (("person-a", "person-plan", 1), ("person-b", "person-plan", 2),
                                                  ("tree-a", "tree-plan", 3))]}
            placed = chat.call_tool(self.store.hub_url, session.id, "studio_request", {
                "method": "POST", "path": "/api/drawings/plans", "body": placing})
            self.assertEqual((placed["path"], placed["body"]), ("/api/drawings/plans", placing))
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
            self.assertEqual(plans, [placing, refused_batch], "each batch is sent once, and a refusal is not retried")
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
            for method, path in (("POST", "/api/documents"), ("GET", "/api/documents/asset-1/bytes"),
                                 ("POST", "/api/drawings/plans/dimension-proposal")):
                for tool in ("studio_request", "studio_schema"):
                    with self.subTest(tool=tool, path=path), self.assertRaises(HubFailure) as refused:
                        chat.call_tool(self.store.hub_url, session.id, tool, {"method": method, "path": path})
                    self.assertEqual(refused.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
            # A documented template can be read as a schema, which is what an
            # exploring turn used to fail on.
            for template in ("/api/options/{option_id}/select", "/api/proposals/sketch", "/api/project/modeling",
                             "/api/proposals/elevation"):
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

    def test_existing_controls_and_candidate_continuation_are_discoverable(self):
        """One short pointer, and the bound path behind it — not a second hand-written contract."""

        session = self.create()
        session.status = "running"
        description = next(tool for tool in _tools_of(chat) if tool["name"] == "studio_request")["description"]
        for stated in ("GET /api/capabilities/candidate.modify_existing?target=",
                       "&elementId=<the element>", "GET /api/capabilities?goal=",
                       "POST /api/capabilities/{capabilityId}/run", "awaitSeconds: 60",
                       "keep is a list", "never send the request again",
                       "GET /api/state?run=<candidateId>", "original baseStateDigest",
                       "Multiple observation and revision cycles"):
            self.assertIn(stated, description, stated)
        for restriction in ("Never generate an intermediate", "READ ONCE", "do not read the index",
                            "yield_time_ms", "functions.wait"):
            self.assertNotIn(restriction, description)
        # The quick sketch and exact-source comparison remain discoverable.
        self.assertIn("/api/proposals/sketch", description)
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
        spec = importlib.util.spec_from_file_location("turn_benchmark", ROOT / "tests/monkeymonitor/run_turn_benchmark.py")
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
                return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": "http://127.0.0.1:8791/", "processId": 123}]
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
                    return [{"appId": "monkeyarch", "state": "running", "url": "http://127.0.0.1:8790/?view=arch", "apiUrl": address + "/", "processId": pid}]
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
                         {"studio_schema", "studio_request", "fab_request", "attachment_read", "chat_present",
                          "computer_inspect", "computer_action", "computer_record"})

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

    def studio(self, session, packs, refusal=None):
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
        from archflow_studio_api.main import create_app as studio_app
        from archflow_studio_api.settings import StudioSettings

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
             patch.object(chat, "build_opener") as opener, \
             patch.object(chat.sys, "stdin", Stream("\n".join(json.dumps(line) for line in lines) + "\n")), \
             patch.object(chat.sys, "stdout", writer):
            opener.return_value.open.side_effect = open_request
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
             patch.object(chat, "_request_json") as request:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(HubFailure):
                    chat.call_tool(self.store.hub_url, "chat", "studio_request", value)
            request.assert_not_called()
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
            with self.subTest(mime=mime, code=code), patch.object(chat, "build_opener") as opener:
                response = io.BytesIO(data)
                response.headers = {"Content-Type": mime}
                opener.return_value.open.return_value = response
                with self.assertRaises(HubFailure) as failure:
                    chat._request_json("http://127.0.0.1:8791", "/api/board/export", "POST", {}, png=True)
                self.assertEqual(failure.exception.error.code, code)

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
        from monkeyhub_api import acp_session as adapter

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
