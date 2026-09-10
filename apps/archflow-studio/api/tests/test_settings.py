"""The project root is chosen by the operator, never defaulted in code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import venv

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app, main
from archflow_studio_api.settings import (
    CAD_EXPORT_ENV,
    CAD_EXPORT_OCCT,
    CAD_EXPORT_OFF,
    CAD_EXPORT_RHINO,
    LOCAL_MODE,
    REMOTE_MODE,
    RHINO_EXPORT_ENV,
    SettingsError,
    StudioSettings,
    cad_export_from_env,
    user_settings_path,
)

from .support import make_empty_project

# The launcher is a Windows PowerShell 5.1 script; its env forwarding is read
# through the interpreter itself. Found by its system path so a shell with a
# stripped PATH still finds it, and skipped where there is none.
POWERSHELL = shutil.which("powershell.exe") or str(
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
)
LAUNCHER = Path(__file__).resolve().parents[2] / "launch-studio.ps1"
ENV_BLOCK_START = "# --- environment the API reads"
ENV_BLOCK_END = "$env:PYTHONUNBUFFERED"


class SettingsTests(unittest.TestCase):
    def test_local_mode_accepts_only_loopback_listeners(self) -> None:
        for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(
                    StudioSettings(project_dir=Path("p"), bind_host=host).bind_host,
                    host,
                )
        for host in ("0.0.0.0", "::", "192.168.1.2", "studio.example"):
            with self.subTest(host=host), self.assertRaisesRegex(SettingsError, "loopback"):
                StudioSettings(project_dir=Path("p"), bind_host=host)

    def test_cli_validates_the_effective_listener_before_starting(self) -> None:
        with patch.dict(os.environ, {
            "ARCHFLOW_STUDIO_PROJECT_DIR": "unused-project",
            "ARCHFLOW_STUDIO_BIND": "127.0.0.1",
        }, clear=True), patch("archflow_studio_api.main.uvicorn.run") as serve:
            with self.assertRaisesRegex(SettingsError, "loopback"):
                main(["--host", "0.0.0.0"])
            serve.assert_not_called()

        with patch.dict(os.environ, {
            "ARCHFLOW_STUDIO_PROJECT_DIR": "unused-project",
            "ARCHFLOW_STUDIO_BIND": "0.0.0.0",
        }, clear=True), patch("archflow_studio_api.main.create_app") as create, patch(
            "archflow_studio_api.main.uvicorn.run"
        ) as serve:
            main(["--host", "127.0.0.1"])
            self.assertEqual(create.call_args.args[0].bind_host, "127.0.0.1")
            self.assertEqual(serve.call_args.kwargs["host"], "127.0.0.1")

    def test_missing_project_dir_refuses_and_names_the_variable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ARCHFLOW_STUDIO_PROJECT_DIR", None)
            with self.assertRaises(SettingsError) as raised:
                StudioSettings.from_env()
        self.assertIn("ARCHFLOW_STUDIO_PROJECT_DIR", str(raised.exception))

    def test_project_dir_comes_from_the_environment(self) -> None:
        with patch.dict(
            os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"}, clear=False
        ):
            self.assertEqual(StudioSettings.from_env().project_dir, Path("some/project"))

    def test_the_reference_run_is_configured_and_otherwise_unset(self) -> None:
        with patch.dict(
            os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"}, clear=False
        ):
            os.environ.pop("ARCHFLOW_STUDIO_REFERENCE_RUN", None)
            self.assertIsNone(StudioSettings.from_env().reference_run)
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_REFERENCE_RUN": " run-002 ",
            },
            clear=False,
        ):
            self.assertEqual(StudioSettings.from_env().reference_run, "run-002")

    def test_the_mode_bind_and_token_are_read_from_the_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"},
            clear=False,
        ):
            for name in (
                "ARCHFLOW_STUDIO_MODE",
                "ARCHFLOW_STUDIO_BIND",
                "ARCHFLOW_STUDIO_TOKEN",
                "ARCHFLOW_STUDIO_ORIGINS",
            ):
                os.environ.pop(name, None)
            settings = StudioSettings.from_env()
        self.assertEqual(settings.mode, LOCAL_MODE)
        self.assertEqual(settings.bind_host, "127.0.0.1")
        self.assertIsNone(settings.api_token)
        self.assertEqual(settings.origins, ())
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_MODE": " remote ",
                "ARCHFLOW_STUDIO_BIND": "0.0.0.0",
                "ARCHFLOW_STUDIO_TOKEN": " s3cret ",
                "ARCHFLOW_STUDIO_ORIGINS": "https://a.example, https://b.example ,",
            },
            clear=False,
        ):
            settings = StudioSettings.from_env()
        self.assertEqual(settings.mode, REMOTE_MODE)
        self.assertEqual(settings.bind_host, "0.0.0.0")
        self.assertEqual(settings.api_token, "s3cret")
        self.assertEqual(
            settings.origins, ("https://a.example", "https://b.example")
        )

    # ---- the one CAD export setting

    def test_a_process_nothing_configured_exports_through_occt(self) -> None:
        self.assertEqual(cad_export_from_env({}), CAD_EXPORT_OCCT)
        self.assertEqual(StudioSettings(project_dir=Path("p")).cad_export, CAD_EXPORT_OCCT)
        settings = StudioSettings(project_dir=Path("p"))
        self.assertTrue(settings.exports)
        self.assertFalse(settings.rhino_lane)

    def test_the_legacy_boolean_only_enables_or_disables_and_never_selects_rhino(self) -> None:
        # An explicit ``0`` written by an older launcher stays effective: no export.
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: "0"}), CAD_EXPORT_OFF)
        # ``1`` meant "export"; export now means the ordinary OCCT path, not Rhino.
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: "1"}), CAD_EXPORT_OCCT)
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: ""}), CAD_EXPORT_OCCT)
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: "yes"}), CAD_EXPORT_OFF)

    def test_the_new_variable_wins_over_the_legacy_one(self) -> None:
        for value, expected in (("rhino", CAD_EXPORT_RHINO), (" OCCT ", CAD_EXPORT_OCCT), ("off", CAD_EXPORT_OFF)):
            with self.subTest(value=value):
                self.assertEqual(
                    cad_export_from_env({CAD_EXPORT_ENV: value, RHINO_EXPORT_ENV: "0"}), expected
                )

    def test_an_unknown_cad_export_is_refused_by_name(self) -> None:
        with self.assertRaises(SettingsError) as raised:
            cad_export_from_env({CAD_EXPORT_ENV: "sideways"})
        self.assertIn(CAD_EXPORT_ENV, str(raised.exception))
        self.assertIn("sideways", str(raised.exception))
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=Path("p"), cad_export="sideways")

    def test_from_env_reads_the_cad_export_through_the_same_rule(self) -> None:
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project", RHINO_EXPORT_ENV: "0"},
            clear=False,
        ):
            os.environ.pop(CAD_EXPORT_ENV, None)
            self.assertEqual(StudioSettings.from_env().cad_export, CAD_EXPORT_OFF)
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project", CAD_EXPORT_ENV: "rhino", RHINO_EXPORT_ENV: "0"},
            clear=False,
        ):
            settings = StudioSettings.from_env()
        self.assertEqual(settings.cad_export, CAD_EXPORT_RHINO)
        self.assertTrue(settings.rhino_lane)

    def test_remote_mode_from_the_environment_refuses_without_a_token(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_MODE": "remote",
            },
            clear=False,
        ):
            os.environ.pop("ARCHFLOW_STUDIO_TOKEN", None)
            with self.assertRaises(SettingsError) as raised:
                StudioSettings.from_env()
        self.assertIn("ARCHFLOW_STUDIO_TOKEN", str(raised.exception))


class UserSettingsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio user settings ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"APPDATA": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(create_app(StudioSettings(project_dir=self.root / "not-a-project")))
        self.addCleanup(self.client.close)

    def test_absent_settings_need_no_project_and_create_no_file(self) -> None:
        self.assertIn("user-settings", self.client.get("/api/protocol").json()["capabilities"])
        response = self.client.get("/api/settings/user")
        self.assertEqual((response.status_code, response.json()), (200, {}))
        self.assertFalse(user_settings_path().exists())

    def test_save_survives_a_fresh_process_configuration_without_switching_the_compiler(self) -> None:
        payload = {"language": "zh-CN", "theme": "light", "fontScale": 1.1,
                   "intentProvider": "codex", "intentModel": "saved-model", "intentTimeoutS": 45.5}
        response = self.client.put("/api/settings/user", json=payload)
        self.assertEqual((response.status_code, response.json()), (200, payload))
        self.assertEqual(json.loads(user_settings_path().read_text(encoding="utf-8")), payload)
        self.assertEqual(self.client.app.state.settings.intent_provider, "deterministic")
        with TestClient(create_app(StudioSettings(project_dir=self.root / "still-not-a-project"))) as restarted:
            self.assertEqual(restarted.get("/api/settings/user").json(), payload)
        self.assertEqual([path.name for path in user_settings_path().parent.iterdir()], ["settings.json"])

    def test_put_replaces_the_file_and_null_clears_an_override(self) -> None:
        self.client.put("/api/settings/user", json={"intentModel": "old-model", "theme": "dark"})
        response = self.client.put("/api/settings/user", json={"language": "en", "intentModel": None})
        self.assertEqual(response.json(), {"language": "en"})
        self.assertEqual(self.client.get("/api/settings/user").json(), {"language": "en"})

    def test_unsupported_values_and_secret_fields_cannot_replace_saved_settings(self) -> None:
        self.client.put("/api/settings/user", json={"theme": "light"})
        for payload in ({"language": "zh"}, {"language": ["en"]}, {"theme": "auto"},
                        {"theme": ["light"]}, {"intentProvider": ["codex"]}, {"fontScale": True},
                        {"fontScale": 2}, {"intentProvider": "new-provider"},
                        {"intentModel": "  "}, {"intentModel": "invalid\u0000model"},
                        {"intentTimeoutS": 0}, {"intentTimeoutS": True},
                        {"intentTimeoutS": "60"}, {"token": "private-token"},
                        {"codexExecutable": "another-program"}):
            with self.subTest(payload=payload):
                response = self.client.put("/api/settings/user", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("private-token", response.text)
                self.assertEqual(self.client.get("/api/settings/user").json(), {"theme": "light"})

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
                self.assertEqual(self.client.get("/api/protocol").status_code, 200)
                self.assertEqual(self.client.put("/api/settings/user", json={"theme": "system"}).status_code, 200)
                self.assertEqual(self.client.get("/api/settings/user").json(), {"theme": "system"})

    def test_failed_atomic_replace_preserves_the_prior_file(self) -> None:
        self.client.put("/api/settings/user", json={"theme": "dark"})
        prior = user_settings_path().read_bytes()
        with patch("archflow_studio_api.settings.os.replace", side_effect=OSError("replace refused")):
            response = self.client.put("/api/settings/user", json={"theme": "light"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(user_settings_path().read_bytes(), prior)
        self.assertEqual([path.name for path in user_settings_path().parent.iterdir()], ["settings.json"])

    def test_remote_does_not_declare_or_access_the_users_settings(self) -> None:
        settings = StudioSettings(project_dir=self.root / "unbound", mode=REMOTE_MODE,
                                  api_token="test-token", origins=("https://studio.example",))
        with TestClient(create_app(settings)) as remote:
            self.assertNotIn("user-settings", remote.get("/api/protocol").json()["capabilities"])
            self.assertEqual(remote.get("/api/settings/user").status_code, 401)
            headers = {"Authorization": "Bearer test-token"}
            with patch("archflow_studio_api.routes.settings.read_user_settings", side_effect=AssertionError("read local file")), \
                 patch("archflow_studio_api.routes.settings.save_user_settings", side_effect=AssertionError("wrote local file")):
                self.assertEqual(remote.get("/api/settings/user", headers=headers).status_code, 404)
                self.assertEqual(remote.put("/api/settings/user", json={"theme": "dark"}, headers=headers).status_code, 404)
                self.assertEqual(remote.put("/api/settings/user", json={"token": "invalid"}, headers=headers).status_code, 404)
        self.assertFalse(user_settings_path().exists())


@unittest.skipUnless(Path(POWERSHELL).is_file(), "Windows launcher")
class LauncherCadExportForwardingTests(unittest.TestCase):
    """The launcher's environment block, run by itself over a runtime.json of each shape.

    Only the statements between the environment marker and the unbuffered
    flag are executed, with ``$runtime`` and ``$projectDir`` set as the
    launcher would have them; no server, install or UI is touched. What is
    read back is the two variables the API's settings read.
    """

    @classmethod
    def setUpClass(cls) -> None:
        text = LAUNCHER.read_text(encoding="utf-8")
        start = text.index(ENV_BLOCK_START)
        end = text.index(ENV_BLOCK_END, start)
        cls.block = text[start:end]
        cls.temporary = tempfile.TemporaryDirectory(prefix="studio launcher env ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.harness = Path(cls.temporary.name) / "forward env.ps1"
        cls.harness.write_text(
            "param([string]$RuntimeConfig, [string]$ProbePython)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "$runtime = Get-Content -LiteralPath $RuntimeConfig -Raw -Encoding utf8 | ConvertFrom-Json\n"
            "$projectDir = [string]$runtime.project_dir\n"
            + cls.block
            + "\n$probe = & $ProbePython -c 'import json; from archflow_studio_api.settings import StudioSettings; s=StudioSettings.from_env(); print(json.dumps(dict(provider=s.intent_provider, model=s.intent_model, timeout=s.intent_timeout_s)))'\n"
            + "if ($LASTEXITCODE -ne 0) { throw 'API environment probe failed' }\n"
            + "[pscustomobject]@{ Cad = $env:ARCHFLOW_STUDIO_CAD_EXPORT; Rhino = $env:ARCHFLOW_STUDIO_RHINO_EXPORT; Project = $env:ARCHFLOW_STUDIO_PROJECT_DIR; Codex = $env:ARCHFLOW_STUDIO_CODEX; ReferenceRun = $env:ARCHFLOW_STUDIO_REFERENCE_RUN; Api = ($probe | ConvertFrom-Json) } | ConvertTo-Json -Compress\n",
            encoding="utf-8-sig",
        )

    def forwarded_values(self, runtime: dict, *, saved: dict | None = None, raw: str | None = None,
                         inherited: dict | None = None) -> dict:
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("ARCHFLOW_STUDIO_")
        }
        environment.update(inherited or {})
        environment["PYTHONPATH"] = os.pathsep.join((str(LAUNCHER.parents[2]), str(LAUNCHER.parent / "api")))
        # A stale value from a previous launch must be cleared, not inherited.
        environment["ARCHFLOW_STUDIO_CAD_EXPORT"] = "stale"
        environment["ARCHFLOW_STUDIO_RHINO_EXPORT"] = "stale"
        with tempfile.TemporaryDirectory(prefix="launcher AppData with spaces ") as appdata:
            environment["APPDATA"] = appdata
            runtime_file = Path(appdata) / "runtime.json"
            runtime_file.write_text(json.dumps({"project_dir": "unused", **runtime}), encoding="utf-8")
            runtime_before = runtime_file.read_bytes()
            settings_file = Path(appdata) / "MonkeyArch" / "settings.json"
            if saved is not None or raw is not None:
                settings_file.parent.mkdir()
                settings_file.write_text(raw if raw is not None else json.dumps(saved), encoding="utf-8")
            before = settings_file.read_bytes() if settings_file.exists() else None
            result = subprocess.run(
                [
                    POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.harness), "-RuntimeConfig", str(runtime_file),
                    "-ProbePython", sys.executable,
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(settings_file.read_bytes() if settings_file.exists() else None, before)
            self.assertEqual(runtime_file.read_bytes(), runtime_before)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout.strip().splitlines()[-1])
        answer["warned"] = "Saved user settings could not be read" in result.stdout
        return answer

    def forwarded(self, runtime: dict) -> tuple[str | None, str | None]:
        answer = self.forwarded_values(runtime)
        return answer["Cad"], answer["Rhino"]

    @staticmethod
    def as_environ(forwarded: tuple[str | None, str | None]) -> dict[str, str]:
        """The two variables as the API process would see them: unset ones absent."""
        cad, rhino = forwarded
        return {
            name: value
            for name, value in ((CAD_EXPORT_ENV, cad), (RHINO_EXPORT_ENV, rhino))
            if value is not None
        }

    def test_a_file_naming_neither_leaves_the_api_to_its_default(self) -> None:
        self.assertEqual(self.forwarded({}), (None, None))

    def test_a_legacy_explicit_false_still_arrives_as_disabled(self) -> None:
        self.assertEqual(self.forwarded({"rhino_export": False}), (None, "0"))

    def test_a_legacy_true_still_arrives_as_enabled(self) -> None:
        self.assertEqual(self.forwarded({"rhino_export": True}), (None, "1"))

    def test_cad_export_is_forwarded_and_wins_over_the_legacy_key(self) -> None:
        self.assertEqual(self.forwarded({"cad_export": "occt", "rhino_export": False}), ("occt", None))
        self.assertEqual(self.forwarded({"cad_export": "rhino"}), ("rhino", None))
        self.assertEqual(self.forwarded({"cad_export": "off"}), ("off", None))

    def test_a_blank_cad_export_does_not_silence_an_explicit_legacy_false(self) -> None:
        # A `cad_export` of nothing but whitespace names no export mode. Forwarded as-is
        # it would be stripped to empty by the API and fall to the OCCT default, while
        # the launcher had already dropped the `rhino_export: false` that meant "off".
        for blank in ("", "   ", " \t "):
            with self.subTest(cad_export=repr(blank)):
                forwarded = self.forwarded({"cad_export": blank, "rhino_export": False})
                self.assertEqual(forwarded, (None, "0"))
                self.assertEqual(cad_export_from_env(self.as_environ(forwarded)), CAD_EXPORT_OFF)

    def test_a_padded_mixed_case_cad_export_still_arrives_as_that_mode(self) -> None:
        # Trimming for the presence decision must not lose an ordinary value that the
        # API accepts after its own strip-and-lower.
        forwarded = self.forwarded({"cad_export": "  Rhino ", "rhino_export": False})
        self.assertEqual(forwarded, ("Rhino", None))
        self.assertEqual(cad_export_from_env(self.as_environ(forwarded)), CAD_EXPORT_RHINO)

    def test_saved_intent_defaults_reach_the_child_over_old_runtime_defaults(self) -> None:
        answer = self.forwarded_values(
            {"intent_provider": "codex", "intent_model": "old-runtime-model", "intent_timeout_s": 120,
             "codex": "unchanged-codex-path", "reference_run": "unchanged-run", "cad_export": "off"},
            saved={"language": "zh-CN", "theme": "light", "fontScale": 1.1,
                   "intentProvider": "anthropic", "intentModel": "saved-model", "intentTimeoutS": 45.5},
            inherited={"ARCHFLOW_STUDIO_INTENT_MODEL": "old-environment-model"},
        )
        self.assertEqual(answer["Api"], {"provider": "anthropic", "model": "saved-model", "timeout": 45.5})
        self.assertEqual((answer["Project"], answer["Codex"], answer["ReferenceRun"], answer["Cad"]),
                         ("unused", "unchanged-codex-path", "unchanged-run", "off"))
        self.assertFalse(answer["warned"])

    def test_cleared_saved_fields_restore_runtime_then_environment_then_api_defaults(self) -> None:
        inherited = {"ARCHFLOW_STUDIO_INTENT_MODEL": "environment-model"}
        for saved in (None, {}, {"intentModel": None, "theme": "dark"}):
            with self.subTest(saved=saved):
                self.assertEqual(self.forwarded_values({"intent_model": "runtime-model"}, saved=saved, inherited=inherited)["Api"]["model"], "runtime-model")
                self.assertEqual(self.forwarded_values({}, saved=saved, inherited=inherited)["Api"]["model"], "environment-model")
        self.assertEqual(self.forwarded_values({})["Api"], {"provider": "deterministic", "model": None, "timeout": 120.0})

    def test_invalid_user_file_warns_and_keeps_the_runtime_defaults(self) -> None:
        for raw in ("{unfinished", '[]', '[{"intentModel":"wrong-root"}]',
                    '{"intentModel":"saved","fontScale":true}', '{"intentTimeoutS":"60"}',
                    '{"intentTimeoutS":-2}', '{"intentProvider":"new-provider"}',
                    '{"intentProvider":["codex"]}', '{"language":["en"]}', '{"theme":["light"]}',
                    '{"intentModel":"saved","token":"private-token"}'):
            with self.subTest(raw=raw):
                answer = self.forwarded_values({"intent_model": "runtime-model"}, raw=raw)
                self.assertEqual(answer["Api"]["model"], "runtime-model")
                self.assertTrue(answer["warned"])

    def test_remote_mode_does_not_read_or_apply_the_local_preferences(self) -> None:
        inherited = {"ARCHFLOW_STUDIO_MODE": "remote", "ARCHFLOW_STUDIO_TOKEN": "test-token",
                     "ARCHFLOW_STUDIO_ORIGINS": "https://studio.example"}
        for raw in ('{"intentModel":"local-user-model"}', '{broken'):
            with self.subTest(raw=raw):
                answer = self.forwarded_values({"intent_model": "remote-runtime-model"}, raw=raw, inherited=inherited)
                self.assertEqual(answer["Api"]["model"], "remote-runtime-model")
                self.assertFalse(answer["warned"])


@unittest.skipUnless(shutil.which("powershell.exe"), "Windows launcher")
class LauncherSettingsTests(unittest.TestCase):
    """Read the launcher's real setup statements without running its UI or servers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="studio launcher ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.project = make_empty_project(cls.root).layout.root
        cls.python = cls.root / "Python environment" / "Scripts" / "python.exe"
        venv.EnvBuilder(with_pip=False).create(cls.python.parent.parent)
        cls.launcher = Path(__file__).resolve().parents[2] / "launch-studio.ps1"
        cls.harness = cls.root / "read launcher config.ps1"
        cls.harness.write_text(
            r'''
param([string]$LauncherPath, [string]$RuntimeConfig, [string]$ProbeRoot)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$studioRoot = Split-Path -Parent $LauncherPath
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($LauncherPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$quiet = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Quiet'
}, $false)
. ([scriptblock]::Create($quiet.Extent.Text))
function New-Splash { }
function Set-SplashStep { }
$startup = $ast.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.TryStatementAst] } | Select-Object -Last 1
# Stop at the first dependency probe, before installs, environment writes or servers.
$reachedProbe = $false
foreach ($statement in $startup.Body.Statements) {
    if ($statement -is [System.Management.Automation.Language.AssignmentStatementAst] -and $statement.Left.Extent.Text -eq '$check') {
        $reachedProbe = $true
        break
    }
    . ([scriptblock]::Create($statement.Extent.Text)) 6>$null
}
if (-not $reachedProbe) { throw 'Launcher dependency probe was not found' }
$probe = Invoke-Quiet $pythonExe ($pythonArgs + @('-c', 'import json, sys; print(json.dumps(dict(executable=sys.executable, isolated=sys.flags.isolated)))'))
if ($probe.ExitCode -ne 0) { throw $probe.Output }
$child = Start-Process -FilePath $pythonExe -ArgumentList ($pythonArgs + @('-V')) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $ProbeRoot 'python.out') -RedirectStandardError (Join-Path $ProbeRoot 'python.err')
[pscustomobject]@{
    Config = $RuntimeConfig; ProjectDir = $projectDir; ApiPort = $apiPort; WebPort = $webPort
    Exe = $pythonExe; Arguments = @($pythonArgs); Probe = ($probe.Output | ConvertFrom-Json); ChildExit = $child.ExitCode
} | ConvertTo-Json -Depth 4 -Compress
''',
            encoding="utf-8-sig",
        )

    def read_config(self, python: str) -> dict:
        config = self.root / "external runtime config.json"
        settings = json.loads(
            self.launcher.with_name("runtime.example.json").read_text(encoding="utf-8")
        )
        settings.update(
            project_dir=str(self.project), python=python, api_port=18080, web_port=15174
        )
        config.write_text(
            json.dumps(settings),
            encoding="utf-8",
        )
        result = self.invoke_config(config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(Path(answer["Config"]), config)
        self.assertEqual(Path(answer["ProjectDir"]), self.project)
        self.assertEqual((answer["ApiPort"], answer["WebPort"]), (18080, 15174))
        self.assertEqual(answer["ChildExit"], 0)
        return answer

    def invoke_config(self, config: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.harness), "-LauncherPath", str(self.launcher),
                "-RuntimeConfig", str(config), "-ProbeRoot", str(self.root),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )

    def test_missing_runtime_config_explains_local_setup(self) -> None:
        config = self.root / "missing runtime.json"
        result = self.invoke_config(config)
        self.assertNotEqual(result.returncode, 0)
        detail = result.stdout + result.stderr
        for required in ("runtime.example.json", "project_dir", "python", "-RuntimeConfig"):
            self.assertIn(required, detail)
        self.assertFalse(config.exists())

    def test_shared_example_requires_a_chosen_project(self) -> None:
        result = self.invoke_config(self.launcher.with_name("runtime.example.json"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set project_dir", result.stdout + result.stderr)

    def test_external_config_starts_venv_python_with_spaces(self) -> None:
        answer = self.read_config(str(self.python))
        self.assertEqual(Path(answer["Exe"]), self.python)
        self.assertEqual(answer["Arguments"], [])
        self.assertEqual(Path(answer["Probe"]["executable"]), self.python)

    def test_quoted_executable_preserves_python_flags(self) -> None:
        answer = self.read_config(f'"{self.python}" -I')
        self.assertEqual(Path(answer["Exe"]), self.python)
        self.assertEqual(answer["Arguments"], ["-I"])
        self.assertEqual(Path(answer["Probe"]["executable"]), self.python)
        self.assertEqual(answer["Probe"]["isolated"], 1)

    @unittest.skipUnless(shutil.which("py"), "Python launcher is not installed")
    def test_legacy_python_launcher_and_default(self) -> None:
        for command in ("py -3.12", ""):
            with self.subTest(command=command):
                answer = self.read_config(command)
                self.assertEqual(answer["Exe"], "py")
                self.assertEqual(answer["Arguments"], ["-3.12"])


@unittest.skipUnless(Path(POWERSHELL).exists(), "Windows launcher")
class LauncherLifecycleTests(unittest.TestCase):
    """Exercise the launcher's owned pipes and identity checks without its desktop UI."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Monkey launcher 中文 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = dict(os.environ, APPDATA=str(self.root / "appdata"),
                                LOCALAPPDATA=str(self.root / "localappdata"))
        self.child = self.root / "private child.py"
        self.child.write_text('''
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(sys.argv[1:], ensure_ascii=False), flush=True)
if sys.argv[1:2] == ["service"]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(dict(status="ok", service="launcher-test", serverVersion="0.1.0",
                processId=os.getpid(), parentProcessId=os.getppid(), managedInstanceId="test-instance",
                sourceRevision="a" * 40)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("PORT " + str(server.server_port), flush=True)
request = sys.stdin.readline()
print("REQUEST " + (request.strip() or "EOF"), flush=True)
time.sleep(0.15)
if sys.argv[1:2] == ["service"]:
    server.shutdown()
    server.server_close()
print("FINISHED", flush=True)
''', encoding="utf-8")
        self.harness = self.root / "exercise launcher.ps1"
        self.harness.write_text(r'''
param([string]$Launcher, [string]$Config)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$settings = Get-Content -LiteralPath $Config -Raw -Encoding utf8 | ConvertFrom-Json
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Launcher, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$names = @('Invoke-Quiet', 'Test-PortFree', 'Get-LogTail', 'Lock-LaunchPorts', 'Get-SourceRevision',
    'ConvertTo-ProcessArgument', 'Start-OwnedProcess', 'Wait-ManagedHealth', 'Get-ManagedViteScript', 'Stop-Children', 'Invoke-HubLaunch')
foreach ($definition in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if ($definition.Name -in $names) { . ([scriptblock]::Create($definition.Extent.Text)) }
}
function New-Splash { }
function Close-Splash { }
function Set-SplashStep { }
function Invoke-Pump([int]$Milliseconds) { $script:PumpCount++; Start-Sleep -Milliseconds 10 }
$script:Children = @(); $script:LaunchLocks = @(); $script:PumpCount = 0
try {
    if ($settings.Action -eq 'hub') {
        $repoRoot = Split-Path (Split-Path (Split-Path $Launcher -Parent) -Parent) -Parent
        $RuntimeRoot = $settings.RuntimeRoot; $HubWebDir = $settings.HubWebDir; $StudioWebDir = $settings.StudioWebDir
        $Python = $settings.Executable; $Port = $settings.Port
        $logRoot = Join-Path $RuntimeRoot 'logs'; $stamp = 'private-test'
        New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
        Invoke-HubLaunch
        $child = $script:Children[0]
        Stop-Children
        [pscustomobject]@{ ExitCode = $child.Process.ExitCode; Url = $script:WebUrl
            OutputLog = $child.OutputLog } | ConvertTo-Json -Compress
        return
    }
    if ($settings.Action -eq 'hold-lock') {
        Lock-LaunchPorts @($settings.Port)
        [IO.File]::WriteAllText($settings.ReadyFile, 'ready')
        [void][Console]::ReadLine()
        return
    }
    if ($settings.Action -eq 'lock') {
        $problem = ''
        try { Lock-LaunchPorts @($settings.Port) } catch { $problem = $_.Exception.Message }
        [pscustomobject]@{ Problem = $problem } | ConvertTo-Json -Compress
        return
    }
    if ($settings.Action -eq 'revision') {
        [pscustomobject]@{ Revision = (Get-SourceRevision $settings.Directory) } | ConvertTo-Json -Compress
        return
    }
    $arguments = @($settings.Arguments)
    if ($settings.Action -eq 'node') { $arguments = @('--input-type=module', '-e', (Get-ManagedViteScript), '--', "$($settings.Port)", 'test-instance') }
    $child = Start-OwnedProcess 'private test child' $settings.Executable $arguments $settings.Directory `
        (Join-Path $settings.Directory 'child.out') (Join-Path $settings.Directory 'child.err')
    $problem = ''
    if ($settings.Action -eq 'health') {
        $deadline = (Get-Date).AddSeconds(10)
        $port = $null
        while (-not $port -and (Get-Date) -lt $deadline -and -not $child.Process.HasExited) {
            $match = [regex]::Match((Get-LogTail $child.OutputLog 10), 'PORT (\d+)')
            if ($match.Success) { $port = [int]$match.Groups[1].Value } else { Invoke-Pump 10 }
        }
        if (-not $port) { throw 'Private health child never announced its port' }
        try { [void](Wait-ManagedHealth "http://127.0.0.1:$port/api/health" 5 $child.Process $settings.Instance 'launcher-test' $settings.Revision) }
        catch { $problem = $_.Exception.Message }
    }
    if ($settings.Action -eq 'node') {
        $deadline = (Get-Date).AddSeconds(10)
        while (-not (Get-LogTail $child.OutputLog 10).Contains('MONKEY_WEB_READY') -and (Get-Date) -lt $deadline -and -not $child.Process.HasExited) { Invoke-Pump 10 }
        if (-not (Get-LogTail $child.OutputLog 10).Contains('MONKEY_WEB_READY')) { throw (Get-LogTail $child.ErrorLog 20) }
    }
    $aliveBeforeStop = -not $child.Process.HasExited
    if ($settings.Eof) { $child.Process.StandardInput.Close() }
    Stop-Children
    [pscustomobject]@{ ExitCode = $child.Process.ExitCode; Problem = $problem; AliveBeforeStop = $aliveBeforeStop
        Pumps = $script:PumpCount; Output = [IO.File]::ReadAllText($child.OutputLog, [Text.Encoding]::UTF8)
        Error = [IO.File]::ReadAllText($child.ErrorLog, [Text.Encoding]::UTF8) } | ConvertTo-Json -Compress
} finally {
    Stop-Children
    foreach ($mutex in $script:LaunchLocks) { $mutex.ReleaseMutex(); $mutex.Dispose() }
}
''', encoding="utf-8-sig")

    def invoke(self, **changes) -> dict:
        settings = dict(Action="pipes", Executable=sys.executable, Directory=str(self.root),
                        Arguments=["-u", str(self.child)], Eof=False, Port=0)
        settings.update(changes)
        config = self.root / "test config.json"
        config.write_text(json.dumps(settings), encoding="utf-8")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(self.harness), "-Launcher", str(LAUNCHER), "-Config", str(config)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=30, env=self.environment,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_owned_stdin_stop_and_eof_wait_for_work_and_preserve_output(self) -> None:
        arguments = ["a space", 'embedded"quote', "trailing slash \\", "中文", ""]
        for eof in (False, True):
            with self.subTest(eof=eof):
                answer = self.invoke(Arguments=["-u", str(self.child), *arguments], Eof=eof)
                self.assertEqual(answer["ExitCode"], 0, answer)
                lines = answer["Output"].splitlines()
                self.assertEqual(json.loads(lines[0]), arguments)
                self.assertEqual(lines[-2:], ["REQUEST EOF" if eof else "REQUEST stop", "FINISHED"])
                self.assertGreater(answer["Pumps"], 0)

    def test_health_requires_owned_instance_and_exact_source_without_stopping_on_mismatch(self) -> None:
        for instance, revision, matches in (("test-instance", "a" * 40, True),
                                            ("another-instance", "a" * 40, False),
                                            ("test-instance", "b" * 40, False)):
            with self.subTest(instance=instance, revision=revision):
                answer = self.invoke(Action="health", Arguments=["-u", str(self.child), "service"],
                                     Instance=instance, Revision=revision)
                self.assertEqual(not answer["Problem"], matches, answer)
                self.assertTrue(answer["AliveBeforeStop"])
                self.assertEqual(answer["ExitCode"], 0, answer)
        # Windows venv Python may retain a redirector process above the serving child.
        virtual_environment = self.root / "managed venv"
        venv.EnvBuilder(with_pip=False).create(virtual_environment)
        answer = self.invoke(Action="health", Arguments=["-u", str(self.child), "service"],
                             Executable=str(virtual_environment / "Scripts/python.exe"),
                             Instance="test-instance", Revision="a" * 40)
        self.assertFalse(answer["Problem"], answer)
        self.assertEqual(answer["ExitCode"], 0, answer)

    def test_foreign_listener_is_left_open(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            answer = self.invoke(Action="lock", Port=port)
            self.assertIn("already in use", answer["Problem"])
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass

    def test_packaged_revision_works_without_git(self) -> None:
        (self.root / "source-version.txt").write_text("a" * 40 + "\n", encoding="utf-8")
        self.assertEqual(self.invoke(Action="revision")["Revision"], "a" * 40)

    def test_hub_branch_starts_and_stops_only_its_private_service(self) -> None:
        for name in ("hub dist", "studio dist"):
            directory = self.root / name
            directory.mkdir()
            (directory / "index.html").write_text("<title>Private launcher test</title>", encoding="utf-8")
        with socket.socket() as selection:
            selection.bind(("127.0.0.1", 0))
            port = selection.getsockname()[1]
        runtime = self.root / "external runtime"
        answer = self.invoke(Action="hub", RuntimeRoot=str(runtime), HubWebDir=str(self.root / "hub dist"),
                             StudioWebDir=str(self.root / "studio dist"), Port=port)
        self.assertEqual(answer["ExitCode"], 0, answer)
        self.assertEqual(answer["Url"], f"http://127.0.0.1:{port}")
        self.assertTrue(Path(answer["OutputLog"]).is_relative_to(runtime))
        with socket.socket() as released:
            released.bind(("127.0.0.1", port))

    def test_second_launcher_cannot_acquire_the_same_port(self) -> None:
        with socket.socket() as selection:
            selection.bind(("127.0.0.1", 0))
            port = selection.getsockname()[1]
        ready = self.root / "first launcher ready"
        config = self.root / "lock owner.json"
        config.write_text(json.dumps(dict(Action="hold-lock", Port=port, ReadyFile=str(ready))), encoding="utf-8")
        owner = subprocess.Popen([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                  str(self.harness), "-Launcher", str(LAUNCHER), "-Config", str(config)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", env=self.environment,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "First launcher did not acquire its private port lock")
            self.assertIn("Another Monkey launcher", self.invoke(Action="lock", Port=port)["Problem"])
            self.assertIsNone(owner.poll())
        finally:
            stdout, stderr = owner.communicate("\n", timeout=10)
        self.assertEqual(owner.returncode, 0, stdout + stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_vite_entry_syncs_and_closes_on_stop_or_eof(self) -> None:
        # A private Vite-shaped HTTP fixture checks the actual Node entry's lifetime,
        # including asynchronous server.close, without installing or opening the user's app.
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "sync-rhino3dm.mjs").write_text("console.log('SYNCED');", encoding="utf-8")
        package = self.root / "node_modules" / "vite"
        package.mkdir(parents=True)
        (package / "package.json").write_text('{"type":"module","exports":"./index.js"}', encoding="utf-8")
        (package / "index.js").write_text('''
import http from 'node:http';
export async function createServer() {
  const server = http.createServer((request, response) => response.end('private test'));
  return {
    listen: () => new Promise(resolve => server.listen(0, '127.0.0.1', resolve)),
    close: () => new Promise(resolve => server.close(() => setTimeout(() => { console.log('CLOSED'); resolve(); }, 120)))
  };
}
''', encoding="utf-8")
        for eof in (False, True):
            with self.subTest(eof=eof):
                answer = self.invoke(Action="node", Executable=shutil.which("node"), Eof=eof)
                self.assertEqual(answer["ExitCode"], 0, answer)
                self.assertIn("SYNCED", answer["Output"])
                self.assertIn("MONKEY_WEB_READY", answer["Output"])
                self.assertIn("CLOSED", answer["Output"])

    @unittest.skipUnless(shutil.which("node") and os.environ.get("ARCHFLOW_LAUNCHER_VITE_MODULE"),
                         "Set ARCHFLOW_LAUNCHER_VITE_MODULE to an installed Vite entry for isolated integration")
    def test_actual_vite_releases_its_private_port_on_stop_and_eof(self) -> None:
        module = Path(os.environ["ARCHFLOW_LAUNCHER_VITE_MODULE"]).resolve()
        self.assertTrue(module.is_file())
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "sync-rhino3dm.mjs").write_text("console.log('SYNCED');", encoding="utf-8")
        (self.root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        (self.root / "index.html").write_text("<title>Private launcher test</title>", encoding="utf-8")
        (self.root / "vite.config.mjs").write_text(
            "export default {cacheDir: './.vite-cache', server: {host: '127.0.0.1'}};", encoding="utf-8")
        package = self.root / "node_modules" / "vite"
        package.mkdir(parents=True)
        (package / "package.json").write_text('{"type":"module","exports":"./index.js"}', encoding="utf-8")
        (package / "index.js").write_text(f"export {{createServer}} from {json.dumps(module.as_uri())};", encoding="utf-8")
        for eof in (False, True):
            with self.subTest(eof=eof):
                with socket.socket() as selection:
                    selection.bind(("127.0.0.1", 0))
                    port = selection.getsockname()[1]
                answer = self.invoke(Action="node", Executable=shutil.which("node"), Eof=eof, Port=port)
                self.assertEqual(answer["ExitCode"], 0, answer)
                self.assertIn("MONKEY_WEB_READY", answer["Output"])
                with socket.socket() as released:
                    released.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
