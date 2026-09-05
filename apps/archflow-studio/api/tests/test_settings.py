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
    LOCAL_MODE,
    REMOTE_MODE,
    SettingsError,
    StudioSettings,
)

from .support import make_empty_project


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
