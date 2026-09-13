"""Real SDK stdio conversations with a local fake agent; no model or CAD calls."""

from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from monkeyhub_api.acp_session import AcpCancelled, AcpSessionError, CodexAcpSession


FAKE_AGENT = r'''
import asyncio, json, os, subprocess, sys
from pathlib import Path
from acp import PROTOCOL_VERSION, RequestError, run_agent
from acp.schema import (
    AgentCapabilities, AgentMessageChunk, InitializeResponse, LoadSessionResponse,
    NewSessionResponse, PermissionOption, PromptResponse, SetSessionConfigOptionResponse,
    SessionConfigOptionSelect, TextContentBlock, ToolCallProgress, ToolCallStart, ToolCallUpdate, UsageUpdate,
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
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(load_session=os.environ.get("ACP_NO_LOAD") != "1"),
        )

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        log("new", cwd=cwd, mcp=[server.model_dump(by_alias=True) for server in mcp_servers])
        self.save()
        return NewSessionResponse(session_id=self.session_id, config_options=self.options())

    async def load_session(self, cwd, session_id, mcp_servers=None, **kwargs):
        log("load", sessionId=session_id, cwd=cwd)
        if not state_path.exists() or session_id != json.loads(state_path.read_text())["sessionId"]:
            raise RequestError.invalid_params({"details": "Unknown fixture session"})
        self.model = json.loads(state_path.read_text())["model"]
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
            update=AgentMessageChunk(session_update="agent_message_chunk", content=TextContentBlock(type="text", text=text)),
        )

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[0].text
        self.cancelled.clear()
        log("prompt", sessionId=session_id, text=text, model=self.model)
        if text == "crash":
            os._exit(17)
        if text == "error":
            raise RequestError.invalid_params({"details": "Fixture failure"})
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

    def prompt(self, session, text="hello", model=None, session_id=None, timeout_s=10):
        session.prompt(text, session_id, model, self.ids.append, timeout_s)

    def calls(self, event):
        path = self.root / "calls.jsonl"
        if not path.exists():
            return []
        return [value for line in path.read_text(encoding="utf-8").splitlines()
                if (value := json.loads(line))["event"] == event]

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
