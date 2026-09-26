"""Actual CLI callbacks and HTTP headers produce content-free turn observations."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_chat
from test_chat import wait_for
from monkeyhub_api import chat
from monkeyhub_api.chat_trace import HubTurnObserver
from monkeyhub_api.main import HubSettings, create_app
from monkeymonitor.store import BUSY_NOTICE, UsageLog
from monkeymonitor.trace import build_traces

HOLD_JOURNAL = r'''
import os, sys
from pathlib import Path
directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
stream = (directory / "usage.lock").open("a+b")
if os.name == "nt":
    import msvcrt
    stream.seek(0)
    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
print("held", flush=True)
sys.stdin.readline()
'''


def bounded(case, action, *, seconds=30, on_timeout=None):
    """Run something that must not block, on a worker, with a bounded wait.

    A blocking journal lock waits forever on POSIX, where an elapsed assertion
    after the call would never be reached. This fails instead of hanging.
    """

    outcome = {}

    def run():
        try:
            outcome["value"] = action()
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        if on_timeout is not None:
            on_timeout()  # free the holder before any cleanup waits on it
        case.fail(f"the turn blocked on diagnostics for more than {seconds}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


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

    def test_cli_usage_carries_its_plan_and_the_shipped_catalog_prices_a_standard_call(self):
        standard = {"input_tokens": 500, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 100,
                    "cache_creation": {"ephemeral_1h_input_tokens": 0}, "output_tokens": 200,
                    "service_tier": "standard", "speed": "standard"}
        cases = (("claude", "claude-opus-5", standard, "api-standard", "matched"),
                 # Fast mode bills at other rates; the standard row must not price it.
                 ("claude", "claude-opus-5", {**standard, "speed": "fast"}, None, "missing_identity"),
                 ("coding-plan", "endpoint-model", standard, "coding-plan", "not_found"))
        for provider, model, usage, plan, status in cases:
            with self.subTest(provider=provider, speed=usage["speed"]), \
                 patch("monkeyhub_api.chat_trace._now", return_value="2026-09-26T10:00:00+00:00"):
                store = UsageLog(Path(self.temp) / f"{provider}-{usage['speed']}")
                trace = HubTurnObserver(store, "turn", "project", provider, None)
                trace.ready()
                trace.claude_usage({"id": "message-one", "model": model, "usage": usage})
                trace.finish("succeeded")
                rows, warnings = store.read()
                self.assertFalse(warnings)
                (call,) = [row for row in rows if row.model_call]
                self.assertEqual((call.provider, call.model), (provider, model))
                self.assertEqual(call.details.get("billing_plan"), plan)
                self.assertEqual(call.rate_match_status, status)
                self.assertFalse([row for row in rows if not row.model_call and "billing_plan" in row.details])
                if status == "matched":
                    price = build_traces([row.to_dict() for row in rows])["traces"][0]["price"]
                    # 500 ordinary x $5 + 400 cached x $0.50 + 100 written x $6.25 + 200 output x $25, per million.
                    self.assertEqual(Decimal(price["amount_usd"]), Decimal("0.008325"))
                    self.assertEqual(price["missing"], [])

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

    def test_a_visual_review_is_a_visual_observation_under_its_turn_with_its_images_and_no_prompt(self):
        trace = HubTurnObserver(self.store, "turn", "project", "codex", None)
        trace.ready()
        arguments = {"taskClass": "spatial_formal", "reason": "first_bundle", "domain": "modeling",
                     "sourceRefs": [{"kind": "model", "runId": "run-001", "stateDigest": "a" * 64, "assetSha256": "b" * 64}],
                     "viewRecipe": ["front", "axon"], "task": "private-review-task",
                     "criteria": [{"criterionId": "entry", "text": "private-review-criterion"}]}
        answer = {"observation": {"reviewId": "vr-0123456789abcdef", "observations": [
                      {"findingId": "f1", "description": "private-review-finding", "escalate": False}]},
                  "usage": {"provider": "codex", "imageInputs": 2, "inputTokens": 1200},
                  "allowance": {"taskClass": "spatial_formal", "allowed": 2, "used": 1}}
        trace.tool("look", "visual_review", arguments, running=True)
        trace.tool("look", "visual_review", arguments, running=False,
                   result={"content": [{"type": "text", "text": json.dumps(answer)}], "structured_content": None})
        trace.tool("again", "visual_review", arguments, running=True)
        trace.tool("again", "visual_review", arguments, running=False, failed=True,
                   result={"content": [{"type": "text", "text": '{"code": "VISUAL_BUDGET_EXHAUSTED"}'}]})
        view = "/api/drawings/model-view?runId=run-001&stateDigest=" + "a" * 64 + "&assetSha256=" + "b" * 64 + "&view=top"
        for identifier, method, path in (("view", "GET", view), ("page", "POST", "/api/board/export")):
            trace.tool(identifier, "studio_request", {"method": method, "path": path}, running=True)
            trace.tool(identifier, "studio_request", {"method": method, "path": path}, running=False, result="{}")
        trace.finish("succeeded")
        rows = {row.event_id: row for row in self.rows()}
        look, again = rows["hub:tool:turn:look"], rows["hub:tool:turn:again"]
        self.assertEqual((look.phase, look.parent_event_id, look.turn_id, look.status),
                         ("tool_call", "hub:turn:turn", "turn", "succeeded"))
        self.assertEqual({key: look.details[key] for key in ("tool_name", "request_kind", "image_inputs", "output_refs")},
                         {"tool_name": "visual_review", "request_kind": "visual_observation", "image_inputs": 2,
                          "output_refs": ["vr-0123456789abcdef"]})
        # A refused look reports no image count: unknown is not zero.
        self.assertEqual((again.status, again.details["request_kind"]), ("failed", "visual_observation"))
        self.assertNotIn("image_inputs", again.details)
        # A raw view or page read hands the Agent one image and is a read, not a mutation.
        for identifier in ("view", "page"):
            self.assertEqual((rows[f"hub:tool:turn:{identifier}"].details["request_kind"],
                              rows[f"hub:tool:turn:{identifier}"].details["image_inputs"]), ("image_read", 1))
        exported = json.dumps([row.to_dict() for row in rows.values()])
        for private in ("private-review-task", "private-review-criterion", "private-review-finding"):
            self.assertNotIn(private, exported)
        (built,) = build_traces([row.to_dict() for row in rows.values()])["traces"]
        span = next(span for span in built["spans"] if span["event_id"] == "hub:tool:turn:look")
        self.assertEqual((span["parent_event_id"], span["details"]["request_kind"], span["details"]["image_inputs"]),
                         ("hub:turn:turn", "visual_observation", 2))

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
    calls = test_chat.ChatTests.calls

    def kill_journal_holder(self):
        if self.holder.poll() is None:
            self.holder.kill()
            self.holder.wait(30)

    def hold_journal(self):
        """Own the real usage journal from another process, as a peer Hub would."""

        directory = self.store.usage_log.path.parent
        self.holder = subprocess.Popen([sys.executable, "-c", HOLD_JOURNAL, str(directory)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.holder.wait, 30)
        self.addCleanup(self.holder.stdin.close)
        self.addCleanup(self.holder.stdout.close)
        self.addCleanup(self.kill_journal_holder)
        line = bounded(self, self.holder.stdout.readline, on_timeout=self.kill_journal_holder)
        self.assertEqual(line.strip(), "held")
        return self.holder

    def test_held_journal_neither_delays_nor_repeats_a_real_hub_turn(self):
        holder = self.hold_journal()
        with patch("monkeyhub_api.main.ChatStore", return_value=self.store):
            app = create_app(HubSettings(runtime_root=self.runtime))
        with patch.object(app.state.applications, "start"), TestClient(app, base_url="http://127.0.0.1:8790") as client:
            created = client.post("/api/chat/sessions", json={"projectDir": str(self.project), "provider": "codex"})
            self.assertEqual(created.status_code, 201, created.text)
            session = created.json()["id"]
            clock = time.perf_counter()
            posted = bounded(self, lambda: client.post(f"/api/chat/sessions/{session}/messages",
                                                       json={"projectId": "chat-project", "content": "hello"}),
                             on_timeout=self.kill_journal_holder)
            elapsed = time.perf_counter() - clock
            self.assertEqual(posted.status_code, 202, posted.text)
            self.assertLess(elapsed, 1.0, "a skipped observation must not delay the turn")
            self.assertIsNone(holder.poll(), "measured while the lock was still held")
            finished = wait_for(lambda: client.get(f"/api/chat/sessions/{session}").json(),
                                lambda row: row["status"] != "running")
            self.assertEqual(finished["status"], "idle", finished.get("error"))
            self.assertEqual(len(self.calls()), 1, "the CLI turn ran exactly once")
            self.assertFalse(self.store.usage_log.path.exists(), "no unlocked fallback write")
            self.assertEqual(self.store.usage_log.read(), ([], [BUSY_NOTICE]))
            holder.stdin.write("\n")
            holder.stdin.flush()
            self.assertEqual(holder.wait(30), 0)
            second = client.post(f"/api/chat/sessions/{session}/messages",
                                 json={"projectId": "chat-project", "content": "hello again"})
            self.assertEqual(second.status_code, 202, second.text)
            self.assertEqual(wait_for(lambda: client.get(f"/api/chat/sessions/{session}").json(),
                                      lambda row: row["status"] != "running")["status"], "idle")
        self.assertEqual(len(self.calls()), 2, "the dropped observations replayed no turn")
        rows, warnings = self.store.usage_log.read()
        self.assertFalse(warnings)
        roots = [row for row in rows if row.phase == "hub_turn"]
        self.assertEqual([row.status for row in roots], ["succeeded"], "only the second turn is recorded")
        self.assertTrue(any(row.details.get("missing_observations") for row in rows))
        notice = build_traces([row.to_dict() for row in rows])["traces"][0]["warnings"]
        self.assertTrue(any("跳过" in warning for warning in notice), notice)

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

    def test_a_visual_review_the_cli_reports_is_traced_under_the_turn_it_answers(self):
        session = self.create()
        saved = self.store._sessions[session.id]
        trace = HubTurnObserver(self.store.usage_log, "turn", session.projectId, "codex", None)
        trace.ready()
        self.store._running[session.id] = chat._Running(trace=trace)
        answer = {"observation": {"reviewId": "vr-00000000000000aa", "observations": []},
                  "usage": {"imageInputs": 3}, "allowance": {"taskClass": "spatial_formal", "allowed": 2, "used": 1}}
        item = {"type": "mcp_tool_call", "id": "look", "tool": "visual_review", "server": "monkeyhub",
                "arguments": {"taskClass": "spatial_formal", "task": "private-cli-review-task"}, "status": "in_progress"}
        try:
            self.store._event(saved, {"type": "item.started", "item": item}, {})
            self.store._event(saved, {"type": "item.completed", "item": {**item, "status": "completed", "result": {
                "content": [{"type": "text", "text": json.dumps(answer)}], "structured_content": None}}}, {})
        finally:
            trace.finish("succeeded")
            self.store._running.pop(session.id, None)
        events, _ = self.store.usage_log.read()
        look = next(row for row in events if row.event_id == "hub:tool:turn:look")
        self.assertEqual((look.turn_id, look.parent_event_id, look.status), ("turn", "hub:turn:turn", "succeeded"))
        self.assertEqual((look.details["request_kind"], look.details["image_inputs"]), ("visual_observation", 3))
        self.assertNotIn("private-cli-review-task", json.dumps([row.to_dict() for row in events]))

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
