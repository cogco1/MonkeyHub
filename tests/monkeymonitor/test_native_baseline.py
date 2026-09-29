"""``run_native_baseline``'s pure event-summary and CLI-resolution logic (#419)."""

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.monkeymonitor import run_native_baseline as native_baseline

NEEDS_CODEX = unittest.skipUnless(shutil.which("codex"), "codex is not on PATH")


class SummarizeTests(unittest.TestCase):
    def test_counts_failures_and_by_name_map(self):
        events = [
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "occt", "tool": "build_box", "status": "completed"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "python script.py", "exit_code": 1}},
            {"type": "item.completed", "item": {"type": "file_change", "path": "study.step"}},
            {"type": "item.completed", "item": {"type": "reasoning", "text": "thinking about the recesses"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}},
        ]
        summary = native_baseline.summarize(events)
        self.assertEqual(summary["tool_calls"], 3)
        self.assertEqual(summary["failed_tool_calls"], 1)
        self.assertEqual(summary["tool_calls_by_name"], {"occt.build_box": 1, "command_execution": 1, "file_change": 1})
        self.assertEqual(summary["model_steps"], 2)
        self.assertEqual(summary["agent_messages"], 1)
        self.assertEqual(summary["usage"], {"input_tokens": 100, "output_tokens": 50})
        self.assertFalse(summary["turn_failed"])

    def test_turn_failed_event_is_reported_even_with_no_items(self):
        self.assertTrue(native_baseline.summarize([{"type": "turn.failed"}])["turn_failed"])

    def test_error_field_marks_a_tool_call_failed_without_a_status(self):
        events = [{"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "occt", "tool": "build_box", "error": "boom"}}]
        summary = native_baseline.summarize(events)
        self.assertEqual(summary["tool_calls"], 1)
        self.assertEqual(summary["failed_tool_calls"], 1)


class NativeCodexTests(unittest.TestCase):
    def test_exits_clearly_when_missing_from_path(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(SystemExit, "codex is not on PATH"):
                native_baseline.native_codex()

    @NEEDS_CODEX
    def test_returns_an_existing_path(self):
        found = native_baseline.native_codex()
        self.assertTrue(Path(found).is_file(), found)


if __name__ == "__main__":
    unittest.main()
