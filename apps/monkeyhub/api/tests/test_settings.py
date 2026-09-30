"""The local account's preferences and this Hub's launch choices, kept and served by the Hub.

Both files keep the location and bytes they had while their code lived in the
Project Runtime package (#486): every installed version shares
%APPDATA%\\MonkeyArch\\settings.json, so each must read what another saved.
"""

from __future__ import annotations

import codecs
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
from tools import source_roots  # noqa: E402 - this checkout's tools, found above

# The checkout's Python source roots, as its architecture policy lists them, go first.
source_roots.put_first(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from monkeyhub_api.main import HubSettings, create_app  # noqa: E402
from monkeyhub_api.settings.models import ApplicationSettingsDto, UserSettingsDto  # noqa: E402
from monkeyhub_api.settings.store import (  # noqa: E402
    SettingsError,
    read_application_settings,
    read_user_settings,
    save_application_settings,
    save_user_settings,
    user_settings_path,
)

PORT = 18792

# What the Project Runtime's settings module wrote before #486, for a file that
# sets every preference. It wrote in text mode, so each line ended in os.linesep.
SAVED_PREFERENCES = """{
  "language": "zh-CN",
  "theme": "light",
  "fontScale": 1.1,
  "uiStyle": "titleblock",
  "intentProvider": "codex",
  "intentModel": "saved-model",
  "intentTimeoutS": 45.5,
  "renderProvider": "gemini",
  "renderModel": "gemini-3-pro-image",
  "renderTimeoutS": 120.0,
  "chatProvider": "coding-plan",
  "chatModel": "glm-4.6",
  "codingPlanBaseUrl": "https://open.example.invalid/api/anthropic",
  "autoUpdate": false
}
"""
SAVED_LAUNCH = """{{
  "projectDir": {project},
  "workspaceDir": {workspace},
  "libraryDir": {library},
  "referenceRun": "run-002",
  "cadExport": "rhino",
  "studioPort": 18801,
  "monitorPort": 18802
}}
"""


class HubCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="hub user settings ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"APPDATA": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.runtime = self.root / "runtime"
        self.client = self.hub(self.runtime)

    def hub(self, runtime_root: Path, **settings: object) -> TestClient:
        # No lifespan: nothing here starts Monitor or a Project Runtime.
        app = create_app(HubSettings(runtime_root=runtime_root, port=PORT, **settings), source_root=ROOT)
        client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
        self.addCleanup(client.close)
        return client


class UserSettingsRouteTests(HubCase):
    def test_absent_settings_need_no_project_and_create_no_file(self) -> None:
        response = self.client.get("/api/settings/user")
        self.assertEqual((response.status_code, response.json()), (200, {}))
        self.assertFalse(user_settings_path().exists())

    def test_save_survives_a_fresh_hub_and_touches_nothing_else(self) -> None:
        payload = {"language": "zh-CN", "theme": "light", "fontScale": 1.1, "uiStyle": "titleblock",
                   "intentProvider": "codex", "intentModel": "saved-model", "intentTimeoutS": 45.5}
        response = self.client.put("/api/settings/user", json=payload)
        self.assertEqual((response.status_code, response.json()), (200, payload))
        self.assertEqual(json.loads(user_settings_path().read_text(encoding="utf-8")), payload)
        # A saved preference reaches a Project Runtime only at its next launch.
        self.assertEqual(self.client.app.state.applications.worker_snapshots(), ())
        self.assertFalse((self.runtime / "config" / "applications.json").exists())
        self.assertEqual(self.hub(self.root / "another runtime").get("/api/settings/user").json(), payload)
        self.assertEqual([path.name for path in user_settings_path().parent.iterdir()], ["settings.json"])

    def test_put_replaces_the_file_and_null_clears_an_override(self) -> None:
        self.client.put("/api/settings/user", json={"intentModel": "old-model", "theme": "dark"})
        response = self.client.put("/api/settings/user", json={"language": "en", "intentModel": None})
        self.assertEqual(response.json(), {"language": "en"})
        self.assertEqual(self.client.get("/api/settings/user").json(), {"language": "en"})

    def test_render_preferences_save_and_a_key_is_never_one(self) -> None:
        payload = {"renderProvider": "gemini", "renderModel": "gemini-3-pro-image", "renderTimeoutS": 120.0}
        self.assertEqual(self.client.put("/api/settings/user", json=payload).json(), payload)
        self.assertEqual(self.client.get("/api/settings/user").json(), payload)
        for invalid in ({"renderApiKey": "synthetic-secret"}, {"renderProvider": "unknown"},
                        {"renderTimeoutS": 301}, {"renderTimeoutS": True}, {"renderTimeoutS": 0.5}):
            with self.subTest(payload=invalid):
                self.assertEqual(self.client.put("/api/settings/user", json=invalid).status_code, 422)
                self.assertEqual(self.client.get("/api/settings/user").json(), payload)
        self.assertNotIn("synthetic-secret", user_settings_path().read_text(encoding="utf-8"))

    def test_unsupported_values_and_secret_fields_cannot_replace_saved_settings(self) -> None:
        self.client.put("/api/settings/user", json={"theme": "light"})
        for payload in ({"language": "zh"}, {"language": ["en"]}, {"theme": "auto"},
                        {"theme": ["light"]}, {"intentProvider": ["codex"]}, {"fontScale": True},
                        {"fontScale": 2}, {"uiStyle": "neon"}, {"uiStyle": ["quiet"]},
                        {"intentProvider": "new-provider"},
                        {"intentModel": "  "}, {"intentModel": "invalid\u0000model"},
                        {"intentTimeoutS": 0}, {"intentTimeoutS": True},
                        {"intentTimeoutS": "60"}, {"token": "private-token"},
                        {"codexExecutable": "another-program"}):
            with self.subTest(payload=payload):
                response = self.client.put("/api/settings/user", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.client.get("/api/settings/user").json(), {"theme": "light"})
        self.assertNotIn("private-token", user_settings_path().read_text(encoding="utf-8"))

    def test_invalid_saved_file_answers_422_and_an_explicit_save_repairs_it(self) -> None:
        user_settings_path().parent.mkdir()
        for raw in ("{unfinished", "[]", '{"intentTimeoutS": 1e309}', '{"fontScale": true}',
                    '{"token":"private-token"}', '{"language":"other"}'):
            with self.subTest(raw=raw):
                user_settings_path().write_text(raw, encoding="utf-8")
                response = self.client.get("/api/settings/user")
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], "USER_SETTINGS_INVALID")
                self.assertNotIn("private-token", response.text)
                self.assertEqual(self.client.get("/api/health").status_code, 200)
                self.assertEqual(self.client.put("/api/settings/user", json={"theme": "system"}).status_code, 200)
                self.assertEqual(self.client.get("/api/settings/user").json(), {"theme": "system"})

    def test_failed_atomic_replace_preserves_the_prior_file(self) -> None:
        self.client.put("/api/settings/user", json={"theme": "dark"})
        prior = user_settings_path().read_bytes()
        with patch("monkeyhub_api.settings.store.os.replace", side_effect=OSError("replace refused")):
            response = self.client.put("/api/settings/user", json={"theme": "light"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(user_settings_path().read_bytes(), prior)
        self.assertEqual([path.name for path in user_settings_path().parent.iterdir()], ["settings.json"])

    def test_an_account_without_an_absolute_appdata_is_refused_by_name(self) -> None:
        for appdata in (None, "relative-roaming"):
            with self.subTest(appdata=appdata), patch.dict(os.environ, {"APPDATA": appdata or ""}):
                self.assertEqual(self.client.get("/api/settings/user").json()["code"], "USER_SETTINGS_UNAVAILABLE")
                response = self.client.put("/api/settings/user", json={"theme": "dark"})
                self.assertEqual((response.status_code, response.json()["code"]), (503, "USER_SETTINGS_UNAVAILABLE"))
                with self.assertRaises(SettingsError):
                    user_settings_path()
        self.assertFalse((self.root / "MonkeyArch").exists())

    def test_a_hub_outside_local_mode_neither_reads_nor_writes_the_users_settings(self) -> None:
        remote = self.hub(self.root / "remote runtime", mode="remote")
        with patch("monkeyhub_api.settings.routes.read_user_settings", side_effect=AssertionError("read local file")), \
             patch("monkeyhub_api.settings.routes.save_user_settings", side_effect=AssertionError("wrote local file")):
            self.assertEqual(remote.get("/api/settings/user").status_code, 404)
            self.assertEqual(remote.put("/api/settings/user", json={"theme": "dark"}).status_code, 404)
            self.assertEqual(remote.put("/api/settings/user", json={"token": "invalid"}).status_code, 404)
        self.assertFalse(user_settings_path().exists())


class SavedSettingsFileTests(HubCase):
    """Files the Project Runtime's settings module wrote read the same, and save to the same bytes."""

    def test_saved_preferences_read_back_unchanged_and_save_to_the_same_bytes(self) -> None:
        expected = UserSettingsDto.model_validate(json.loads(SAVED_PREFERENCES))
        path = Path(os.environ["APPDATA"]) / "MonkeyArch" / "settings.json"
        self.assertEqual(user_settings_path(), path)
        path.parent.mkdir()
        for newline in ("\n", "\r\n"):
            for bom in (b"", codecs.BOM_UTF8):
                with self.subTest(newline=newline, bom=bom):
                    path.write_bytes(bom + SAVED_PREFERENCES.replace("\n", newline).encode("utf-8"))
                    self.assertEqual(read_user_settings(), expected)
                    response = self.client.get("/api/settings/user")
                    self.assertEqual((response.status_code, response.json()), (200, json.loads(SAVED_PREFERENCES)))
        save_user_settings(read_user_settings())
        self.assertEqual(path.read_bytes(), SAVED_PREFERENCES.replace("\n", os.linesep).encode("utf-8"))

    def test_saved_launch_choices_read_back_unchanged_and_save_to_the_same_bytes(self) -> None:
        text = SAVED_LAUNCH.format(**{name: json.dumps(str(self.root / folder), ensure_ascii=False) for name, folder in
                                      (("project", "project"), ("workspace", "projects"), ("library", "library"))})
        path = self.runtime / "config" / "applications.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(text.replace("\n", os.linesep).encode("utf-8"))
        saved = read_application_settings(self.runtime)
        self.assertEqual(saved, ApplicationSettingsDto.model_validate(json.loads(text)))
        response = self.client.get("/api/settings/apps")
        self.assertEqual((response.status_code, response.json()), (200, json.loads(text)))
        path.unlink()
        save_application_settings(self.runtime, saved)
        self.assertEqual(path.read_bytes(), text.replace("\n", os.linesep).encode("utf-8"))
        with self.assertRaises(SettingsError):
            save_application_settings(Path("relative-runtime"), saved)


if __name__ == "__main__":
    unittest.main()
