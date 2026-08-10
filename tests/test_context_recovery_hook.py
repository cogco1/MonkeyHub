from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".codex" / "config.toml"
HOOK = ROOT / ".codex" / "hooks" / "context_recovery.py"
REGISTRY = ROOT / "governance" / "work_registry.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry() -> dict[str, object]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _registry_item(item_id: str) -> dict[str, object]:
    for item in _registry()["items"]:
        if item["id"] == item_id:
            return item
    raise AssertionError(f"missing registry item {item_id}")


def _active_ids() -> tuple[str, ...]:
    return tuple(
        sorted(
            item["id"]
            for item in _registry()["items"]
            if item["status"] == "active"
        )
    )


def _run_hook(payload: dict[str, object]) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=ROOT,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def _hook_module():
    spec = importlib.util.spec_from_file_location(
        "archflow_context_recovery_test",
        HOOK,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("context recovery hook cannot be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContextRecoveryHookTests(unittest.TestCase):
    def test_project_config_uses_exact_bounded_compaction_contract(self):
        with CONFIG.open("rb") as stream:
            config = tomllib.load(stream)

        self.assertEqual(
            "body_after_prefix",
            config["model_auto_compact_token_limit_scope"],
        )
        self.assertEqual(6000, config["tool_output_token_limit"])
        self.assertNotIn("model_context_window", config)
        self.assertNotIn("model_auto_compact_token_limit", config)
        self.assertIn("Never upgrade an", config["compact_prompt"])
        self.assertTrue(config["features"]["hooks"])

        precompact = config["hooks"]["PreCompact"]
        session_start = config["hooks"]["SessionStart"]
        self.assertEqual("^(manual|auto)$", precompact[0]["matcher"])
        self.assertEqual("^compact$", session_start[0]["matcher"])
        self.assertEqual(
            2500,
            session_start[0]["hooks"][0]["additionalContextLimit"],
        )
        for event in (precompact, session_start):
            handler = event[0]["hooks"][0]
            self.assertIn("git rev-parse --show-toplevel", handler["command"])
            self.assertIn(
                "git rev-parse --show-toplevel", handler["command_windows"]
            )

    def test_precompact_verifies_all_active_capsules_without_writes(self):
        m033_card = ROOT / _registry_item("M033")["card"]
        guarded = (
            REGISTRY,
            ROOT / "docs" / "DYNAMIC_MAP.md",
            m033_card,
        )
        before = {path: _digest(path) for path in guarded}
        output = _run_hook(
            {
                "cwd": str(ROOT),
                "hook_event_name": "PreCompact",
                "trigger": "auto",
                "transcript_path": r"C:\private\must-not-be-read.jsonl",
            }
        )
        after = {path: _digest(path) for path in guarded}

        self.assertTrue(output["continue"])
        for item_id in _active_ids():
            self.assertIn(item_id, output["systemMessage"])
        self.assertEqual(before, after)
        self.assertNotIn("private", json.dumps(output).lower())

    def test_compact_session_start_injects_bounded_non_authoritative_context(self):
        output = _run_hook(
            {
                "cwd": str(ROOT),
                "hook_event_name": "SessionStart",
                "source": "compact",
                "transcript_path": r"C:\private\must-not-be-read.jsonl",
            }
        )
        context = output["hookSpecificOutput"]["additionalContext"]

        self.assertTrue(output["continue"])
        self.assertLessEqual(len(context), 9000)
        self.assertIn("ARCHFLOW COMPACTION RECOVERY @1", context)
        self.assertIn("orientation only", context)
        self.assertIn("Git HEAD:", context)
        active_ids = _active_ids()
        for item_id in active_ids:
            self.assertIn(f"Active card {item_id}", context)
        if active_ids:
            self.assertIn("Write scope:", context)
            self.assertIn("Stop conditions:", context)
            self.assertIn("P046 capsule sha256:", context)
        else:
            self.assertIn("Active work cards: none", context)
            self.assertNotIn("Write scope:", context)
            self.assertNotIn("P046 capsule sha256:", context)
        self.assertNotIn("must-not-be-read", context)
        self.assertNotIn(str(ROOT), context)

    def test_zero_active_card_context_does_not_fabricate_authority(self):
        context = _hook_module().render_context(
            {
                "git": {
                    "branch": "test-branch",
                    "head": "a" * 40,
                    "dirty_paths": (),
                },
                "active_cards": (),
            }
        )

        self.assertIn("Active work cards: none", context)
        self.assertNotIn("Active card ", context)
        self.assertNotIn("Write scope:", context)
        self.assertNotIn("Stop conditions:", context)
        self.assertNotIn("P046 capsule sha256:", context)

    def test_invalid_event_fails_closed_without_echoing_input(self):
        output = _run_hook(
            {
                "cwd": str(ROOT),
                "hook_event_name": "PostCompact",
                "trigger": "auto",
                "secret": "do-not-echo-this",
            }
        )

        self.assertFalse(output["continue"])
        self.assertIn("hook-event-invalid", output["stopReason"])
        self.assertNotIn("do-not-echo-this", json.dumps(output))


if __name__ == "__main__":
    unittest.main()
