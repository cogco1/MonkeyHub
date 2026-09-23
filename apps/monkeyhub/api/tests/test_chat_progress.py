"""User-facing progress is a transient projection of provider/runtime facts."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from monkeyhub_api import chat
from monkeyhub_api.models import ChatMessage


RUN_PATH = Path(__file__).resolve().parents[2] / "run.py"
SPEC = importlib.util.spec_from_file_location("monkeyhub_run_progress_test", RUN_PATH)
assert SPEC and SPEC.loader
HUB_RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HUB_RUN)
ProgressChatStore = HUB_RUN._progress_chat_store()


class ChatProgressProjectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Hub progress ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = ProgressChatStore(self.root / "runtime", "http://127.0.0.1:8790", commands={})
        self.store._loaded = True
        now = chat._now()
        self.session = chat._SavedChat(
            id=str(uuid4()), projectId="progress-project", projectDir=str(self.root / "project"),
            title="Progress", provider="codex", status="running", transport="acp",
            acpSessionId="fixture/session", createdAt=now, updatedAt=now,
            messages=[ChatMessage(id=str(uuid4()), role="user", content="继续做平面图", createdAt=now)],
        )
        self.store._sessions[self.session.id] = self.session
        self.store._running[self.session.id] = chat._Running()

    def visible_progress(self):
        return [row for row in self.store.get(self.session.id).messages if ":progress:" in row.id]

    def test_provider_summary_is_transient_and_never_enters_saved_chat(self):
        for text in ("Checking ", "circulation."):
            self.store._acp_update(self.session.id, {
                "sessionId": "fixture/session",
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": text},
                },
            }, {})
        visible = self.visible_progress()
        self.assertEqual(len(visible), 1)
        self.assertEqual("Checking circulation.", visible[0].content)
        self.assertEqual(visible[0].status, "streaming")
        self.assertFalse(any(":progress:" in row.id for row in self.session.messages))

        self.store._save(self.session)
        saved = (self.root / "runtime" / "chats" / f"{self.session.id}.json").read_text(encoding="utf-8")
        self.assertNotIn("circulation", saved)
        self.assertFalse(any(":progress:" in row["id"] for row in json.loads(saved)["messages"]))

    def test_board_handoff_claims_sync_only_after_success(self):
        item = {
            "id": "board-update", "server": "monkeyhub", "tool": "studio_request",
            "arguments": {"method": "PUT", "path": "/api/board"},
            "status": "in_progress", "result": None, "error": None,
        }
        self.store._tool_message(self.session, item, "item.started", {})
        before = "\n".join(row.content for row in self.visible_progress())
        self.assertIn("正在把这一步的结果同步到 Board", before)
        self.assertNotIn("已经同步到 Board", before)

        item["status"] = "completed"
        item["result"] = {"content": [{"type": "text", "text": "{}"}]}
        self.store._tool_message(self.session, item, "item.completed", {})
        after = "\n".join(row.content for row in self.visible_progress())
        self.assertIn("已经同步到 Board", after)
        self.assertIn("先去审核和批注", after)

    def test_failed_board_update_never_becomes_success_narration(self):
        item = {
            "id": "board-failed", "server": "monkeyhub", "tool": "studio_request",
            "arguments": {"method": "PUT", "path": "/api/board"},
            "status": "failed", "result": None, "error": "board write refused",
        }
        self.store._tool_message(self.session, item, "item.completed", {})
        text = "\n".join(row.content for row in self.visible_progress())
        self.assertIn("这一步没有完成", text)
        self.assertNotIn("已经同步到 Board", text)

    def test_schema_queries_never_narrate_project_writes(self):
        for kind, status in (("item.started", "in_progress"), ("item.completed", "completed")):
            with self.subTest(kind=kind):
                item = {
                    "id": "board-schema", "server": "monkeyhub", "tool": "studio_schema",
                    "arguments": {"method": "PUT", "path": "/api/board"},
                    "status": status, "error": None,
                    "result": {"content": [{"type": "text", "text": "{}"}]},
                }
                self.store._tool_message(self.session, item, kind, {})
                progress = "\n".join(row.content for row in self.visible_progress())
                self.assertIn("studio_schema", progress)
                self.assertNotIn("已经同步", progress)
                self.assertNotIn("正在把", progress)
        self.assertTrue(any("studio_schema" in row.content for row in self.session.messages))

    def test_tools_supply_live_progress_when_provider_sends_no_summary(self):
        def update(call_id, status):
            self.store._acp_update(self.session.id, {
                "sessionId": "fixture/session",
                "update": {"sessionUpdate": "tool_call", "toolCallId": call_id,
                           "title": "Read project files", "status": status,
                           "rawOutput": "private tool output"},
            }, {})

        with patch.object(chat, "_now", return_value="2026-09-22T10:00:01Z"):
            update("read-1", "in_progress")
        self.assertEqual(self.visible_progress()[0].status, "streaming")
        self.assertIn("当前操作：Read project files", self.visible_progress()[0].content)
        update("read-1", "completed")
        self.assertEqual(self.visible_progress()[0].status, "complete")
        with patch.object(chat, "_now", return_value="2026-09-22T10:00:02Z"):
            update("read-2", "failed")
        self.assertEqual(len(self.visible_progress()), 1)
        self.assertEqual(self.visible_progress()[0].status, "failed")
        visible = self.store.get(self.session.id).messages
        progress_index = visible.index(self.visible_progress()[0])
        self.assertGreater(progress_index, next(i for i, row in enumerate(visible) if row.id.endswith(":read-1")))
        self.assertEqual(abs(progress_index - next(i for i, row in enumerate(visible) if row.id.endswith(":read-2"))), 1)
        self.assertNotIn("private tool output", self.visible_progress()[0].content)
        # Full receipts are still expandable; the narrative never enters context.
        self.assertEqual(len([row for row in self.session.messages if row.role == "tool"]), 2)
        self.assertFalse(any(":progress:" in row.id for row in self.session.messages))

    def test_stale_or_stopped_acp_events_cannot_project_progress(self):
        event = {"sessionId": "old/session", "update": {
            "sessionUpdate": "tool_call", "toolCallId": "late", "title": "Read files", "status": "in_progress",
        }}
        self.store._acp_update(self.session.id, event, {})
        self.assertEqual([], self.visible_progress())
        event["sessionId"] = self.session.acpSessionId
        self.store._running[self.session.id].stop.set()
        self.store._acp_update(self.session.id, event, {})
        self.assertEqual([], self.visible_progress())

    def test_cancelled_and_interrupted_writes_never_narrate_success(self):
        for status in ("cancelled", "interrupted"):
            with self.subTest(status=status):
                self.store._progress_rows.pop(self.session.id, None)
                item = {
                    "id": f"board-{status}", "server": "monkeyhub", "tool": "studio_request",
                    "arguments": {"method": "PUT", "path": "/api/board"},
                    "status": status, "error": None,
                    "result": {"content": [{"type": "text", "text": "{}"}]},
                }
                self.store._tool_message(self.session, item, "item.completed", {})
                text = "\n".join(row.content for row in self.visible_progress())
                self.assertIn("这一步没有完成", text)
                self.assertNotIn("已经同步到 Board", text)

    def test_completion_without_a_confirmed_result_never_narrates_success(self):
        for status, result in (("", None), ("in_progress", {}), ("completed", None)):
            with self.subTest(status=status, result=result):
                self.store._progress_rows.pop(self.session.id, None)
                item = {
                    "id": f"unconfirmed-{status}", "server": "monkeyhub", "tool": "studio_request",
                    "arguments": {"method": "PUT", "path": "/api/board"},
                    "status": status, "result": result, "error": None,
                }
                self.store._tool_message(self.session, item, "item.completed", {})
                self.assertNotIn("已经同步到 Board", "\n".join(row.content for row in self.visible_progress()))

    def test_mcp_error_envelope_never_narrates_success(self):
        item = {
            "id": "board-mcp-error", "server": "monkeyhub", "tool": "studio_request",
            "arguments": {"method": "PUT", "path": "/api/board"},
            "status": "completed", "error": None,
            "result": {"isError": True, "content": [{"type": "text", "text": "write refused"}]},
        }
        self.store._tool_message(self.session, item, "item.completed", {})
        text = "\n".join(row.content for row in self.visible_progress())
        self.assertIn("这一步没有完成", text)
        self.assertNotIn("已经同步到 Board", text)

    def test_candidate_ready_requires_successful_verified_readback(self):
        good = {
            "id": "candidate-good", "server": "monkeyhub", "tool": "studio_request",
            "arguments": {"method": "GET", "path": "/api/jobs/job-1"},
            "status": "completed", "error": None,
            "result": {"content": [{"type": "text", "text": json.dumps({
                "status": "succeeded", "candidateId": "candidate-18", "readback": "ok",
            })}]},
        }
        self.store._tool_message(self.session, good, "item.completed", {})
        self.assertIn("候选 candidate-18 已经生成", "\n".join(row.content for row in self.visible_progress()))

        self.store._progress_rows[self.session.id].clear()
        bad = {
            **good, "id": "candidate-bad",
            "result": {"content": [{"type": "text", "text": json.dumps({
                "status": "succeeded", "candidateId": "candidate-19", "readback": "failed",
            })}]},
        }
        self.store._tool_message(self.session, bad, "item.completed", {})
        self.assertNotIn("candidate-19", "\n".join(row.content for row in self.visible_progress()))


if __name__ == "__main__":
    unittest.main()
