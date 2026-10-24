"""Where a Hub chat's Claude turn may not write: the bound project and the Hub's own configuration and records.

The rules are the CLI's Edit rules, which also hold Write (measured with CLI
2.1.283 for #599 and S-1); this checks which places a turn is given rules for.
A chat's scratch stays writable.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from monkeyhub_api.chat import providers


def rule(path: Path, suffix: str) -> str:
    text = str(path)
    if os.name == "nt":
        return f"Edit(//{text[0].lower()}{text[2:].replace(chr(92), '/')}{suffix})"
    return f"Edit(/{text}{suffix})"


class AgentWritePathTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        self.runtime, self.project = base / "MonkeyHub", base / "projects" / "housing"
        (self.runtime / "chats" / "chat-1" / "scratch").mkdir(parents=True)
        (self.runtime / "odd-folder").mkdir()
        (self.runtime / "hub.lock").write_text("", encoding="utf-8")
        self.environment = {"APPDATA": str(base / "Roaming"), "CLAUDE_CONFIG_DIR": str(base / "claude-config"),
                            "MONKEYHUB_SKILL_PACKAGES": str(base / "my-skills")}
        self.base = base

    def denied(self, *, installed: bool = False) -> tuple[str, ...]:
        checkout = None if installed else ROOT
        with patch.dict(os.environ, self.environment), patch.object(providers, "_source_checkout", return_value=checkout):
            return providers._claude_denied(str(self.project), self.runtime)

    def test_without_the_runtime_root_only_the_project_is_named(self) -> None:
        self.assertEqual(providers._claude_denied(str(self.project)), (rule(self.project, "/**"),))

    def test_the_hubs_configuration_and_records_are_not_written(self) -> None:
        denied = self.denied()
        self.assertEqual(denied[0], rule(self.project, "/**"))
        for place in ("config", "diagnostics", "cache", "runtime", "skill-packages", "updates", "versions", "workspace",
                      "logs", "odd-folder"):
            with self.subTest(place=place):
                self.assertIn(rule(self.runtime / place, "/**"), denied, "named also before it exists")
        self.assertIn(rule(self.runtime / "hub.lock", ""), denied)
        self.assertIn(rule(self.base / "Roaming" / "MonkeyArch", "/**"), denied, "the account's Hub settings")
        self.assertIn(rule(self.base / "my-skills", "/**"), denied, "the user's skill folder where it is moved")
        self.assertIn(rule(self.base / "claude-config", "/**"), denied, "Claude Code's own configuration")

    def test_a_chats_records_and_attachments_are_guarded_and_its_scratch_is_not(self) -> None:
        denied = self.denied()
        self.assertIn(rule(self.runtime / "chats", "/*.json"), denied)
        self.assertIn(rule(self.runtime / "chats", "/*/attachments/**"), denied)
        self.assertFalse([item for item in denied if item.startswith(rule(self.runtime / "chats", "")[:-1] + "/**")
                          or "scratch" in item], "no rule reaches a chat's scratch")

    def test_an_installed_hubs_own_files_are_guarded_and_a_source_checkout_is_not(self) -> None:
        release = Path(providers.__file__).resolve().parents[5]
        installed = release.parent.parent if release.parent.name == "versions" else release
        self.assertIn(rule(installed, "/**"), self.denied(installed=True))
        self.assertNotIn(rule(installed, "/**"), self.denied(installed=False))


if __name__ == "__main__":
    unittest.main()
