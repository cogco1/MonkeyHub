"""Real SDK stdio conversations with a local fake agent; no model or CAD calls."""

from concurrent.futures import Future, ThreadPoolExecutor
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from monkeyhub_api.chat.acp_session import AcpCancelled, AcpSessionError, CodexAcpSession


PNG_IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"


FAKE_AGENT = r'''
import asyncio, json, os, subprocess, sys
from pathlib import Path
from acp import PROTOCOL_VERSION, RequestError, run_agent
from acp.schema import (
    AgentCapabilities, AgentMessageChunk, InitializeResponse, LoadSessionResponse,
    NewSessionResponse, ListSessionsResponse, SessionInfo, PermissionOption, PromptCapabilities, PromptResponse, SetSessionConfigOptionResponse,
    SessionConfigOptionSelect, TextContentBlock, ImageContentBlock, UserMessageChunk,
    ToolCallProgress, ToolCallStart, ToolCallUpdate, UsageUpdate,
)

root = Path(os.environ["ACP_FIXTURE_ROOT"])
state_path = root / "state.json"

def log(event, **fields):
    with (root / "calls.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"event": event, "pid": os.getpid(), **fields}) + "\n")

class FakeAgent:
    def __init__(self):
        self.session_id = "fixture/session:not-a-uuid"
        self.model = "default-model"
        self.cancelled = asyncio.Event()
        if os.environ.get("ACP_STALL_EXIT") == "1":
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
            )
            log("child", childPid=child.pid)

    def on_connect(self, client):
        self.client = client

    def options(self):
        return [SessionConfigOptionSelect(
            id="model-choice", name="Model", category="model", type="select",
            current_value=self.model,
            options=[{"group": "models", "name": "Models", "options": [
                {"value": value, "name": value}
                for value in ("default-model", "fast-model", "quality-model")
            ]}],
        )]

    def save(self):
        state_path.write_text(json.dumps({"sessionId": self.session_id, "model": self.model}), encoding="utf-8")

    async def initialize(self, protocol_version, client_capabilities=None, **kwargs):
        log("initialize", capabilities=client_capabilities.model_dump(by_alias=True))
        capability = os.environ.get("ACP_IMAGE_CAPABILITY", "true")
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=os.environ.get("ACP_NO_LOAD") != "1",
                **({"prompt_capabilities": PromptCapabilities(image=capability == "true")}
                   if capability != "missing" else {}),
            ),
        )

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        log("new", cwd=cwd, mcp=[server.model_dump(by_alias=True) for server in mcp_servers])
        self.save()
        return NewSessionResponse(session_id=self.session_id, config_options=self.options())

    async def list_sessions(self, cwd=None, cursor=None, **kwargs):
        log("list", cwd=cwd, cursor=cursor)
        if os.environ.get("ACP_LIST_PAGES") == "1" and cursor is None:
            return ListSessionsResponse(sessions=[], next_cursor="page-2")
        return ListSessionsResponse(sessions=[SessionInfo(session_id=self.session_id, cwd=str(root))])

    async def load_session(self, cwd, session_id, mcp_servers=None, **kwargs):
        log("load", sessionId=session_id, cwd=cwd)
        if not state_path.exists() or session_id != json.loads(state_path.read_text())["sessionId"]:
            raise RequestError.invalid_params({"details": "Unknown fixture session"})
        if os.environ.get("ACP_WRITER_BUSY") == "1":
            raise RequestError.invalid_request("already has an active writer")
        await asyncio.sleep(float(os.environ.get("ACP_LOAD_DELAY", "0")))
        if os.environ.get("ACP_LOAD_ERROR") == "1":
            raise RequestError.invalid_request("Fixture load lost its native subscription")
        self.model = json.loads(state_path.read_text())["model"]
        case = os.environ.get("ACP_HISTORY_CASE")
        if case in {"foreign", "missing", "long-id", "raw-limit"}:
            await self.client.session_update(
                session_id="foreign-native-session" if case == "foreign" else self.session_id,
                update=AgentMessageChunk(session_update="agent_message_chunk",
                    message_id=None if case == "missing" else "x" * 501 if case == "long-id" else "case-message",
                    content=TextContentBlock(type="text", text="x" * 8_000_001 if case == "raw-limit" else "bad public chunk")),
            )
        if case == "role-reuse":
            await self.client.session_update(session_id=self.session_id,
                update=UserMessageChunk(session_update="user_message_chunk", message_id="old agent message",
                    content=TextContentBlock(type="text", text="wrong role")))
        if case == "ignored":
            await self.client.session_update(session_id=self.session_id,
                update=AgentMessageChunk(session_update="agent_message_chunk", message_id="media",
                    content=ImageContentBlock(type="image", mime_type="image/png", data="AA==")))
            await self.client.session_update(session_id=self.session_id,
                update=ToolCallStart(session_update="tool_call", tool_call_id="history-tool", title="Retained tool",
                    kind="read", status="completed", raw_output={"secret-like fixture": "not public history"}))
        for message in ("old user message", "old agent message"):
            await self.emit(message)
        return LoadSessionResponse(config_options=self.options())

    async def set_config_option(self, config_id, session_id, value, **kwargs):
        assert config_id == "model-choice"
        log("model", value=value)
        self.model = value
        self.save()
        return SetSessionConfigOptionResponse(config_options=self.options())

    async def emit(self, text):
        await self.client.session_update(
            session_id=self.session_id,
            update=AgentMessageChunk(session_update="agent_message_chunk", message_id=text, content=TextContentBlock(type="text", text=text)),
        )

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[0].text
        self.cancelled.clear()
        log("prompt", sessionId=session_id, text=text, model=self.model,
            blocks=[block.model_dump(by_alias=True, exclude_none=True) for block in prompt])
        if text == "crash":
            os._exit(17)
        if text == "error":
            raise RequestError.invalid_params({"details": "Fixture failure"})
        if text == "image-model-error" and any(block.type == "image" for block in prompt):
            raise RequestError.invalid_request("The current model does not support image input")
        if text == "stall":
            await self.emit("waiting")
            await self.cancelled.wait()
        elif text in {"active-message", "active-tool", "foreign-activity"}:
            for index in range(6):
                if text == "active-tool":
                    update = ToolCallProgress(session_update="tool_call_update", tool_call_id="active-tool",
                                              status="in_progress", raw_output={"step": index})
                else:
                    update = AgentMessageChunk(session_update="agent_message_chunk",
                                               content=TextContentBlock(type="text", text=f"step {index}"))
                await self.client.session_update(
                    session_id="another-session" if text == "foreign-activity" else session_id, update=update,
                )
                await asyncio.sleep(0.15)
        elif text in {"permission", "delayed-permission"}:
            if text == "delayed-permission":
                await asyncio.sleep(0.3)
            result = await self.client.request_permission(
                session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id="call-1", title="Fixture command", kind="execute"),
                options=[
                    PermissionOption(option_id="allow", name="Allow once", kind="allow_once"),
                    PermissionOption(option_id="reject", name="Reject once", kind="reject_once"),
                ],
            )
            outcome = result.outcome.model_dump(by_alias=True)
            log("permission", outcome=outcome)
            if text == "delayed-permission":
                await asyncio.sleep(0.3)
            await self.emit(json.dumps(outcome))
        else:
            await self.client.session_update(session_id=session_id, update=ToolCallStart(
                session_update="tool_call", tool_call_id="fixture-tool", title="Fixture read", kind="read",
                status="completed", raw_output={"value": text},
            ))
            await self.emit("answer: " + text)
        return PromptResponse(stop_reason="cancelled" if self.cancelled.is_set() else "end_turn")

    async def cancel(self, session_id, **kwargs):
        log("cancel", sessionId=session_id)
        if os.environ.get("ACP_IGNORE_CANCEL") != "1":
            self.cancelled.set()

async def main():
    log("start")
    try:
        await run_agent(FakeAgent())
    finally:
        if os.environ.get("ACP_STALL_EXIT") == "1":
            await asyncio.Event().wait()
        await asyncio.sleep(float(os.environ.get("ACP_EXIT_DELAY", "0")))
        log("exit")

asyncio.run(main())
'''


class AcpSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "fake_agent.py"
        self.script.write_text(FAKE_AGENT, encoding="utf-8")
        self.updates = []
        self.ids = []
        self.permission_requests = []
        self.permission_ready = threading.Event()
        self.waiting = threading.Event()
        self.permission = Future()
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
        self.temp.cleanup()

    def make_session(self, default_model=None, **env):
        session = CodexAcpSession(
            command=(sys.executable, "-u", str(self.script)), cwd=str(self.root),
            environment={**os.environ, "ACP_FIXTURE_ROOT": str(self.root), **env},
            mcp_servers=[{"name": "bound-project", "command": "fixture-unused", "args": [], "env": []}],
            on_update=self.on_update, on_permission=self.on_permission,
            default_model=default_model,
        )
        self.sessions.append(session)
        return session

    def on_update(self, event):
        self.updates.append(event)
        if event["update"].get("content", {}).get("text") == "waiting":
            self.waiting.set()

    def on_permission(self, request):
        self.permission_requests.append(request)
        self.permission_ready.set()
        return self.permission

    def prompt(self, session, text="hello", model=None, session_id=None, timeout_s=10, *, images=()):
        session.prompt(text, session_id, model, self.ids.append, timeout_s, images=images)

    def calls(self, event):
        path = self.root / "calls.jsonl"
        if not path.exists():
            return []
        return [value for line in path.read_text(encoding="utf-8").splitlines()
                if (value := json.loads(line))["event"] == event]

    def test_explicit_restore_lists_then_loads_without_prompt_and_returns_public_history(self):
        original = self.make_session()
        self.prompt(original)
        original.close()
        before = len(self.calls("prompt"))
        restored = self.make_session(ACP_LIST_PAGES="1")
        history = restored.restore(self.ids[0], str(self.root), 10)
        self.assertEqual(len(self.calls("prompt")), before)
        self.assertEqual([row["update"]["content"]["text"] for row in history], ["old user message", "old agent message"])
        self.assertEqual([row["cursor"] for row in self.calls("list")], [None, "page-2"])
        self.prompt(restored, "continued", session_id=self.ids[0])
        self.assertEqual(len(self.calls("new")), 1)
        self.assertEqual(len(self.calls("load")), 1)
        self.assertEqual(self.calls("prompt")[-1]["sessionId"], self.ids[0])

    def test_explicit_restore_refuses_unknown_or_wrong_cwd_without_load(self):
        for identifier, cwd in [("unknown", str(self.root)), ("fixture/session:not-a-uuid", str(self.root.parent))]:
            with self.subTest(identifier=identifier, cwd=cwd):
                session = self.make_session()
                with self.assertRaises(AcpSessionError):
                    session.restore(identifier, cwd, 10)
                session.close()
        self.assertEqual(self.calls("load"), [])
        self.assertEqual(self.calls("prompt"), [])
        self.assertEqual(self.calls("new"), [])

    def test_explicit_restore_preserves_native_writer_conflict_and_never_prompts(self):
        original = self.make_session()
        self.prompt(original)
        original.close()
        session = self.make_session(ACP_WRITER_BUSY="1")
        with self.assertRaisesRegex(AcpSessionError, "active writer"):
            session.restore(self.ids[0], str(self.root), 10)
        self.assertEqual(len(self.calls("prompt")), 1)


    def test_owned_idle_snapshot_uses_same_adapter_without_listing_or_prompt(self):
        session = self.make_session(ACP_HISTORY_CASE="ignored")
        self.prompt(session)
        session_id = self.ids[0]
        self.assertTrue(session.history_verified)
        process = session._process
        before_updates = list(self.updates)
        history = session.snapshot(session_id, 10)
        self.assertEqual([row["update"]["content"]["text"] for row in history], ["old user message", "old agent message"])
        self.assertEqual(self.updates, before_updates)
        self.assertIs(session._process, process)
        self.assertEqual(len(self.calls("start")), 1)
        self.assertEqual(len(self.calls("new")), 1)
        self.assertEqual(len(self.calls("prompt")), 1)
        self.assertEqual(self.calls("list"), [])
        self.assertEqual(self.calls("load")[0]["sessionId"], session_id)
        self.assertFalse(session.ownership_uncertain)

    def test_restored_history_requires_store_confirmation_before_snapshot(self):
        original = self.make_session()
        self.prompt(original)
        original.close()
        session_id = self.ids[0]
        restored = self.make_session()
        restored.restore(session_id, str(self.root), 10)
        self.assertFalse(restored.history_verified)
        loads = len(self.calls("load"))
        with self.assertRaisesRegex(AcpSessionError, "history-verified"):
            restored.snapshot(session_id, 10)
        self.assertEqual(len(self.calls("load")), loads)
        with self.assertRaisesRegex(AcpSessionError, "no restored public history"):
            restored.confirm_history("wrong-session")
        restored.confirm_history(session_id)
        self.assertTrue(restored.history_verified)
        with self.assertRaisesRegex(AcpSessionError, "same continuously owned"):
            restored.snapshot("wrong-session", 10)
        self.assertTrue(restored.history_verified)
        self.assertEqual(len(restored.snapshot(session_id, 10)), 2)

    def test_prompt_reconstruction_does_not_claim_verified_discarded_history(self):
        original = self.make_session()
        self.prompt(original)
        original.close()
        session_id = self.ids[0]
        reconstructed = self.make_session()
        self.prompt(reconstructed, "continued", session_id=session_id)
        self.assertFalse(reconstructed.history_verified)
        with self.assertRaisesRegex(AcpSessionError, "history-verified"):
            reconstructed.snapshot(session_id, 10)
        with self.assertRaisesRegex(AcpSessionError, "no restored public history"):
            reconstructed.confirm_history(session_id)
        self.assertEqual(len(self.calls("load")), 1)

    def test_snapshot_rejects_bad_public_identities_without_losing_old_ownership(self):
        for case, error in [("foreign", "different native session"), ("missing", "stable public message"),
                            ("long-id", "stable public message"), ("role-reuse", "different roles")]:
            with self.subTest(case=case):
                session = self.make_session(ACP_HISTORY_CASE=case)
                self.prompt(session)
                process = session._process
                with self.assertRaisesRegex(AcpSessionError, error):
                    session.snapshot(self.ids[-1], 10)
                self.assertTrue(session.history_verified)
                self.assertFalse(session.ownership_uncertain)
                self.assertIs(session._process, process)
                self.assertIsNone(process.returncode)
                session.close()

    def test_snapshot_chunk_limit_fails_whole_read_without_silent_truncation(self):
        session = self.make_session()
        self.prompt(session)
        session_id = self.ids[0]
        with patch("monkeyhub_api.chat.acp_session._MAX_HISTORY_CHUNKS", 2):
            self.assertEqual(len(session.snapshot(session_id, 10)), 2)
        with patch("monkeyhub_api.chat.acp_session._MAX_HISTORY_CHUNKS", 1):
            with self.assertRaisesRegex(AcpSessionError, "too large"):
                session.snapshot(session_id, 10)
        self.assertTrue(session.history_verified)
        self.assertFalse(session.ownership_uncertain)
        self.assertEqual(len(session.snapshot(session_id, 10)), 2)
        self.assertEqual(len(self.calls("start")), 1)
        self.assertEqual(len(self.calls("new")), 1)

    def test_snapshot_raw_character_limit_is_checked_before_projection(self):
        session = self.make_session(ACP_HISTORY_CASE="raw-limit")
        self.prompt(session)
        with self.assertRaisesRegex(AcpSessionError, "too large"):
            session.snapshot(self.ids[0], 10)
        self.assertTrue(session.history_verified)
        self.assertFalse(session.ownership_uncertain)

    def test_fresh_restore_collector_failure_keeps_owner_without_confirmation(self):
        original = self.make_session()
        self.prompt(original)
        original.close()
        restored = self.make_session(ACP_HISTORY_CASE="missing")
        with self.assertRaisesRegex(AcpSessionError, "stable public message"):
            restored.restore(self.ids[0], str(self.root), 10)
        self.assertIsNotNone(restored._process)
        self.assertIsNone(restored._process.returncode)
        self.assertFalse(restored.history_verified)
        self.assertFalse(restored.ownership_uncertain)
        with self.assertRaisesRegex(AcpSessionError, "no restored public history"):
            restored.confirm_history(self.ids[0])

    def test_load_rpc_failure_marks_uncertain_even_when_adapter_remains_alive(self):
        for environment, timeout, error in [({"ACP_LOAD_ERROR": "1"}, 10, "lost its native subscription"),
                                            ({"ACP_LOAD_DELAY": "1"}, 0.05, "in time")]:
            with self.subTest(environment=environment):
                session = self.make_session(**environment)
                self.prompt(session)
                session_id = self.ids[-1]
                process = session._process
                with self.assertRaisesRegex(AcpSessionError, error):
                    session.snapshot(session_id, timeout)
                self.assertTrue(session.ownership_uncertain)
                self.assertFalse(session.history_verified)
                self.assertIsNone(process.returncode)
                loads, turns = len(self.calls("load")), len(self.calls("prompt"))
                with self.assertRaisesRegex(AcpSessionError, "ownership is uncertain"):
                    session.snapshot(session_id, 10)
                with self.assertRaisesRegex(AcpSessionError, "ownership is uncertain"):
                    self.prompt(session, "must not be sent", session_id=session_id)
                with self.assertRaisesRegex(AcpSessionError, "no restored public history"):
                    session.confirm_history(session_id)
                self.assertEqual(len(self.calls("load")), loads)
                self.assertEqual(len(self.calls("prompt")), turns)
                session.close()

    def test_idle_snapshot_admission_blocks_prompt_steer_and_cancel(self):
        session = self.make_session()
        self.prompt(session)
        session_id = self.ids[0]
        entered, release = threading.Event(), asyncio.Event()
        load = session._connection.load_session

        async def held_load(**kwargs):
            entered.set()
            await release.wait()
            return await load(**kwargs)

        with patch.object(session._connection, "load_session", held_load), ThreadPoolExecutor(max_workers=1) as pool:
            snapshot = pool.submit(session.snapshot, session_id, 10)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(AcpSessionError, "already running"):
                    self.prompt(session, "must not be sent", session_id=session_id)
                sending = []
                self.assertEqual(session.steer("must not be sent", lambda: sending.append(True)), "idle")
                session.cancel()
                session.cancel_turn()
                self.assertEqual(sending, [])
                self.assertEqual(self.calls("cancel"), [])
                self.assertTrue(session.history_verified)
            finally:
                session._loop.call_soon_threadsafe(release.set)
            self.assertEqual(len(snapshot.result(timeout=5)), 2)

    def test_pending_steer_tail_blocks_snapshot_after_prompt_has_ended(self):
        session = self.make_session()
        self.prompt(session)
        session_id = self.ids[0]
        entered, release = threading.Event(), asyncio.Event()
        session._can_steer = True

        async def held_steer(method, body):
            self.assertEqual(method, "session/steering")
            entered.set()
            await release.wait()
            return {"outcome": "injected"}

        with patch.object(session._connection, "ext_method", held_steer), ThreadPoolExecutor(max_workers=2) as pool:
            turn = pool.submit(self.prompt, session, "stall", session_id=session_id)
            try:
                self.assertTrue(self.waiting.wait(5))
                steer = pool.submit(session.steer, "tail")
                self.assertTrue(entered.wait(5))
                session.cancel()
                with self.assertRaises(AcpCancelled):
                    turn.result(timeout=5)
                self.assertEqual(session.pending_steers, 1)
                with self.assertRaisesRegex(AcpSessionError, "already running"):
                    session.snapshot(session_id, 10)
                with self.assertRaisesRegex(AcpSessionError, "already running"):
                    self.prompt(session, "late prompt", session_id=session_id)
            finally:
                session._loop.call_soon_threadsafe(release.set)
            self.assertEqual(steer.result(timeout=5), "injected")
            self.assertEqual(session.pending_steers, 0)

    def test_pending_cancel_tail_blocks_snapshot_after_cancelled_prompt_ended(self):
        session = self.make_session()
        self.prompt(session)
        session_id = self.ids[0]
        entered, release = threading.Event(), asyncio.Event()
        cancel = session._connection.cancel

        async def held_cancel(**kwargs):
            await cancel(**kwargs)
            entered.set()
            await release.wait()

        with patch.object(session._connection, "cancel", held_cancel), ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(self.prompt, session, "stall", session_id=session_id)
            try:
                self.assertTrue(self.waiting.wait(5))
                session.cancel()
                self.assertTrue(entered.wait(5))
                with self.assertRaises(AcpCancelled):
                    turn.result(timeout=5)
                self.assertGreater(session.pending_controls, 0)
                with self.assertRaisesRegex(AcpSessionError, "already running"):
                    session.snapshot(session_id, 10)
            finally:
                session._loop.call_soon_threadsafe(release.set)

    def test_close_failure_is_reported_again_instead_of_apparent_success(self):
        session = self.make_session()

        async def failed_close():
            raise OSError("Fixture cleanup failure")

        with patch.object(session, "_close", failed_close):
            with self.assertRaisesRegex(AcpSessionError, "shutdown failed.*Fixture cleanup failure"):
                session.close()
        self.assertFalse(session._thread.is_alive())
        self.assertTrue(session.ownership_uncertain)
        with self.assertRaisesRegex(AcpSessionError, "shutdown failed"):
            session.close()
        self.sessions.remove(session)

    def test_close_cannot_report_success_when_its_loop_has_already_closed(self):
        session = self.make_session()
        session._loop.call_soon_threadsafe(session._loop.stop)
        session._thread.join(timeout=5)
        self.assertFalse(session._thread.is_alive())
        with self.assertRaisesRegex(AcpSessionError, "shutdown failed.*closed"):
            session.close()
        self.assertTrue(session.ownership_uncertain)
        with self.assertRaisesRegex(AcpSessionError, "shutdown failed"):
            session.close()
        self.sessions.remove(session)

    def test_pending_cancel_turn_rpc_blocks_snapshot_until_it_completes(self):
        session = self.make_session()
        self.prompt(session)
        entered, release = threading.Event(), asyncio.Event()

        async def held_cancel():
            entered.set()
            await release.wait()

        with patch.object(session, "_send_cancel", held_cancel), ThreadPoolExecutor(max_workers=1) as pool:
            cancelling = pool.submit(session.cancel_turn)
            try:
                self.assertTrue(entered.wait(5))
                self.assertGreater(session.pending_controls, 0)
                with self.assertRaisesRegex(AcpSessionError, "already running"):
                    session.snapshot(self.ids[0], 10)
                self.assertTrue(session.history_verified)
            finally:
                session._loop.call_soon_threadsafe(release.set)
            cancelling.result(timeout=5)

    def test_failed_process_cleanup_prevents_automatic_new_adapter(self):
        session = self.make_session()

        async def failed_disposal():
            raise OSError("Fixture process cleanup failure")

        with patch.object(session, "_dispose_process", failed_disposal):
            cleanup = asyncio.run_coroutine_threadsafe(session._stop_process(), session._loop)
            with self.assertRaisesRegex(OSError, "process cleanup failure"):
                cleanup.result(timeout=5)
        self.assertTrue(session.ownership_uncertain)
        with self.assertRaisesRegex(AcpSessionError, "ownership is uncertain"):
            self.prompt(session, "must not start a replacement")
        self.assertEqual(self.calls("start"), [])

    def test_snapshot_cannot_return_history_after_close_has_started(self):
        session = self.make_session()
        self.prompt(session)
        session_id = self.ids[0]
        load_entered, close_entered = threading.Event(), threading.Event()
        release_load, release_close = asyncio.Event(), asyncio.Event()
        load, close = session._connection.load_session, session._close

        async def held_load(**kwargs):
            load_entered.set()
            await release_load.wait()
            return await load(**kwargs)

        async def held_close():
            close_entered.set()
            await release_close.wait()
            await close()

        with patch.object(session._connection, "load_session", held_load), patch.object(session, "_close", held_close), ThreadPoolExecutor(max_workers=2) as pool:
            snapshot = pool.submit(session.snapshot, session_id, 10)
            try:
                self.assertTrue(load_entered.wait(5))
                closing = pool.submit(session.close)
                self.assertTrue(close_entered.wait(5))
                session._loop.call_soon_threadsafe(release_load.set)
                with self.assertRaisesRegex(AcpSessionError, "closed or lost ownership"):
                    snapshot.result(timeout=5)
                self.assertFalse(session.history_verified)
            finally:
                session._loop.call_soon_threadsafe(release_load.set)
                session._loop.call_soon_threadsafe(release_close.set)
            closing.result(timeout=5)
        self.assertFalse(session._thread.is_alive())

    def test_one_process_multiple_turns_and_model_returns_to_default(self):
        session = self.make_session()
        self.prompt(session, "one", "fast-model")
        self.prompt(session, "two", "quality-model", self.ids[0])
        self.prompt(session, "three", None, self.ids[0])
        self.assertEqual(len(self.calls("start")), 1)
        self.assertEqual(len(self.calls("new")), 1)
        self.assertEqual(self.calls("load"), [])
        turns = self.calls("prompt")
        self.assertEqual([turn["model"] for turn in turns], ["fast-model", "quality-model", "default-model"])
        self.assertEqual(len({turn["pid"] for turn in turns}), 1)
        self.assertEqual(self.ids, ["fixture/session:not-a-uuid"])
        self.assertTrue(all(event["sessionId"] == self.ids[0] for event in self.updates))
        self.assertTrue(any(event["update"]["sessionUpdate"] == "tool_call" for event in self.updates))
        self.assertEqual(self.calls("new")[0]["mcp"][0]["name"], "bound-project")
        self.assertFalse(self.calls("initialize")[0]["capabilities"]["terminal"])

    def test_restore_opaque_id_without_replaying_visible_history(self):
        first = self.make_session()
        self.prompt(first)
        session_id = self.ids[0]
        first.close()
        self.updates.clear()
        second = self.make_session()
        self.prompt(second, "continued", session_id=session_id)
        self.assertEqual(self.ids, [session_id, session_id])
        self.assertEqual(len(self.calls("new")), 1)
        self.assertEqual(len(self.calls("load")), 1)
        self.assertEqual([event["update"]["content"]["text"] for event in self.updates
                          if "content" in event["update"]], ["answer: continued"])

    def test_image_blocks_survive_stdio_and_resume_without_replaying_attachments(self):
        gif = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        images = (("image/png", PNG_IMAGE), ("image/gif", gif))
        first = self.make_session()
        self.prompt(first, "inspect images", "quality-model", images=images)
        session_id = self.ids[0]
        self.prompt(first, "plain follow-up", "quality-model", session_id)
        first.close()
        self.updates.clear()
        restored = self.make_session(default_model=first.default_model)
        self.prompt(restored, "new image after reopen", "quality-model", session_id, images=images[:1])

        turns = self.calls("prompt")
        self.assertEqual([turn["model"] for turn in turns], ["quality-model"] * 3)
        self.assertEqual([turn["sessionId"] for turn in turns], [session_id] * 3)
        self.assertEqual(turns[0]["pid"], turns[1]["pid"])
        self.assertEqual(len(self.calls("new")), 1)
        self.assertEqual([call["sessionId"] for call in self.calls("load")], [session_id])
        self.assertEqual(turns[0]["blocks"][0], {"type": "text", "text": "inspect images"})
        for block, (mime, encoded) in zip(turns[0]["blocks"][1:], images, strict=True):
            self.assertEqual(block, {"type": "image", "mimeType": mime, "data": encoded})
            self.assertEqual(base64.b64decode(block["data"], validate=True), base64.b64decode(encoded))
        self.assertEqual(turns[1]["blocks"], [{"type": "text", "text": "plain follow-up"}])
        self.assertEqual(turns[2]["blocks"][1:], [{"type": "image", "mimeType": "image/png", "data": PNG_IMAGE}])
        self.assertEqual([event["update"]["content"]["text"] for event in self.updates
                          if "content" in event["update"]], ["answer: new image after reopen"])

    def test_images_require_advertised_capability_without_text_only_downgrade(self):
        for capability in ("missing", "false"):
            with self.subTest(capability=capability):
                session = self.make_session(ACP_IMAGE_CAPABILITY=capability)
                with self.assertRaisesRegex(AcpSessionError, "does not support image attachments"):
                    self.prompt(session, "inspect image", images=(("image/png", PNG_IMAGE),))
                session.close()
                self.assertEqual(self.calls("new"), [])
                self.assertEqual(self.calls("load"), [])
                self.assertEqual(self.calls("prompt"), [])
                self.assertEqual(self.ids, [])

    def test_restore_without_capability_never_falls_back_to_new(self):
        session = self.make_session(ACP_NO_LOAD="1")
        with self.assertRaisesRegex(AcpSessionError, "does not support restoring"):
            self.prompt(session, session_id="saved/session")
        self.assertEqual(self.calls("new"), [])
        self.assertEqual(self.calls("load"), [])
        self.assertEqual(self.calls("prompt"), [])
        self.assertEqual(self.ids, [])

    def test_restore_remembers_default_after_explicit_model_selection(self):
        first = self.make_session()
        observed_defaults = []
        first.prompt("first", None, "quality-model", lambda session_id: (
            self.ids.append(session_id), observed_defaults.append(first.default_model),
        ), 10)
        remembered_default = first.default_model
        first.close()
        second = self.make_session(default_model=remembered_default)
        self.prompt(second, "back to default", model=None, session_id=self.ids[0])
        self.assertEqual(observed_defaults, ["default-model"])
        self.assertEqual([turn["model"] for turn in self.calls("prompt")], ["quality-model", "default-model"])
        self.assertEqual(len(self.calls("load")), 1)

    def test_unknown_session_never_falls_back_to_new(self):
        session = self.make_session()
        with self.assertRaisesRegex(AcpSessionError, "Unknown fixture session"):
            self.prompt(session, session_id="missing/session")
        self.assertEqual(len(self.calls("load")), 1)
        self.assertEqual(self.calls("new"), [])
        self.assertEqual(self.calls("prompt"), [])

    def test_agent_error_includes_string_details(self):
        session = self.make_session()
        with self.assertRaisesRegex(AcpSessionError, "Fixture failure"):
            self.prompt(session, "error")

    def test_image_model_rejection_preserves_string_error_data(self):
        session = self.make_session()
        with self.assertRaisesRegex(AcpSessionError, "The current model does not support image input"):
            self.prompt(session, "image-model-error", images=(("image/png", PNG_IMAGE),))
        self.assertEqual(len(self.calls("prompt")), 1)

    def test_unknown_model_fails_before_prompt_without_silent_substitution(self):
        session = self.make_session()
        with self.assertRaisesRegex(AcpSessionError, "does not offer model"):
            self.prompt(session, model="unavailable-model")
        self.assertEqual(self.calls("prompt"), [])
        self.assertEqual(self.ids, ["fixture/session:not-a-uuid"])

    def test_permission_waits_for_selected_advertised_option(self):
        session = self.make_session()
        with ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(self.prompt, session, "permission")
            self.assertTrue(self.permission_ready.wait(5))
            self.assertFalse(turn.done())
            request = self.permission_requests[0]
            self.assertEqual(request["toolCall"]["toolCallId"], "call-1")
            self.assertEqual([option["optionId"] for option in request["options"]], ["allow", "reject"])
            self.permission.set_result("allow")
            turn.result(timeout=5)
        self.assertEqual(self.calls("permission")[0]["outcome"]["optionId"], "allow")

    def test_unknown_permission_option_is_cancelled(self):
        session = self.make_session()
        self.permission.set_result("not-offered")
        self.prompt(session, "permission")
        self.assertEqual(self.calls("permission")[0]["outcome"]["outcome"], "cancelled")

    def test_cancel_pending_permission_then_continue_same_process(self):
        session = self.make_session()
        with ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(self.prompt, session, "permission")
            self.assertTrue(self.permission_ready.wait(5))
            session.cancel()
            with self.assertRaises(AcpCancelled):
                turn.result(timeout=5)
        self.assertIsNone(self.permission.result())
        self.prompt(session, "after cancellation", session_id=self.ids[0])
        self.assertEqual(len(self.calls("start")), 1)
        self.assertEqual(len(self.calls("cancel")), 1)
        self.assertEqual(self.calls("permission")[0]["outcome"]["outcome"], "cancelled")

    def test_cancel_sends_notification_and_preserves_session(self):
        session = self.make_session()
        with ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(self.prompt, session, "stall")
            self.assertTrue(self.waiting.wait(5))
            session.cancel()
            with self.assertRaises(AcpCancelled):
                turn.result(timeout=5)
        self.prompt(session, "next", session_id=self.ids[0])
        self.assertEqual(len(self.calls("cancel")), 1)
        self.assertEqual(len(self.calls("start")), 1)

    def test_timeout_stops_adapter_and_does_not_resend_turn(self):
        session = self.make_session(ACP_IGNORE_CANCEL="1")
        self.prompt(session)
        process = session._process
        with self.assertRaisesRegex(AcpSessionError, "timed out"):
            self.prompt(session, "stall", session_id=self.ids[0], timeout_s=0.3)
        self.assertIsNotNone(process.returncode)
        self.assertEqual([call["text"] for call in self.calls("prompt")], ["hello", "stall"])
        self.assertEqual(len(self.calls("start")), 1)
        self.assertEqual(len(self.calls("cancel")), 1)
        self.assertIsNone(session._turn_task)
        self.assertIsNone(session._activity_timeout)

    def test_session_messages_and_tools_renew_inactivity_timeout(self):
        session = self.make_session()
        self.prompt(session)
        for text in ("active-message", "active-tool"):
            with self.subTest(activity=text):
                started = time.monotonic()
                self.prompt(session, text, session_id=self.ids[0], timeout_s=0.6)
                self.assertGreater(time.monotonic() - started, 0.6)
                self.assertIsNone(session._turn_task)
                self.assertIsNone(session._activity_timeout)
        self.assertEqual(self.calls("cancel"), [])
        self.assertEqual([call["text"] for call in self.calls("prompt")], ["hello", "active-message", "active-tool"])

    def test_other_session_activity_does_not_renew_timeout(self):
        session = self.make_session()
        self.prompt(session)
        process = session._process
        with self.assertRaisesRegex(AcpSessionError, "no session activity"):
            self.prompt(session, "foreign-activity", session_id=self.ids[0], timeout_s=0.4)
        self.assertIsNotNone(process.returncode)
        self.assertEqual(len(self.calls("cancel")), 1)
        self.assertEqual([call["text"] for call in self.calls("prompt")], ["hello", "foreign-activity"])

    def test_permission_request_and_response_renew_timeout(self):
        session = self.make_session()
        self.prompt(session)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(self.prompt, session, "delayed-permission", session_id=self.ids[0], timeout_s=0.6)
            self.assertTrue(self.permission_ready.wait(5))
            time.sleep(0.4)
            self.permission.set_result("allow")
            turn.result(timeout=5)
        self.assertGreater(time.monotonic() - started, 0.6)
        self.assertEqual(self.calls("cancel"), [])
        self.assertEqual(self.calls("permission")[0]["outcome"]["optionId"], "allow")

    def test_unanswered_permission_is_still_bounded_by_inactivity(self):
        session = self.make_session()
        self.prompt(session)
        process = session._process
        with self.assertRaisesRegex(AcpSessionError, "no session activity"):
            self.prompt(session, "permission", session_id=self.ids[0], timeout_s=0.4)
        self.assertTrue(self.permission_ready.is_set())
        self.assertIsNone(self.permission.result())
        self.assertIsNotNone(process.returncode)
        self.assertEqual(len(self.calls("cancel")), 1)

    def test_adapter_crash_is_reported_without_retry(self):
        session = self.make_session()
        self.prompt(session)
        process = session._process
        with self.assertRaises(AcpSessionError):
            self.prompt(session, "crash", session_id=self.ids[0])
        self.assertEqual(process.returncode, 17)
        self.assertEqual(len(self.calls("start")), 1)
        self.assertEqual([call["text"] for call in self.calls("prompt")], ["hello", "crash"])

    def test_close_reaps_own_process_and_loop_only(self):
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
        )
        try:
            session = self.make_session()
            self.prompt(session)
            process = session._process
            session.close()
            session.close()
            self.assertIsNotNone(process.returncode)
            self.assertFalse(session._thread.is_alive())
            self.assertIsNone(unrelated.poll())
            self.assertEqual(len(self.calls("exit")), 1)
            with self.assertRaisesRegex(AcpSessionError, "closed"):
                self.prompt(session)
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_close_during_pending_permission_unblocks_prompt(self):
        session = self.make_session()
        with ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(self.prompt, session, "permission")
            self.assertTrue(self.permission_ready.wait(5))
            session.close()
            with self.assertRaises(AcpCancelled):
                turn.result(timeout=5)
        self.assertIsNone(self.permission.result())
        self.assertFalse(session._thread.is_alive())

    def test_close_waits_for_failure_cleanup_already_in_progress(self):
        session = self.make_session(ACP_EXIT_DELAY="0.5")
        self.prompt(session)
        process = session._process
        with ThreadPoolExecutor(max_workers=1) as pool:
            turn = pool.submit(self.prompt, session, "error")
            deadline = time.monotonic() + 5
            while session._process is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(session._process)
            self.assertIsNone(process.returncode)
            session.close()
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(AcpSessionError):
                turn.result(timeout=5)
        self.assertFalse(session._thread.is_alive())
        self.assertEqual(len(self.calls("exit")), 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows adapter child-process cleanup")
    def test_forced_windows_close_reaps_adapter_child(self):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        session = self.make_session(ACP_STALL_EXIT="1")
        self.prompt(session)
        process = session._process
        child_id = self.calls("child")[0]["childPid"]
        child = kernel.OpenProcess(0x100001, False, child_id)
        self.assertTrue(child)
        try:
            session.close()
            self.assertIsNotNone(process.returncode)
            self.assertEqual(kernel.WaitForSingleObject(child, 0), 0)
        finally:
            # Hold the original process handle throughout, so cleanup can
            # never address an unrelated process after PID reuse.
            if kernel.WaitForSingleObject(child, 0) == 258:
                kernel.TerminateProcess(child, 1)
                kernel.WaitForSingleObject(child, 3000)
            kernel.CloseHandle(child)


if __name__ == "__main__":
    unittest.main()
