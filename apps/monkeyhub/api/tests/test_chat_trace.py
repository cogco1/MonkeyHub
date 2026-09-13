"""Actual CLI callbacks and HTTP headers produce content-free turn observations."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_chat
from monkeyhub_api import chat
from monkeyhub_api.chat_trace import HubTurnObserver
from monkeymonitor.store import UsageLog
from monkeymonitor.trace import build_traces


class HubTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.store = UsageLog(Path(self.temp))

    def rows(self):
        rows, warnings = self.store.read()
        self.assertFalse(warnings)
        return rows

    def test_live_turn_parallel_tools_and_permissions_do_not_add_model_rounds(self):
        trace = HubTurnObserver(self.store, "turn-one", "project-one", "codex", "model-a")
        trace.bind("native-session")
        trace.ready()
        trace.tool("a", "studio_schema", {"path": "/api/proposals"}, running=True)
        trace.tool("b", "studio_request", {"path": "/api/state"}, running=True)
        live = self.rows()
        self.assertEqual(sum(r.status == "running" and r.phase == "tool_call" for r in live), 2)
        trace.tool("a", "studio_schema", {}, running=False)
        self.assertIsNone(trace.round_id)
        trace.permission("permission-one")
        trace.tool("b", "studio_request", {}, running=False)
        self.assertIsNone(trace.round_id)
        trace.permission("permission-one", completed=True)
        self.assertEqual(trace.rounds, 2)
        trace.tool("a", "studio_schema", {}, running=False)
        trace.first_response()
        trace.first_response()
        trace.finish("succeeded")
        rows = self.rows()
        self.assertEqual(sum(r.phase == "first_response" for r in rows), 1)
        self.assertEqual(sum(r.phase == "tool_call" for r in rows), 2)
        self.assertEqual(sum(r.phase == "provider_round" for r in rows), 2)
        self.assertTrue(all(r.status != "running" for r in rows))
        self.assertTrue(all(r.turn_id == "turn-one" and r.session_id == "native-session" for r in rows))
        self.assertTrue(all(r.duration_ms is not None for r in rows))
        self.assertTrue(all(r.tokens.input_tokens is None for r in rows))

    def test_cli_message_metadata_is_counted_once_without_content_or_inference_duration(self):
        trace = HubTurnObserver(self.store, "turn", "project", "claude", None)
        trace.ready()
        message = {"id": "message-one", "model": "exact-model", "content": [{"text": "private-response"}],
                   "usage": {"input_tokens": 100, "cache_read_input_tokens": 50,
                             "cache_creation_input_tokens": 0, "output_tokens": 20}}
        trace.claude_usage(message)
        trace.claude_usage(message)
        trace.tool("a", "private-command-title", {"body": {"prompt": "private-prompt"}}, running=True)
        trace.finish("cancelled")
        rows = self.rows()
        usage = [row for row in rows if row.model_call]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].tokens.input_tokens, 150)
        self.assertEqual(usage[0].tokens.cached_input_tokens, 50)
        self.assertEqual(usage[0].model, "exact-model")
        self.assertEqual(usage[0].timing_scope, "unknown")
        exported = json.dumps([row.to_dict() for row in rows])
        for private in ("private-response", "private-prompt", "private-command-title"):
            self.assertNotIn(private, exported)

    def test_completed_tool_without_start_keeps_tool_and_prior_agent_duration_unknown(self):
        clock = [0.0]
        origin = datetime(2026, 9, 13, tzinfo=timezone.utc)
        with patch("monkeyhub_api.chat_trace.perf_counter", side_effect=lambda: clock[0]), \
             patch("monkeyhub_api.chat_trace._now", side_effect=lambda: (origin + timedelta(seconds=clock[0])).isoformat()):
            trace = HubTurnObserver(self.store, "turn", "project", "codex", None)
            trace.ready()
            clock[0] = 5.0
            trace.tool("completed-only", "studio_request", {"path": "/api/state"}, running=False)
            clock[0] = 6.0
            trace.finish("succeeded")
        rows = self.rows()
        tool = next(row for row in rows if row.phase == "tool_call")
        self.assertEqual(tool.status, "succeeded")
        self.assertIsNone(tool.duration_ms)
        self.assertIsNone(tool.ended_at)
        self.assertEqual(tool.details["wait_reason"], "missing_tool_start")
        preceding = next(row for row in rows if row.event_id.endswith(":round:1"))
        self.assertIsNone(preceding.duration_ms)
        self.assertIsNone(preceding.ended_at)
        self.assertEqual(preceding.details["provider_timing_basis"], "missing_tool_start")
        actual = build_traces([row.to_dict() for row in rows])["traces"][0]
        self.assertEqual(actual["summary"]["elapsed_ms"], 6000)
        self.assertEqual(actual["summary"]["unattributed_ms"], 5000)
        self.assertEqual(next(row for row in actual["attribution"] if row["lane"] == "agent")["duration_ms"], 1000)

    def test_failed_context_and_broken_store_do_not_leave_running_phase_or_raise(self):
        trace = HubTurnObserver(self.store, "turn", "project", "codex", None)
        trace.finish("failed")
        self.assertTrue(all(row.status == "failed" for row in self.rows()))
        with patch.object(self.store, "append", side_effect=OSError("unavailable")):
            with self.assertLogs("monkeyhub_api.chat_trace", level="WARNING"):
                trace = HubTurnObserver(self.store, "another", "project", "codex", None)
                trace.ready()
                trace.finish("succeeded")

    def test_parallel_http_reads_keep_same_trace_and_next_tool_cannot_inherit_it(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append((self.headers.get("X-Monkey-Turn-Id"), self.headers.get("X-Monkey-Parent-Span-Id")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            token = chat._trace_headers.set({"X-Monkey-Turn-Id": "turn", "X-Monkey-Parent-Span-Id": "hub:turn:turn"})
            try:
                chat._together({"a": (base, "/a"), "b": (base, "/b")}, 2)
                def actual(*_):
                    return chat._request_json(base, "/c")
                with patch.object(chat, "_call_tool", side_effect=actual):
                    chat.call_tool(base, "chat", "studio_request", {})
            finally:
                chat._trace_headers.reset(token)
            self.assertEqual(captured[:2], [("turn", "hub:turn:turn")] * 2)
            self.assertEqual(captured[2], (None, None))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_non_finite_tool_payload_cannot_fail_the_observed_turn(self):
        trace = HubTurnObserver(self.store, "turn", "project", "codex", None)
        trace.ready()
        with self.assertLogs("monkeyhub_api.chat_trace", level="WARNING"):
            trace.tool("invalid", "studio_request", {"body": {"value": float("nan")}}, running=True)
        trace.finish("succeeded")
        self.assertEqual(next(row for row in self.rows() if row.phase == "hub_turn").status, "succeeded")


class CliTraceTests(unittest.TestCase):
    # Reuse the existing isolated fake CLI process setup, without inheriting
    # and rerunning unrelated chat tests. This checks real stream boundaries.
    setUp = test_chat.ChatTests.setUp
    close_store = test_chat.ChatTests.close_store
    create = test_chat.ChatTests.create
    post = test_chat.ChatTests.post
    finished = test_chat.ChatTests.finished

    def test_cli_progress_update_keeps_tool_open_until_completion(self):
        session = self.create()
        saved = self.store._sessions[session.id]
        trace = HubTurnObserver(self.store.usage_log, "turn", session.projectId, "codex", None)
        trace.ready()
        self.store._running[session.id] = chat._Running(trace=trace)
        item = {"type": "mcp_tool_call", "id": "one", "tool": "studio_request", "server": "monkeyhub",
                "arguments": {"method": "GET", "path": "/api/state"}, "status": "in_progress"}
        try:
            for kind in ("item.started", "item.updated"):
                self.store._event(saved, {"type": kind, "item": item}, {})
            events, _ = self.store.usage_log.read()
            self.assertEqual(next(row for row in events if row.phase == "tool_call").status, "running")
            self.assertEqual(trace.rounds, 1)
            self.store._event(saved, {"type": "item.completed", "item": {**item, "status": "completed"}}, {})
            events, _ = self.store.usage_log.read()
            self.assertEqual(next(row for row in events if row.phase == "tool_call").status, "succeeded")
            self.assertEqual(trace.rounds, 2)
        finally:
            trace.finish("succeeded")
            self.store._running.pop(session.id, None)

    def test_cli_turn_is_live_then_completed_under_exact_native_identity(self):
        session = self.create()
        posted = self.post(session, "tool-test private-request-for-trace")
        turn_id = posted.messages[-1].id
        result = self.finished(session)
        self.assertEqual(result.status, "idle", result.error)
        events, warnings = self.store.usage_log.read()
        self.assertFalse(warnings)
        roots = [row for row in events if row.phase == "hub_turn"]
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].turn_id, turn_id)
        self.assertEqual(roots[0].session_id, self.store._sessions[session.id].nativeSessionId)
        self.assertEqual(roots[0].status, "succeeded")
        self.assertTrue(any(row.phase == "tool_call" for row in events))
        self.assertTrue(all(row.turn_id == turn_id for row in events))
        self.assertNotIn("private-request-for-trace", json.dumps([row.to_dict() for row in events]))


if __name__ == "__main__":
    unittest.main()
