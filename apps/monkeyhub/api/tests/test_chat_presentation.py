"""External presentation keeps exact results without starting a model provider."""

import base64
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient
from PIL import Image
import uvicorn

from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application.artifacts import save_document
from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.settings import StudioSettings
from monkeyhub_api import chat
from monkeyhub_api.chat import ChatStore
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import ChatCreateRequest, ChatDocumentRef, ChatPostRequest, ChatProvider


GATED_CLI = r'''
import json, sys, time
from pathlib import Path
from uuid import uuid4
provider, gate = sys.argv[1], Path(sys.argv[2])
if "mcp" in sys.argv and "list" in sys.argv:
    print("[]", flush=True)
    sys.exit(0)
sys.stdin.read()
native = str(uuid4())
def emit(value):
    print(json.dumps(value), flush=True)
if provider == "codex":
    emit({"type": "thread.started", "thread_id": native})
    emit({"type": "item.completed", "item": {"type": "agent_message", "id": "before", "text": "Before media gate"}})
else:
    emit({"type": "system", "session_id": native})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Before media gate"}]}})
deadline = time.monotonic() + 20
while not gate.is_file():
    if time.monotonic() >= deadline:
        raise RuntimeError("Media gate was not released")
    time.sleep(0.02)
if provider == "codex":
    emit({"type": "item.completed", "item": {"type": "agent_message", "id": "after", "text": "Native text after media"}})
    emit({"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "late-tool", "server": "monkeyhub",
        "tool": "attachment_read", "arguments": {"attachmentId": "fixture"}, "status": "completed",
        "result": {"content": [{"type": "text", "text": "Late tool completed"}]}, "error": None}})
    emit({"type": "turn.completed"})
else:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Native text after media"},
        {"type": "tool_use", "id": "late-tool", "name": "mcp__monkeyhub__attachment_read", "input": {"attachmentId": "fixture"}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "late-tool",
        "is_error": False, "content": "Late tool completed"}]}})
    emit({"type": "result", "session_id": native, "result": "Native text after media", "is_error": False})
'''


class ChatPresentationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="monkeyhub-presentation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.project = self.root / "project"
        self.repository = FilesystemProjectRepository.initialize(
            self.project, project_id="presentation-project",
            initial_state={"project_id": "presentation-project", "version": 0},
        )
        self.store = self.new_store()
        self.client = self.new_client(self.store)
        self.bound = self.bind()
        self.turn = str(uuid4())
        self.user_id = str(uuid4())
        self.assistant_id = str(uuid4())
        self.image = BytesIO()
        Image.new("RGB", (8, 6), "blue").save(self.image, format="PNG")

    def new_store(self):
        store = ChatStore(self.runtime, "http://127.0.0.1:8790", commands={})
        self.addCleanup(store.shutdown)
        return store

    def new_client(self, store, host="127.0.0.1"):
        with patch("monkeyhub_api.main.ChatStore", return_value=store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        # No lifespan is needed: these operations must not start a runtime or provider.
        client = TestClient(app, base_url="http://127.0.0.1:8790", client=(host, 42000))
        self.addCleanup(client.close)
        return client

    def bind(self, *, client=None, expected=200, **changes):
        response = (client or self.client).post("/api/chat/presentation/bind", json={
            "projectDir": str(self.project), "sourceSessionId": "codex-source-session",
            "provider": "codex", "title": "Entrance study", **changes,
        })
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def payload(self, kind="assistant", **changes):
        return {"projectId": self.bound["projectId"], "sourceSessionId": self.bound["sourceSessionId"],
                "turnId": self.turn, "messageId": self.user_id if kind == "user" else self.assistant_id,
                "revision": 0, "kind": kind, "content": "Study the entrance" if kind == "user" else "Entrance candidate ready",
                "status": "complete", **changes}

    def present(self, kind="assistant", *, client=None, token=None, expected=200, **changes):
        response = (client or self.client).post(
            f"/api/chat/sessions/{self.bound['chatId']}/presentation",
            headers={"Authorization": f"Bearer {self.bound['token'] if token is None else token}"},
            json=self.payload(kind, **changes),
        )
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def saved(self):
        return json.loads((self.runtime / "chats" / f"{self.bound['chatId']}.json").read_text(encoding="utf-8"))

    def image_upload(self):
        return {"name": "入口草图.png", "mimeType": "image/png",
                "data": base64.b64encode(self.image.getvalue()).decode("ascii")}

    @contextmanager
    def live_server(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime, port=port))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               lifespan="off", log_level="error", ws="none"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        previous_url = self.store.hub_url
        self.store.hub_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started, "Loopback Hub did not start")
            yield self.store.hub_url
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            self.store.hub_url = previous_url
            self.assertFalse(thread.is_alive(), "Loopback Hub did not stop")

    def mcp(self, requests, *, connection=None):
        connection = connection or {"command": sys.executable, "args": [str(Path(chat.__file__)), "--mcp",
            "--hub-url", self.store.hub_url, "--project-dir", str(self.project),
            "--source-session-id", self.bound["sourceSessionId"]]}
        environment = {**os.environ, "PYTHONUTF8": "1", **connection.get("env", {})}
        result = subprocess.run([connection["command"], *connection["args"]],
            input="".join(json.dumps(row) + "\n" for row in requests), capture_output=True,
            text=True, encoding="utf-8", timeout=20, cwd=self.root, env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), len(requests), result.stdout)
        for reply in replies:
            self.assertNotIn("error", reply, reply)
            self.assertFalse(reply["result"].get("isError"), reply)
        for token in self.store._presentation_tokens.values():
            self.assertNotIn(token, result.stdout)
            self.assertNotIn(token, result.stderr)
        return replies

    @staticmethod
    def tool_call(identifier, name, arguments):
        return {"jsonrpc": "2.0", "id": identifier, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}

    def wait_chat(self, session_id, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = self.store.get(session_id)
            if predicate(value):
                return value
            time.sleep(0.02)
        self.fail(f"Chat did not reach the expected condition: {self.store.get(session_id).model_dump()}")

    def test_rebinding_reuses_exact_project_and_source_without_provider(self):
        before = {str(path.relative_to(self.project)): path.read_bytes()
                  for path in self.project.rglob("*") if path.is_file()}
        with patch.object(self.store, "providers", side_effect=AssertionError("No provider lookup")), \
                patch("monkeyhub_api.chat.subprocess.Popen", side_effect=AssertionError("No provider invocation")):
            rebound = self.bind()
            self.assertEqual(rebound, self.bound)
            self.present("user")
            self.present()
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.saved()["sourceSessionId"], self.bound["sourceSessionId"])
        self.assertNotIn(self.bound["token"], json.dumps(self.saved()))
        self.assertEqual(before, {str(path.relative_to(self.project)): path.read_bytes()
                                  for path in self.project.rglob("*") if path.is_file()})
        other = self.root / "other"
        FilesystemProjectRepository.initialize(other, project_id="other-project",
            initial_state={"project_id": "other-project", "version": 0})
        separate = self.bind(projectDir=str(other))
        self.assertNotEqual(separate["chatId"], self.bound["chatId"])
        self.assertEqual(separate["projectId"], "other-project")

    def test_two_turns_preserve_order_stable_ids_and_revision_updates(self):
        user = self.present("user")
        self.assertEqual(user["status"], "running")
        first = self.present(status="streaming", content="First paragraph")
        first_time = next(row["createdAt"] for row in first["messages"] if row["id"] == self.assistant_id)
        completed = self.present(revision=1, content="First paragraph\n\nFinal result")
        self.assertEqual(completed["status"], "idle")
        self.assertEqual(completed["messages"][-1]["createdAt"], first_time)
        self.assertEqual(completed["messages"][-1]["presentationRevision"], 1)
        first_ids = [self.user_id, self.assistant_id]
        first_turn = self.turn
        self.turn, self.user_id, self.assistant_id = str(uuid4()), str(uuid4()), str(uuid4())
        self.present("user", content="Keep the wall and revise the canopy")
        final = self.present(content="Canopy revision ready")
        self.assertEqual([row["id"] for row in final["messages"]], first_ids + [self.user_id, self.assistant_id])
        self.assertEqual([row["sourceTurnId"] for row in final["messages"]], [first_turn, first_turn, self.turn, self.turn])
        self.assertEqual([row["role"] for row in final["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(self.new_store().get(self.bound["chatId"]).model_dump(), final)

    def test_progress_is_transient_and_next_turn_clears_it(self):
        self.present("user")
        progress_id = str(uuid4())
        first = self.present("progress", messageId=progress_id, content="Checking the original", status="streaming")
        first_row = next(row for row in first["messages"] if row["role"] == "tool")
        second = self.present("progress", messageId=progress_id, revision=1,
                              content="Preparing the preview", status="streaming")
        rows = [row for row in second["messages"] if row["role"] == "tool"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], first_row["id"])
        self.assertEqual(rows[0]["content"], "Preparing the preview")
        self.assertNotIn("Preparing the preview", json.dumps(self.saved()))
        self.assertEqual([row["role"] for row in self.saved()["messages"]], ["user"])
        self.present()
        self.assertFalse(any(row.role == "tool" for row in self.new_store().get(self.bound["chatId"]).messages))
        self.turn, self.user_id = str(uuid4()), str(uuid4())
        next_turn = self.present("user", content="Next revision")
        self.assertFalse(any(row["role"] == "tool" for row in next_turn["messages"]))

    def test_image_survives_replay_and_reopen_for_inline_and_download(self):
        self.present("user")
        upload = self.image_upload()
        published = self.present(content="", attachments=[upload])
        replay = self.present(content="", attachments=[upload])
        self.assertEqual(replay, published)
        self.assertEqual(len(self.saved()["messages"]), 2)
        image = published["messages"][-1]["attachments"][0]
        reopened = self.new_store()
        client = self.new_client(reopened)
        prefix = f"/api/chat/sessions/{self.bound['chatId']}/attachments/{image['id']}"
        inline, download = client.get(prefix + "?inline=true"), client.get(prefix)
        self.assertEqual(inline.status_code, 200, inline.text)
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(inline.content, self.image.getvalue())
        self.assertEqual(download.content, self.image.getvalue())
        self.assertEqual(inline.headers["content-type"], "image/png")
        self.assertTrue(inline.headers["content-disposition"].startswith("inline;"))
        self.assertTrue(download.headers["content-disposition"].startswith("attachment;"))
        self.assertEqual(inline.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.present(client=client, content="", attachments=[upload], expected=403)["code"],
                         "CHAT_PRESENTATION_BIND_REQUIRED")
        rebound = self.bind(client=client)
        self.assertEqual(rebound["chatId"], self.bound["chatId"])
        self.assertNotEqual(rebound["token"], self.bound["token"])
        self.assertEqual(self.present(client=client, token=rebound["token"], content="", attachments=[upload]), published)

    def test_wrong_binding_or_revision_cannot_change_saved_result(self):
        for changes in ({"chatId": self.bound["chatId"], "sourceSessionId": "another-source"},
                        {"chatId": self.bound["chatId"], "provider": "claude"}):
            self.assertEqual(self.bind(expected=409, **changes)["code"], "CHAT_PRESENTATION_MISMATCH")
        self.assertEqual(self.present("user", token="wrong", expected=403)["code"], "CHAT_PRESENTATION_BIND_REQUIRED")
        for changes in ({"projectId": "another-project"}, {"sourceSessionId": "another-source"}):
            self.assertEqual(self.present("user", expected=409, **changes)["code"], "CHAT_PRESENTATION_MISMATCH")
        self.present("user")
        self.present(status="streaming", revision=2, content="One stable snapshot")
        expected = self.saved()
        self.assertEqual(self.present(status="streaming", revision=1, expected=409)["code"], "CHAT_PRESENTATION_STALE")
        self.assertEqual(self.present(status="streaming", revision=2, content="Different snapshot", expected=409)["code"],
                         "CHAT_PRESENTATION_CONFLICT")
        self.assertEqual(self.present(status="streaming", revision=3, turnId=str(uuid4()), expected=409)["code"],
                         "CHAT_PRESENTATION_CONFLICT")
        self.assertEqual(self.saved(), expected)
        complete = self.present(revision=3)
        self.assertEqual(self.present(revision=4, content="Overwrite finished result", expected=409)["code"],
                         "CHAT_PRESENTATION_COMPLETE")
        self.assertEqual(self.store.get(self.bound["chatId"]).model_dump(), complete)

    def test_external_session_cannot_start_native_provider(self):
        with patch.object(self.store, "providers", side_effect=AssertionError("No provider lookup")), \
                patch("monkeyhub_api.chat.subprocess.Popen", side_effect=AssertionError("No provider invocation")):
            response = self.client.post(f"/api/chat/sessions/{self.bound['chatId']}/messages", json={
                "projectId": self.bound["projectId"], "content": "Run a new native turn",
            })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "CHAT_EXTERNAL_SOURCE")
        self.assertEqual(self.saved()["messages"], [])

    def test_stop_interrupts_external_turn_and_refuses_late_result(self):
        self.present("user")
        self.present(status="streaming", content="Working")
        stopped = self.client.post(f"/api/chat/sessions/{self.bound['chatId']}/stop")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["status"], "interrupted")
        self.assertEqual(stopped.json()["messages"][-1]["status"], "interrupted")
        self.bind()
        self.assertEqual(self.store.get(self.bound["chatId"]).status, "interrupted")
        self.assertEqual(self.present(revision=1, expected=409)["code"], "CHAT_PRESENTATION_COMPLETE")
        self.assertEqual(self.present(messageId=str(uuid4()), expected=409)["code"], "CHAT_PRESENTATION_TURN_CLOSED")
        self.assertEqual(self.store.get(self.bound["chatId"]).model_dump(), stopped.json())

    def test_registered_document_keeps_exact_reference_and_revalidates_bytes(self):
        binding = ProjectBinding(FilesystemProjectRepository.open(self.project), project_id=self.bound["projectId"],
            project_dir=self.project, settings=StudioSettings(project_dir=self.project, cad_export="off"))
        document = save_document(binding, None, "Registered entrance.png", "image/png", self.image_upload()["data"])
        reference = {"runId": document.run_id, "assetSha256": document.asset_sha256, "revisionRef": None, "pageIndex": 0}
        self.present("user")
        for changes, code in (({"revisionRef": "unknown-revision"}, "CHAT_DOCUMENT_MISMATCH"),
                              ({"pageIndex": 1}, "CHAT_DOCUMENT_MISMATCH"),
                              ({"assetSha256": "a" * 64}, "CHAT_DOCUMENT_MISMATCH")):
            response = self.present(documents=[{**reference, **changes}], expected=409)
            self.assertEqual(response["code"], code)
        self.assertEqual(len(self.saved()["messages"]), 1)
        result = self.present(content="Registered source", documents=[reference])
        self.assertEqual(result["messages"][-1]["documents"], [{**reference, "fileName": document.file_name, "mimeType": "image/png"}])
        client = self.new_client(self.new_store())
        prefix = f"/api/chat/sessions/{self.bound['chatId']}/documents/{self.assistant_id}/0"
        inline, download = client.get(prefix), client.get(prefix + "?download=true")
        self.assertEqual(inline.status_code, 200, inline.text)
        self.assertEqual(inline.content, self.image.getvalue())
        self.assertEqual(download.content, self.image.getvalue())
        self.assertEqual(inline.headers["etag"], f'"{document.asset_sha256}"')
        self.assertTrue(inline.headers["content-disposition"].startswith("inline;"))
        self.assertTrue(download.headers["content-disposition"].startswith("attachment;"))
        absent = client.get(prefix.removesuffix("/0") + "/1")
        self.assertEqual(absent.status_code, 404, absent.text)
        self.assertEqual(absent.json()["code"], "CHAT_DOCUMENT_NOT_FOUND")
        path = binding.repository.layout.resolve_relative(f"objects/sha256/{document.asset_sha256[:2]}/{document.asset_sha256}")
        path.write_bytes(b"modified outside document registration")
        changed = client.get(prefix)
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(changed.json()["code"], "DOCUMENT_DIGEST_MISMATCH")

    def test_null_revision_selects_original_metadata_despite_legacy_byte_wildcard(self):
        binding = ProjectBinding(FilesystemProjectRepository.open(self.project), project_id=self.bound["projectId"],
            project_dir=self.project, settings=StudioSettings(project_dir=self.project, cad_export="off"))
        original = save_document(binding, None, "Original source.png", "image/png", self.image_upload()["data"])
        generated = replace(original, file_name="Generated drawing.png", revision_ref="retained-generated-revision",
                            drawing_id="drawing-1", generated_at="2026-09-20T10:00:00Z")
        reference = ChatDocumentRef(runId=original.run_id, assetSha256=original.asset_sha256,
                                    revisionRef=None, pageIndex=0)
        self.present("user")
        # The document owner has separate registrations for the same image bytes.
        # Its retained legacy API may answer null with the first generated row;
        # the presentation must still keep the explicitly selected original identity.
        with patch("archflow_studio_api.application.artifacts.list_documents", return_value=(generated, original)), \
                patch("archflow_studio_api.application.artifacts.document_bytes", return_value=(generated, self.image.getvalue())) as read:
            resolved, data = self.store._document(self.store._sessions[self.bound["chatId"]], reference)
            self.assertEqual((resolved.file_name, resolved.revision_ref, resolved.pages),
                             (original.file_name, None, original.pages))
            self.assertEqual(data, self.image.getvalue())
            self.assertEqual(read.call_args.args[1:], (original.run_id, original.asset_sha256, None))
            result = self.present(documents=[reference.model_dump()])
            shown = result["messages"][-1]["documents"][0]
            self.assertEqual((shown["fileName"], shown["revisionRef"], shown["pageIndex"]),
                             (original.file_name, None, 0))
            response = self.client.get(f"/api/chat/sessions/{self.bound['chatId']}/documents/{self.assistant_id}/0")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, self.image.getvalue())
            self.assertIn("Original%20source.png", response.headers["content-disposition"])
            self.assertNotIn("Generated", response.headers["content-disposition"])

    def test_presentation_mutations_require_loopback_client(self):
        remote = self.new_client(self.store, host="192.0.2.10")
        self.assertEqual(self.bind(client=remote, expected=403)["code"], "LOCAL_PRESENTER_REQUIRED")
        self.assertEqual(self.present("user", client=remote, expected=403)["code"], "LOCAL_PRESENTER_REQUIRED")
        self.assertEqual(self.saved()["messages"], [])

    def test_native_cli_events_after_mcp_media_keep_both_results(self):
        fake = self.root / "gated_cli.py"
        fake.write_text(GATED_CLI, encoding="utf-8")
        image_path = self.root / "native-preview.png"
        image_path.write_bytes(self.image.getvalue())
        with self.live_server():
            for provider in ("codex", "claude"):
                with self.subTest(provider=provider):
                    gate = self.root / f"{provider}-gate"
                    self.store.commands = {provider: (sys.executable, str(fake), provider, str(gate))}
                    available = ChatProvider(id=provider, label=provider, available=True, installed=True, detail="Fixture CLI")
                    with patch.object(self.store, "providers", return_value=[available]):
                        session = self.store.create(ChatCreateRequest(projectDir=str(self.project), provider=provider))
                        self.store.post(session.id, ChatPostRequest(projectId=session.projectId, content="Show the preview"))
                    try:
                        self.wait_chat(session.id, lambda row: any(message.content == "Before media gate" for message in row.messages))
                        connection = self.store._tool_connection(self.store._sessions[session.id])
                        command, environment = self.store._command(self.store._sessions[session.id])
                        self.assertIn("MONKEYHUB_PRESENTATION_TOKEN", environment)
                        token = environment["MONKEYHUB_PRESENTATION_TOKEN"]
                        self.assertNotIn(token, json.dumps(connection))
                        self.assertNotIn(token, " ".join(command))
                        connection = {**connection, "env": environment}
                        media_id = str(uuid4())
                        replies = self.mcp([self.tool_call(1, "chat_present", {"messageId": media_id,
                            "kind": "assistant", "status": "streaming", "content": "Preview from native tool",
                            "attachments": [{"path": str(image_path)}]})], connection=connection)
                        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["status"], "running")
                        media = next(row for row in self.store.get(session.id).messages if row.id == media_id)
                        self.assertEqual(len(media.attachments), 1)
                    finally:
                        gate.touch()
                    finished = self.wait_chat(session.id, lambda row: row.status != "running")
                    self.assertEqual(finished.status, "idle", finished.error)
                    self.assertTrue(any(row.content == "Native text after media" for row in finished.messages))
                    self.assertTrue(any(row.id.endswith(":late-tool") and row.status == "complete" for row in finished.messages))
                    media = next(row for row in finished.messages if row.id == media_id)
                    self.assertEqual(media.status, "complete")
                    self.assertEqual(self.store.attachment(session.id, media.attachments[0].id)[1].read_bytes(), self.image.getvalue())
                    reopened = self.new_store().get(session.id)
                    self.assertEqual(reopened.model_dump(), finished.model_dump())

    def test_external_mcp_live_protocol_two_turns_path_image_and_rebind(self):
        image_path = self.root / "external-preview.png"
        image_path.write_bytes(self.image.getvalue())
        second_turn, second_user, second_assistant = str(uuid4()), str(uuid4()), str(uuid4())
        with self.live_server():
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                self.tool_call(3, "presentation_bind", {}),
                self.tool_call(4, "chat_present", {"turnId": self.turn, "messageId": self.user_id, "kind": "user", "content": "First external request"}),
                self.tool_call(5, "chat_present", {"turnId": self.turn, "messageId": str(uuid4()), "kind": "progress", "content": "Preparing external image"}),
                self.tool_call(6, "chat_present", {"turnId": self.turn, "messageId": self.assistant_id, "kind": "assistant",
                    "content": "External image ready", "attachments": [{"path": str(image_path)}]}),
                self.tool_call(7, "chat_present", {"turnId": second_turn, "messageId": second_user, "kind": "user", "content": "Second external request"}),
                self.tool_call(8, "chat_present", {"turnId": second_turn, "messageId": second_assistant, "kind": "assistant", "content": "Second external response"}),
            ]
            replies = self.mcp(requests)
            self.assertEqual(replies[0]["result"]["protocolVersion"], "2024-11-05")
            names = {tool["name"] for tool in replies[1]["result"]["tools"]}
            self.assertTrue({"presentation_bind", "chat_present"}.issubset(names))
            bound = json.loads(replies[2]["result"]["content"][0]["text"])
            self.assertEqual(bound["chatId"], self.bound["chatId"])
            self.assertNotIn("token", bound)
            self.assertIn("instructions", bound)
            rebound = self.mcp([self.tool_call(1, "presentation_bind", {}),
                               self.tool_call(2, "chat_present", requests[-1]["params"]["arguments"])])
            self.assertEqual(json.loads(rebound[0]["result"]["content"][0]["text"])["chatId"], self.bound["chatId"])
            final = self.store.get(self.bound["chatId"])
            self.assertEqual(final.status, "idle")
            self.assertEqual([row.id for row in final.messages], [self.user_id, self.assistant_id, second_user, second_assistant])
            self.assertEqual(len(self.store.list()), 1)
            attachment = final.messages[1].attachments[0]
            image_path.write_bytes(b"local source changed after publication")
            response = self.client.get(f"/api/chat/sessions/{final.id}/attachments/{attachment.id}?inline=true")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, self.image.getvalue())
            self.assertNotIn(self.bound["token"], json.dumps(self.saved()))
            self.assertEqual(self.new_store().get(final.id).model_dump(), final.model_dump())

    def test_external_rebind_after_restart_resumes_stream_then_final_settles_media(self):
        self.present("user")
        upload = self.image_upload()
        self.present(status="streaming", content="First preview", attachments=[upload])
        reopened = self.new_store()
        client = self.new_client(reopened)
        interrupted = reopened.get(self.bound["chatId"])
        self.assertEqual(interrupted.status, "interrupted")
        self.assertEqual(interrupted.error.code, "CHAT_INTERRUPTED")
        self.assertEqual(interrupted.messages[-1].status, "streaming")
        rebound = self.bind(client=client)
        token = rebound["token"]
        self.assertEqual(reopened.get(self.bound["chatId"]).status, "running")
        replay = self.present(client=client, token=token, status="streaming", content="First preview", attachments=[upload])
        self.assertEqual(len(replay["messages"]), 2)
        self.present(client=client, token=token, revision=1, status="streaming", content="Revised preview", attachments=[upload])
        final = self.present(client=client, token=token, messageId=str(uuid4()), content="Final native-sized result")
        self.assertEqual(final["status"], "idle")
        media = next(row for row in final["messages"] if row["id"] == self.assistant_id)
        self.assertEqual(media["status"], "complete")
        self.assertEqual(len(media["attachments"]), 1)
        self.assertEqual(self.present(client=client, token=token, revision=1, status="streaming",
                                     content="Revised preview", attachments=[upload]), final)
        self.assertEqual(self.present(client=client, token=token, revision=1, status="streaming",
                                     content="Changed replay", attachments=[upload], expected=409)["code"],
                         "CHAT_PRESENTATION_CONFLICT")
        self.assertEqual(self.present(client=client, token=token, revision=2, status="streaming",
                                     content="Revised preview", attachments=[upload], expected=409)["code"],
                         "CHAT_PRESENTATION_COMPLETE")
        self.assertEqual(self.new_store().get(self.bound["chatId"]).model_dump(), final)


if __name__ == "__main__":
    unittest.main()
