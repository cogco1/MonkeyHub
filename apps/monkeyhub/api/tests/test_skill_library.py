"""The library project's skills reach Claude through its own plugin mechanism (#252).

The Hub reads the library through its Runtime API, writes a plugin directory
into its own cache - never into a project - and passes it with --plugin-dir.
With no library set, the Claude command has no Skill tool (#463).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.main import create_app as create_studio
from archflow_studio_api.settings import StudioSettings, save_application_settings
from archflow_studio_api.transport.settings import ApplicationSettingsDto
from monkeyhub_api import chat, skill_plugins
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import ChatCreateRequest

LIBRARY_ID = "skill-library"
HATCH = {"projectId": LIBRARY_ID, "name": "hatch-review",
         "description": "Review a plan's hatching: \"double\" hatches, poché and the recipe.",
         "body": "1. Read the drawing recipe.\n2. List every region hatched twice.\n"}
SHEET = {"projectId": LIBRARY_ID, "name": "section-sheet",
         "description": "Lay out a section sheet at 1:50.", "body": "Start from the A1 title block.\n"}


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class SkillLibraryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub 技能 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.project = self.root / "chat-project"
        self.library = self.root / LIBRARY_ID
        for path, name in ((self.project, "chat-project"), (self.library, LIBRARY_ID)):
            FilesystemProjectRepository.initialize(path, project_id=name,
                                                   initial_state={"project_id": name, "version": 0})
        environment = patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.studio = TestClient(create_studio(StudioSettings(project_dir=self.library, cad_export="off")))
        self.addCleanup(self.studio.close)
        self.fetched: list[str] = []

    def add(self, payload: dict) -> dict:
        response = self.studio.post("/api/skills", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def index(self) -> dict:
        return self.studio.get("/api/skills").json()

    def fetch(self, skill_id: str, version: int) -> dict:
        self.fetched.append(f"{skill_id}@{version}")
        return self.studio.get(f"/api/skills/{skill_id}", params={"version": version}).json()

    def request_json(self, base, path, method="GET", body=None, timeout=180, **_):
        """The library Runtime's API, as chat._request_json reaches it."""
        self.assertEqual((base, method), ("http://127.0.0.1:9001", "GET"))
        if path.startswith("/api/skills/"):
            self.fetched.append(path)
        response = self.studio.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_plugin_layout_is_built_once_per_index(self):
        self.add(HATCH)
        self.add(SHEET)
        cache = self.runtime / "cache" / "skill-plugins"
        built = skill_plugins.materialize(cache, self.index(), self.fetch)
        self.assertEqual(built.parent, cache)
        self.assertEqual(json.loads((built / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["name"],
                         "monkeyhub-library")
        self.assertEqual(sorted(path.name for path in (built / "skills").iterdir()), ["hatch-review", "section-sheet"])
        self.assertEqual((built / "skills/hatch-review/SKILL.md").read_text(encoding="utf-8"), (
            "---\nname: hatch-review\n"
            "description: \"Review a plan's hatching: \\\"double\\\" hatches, poché and the recipe.\"\n"
            "---\n\n1. Read the drawing recipe.\n2. List every region hatched twice.\n"))
        self.assertEqual(sorted(self.fetched), ["skill:hatch-review@1", "skill:section-sheet@1"])
        self.assertEqual([path.name for path in cache.iterdir()], [built.name], "no staging directory is left")

        # An unchanged index reads no body and writes nothing.
        before = snapshot(cache)
        self.fetched.clear()
        self.assertEqual(skill_plugins.materialize(cache, self.index(), self.fetch), built)
        self.assertEqual((self.fetched, snapshot(cache)), ([], before))

        # A new version is a new index, and a new directory built from it.
        self.add({**HATCH, "body": "Check poché first.\n", "supersedesVersion": 1})
        rebuilt = skill_plugins.materialize(cache, self.index(), self.fetch)
        self.assertNotEqual(rebuilt, built)
        self.assertEqual(sorted(self.fetched), ["skill:hatch-review@2", "skill:section-sheet@1"])
        self.assertIn("Check poché first.", (rebuilt / "skills/hatch-review/SKILL.md").read_text(encoding="utf-8"))

        # A library with no skills has nothing to load.
        self.assertIsNone(skill_plugins.materialize(cache, {"projectId": LIBRARY_ID, "skills": []}, self.fetch))

        # A name read back is a folder name: one studio.skills would not accept is refused, not written.
        before = snapshot(cache)
        forged = {"id": "skill:../escape", "version": 1, "name": "../escape", "description": "x"}
        with self.assertRaises(chat.HubFailure) as refused:
            skill_plugins.materialize(cache, {"projectId": LIBRARY_ID, "skills": [forged]},
                                      lambda skill_id, version: {**forged, "body": "x"})
        self.assertEqual(refused.exception.error.code, "CHAT_SKILL_LIBRARY_INVALID")
        self.assertEqual(snapshot(cache), before)
        self.assertFalse((self.runtime / "cache" / "escape").exists())

    def claude_command(self, store: chat.ChatStore) -> list[str]:
        """The command one conversation's next turn starts Claude with."""
        with store._lock:
            command, _ = store._command(store._sessions[self.session.id])
        return command

    def test_the_claude_command_gains_the_plugin_only_when_a_library_is_set(self):
        commands = {name: (sys.executable, "-c", "pass") for name in ("codex", "claude")}
        store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=commands)
        self.addCleanup(store.shutdown)
        self.session = store.create(ChatCreateRequest(projectDir=str(self.project), provider="claude"))
        self.add(HATCH)

        # No library: nothing is read, and the chat has no Skill tool at all (#463).
        with patch.object(chat, "_request_json", side_effect=AssertionError("no library, no call")):
            plain = self.claude_command(store)
        self.assertIn("--disable-slash-commands", plain)
        for flag in ("--plugin-dir", "--setting-sources", "--settings"):
            self.assertNotIn(flag, plain)

        save_application_settings(self.runtime, ApplicationSettingsDto(libraryDir=str(self.library)))
        projects = snapshot(self.project), snapshot(self.library)
        prepared = []
        with patch.object(chat, "_prepare_studio", side_effect=lambda hub, session, budget: prepared.append(session)), \
                patch.object(chat, "_bound_studio", return_value=("http://127.0.0.1:9001", {})), \
                patch.object(chat, "_request_json", side_effect=self.request_json):
            loaded = self.claude_command(store)
            again = self.claude_command(store)
        self.assertEqual(prepared[0], {"projectId": LIBRARY_ID, "projectDir": str(self.library.resolve())})
        plugin = Path(loaded[loaded.index("--plugin-dir") + 1])
        self.assertTrue(plugin.is_relative_to(self.runtime / "cache" / "skill-plugins"))
        self.assertTrue((plugin / "skills/hatch-review/SKILL.md").is_file())
        self.assertEqual(again[again.index("--plugin-dir") + 1], str(plugin))
        self.assertEqual(self.fetched, ["/api/skills/skill:hatch-review?version=1"], "the second chat fetched no body")
        # Only the library's skills: no user settings, no bundled skills, and
        # the known leftovers turned off with the one value the CLI accepts.
        # A wrong shape makes the CLI ignore the whole --settings value.
        position = loaded.index("--plugin-dir")
        self.assertEqual(loaded[position:position + 6], [
            "--plugin-dir", str(plugin), "--setting-sources", "project,local", "--settings",
            json.dumps({"disableBundledSkills": True, "skillOverrides": {"design": "off", "doctor": "off"}})])
        self.assertEqual(json.loads(loaded[position + 5]),
                         {"disableBundledSkills": True, "skillOverrides": {"design": "off", "doctor": "off"}})
        self.assertNotIn("--disable-slash-commands", loaded)
        without = plain.index("--disable-slash-commands")
        self.assertEqual(loaded[:position] + loaded[position + 6:], plain[:without] + plain[without + 1:])
        # Starting the chat changed no project: not its own, not the library.
        self.assertEqual((snapshot(self.project), snapshot(self.library)), projects)

        # A library that cannot be read says so; the turn does not go without it.
        with patch.object(chat, "_prepare_studio", side_effect=chat.HubFailure(503, "CHAT_STUDIO_UNAVAILABLE", "Not ready.")):
            with self.assertRaises(chat.HubFailure) as refused:
                self.claude_command(store)
        self.assertEqual(refused.exception.error.code, "CHAT_SKILL_LIBRARY_UNAVAILABLE")

    def test_a_library_chat_carries_only_sign_in_and_network_from_user_settings(self):
        commands = {name: (sys.executable, "-c", "pass") for name in ("codex", "claude")}
        store = chat.ChatStore(self.runtime, "http://127.0.0.1:8790", commands=commands)
        self.addCleanup(store.shutdown)
        user = self.root / "claude" / "settings.json"
        user.parent.mkdir(parents=True)
        user.write_text(json.dumps({
            "env": {"HTTPS_PROXY": "http://proxy.example.invalid:8080"}, "apiKeyHelper": "fixture-helper",
            "enabledPlugins": {"superpowers@market": True}, "hooks": {"Stop": []}, "theme": "dark",
        }), encoding="utf-8")
        self.add(HATCH)
        library = skill_plugins.Library(self.index(), self.fetch)
        plan = {"ANTHROPIC_BASE_URL": "https://fixture.example.invalid", "ANTHROPIC_AUTH_TOKEN": "fixture-plan-token"}
        for provider in ("claude", "coding-plan"):
            with self.subTest(provider=provider), patch.object(chat, "_coding_plan_env", return_value=plan):
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
                self.assertNotIn("--settings", plain)

    def test_the_library_setting_names_a_complete_project(self):
        app = create_app(HubSettings(runtime_root=self.runtime))
        with TestClient(app, base_url="http://127.0.0.1:8790") as client:
            settings = client.get("/api/settings/apps").json()
            self.assertIsNone(settings.get("libraryDir"))
            missing = client.put("/api/settings/apps", json={**settings, "libraryDir": str(self.root / "missing")})
            self.assertEqual((missing.status_code, missing.json()["code"]), (422, "CHAT_PROJECT_INVALID"))
            relative = client.put("/api/settings/apps", json={**settings, "libraryDir": "library"})
            self.assertEqual(relative.status_code, 422)
            saved = client.put("/api/settings/apps", json={**settings, "libraryDir": str(self.library)})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["libraryDir"], str(self.library.resolve()))


if __name__ == "__main__":
    unittest.main()
