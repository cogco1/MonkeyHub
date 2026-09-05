"""The project root is chosen by the operator, never defaulted in code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import venv

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
            "param([string]$RuntimeJson)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "$runtime = $RuntimeJson | ConvertFrom-Json\n"
            "$projectDir = [string]$runtime.project_dir\n"
            + cls.block
            + "\n[pscustomobject]@{ Cad = $env:ARCHFLOW_STUDIO_CAD_EXPORT; Rhino = $env:ARCHFLOW_STUDIO_RHINO_EXPORT } | ConvertTo-Json -Compress\n",
            encoding="utf-8-sig",
        )

    def forwarded(self, runtime: dict) -> tuple[str | None, str | None]:
        environment = {
            key: value for key, value in os.environ.items()
            if key not in ("ARCHFLOW_STUDIO_CAD_EXPORT", "ARCHFLOW_STUDIO_RHINO_EXPORT")
        }
        # A stale value from a previous launch must be cleared, not inherited.
        environment["ARCHFLOW_STUDIO_CAD_EXPORT"] = "stale"
        environment["ARCHFLOW_STUDIO_RHINO_EXPORT"] = "stale"
        result = subprocess.run(
            [
                POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.harness), "-RuntimeJson", json.dumps({"project_dir": "unused", **runtime}),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
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
        config.write_text(
            json.dumps(
                {
                    "schema_version": "archflow-studio-runtime@1",
                    "project_dir": str(self.project),
                    "python": python,
                    "api_port": 18080,
                    "web_port": 15174,
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.harness), "-LauncherPath", str(self.launcher),
                "-RuntimeConfig", str(config), "-ProbeRoot", str(self.root),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(Path(answer["Config"]), config)
        self.assertEqual(Path(answer["ProjectDir"]), self.project)
        self.assertEqual((answer["ApiPort"], answer["WebPort"]), (18080, 15174))
        self.assertEqual(answer["ChildExit"], 0)
        return answer

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


if __name__ == "__main__":
    unittest.main()
