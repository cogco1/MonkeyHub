"""The browser-mode launcher's owned pipes, port lock, revision and health checks.

Exercised without its desktop UI: the harness dot-sources the launcher's functions
by name, stubs the launch window, and drives a private child. Windows PowerShell
5.1 only; skipped elsewhere.
"""

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
import venv

ROOT = Path(__file__).resolve().parents[4]

# The launcher is a Windows PowerShell 5.1 script. Found by its system path so a
# shell with a stripped PATH still finds it, and skipped where there is none.
POWERSHELL = shutil.which("powershell.exe") or str(
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
)
LAUNCHER = ROOT / "apps/monkeyhub/launch-hub.ps1"


@unittest.skipUnless(Path(POWERSHELL).exists(), "Windows launcher")
class LauncherLifecycleTests(unittest.TestCase):
    """Exercise the Hub launcher's owned pipes and identity checks without its desktop UI."""

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
    'ConvertTo-ProcessArgument', 'Start-OwnedProcess', 'Wait-ManagedHealth', 'Stop-Children', 'Invoke-HubLaunch')
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

    def test_hub_launch_starts_and_stops_only_its_private_service(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
