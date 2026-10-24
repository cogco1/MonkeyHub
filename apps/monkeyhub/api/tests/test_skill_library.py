"""The skills installed on this machine reach every Claude chat through its own plugin mechanism (S-1, #252).

The Hub reads the skill folders itself - no project and no Project Runtime -
writes a plugin directory into its own cache, never into a project, and passes
it with --plugin-dir. With no skill installed, the Claude command has no Skill
tool (#463). A person's answer to a skill question is kept by the Hub as a grant.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
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

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from project_runtime.application import skill_access, skill_library
from monkeyhub_api.chat import preparation, providers, skill_plugins, store as chat, transport
from monkeyhub_api.app.composition import HubSettings, create_app
from monkeyhub_api.models import ChatCreateRequest
from monkeyhub_api.settings import skill_grants

HATCH = {"name": "hatch-review", "description": "Review a plan's hatching: \"double\" hatches, poché and the recipe.",
         "body": "1. Read the drawing recipe.\n2. List every region hatched twice.\n"}
SHEET = {"name": "section-sheet", "description": "Lay out a section sheet at 1:50.", "body": "Start from the A1 title block.\n"}


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class SkillLibraryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub 技能 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.project = self.root / "chat-project"
        FilesystemProjectRepository.initialize(self.project, project_id="chat-project",
                                               initial_state={"project_id": "chat-project", "version": 0})
        self.user = self.root / "local" / "MonkeyHub" / "skill-packages"
        self.bundled = self.root / "bundled"
        self.bundled.mkdir()
        for value in (patch.dict(os.environ, {
                "CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                "APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local")}),
                patch.object(skill_library, "BUNDLED", self.bundled)):
            value.start()
            self.addCleanup(value.stop)
        os.environ.pop("MONKEYHUB_SKILL_PACKAGES", None)

    def install(self, skill: dict) -> None:
        """Put a skill's folder in the user's skill folder, as a person does."""
        folder = self.user / skill["name"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "skill.json").write_text(json.dumps({"name": skill["name"], "description": skill["description"]}),
                                           encoding="utf-8")
        (folder / "SKILL.md").write_text(skill["body"], encoding="utf-8")

    def test_the_plugin_layout_is_built_once_per_state_of_the_folders(self):
        self.install(HATCH)
        self.install(SHEET)
        cache = self.runtime / "cache" / "skill-plugins"
        built = skill_plugins.materialize(cache, skill_plugins.installed_library())
        self.assertEqual(built.parent, cache)
        self.assertEqual(json.loads((built / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["name"],
                         "monkeyhub-library")
        self.assertEqual(sorted(path.name for path in (built / "skills").iterdir()), ["hatch-review", "section-sheet"])
        self.assertEqual((built / "skills/hatch-review/SKILL.md").read_text(encoding="utf-8"), (
            "---\nname: hatch-review\n"
            "description: \"Review a plan's hatching: \\\"double\\\" hatches, poché and the recipe.\"\n"
            "---\n\n1. Read the drawing recipe.\n2. List every region hatched twice.\n"))
        self.assertEqual([path.name for path in cache.iterdir()], [built.name], "no staging directory is left")

        # Unchanged folders write nothing.
        before = snapshot(cache)
        self.assertEqual(skill_plugins.materialize(cache, skill_plugins.installed_library()), built)
        self.assertEqual(snapshot(cache), before)

        # A changed skill is a new directory built from it.
        self.install({**HATCH, "body": "Check poché first.\n"})
        rebuilt = skill_plugins.materialize(cache, skill_plugins.installed_library())
        self.assertNotEqual(rebuilt, built)
        self.assertIn("Check poché first.", (rebuilt / "skills/hatch-review/SKILL.md").read_text(encoding="utf-8"))

        # Nothing installed has nothing to load.
        self.assertIsNone(skill_plugins.materialize(cache, skill_plugins.Library(())))

        # A name read back is a folder name: one project_runtime.skills would not accept is refused, not written.
        before = snapshot(cache)
        forged = {"id": "skill:../escape", "name": "../escape", "description": "x", "code": "0" * 12,
                  "source": "user", "body": "x"}
        with self.assertRaises(chat.HubFailure) as refused:
            skill_plugins.materialize(cache, skill_plugins.Library((forged,)))
        self.assertEqual(refused.exception.error.code, "CHAT_SKILL_LIBRARY_INVALID")
        self.assertEqual(snapshot(cache), before)
        self.assertFalse((self.runtime / "cache" / "escape").exists())

    def claude_command(self, store: chat.ChatStore) -> list[str]:
        """The command one conversation's next turn starts Claude with."""
        with store._lock:
            command, _ = store._command(store._sessions[self.session.id])
        return command

    def test_every_chat_is_given_the_installed_skills_without_a_runtime(self):
        commands = {name: (sys.executable, "-c", "pass") for name in ("codex", "claude")}
        store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=commands)
        self.addCleanup(store.shutdown)
        self.session = store.create(ChatCreateRequest(projectDir=str(self.project), provider="claude"))
        nobody = {"side_effect": AssertionError("the skills are read from the folders, not through a Runtime")}

        # Nothing installed: the chat has no Skill tool at all (#463), and no user settings either.
        with patch.object(transport, "_request_json", **nobody), patch.object(preparation, "_prepare_studio", **nobody):
            plain = self.claude_command(store)
        self.assertIn("--disable-slash-commands", plain)
        self.assertEqual(plain[plain.index("--disable-slash-commands"):plain.index("--disable-slash-commands") + 3],
                         ["--disable-slash-commands", "--setting-sources", "project,local"])
        for flag in ("--plugin-dir", "--settings"):
            self.assertNotIn(flag, plain)

        # Installed once, in every chat of every project.
        self.install(HATCH)
        project = snapshot(self.project)
        with patch.object(transport, "_request_json", **nobody), patch.object(preparation, "_prepare_studio", **nobody):
            loaded = self.claude_command(store)
            again = self.claude_command(store)
        plugin = Path(loaded[loaded.index("--plugin-dir") + 1])
        self.assertTrue(plugin.is_relative_to(self.runtime / "cache" / "skill-plugins"))
        self.assertTrue((plugin / "skills/hatch-review/SKILL.md").is_file())
        self.assertEqual(again[again.index("--plugin-dir") + 1], str(plugin))
        # Only these skills: no user settings, no bundled skills, and the known
        # leftovers turned off with the one value the CLI accepts.
        position = loaded.index("--plugin-dir")
        self.assertEqual(loaded[position:position + 6], [
            "--plugin-dir", str(plugin), "--setting-sources", "project,local", "--settings",
            json.dumps({"disableBundledSkills": True, "skillOverrides": {"design": "off", "doctor": "off"}})])
        self.assertNotIn("--disable-slash-commands", loaded)
        without = plain.index("--disable-slash-commands")
        self.assertEqual(loaded[:position] + loaded[position + 6:], plain[:without] + plain[without + 3:])
        self.assertEqual(snapshot(self.project), project, "starting the chat changed no project")

    def test_a_chat_with_skills_carries_only_sign_in_and_network_from_user_settings(self):
        commands = {name: (sys.executable, "-c", "pass") for name in ("codex", "claude")}
        store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=commands)
        self.addCleanup(store.shutdown)
        user = self.root / "claude" / "settings.json"
        user.parent.mkdir(parents=True)
        user.write_text(json.dumps({
            "env": {"HTTPS_PROXY": "http://proxy.example.invalid:8080"}, "apiKeyHelper": "fixture-helper",
            "enabledPlugins": {"superpowers@market": True}, "hooks": {"Stop": []}, "theme": "dark",
        }), encoding="utf-8")
        self.install(HATCH)
        library = skill_plugins.installed_library()
        plan = {"ANTHROPIC_BASE_URL": "https://fixture.example.invalid", "ANTHROPIC_AUTH_TOKEN": "fixture-plan-token"}
        for provider in ("claude", "coding-plan"):
            with self.subTest(provider=provider), patch.object(providers, "_coding_plan_env", return_value=plan):
                self.session = store.create(ChatCreateRequest(projectDir=str(self.project), provider=provider))
                with store._lock:
                    command, _ = store._command(store._sessions[self.session.id], library=lambda: library)
                self.assertEqual(json.loads(command[command.index("--settings") + 1]), {
                    "env": {"HTTPS_PROXY": "http://proxy.example.invalid:8080"}, "apiKeyHelper": "fixture-helper",
                    "disableBundledSkills": True, "skillOverrides": {"design": "off", "doctor": "off"}})
                self.assertEqual(command[command.index("--setting-sources") + 1], "project,local")
                with store._lock:
                    plain, _ = store._command(store._sessions[self.session.id], library=lambda: None)
                self.assertIn("--disable-slash-commands", plain)
                self.assertEqual(plain[plain.index("--setting-sources") + 1], "project,local")
                self.assertEqual(json.loads(plain[plain.index("--settings") + 1]), {
                    "env": {"HTTPS_PROXY": "http://proxy.example.invalid:8080"}, "apiKeyHelper": "fixture-helper"})

    def test_a_hub_chat_keeps_no_claude_auto_memory(self):
        """#252: project_runtime.memory is the one project memory; Claude Code's own auto-memory is off, Codex untouched."""
        commands = {name: (sys.executable, "-c", "pass") for name in ("codex", "claude")}
        store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=commands)
        self.addCleanup(store.shutdown)
        plan = {"ANTHROPIC_BASE_URL": "https://fixture.example.invalid", "ANTHROPIC_AUTH_TOKEN": "fixture-plan-token"}
        for provider, expected in (("claude", "1"), ("coding-plan", "1"), ("codex", None)):
            with self.subTest(provider=provider), patch.object(providers, "_coding_plan_env", return_value=plan), \
                    patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": ""}):
                os.environ.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY")
                session = store.create(ChatCreateRequest(projectDir=str(self.project), provider=provider))
                with store._lock, patch.object(store, "_codex_mcp", return_value={}):
                    _, environment = store._command(store._sessions[session.id], library=lambda: None)
                self.assertEqual(environment.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"), expected)

    def test_a_persons_answer_is_kept_by_the_hub_as_a_grant(self):
        commands = {name: (sys.executable, "-c", "pass") for name in ("codex", "claude")}
        store = chat.ChatStore(self.runtime.resolve(), "http://127.0.0.1:8790", commands=commands)
        self.addCleanup(store.shutdown)
        ask = {"access": {"ssh": ["renou-win"]}, "broad": {}, "skill": {"name": "blender-render", "source": "user"},
               "projectId": "housing-13-units", "codeSha256": "c" * 64}
        once = store._allow_skill(ask, once=True, ref="question-1")
        always = store._allow_skill(ask, once=False, ref="question-2")
        wide = store._allow_skill({**ask, "access": {"programs": ["python"]}, "broad": {"programs": ["python"]}},
                                  once=False, ref="question-3")
        rows = {row.grant_id: row for row in skill_grants.read(self.runtime.resolve()).grants}
        self.assertEqual((rows[once].skill.name, rows[once].once["projectId"], rows[once].once["codeSha256"]),
                         ("blender-render", "housing-13-units", "c" * 64), "only this time: this run's project and code")
        self.assertIsNone(rows[always].skill, "always allow: every skill and project")
        self.assertEqual(rows[wide].skill.name, "blender-render", "what allows anything: this skill only")
        grants = skill_access.read_grants(skill_grants.grants_path(self.runtime.resolve()))
        self.assertEqual(skill_access.needed({"ssh": ["renou-win"]}, grants, skill_access.SkillRef("other", "user"),
                                             resolve=str), {})
        self.assertTrue(all(row.method == "card" for row in rows.values()))
        self.assertLessEqual(datetime.fromisoformat(rows[once].granted_at), datetime.now(timezone.utc))

    def test_the_settings_page_lists_and_withdraws_grants_without_a_project(self):
        app = create_app(HubSettings(runtime_root=self.runtime))
        with TestClient(app, base_url="http://127.0.0.1:8790") as client:
            self.assertEqual(client.get("/api/settings/skill-grants").json()["grants"], [])
            row = skill_grants.allow(self.runtime.resolve(), {"programs": ["blender"]}, method="card")
            listed = client.get("/api/settings/skill-grants").json()["grants"]
            self.assertEqual([(item["grantId"], item["access"]["programs"]) for item in listed], [(row.grant_id, ["blender"])])
            withdrawn = client.post(f"/api/settings/skill-grants/{row.grant_id}/revoke")
            self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
            self.assertIsNotNone(withdrawn.json()["revokedAt"])
            self.assertEqual(client.post("/api/settings/skill-grants/nobody/revoke").status_code, 404)
            self.install({**HATCH, "name": "blender-render"})
            carried = client.post("/api/settings/skill-grants/carry", json={"projectId": "0925", "approvals": [
                {"approvalId": "a1", "skillId": "skill:blender-render", "version": 1, "scope": "version",
                 "status": "active", "access": {"programs": ["blender"], "ssh": ["renou-win"]}},
                {"approvalId": "a2", "skillId": "skill:blender-render", "version": 1, "scope": "version",
                 "status": "revoked", "access": {"network": True}}]})
            self.assertEqual(carried.status_code, 200, carried.text)
            self.assertEqual([(item["ref"], item["skill"]) for item in carried.json()],
                             [("a1", {"name": "blender-render", "source": "user"})])


if __name__ == "__main__":
    unittest.main()
