"""#334: provider keys entered in Hub settings.

A key is written once into the account's credential store and never comes back:
not in a response, an error, a transcript or a child's argv. The environment
still wins, and the Coding Plan endpoint and token reach Coding Plan only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient  # noqa: E402

from archflow_studio_api.transport.settings import ApplicationSettingsDto  # noqa: E402
from monkeyhub_api import chat, credentials  # noqa: E402
from monkeyhub_api.applications import Applications  # noqa: E402
from monkeyhub_api.chat import ChatStore  # noqa: E402
from monkeyhub_api.main import HubSettings, create_app  # noqa: E402
from monkeyhub_api.models import HubFailure  # noqa: E402

GEMINI_KEY = "AIzaSyFixture-gemini-key-0001"
PLAN_TOKEN = "fixture.plan-token-0002"


class CredentialTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub keys ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.appdata = self.root / "roaming"
        self.store = credentials.MemorySecretStore()
        credentials.use_secret_store(self.store)
        self.addCleanup(credentials.use_secret_store, credentials.UnavailableSecretStore())
        # Nothing of the account's: no settings, no Claude CLI configuration, no home.
        environment = patch.dict(os.environ, {
            "APPDATA": str(self.appdata), "LOCALAPPDATA": str(self.root / "local"),
            "USERPROFILE": str(self.root / "home"), "HOME": str(self.root / "home"),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex"),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def save_preferences(self, **preferences):
        (self.appdata / "MonkeyArch").mkdir(parents=True, exist_ok=True)
        (self.appdata / "MonkeyArch" / "settings.json").write_text(json.dumps(preferences), encoding="utf-8")


class KeyValueTests(unittest.TestCase):
    def test_a_key_is_visible_ascii_of_a_sensible_length(self):
        self.assertEqual(credentials.checked(f"  {GEMINI_KEY}\n"), GEMINI_KEY)
        for bad in ("short", "has a space inside it", "x" * 1025, "密钥密钥密钥密钥", '"quoted-key-value"'[:-1] + "\u200b"):
            with self.subTest(bad=bad[:12]), self.assertRaises(credentials.CredentialError) as refused:
                credentials.checked(bad)
            self.assertNotIn(bad.strip(), str(refused.exception), "a refusal never repeats the key")

    def test_nothing_is_read_from_the_account_until_the_hub_opens_it(self):
        credentials.use_secret_store(credentials.UnavailableSecretStore())
        self.assertFalse(credentials.secret_store().available)
        self.assertIsNone(credentials.saved("gemini"))
        with self.assertRaises(credentials.CredentialError):
            credentials.save("gemini", GEMINI_KEY)


class CredentialHttpTests(CredentialTestCase):
    def setUp(self):
        super().setUp()
        app = create_app(HubSettings(runtime_root=self.root / "runtime", port=18791), source_root=ROOT)
        self.client = TestClient(app, base_url="http://127.0.0.1:18791")

    def statuses(self):
        response = self.client.get("/api/credentials")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(GEMINI_KEY, response.text)
        self.assertNotIn(PLAN_TOKEN, response.text)
        return {row["id"]: row for row in response.json()}

    def test_a_key_goes_in_once_and_only_its_status_comes_back(self):
        self.assertEqual(self.statuses()["gemini"], {"id": "gemini", "configured": False, "source": None, "variable": None,
                                                     "saved": False, "storeAvailable": True})
        saved = self.client.put("/api/credentials/gemini", json={"key": f" {GEMINI_KEY} "})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn(GEMINI_KEY, saved.text)
        self.assertEqual(saved.json()["source"], "saved")
        self.assertEqual(self.store.values["MonkeyHub/gemini-api-key"], GEMINI_KEY)
        self.assertTrue(self.statuses()["gemini"]["configured"])
        cleared = self.client.delete("/api/credentials/gemini")
        self.assertEqual(cleared.json()["configured"], False)
        self.assertNotIn("MonkeyHub/gemini-api-key", self.store.values)

    def test_the_environment_wins_and_is_named(self):
        self.store.write("MonkeyHub/gemini-api-key", GEMINI_KEY)
        with patch.dict(os.environ, {"MONKEYHUB_RENDER_API_KEY": "from-the-environment-0003"}):
            row = self.statuses()["gemini"]
            self.assertEqual((row["source"], row["variable"], row["saved"]), ("environment", "MONKEYHUB_RENDER_API_KEY", True))
            self.assertEqual(credentials.resolve("gemini"), "from-the-environment-0003")
        self.assertEqual(credentials.resolve("gemini"), GEMINI_KEY)

    def test_refusals_never_echo_what_was_sent(self):
        refused = self.client.put("/api/credentials/gemini", json={"key": "has a space in the middle"})
        self.assertEqual((refused.status_code, refused.json()["code"]), (422, "CREDENTIAL_INVALID"))
        self.assertNotIn("has a space", refused.text)
        malformed = self.client.put("/api/credentials/gemini", json={"key": GEMINI_KEY, "extra": PLAN_TOKEN})
        self.assertEqual((malformed.status_code, malformed.json()["code"]), (422, "CREDENTIAL_REQUEST_INVALID"))
        self.assertNotIn(PLAN_TOKEN, malformed.text)
        self.assertNotIn(GEMINI_KEY, malformed.text)
        unknown = self.client.put("/api/credentials/somewhere-else", json={"key": GEMINI_KEY})
        self.assertEqual(unknown.status_code, 422)
        self.assertNotIn(GEMINI_KEY, unknown.text)
        self.assertEqual(self.store.values, {})

    def test_without_an_account_store_nothing_is_saved(self):
        credentials.use_secret_store(credentials.UnavailableSecretStore())
        refused = self.client.put("/api/credentials/gemini", json={"key": GEMINI_KEY})
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "CREDENTIAL_STORE_UNAVAILABLE"))
        self.assertFalse(self.statuses()["gemini"]["storeAvailable"])

    def test_the_gemini_check_lists_one_model_and_says_only_whether_google_took_the_key(self):
        self.assertEqual(self.client.post("/api/credentials/gemini/check").json()["result"], "missing")
        self.store.write("MonkeyHub/gemini-api-key", GEMINI_KEY)
        seen = []

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, size=-1):
                return b"{"

        def accept(request, timeout):
            seen.append((request.full_url, request.get_header("X-goog-api-key")))
            return Answer()

        with patch("urllib.request.urlopen", accept):
            accepted = self.client.post("/api/credentials/gemini/check")
        self.assertEqual(accepted.json()["result"], "accepted")
        self.assertNotIn(GEMINI_KEY, accepted.text)
        self.assertEqual(seen, [("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", GEMINI_KEY)])
        refusal = HTTPError("https://generativelanguage.googleapis.com", 400, f"bad key {GEMINI_KEY}", {}, None)
        with patch("urllib.request.urlopen", side_effect=refusal):
            rejected = self.client.post("/api/credentials/gemini/check")
        self.assertEqual(rejected.json()["result"], "rejected")
        self.assertNotIn(GEMINI_KEY, rejected.text)
        with patch("urllib.request.urlopen", side_effect=URLError("offline")):
            self.assertEqual(self.client.post("/api/credentials/gemini/check").json()["result"], "unreachable")

    def test_only_listed_key_pages_open_in_the_system_browser(self):
        with patch("webbrowser.open", return_value=True) as opened:
            self.assertEqual(self.client.post("/api/links/gemini-keys/open").json(), {"opened": True})
            refused = self.client.post("/api/links/somewhere-else/open")
        opened.assert_called_once_with("https://aistudio.google.com/apikey")
        self.assertEqual((refused.status_code, refused.json()["code"]), (422, "CREDENTIAL_REQUEST_INVALID"))


class RenderLaunchTests(CredentialTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "project.json").write_text("{}", encoding="utf-8")
        self.applications = Applications(self.root / "source", self.root / "runtime", hub_port=18790)

    def launch(self):
        _, environment = self.applications._command("studio", ApplicationSettingsDto.model_validate({"projectDir": str(self.project)}))
        return environment

    def test_a_saved_key_reaches_the_next_runtime_and_the_environment_still_wins(self):
        self.save_preferences(renderProvider="gemini", renderModel="gemini-3.1-flash-image")
        self.assertNotIn("ARCHFLOW_STUDIO_RENDER_API_KEY", self.launch())
        self.store.write("MonkeyHub/gemini-api-key", GEMINI_KEY)
        self.assertEqual(self.launch()["ARCHFLOW_STUDIO_RENDER_API_KEY"], GEMINI_KEY)
        with patch.dict(os.environ, {"MONKEYHUB_RENDER_API_KEY": "from-the-environment-0003"}):
            environment = self.launch()
        self.assertEqual(environment["ARCHFLOW_STUDIO_RENDER_API_KEY"], "from-the-environment-0003")
        self.assertNotIn("MONKEYHUB_RENDER_API_KEY", environment)
        self.save_preferences(renderProvider="off")
        self.assertFalse(any("RENDER" in key for key in self.launch()), "render off carries no key at all")


class CodingPlanTests(CredentialTestCase):
    def test_a_saved_endpoint_and_token_make_coding_plan_available(self):
        store = ChatStore(self.root / "runtime", "http://127.0.0.1:18790", commands={"claude": ("claude.exe",)})
        self.addCleanup(store.shutdown)
        with patch.object(chat, "_check_providers", return_value={}):
            self.assertFalse({row.id: row for row in store.providers()}["coding-plan"].available)
            self.save_preferences(codingPlanBaseUrl="https://plan.example.invalid/anthropic")
            self.assertFalse({row.id: row for row in store.providers()}["coding-plan"].available, "an endpoint alone is not enough")
            self.store.write("MonkeyHub/coding-plan-token", PLAN_TOKEN)
            row = {row.id: row for row in store.providers()}["coding-plan"]
        self.assertTrue(row.available)
        self.assertIn("saved in Hub settings", row.detail)
        self.assertNotIn(PLAN_TOKEN, row.model_dump_json())
        self.assertEqual(chat._coding_plan_env(), {"ANTHROPIC_BASE_URL": "https://plan.example.invalid/anthropic",
                                                   "ANTHROPIC_AUTH_TOKEN": PLAN_TOKEN})

    def test_saved_keys_are_redacted_wherever_they_surface(self):
        self.store.write("MonkeyHub/coding-plan-token", PLAN_TOKEN)
        self.store.write("MonkeyHub/gemini-api-key", GEMINI_KEY)
        self.assertEqual(chat._redact(f"sent {PLAN_TOKEN} and {GEMINI_KEY}", {}), "sent [redacted] and [redacted]")

    def test_an_endpoint_must_be_a_plain_http_address(self):
        from archflow_studio_api.transport.settings import UserSettingsDto
        from pydantic import ValidationError

        self.assertEqual(UserSettingsDto.model_validate({"codingPlanBaseUrl": "https://open.example.invalid/api/anthropic"})
                         .coding_plan_base_url, "https://open.example.invalid/api/anthropic")
        for bad in ("ftp://example.invalid", "https://user:secret@example.invalid", "https://example.invalid/?token=x", "not a url"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                UserSettingsDto.model_validate({"codingPlanBaseUrl": bad})


class LoginTests(CredentialTestCase):
    def test_sign_in_opens_the_cli_login_in_a_console_of_its_own(self):
        store = ChatStore(self.root / "runtime", "http://127.0.0.1:18790",
                          commands={"codex": (r"C:\Users\fixture\AppData\Roaming\npm\codex.cmd",)})
        self.addCleanup(store.shutdown)
        with patch.object(chat, "_open_console") as opened, patch.object(chat.os, "name", "nt"):
            store.open_login("codex")
            with self.assertRaises(HubFailure) as missing:
                store.open_login("claude")
        line, cwd = opened.call_args.args
        self.assertEqual(line, r'cmd.exe /d /k ""C:\Users\fixture\AppData\Roaming\npm\codex.cmd" "login""')
        self.assertEqual(cwd, str(Path.home()))
        self.assertEqual(missing.exception.error.code, "CHAT_PROVIDER_MISSING")


if __name__ == "__main__":
    unittest.main()
