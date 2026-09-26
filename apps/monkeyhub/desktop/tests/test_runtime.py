"""Opt-in Windows EXE lifecycle tests against real isolated Hub/Studio workers.

Build the production web clients and desktop EXE from this checkout, then set
MONKEYHUB_DESKTOP_EXE to its absolute path and run this file with unittest or
pytest. The Python interpreter running the tests supplies the Hub dependencies.
These checks observe a native window, completed WebView navigation, an unsent
chat draft through Windows UI Automation, identity-verified HTTP and process
exit; they do not claim visual rendering or interactive modeling acceptance.
"""

from contextlib import contextmanager, ExitStack
import base64
import ctypes
from ctypes import wintypes
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import site
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[4]
INSTALLED = os.environ.get("MONKEYHUB_INSTALLED_ROOT")
APPLICATION_ROOT = Path(INSTALLED) if INSTALLED else ROOT
EXE = os.environ.get("MONKEYHUB_DESKTOP_EXE")
START = re.compile(r"event=start pid=(\d+) url=(\S+) instance=(\S+) source=([0-9a-f]{40})")
STATE = re.compile(r"event=state state=(\w+) detail=(.*)")
PAGE_LOADED = re.compile(r"event=page-loaded url=(\S+)")


def wait_for(check, message, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(message() if callable(message) else message)


def request(url, *, method="GET", payload=None, raw=False, timeout=5):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    with build_opener(ProxyHandler({})).open(Request(
        url, method=method, data=body, headers={"Content-Type": "application/json"},
    ), timeout=timeout) as response:
        return response.read() if raw else json.load(response)


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def free_ports(count):
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return [listener.getsockname()[1] for listener in sockets]


def windows_read_probe(path):
    """Report the native error for the same read, never change access or locks."""
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        return {"operation": "CreateFileW(GENERIC_READ, shared read/write/delete)",
                "winerror": ctypes.get_last_error()}
    try:
        byte, count = ctypes.create_string_buffer(1), wintypes.DWORD()
        ok = kernel.ReadFile(handle, byte, 1, ctypes.byref(count), None)
        return {"operation": "ReadFile(first byte)", "ok": bool(ok),
                "winerror": 0 if ok else ctypes.get_last_error(), "bytesRead": count.value}
    finally:
        kernel.CloseHandle(handle)


class WindowsProcesses:
    """Retain handles to verified test processes, so PID reuse cannot target others."""

    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = (
            (self.kernel.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (self.kernel.TerminateProcess, [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            (self.kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.user.EnumWindows, [self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            (self.user.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (self.user.PostMessageW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        self.handles = {}
        self.webviews = set()
        self.cleaned = False

    def track_webviews(self, shell_pid):
        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260),
            ]
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for function in (self.kernel.Process32FirstW, self.kernel.Process32NextW):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            function.restype = wintypes.BOOL
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        rows = {}
        try:
            entry = ProcessEntry(dwSize=ctypes.sizeof(ProcessEntry))
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                rows[entry.pid] = (entry.parent, entry.exe.lower())
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        owned = {shell_pid}
        while descendants := {pid for pid, (parent, _) in rows.items() if parent in owned} - owned:
            owned.update(descendants)
        for pid in owned - {shell_pid}:
            if rows[pid][1] == "msedgewebview2.exe":
                try:
                    self.track(pid)
                    self.webviews.add(pid)
                except OSError as error:
                    if error.winerror != 87:  # It can exit between the snapshot and OpenProcess.
                        raise

    def track(self, pid):
        if pid in self.handles and self.exited(pid):
            self.kernel.CloseHandle(self.handles.pop(pid))
        if pid not in self.handles:
            handle = self.kernel.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handles[pid] = handle

    def exited(self, pid):
        result = self.kernel.WaitForSingleObject(self.handles[pid], 0)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def kill(self, pid):
        if not self.exited(pid) and not self.kernel.TerminateProcess(self.handles[pid], 97):
            raise ctypes.WinError(ctypes.get_last_error())

    @contextmanager
    def suspend(self, pid):
        """Temporarily stall only the retained, identity-verified Hub process."""
        if pid not in self.handles or self.exited(pid):
            raise AssertionError("The owned Hub must be alive before its health is stalled")

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                        ("tid", wintypes.DWORD), ("pid", wintypes.DWORD),
                        ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                        ("flags", wintypes.DWORD)]
        signatures = (
            (self.kernel.CreateToolhelp32Snapshot, [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.OpenThread, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.GetProcessIdOfThread, [wintypes.HANDLE], wintypes.DWORD),
            (self.kernel.SuspendThread, [wintypes.HANDLE], wintypes.DWORD),
            (self.kernel.ResumeThread, [wintypes.HANDLE], wintypes.DWORD),
            (self.kernel.Thread32First, [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            (self.kernel.Thread32Next, [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        suspended = {}
        try:
            for _ in range(5):
                snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000004, 0)
                if snapshot == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                added = False
                try:
                    entry = ThreadEntry(size=ctypes.sizeof(ThreadEntry))
                    more = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
                    while more:
                        if entry.pid == pid and entry.tid not in suspended:
                            handle = self.kernel.OpenThread(0x0002 | 0x0040, False, entry.tid)
                            if handle:
                                if self.kernel.GetProcessIdOfThread(handle) != pid:
                                    self.kernel.CloseHandle(handle)
                                    raise AssertionError("The thread's process identity changed")
                                if self.kernel.SuspendThread(handle) == 0xFFFFFFFF:
                                    error = ctypes.WinError(ctypes.get_last_error())
                                    self.kernel.CloseHandle(handle)
                                    raise error
                                suspended[entry.tid] = handle
                                added = True
                            elif ctypes.get_last_error() != 87:  # The thread may have just exited.
                                raise ctypes.WinError(ctypes.get_last_error())
                        more = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
                finally:
                    self.kernel.CloseHandle(snapshot)
                if not added:
                    break
            else:
                raise AssertionError("Owned Hub threads did not settle while suspending")
            if not suspended:
                raise AssertionError("No owned Hub threads were suspended")
            yield
        finally:
            errors = []
            for handle in reversed(tuple(suspended.values())):
                if self.kernel.ResumeThread(handle) == 0xFFFFFFFF:
                    errors.append(ctypes.WinError(ctypes.get_last_error()))
                self.kernel.CloseHandle(handle)
            if errors:
                raise errors[0]

    def windows(self, pid):
        found = []

        @self.callback_type
        def collect(hwnd, _):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and self.user.IsWindowVisible(hwnd):
                caption = ctypes.create_unicode_buffer(512)
                self.user.GetWindowTextW(hwnd, caption, len(caption))
                if caption.value.startswith("MonkeyHub"):
                    found.append((hwnd, caption.value))
            return True

        self.user.EnumWindows(collect, 0)
        return found

    def close_window(self, pid):
        self.track_webviews(pid)
        for hwnd, _ in self.windows(pid):
            if not self.user.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
                raise ctypes.WinError(ctypes.get_last_error())

    def cleanup(self):
        # WebView2 owns asynchronous profile flushing after the shell exits.
        # These handles were captured from this shell's actual descendant tree.
        failure = None
        stopped = True
        try:
            wait_for(lambda: all(self.exited(pid) for pid in self.webviews),
                     "This test's WebView2 children did not finish shutdown", timeout=20)
        except Exception as error:
            failure = error
        for pid, handle in self.handles.items():
            try:
                self.kill(pid)
                if self.kernel.WaitForSingleObject(handle, 5000) != 0:
                    raise AssertionError(f"Owned test process {pid} did not exit during cleanup")
            except Exception as error:
                stopped = False
                failure = failure or error
            finally:
                self.kernel.CloseHandle(handle)
        self.cleaned = stopped
        if failure is not None:
            raise failure


@unittest.skipUnless(os.name == "nt" and EXE, "Set MONKEYHUB_DESKTOP_EXE to run the real Windows EXE tests")
class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path(EXE).is_absolute() and Path(EXE).is_file(), EXE)
        self.assertTrue((APPLICATION_ROOT / "apps/monkeyhub/web/dist/index.html").is_file(), "Build the Hub frontend first")
        for directory in (APPLICATION_ROOT, APPLICATION_ROOT / "apps/archflow-studio/api", APPLICATION_ROOT / "apps/monkeyhub/api"):
            if str(directory) not in sys.path:
                sys.path.insert(0, str(directory))
        from archflow_studio_api.settings import save_application_settings
        from archflow_studio_api.transport.settings import ApplicationSettingsDto
        from monkeyhub_api.applications import source_revision

        self.revision = source_revision(APPLICATION_ROOT)
        self.assertIsNotNone(self.revision)
        # Explicit cleanup below waits for this instance's asynchronous WebView exit.
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub desktop 测试 ", delete=False)
        self.addCleanup(self.cleanup_temporary, temporary)
        self.root = Path(temporary.name)
        self.events = []
        self.runtime = self.root / "local/MonkeyHub" if INSTALLED else self.root / "runtime"
        self.native = WindowsProcesses()
        self.addCleanup(self.cleanup_processes)
        self.shells = []
        self.addCleanup(self.cleanup_shells)
        self.studio_port, self.monitor_port = free_ports(2)
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("ARCHFLOW_STUDIO_") and key != "MONKEYMONITOR_DATA_DIR"
        }
        self.environment.update({
            "APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local"),
            "USERPROFILE": str(self.root / "profile"), "CODEX_HOME": str(self.root / "codex"),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"), "PYTHONUTF8": "1",
        })
        # Isolating APPDATA must not hide this interpreter's installed user site.
        user_site = site.getusersitepackages()
        if not INSTALLED and site.ENABLE_USER_SITE and user_site in sys.path:
            self.environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
                self.environment.get("PYTHONPATH"), user_site,
            )))
        if INSTALLED:
            # Keep only OS tools. The EXE must resolve its own Python, Node and
            # production assets without a checkout, venv, user site or Vite.
            windows = Path(os.environ["SystemRoot"])
            self.environment["PATH"] = os.pathsep.join(str(path) for path in (
                windows / "System32", windows, windows / "System32/WindowsPowerShell/v1.0",
            ))
            for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "NODE_PATH"):
                self.environment.pop(key, None)
            self.assertTrue(Path(sys.executable).is_relative_to(APPLICATION_ROOT))
            import archflow, archflow_studio_api, monkeyhub_api
            for module in (archflow, archflow_studio_api, monkeyhub_api):
                self.assertTrue(Path(module.__file__).is_relative_to(APPLICATION_ROOT), module.__file__)
        spec = importlib.util.spec_from_file_location("desktop_project_fixture", ROOT / "apps/archflow-studio/api/tests/support.py")
        self.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixture)
        self.fixture.make_project(self.root / "projects")
        self.project = self.root / "projects" / self.fixture.PROJECT_ID
        from archflow.project.repository import FilesystemProjectRepository
        self.project_locks = frozenset(FilesystemProjectRepository.open(self.project).lock_paths())
        self.before = self.project_bytes()
        self.opened_bytes = None
        save_application_settings(self.runtime, ApplicationSettingsDto(
            projectDir=str(self.project), referenceRun=self.fixture.REFERENCE_RUN_ID,
            workspaceDir=str(self.root / "projects"),
            cadExport="off", studioPort=self.studio_port, monitorPort=self.monitor_port,
        ))
        self.saved_settings = (self.runtime / "config/applications.json").read_bytes()
        if INSTALLED:
            self.user_file = self.runtime / "user-retained.txt"
            self.user_file.write_bytes(b"User data must survive reinstall and reopen.\n")

    def project_bytes(self):
        # P036's two declared advisory locks carry no project content. Windows
        # forbids reading their locked byte during an ordinary guarded read.
        # Do not exclude arbitrary *.lock/temp files or swallow content errors.
        self.record("project-snapshot-start",
                    excludedRepositoryLocks=sorted(str(path.relative_to(self.project)) for path in self.project_locks))
        contents = {}
        for path in self.project.rglob("*"):
            if not path.is_file() or path in self.project_locks:
                continue
            try:
                contents[str(path.relative_to(self.project))] = path.read_bytes()
            except OSError as error:
                self.record("project-read-failed", path=str(path.relative_to(self.project)),
                            operation="Path.read_bytes", errno=error.errno,
                            winerror=getattr(error, "winerror", None),
                            exception=repr(error), stack=traceback.format_exc(),
                            nativeRead=windows_read_probe(path),
                            trackedProcesses={str(pid): {"exited": self.native.exited(pid)}
                                              for pid in self.native.handles})
                raise
        self.record("project-snapshot-complete", files=len(contents))
        return contents

    def record(self, event, **details):
        row = {"event": event, "time": time.time(), "monotonic": time.monotonic(),
               "testProcessId": os.getpid(), **details}
        self.events.append(row)
        if event == "project-read-failed":
            print(json.dumps(row, ensure_ascii=False), file=sys.stderr)
        if output := os.environ.get("MONKEYHUB_TEST_ARTIFACTS"):
            directory = Path(output) / self.id()
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "events.json").write_text(
                json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")

    def cleanup_temporary(self, temporary):
        self.assertTrue(all(shell.poll() is not None for shell in self.shells), "Own EXE still running")
        if not self.native.cleaned:
            self.dump_logs()
            self.fail(f"Own processes did not finish cleanup; retained diagnostics in {self.root}")
        deadline = time.monotonic() + 10
        while True:
            try:
                temporary.cleanup()
                return
            except OSError as error:
                if error.winerror not in {5, 32, 145} or time.monotonic() >= deadline:
                    self.dump_logs()
                    raise
                time.sleep(0.2)

    def cleanup_processes(self):
        try:
            self.native.cleanup()
        except Exception:
            self.dump_logs()
            raise

    def dump_logs(self):
        for path in sorted(self.runtime.glob("logs/*.log")):
            print(f"\n{self.id()} — {path.name}\n{path.read_text(encoding='utf-8', errors='replace')[-12000:]}", file=sys.stderr)
            if output := os.environ.get("MONKEYHUB_TEST_ARTIFACTS"):
                directory = Path(output) / self.id()
                directory.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, directory / path.name)

    def tearDown(self):
        self.record("test-finished", shells=[{"pid": shell.pid, "exitCode": shell.poll()} for shell in self.shells])
        result = self._outcome.result
        failures = getattr(result, "failures", ()) + getattr(result, "errors", ())
        if any(test is self for test, _ in failures):
            self.dump_logs()

    def cleanup_shells(self):
        for shell in self.shells:
            if shell.poll() is None:
                self.native.track_webviews(shell.pid)
                self.native.close_window(shell.pid)
                try:
                    shell.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    shell.kill()  # Only the Popen object created by this test.
                    shell.wait(timeout=10)

    def launch(self, port=None, *, trial=False):
        self.record("launch-start", trial=trial)
        previous = set((self.runtime / "logs").glob("desktop-*.log"))
        command = [str(Path(EXE)), "--source-root", str(ROOT), "--python", sys.executable,
                   "--runtime-root", str(self.runtime), "--startup-timeout-seconds", "40"]
        if INSTALLED:
            command = [str(Path(EXE))]  # Exercise the installed double-click defaults.
        if port is not None:
            command.extend(("--port", str(port)))
        if trial:
            command.append("--update-trial")
        self.shell = subprocess.Popen(
            command, cwd=self.root, env=self.environment,
            stdin=subprocess.PIPE if trial else subprocess.DEVNULL,
            stdout=subprocess.PIPE if trial else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.shells.append(self.shell)
        self.record("shell-created", pid=self.shell.pid)
        self.log = wait_for(lambda: next(iter(set((self.runtime / "logs").glob("desktop-*.log")) - previous), None),
                            lambda: f"No desktop log; EXE exit={self.shell.poll()}")
        return self.shell

    def trial_ready(self):
        self.wait_state("verifying_update")
        handshake = json.loads(self.shell.stdout.readline())
        self.assertEqual(handshake["event"], "update-ready")
        self.assertEqual(handshake["parentProcessId"], self.shell.pid)
        self.assertEqual(handshake["sourceRevision"], self.revision)
        health = request(f'http://127.0.0.1:{handshake["port"]}/api/health')
        for key in ("processId", "parentProcessId", "managedInstanceId", "sourceRevision"):
            self.assertEqual(handshake[key], health[key])
        self.native.track(handshake["processId"])
        self.assertNotIn("ready", self.states())
        self.assertFalse(any(url.startswith("http://127.0.0.1:") for url in PAGE_LOADED.findall(self.log_text())))
        return handshake

    def test_update_trial_waits_for_commit_before_loading_project_ui(self):
        self.launch(trial=True)
        self.trial_ready()
        self.shell.stdin.write(b"commit\n")
        self.shell.stdin.flush()
        self.shell.stdin.close()
        self.ready()
        self.assertIsNone(self.shell.poll(), "Committed trial must survive helper exit")
        self.shell.stdout.close()

    def test_lost_update_helper_drains_trial_and_allows_same_runtime_reopen(self):
        self.launch(trial=True)
        handshake = self.trial_ready()
        self.shell.stdin.close()  # An update helper crash must not strand a Hub.
        self.shell.wait(timeout=45)
        self.shell.stdout.close()
        wait_for(lambda: self.native.exited(handshake["processId"]), "Trial Hub did not release its runtime")
        self.assertFalse(port_open(handshake["port"]))
        self.assertEqual(self.project_bytes(), self.before)
        self.launch()
        self.ready()

    def log_text(self):
        return self.log.read_text(encoding="utf-8", errors="replace")

    def states(self):
        return [match.group(1) for match in STATE.finditer(self.log_text())]

    def wait_state(self, state):
        def check():
            states = self.states()
            if state == "ready" and "failed" in states:
                self.fail(self.log_text())
            return state in states
        wait_for(check, lambda: f"Missing {state}: {self.log_text()}")

    def ready(self):
        self.wait_state("ready")
        start = START.search(self.log_text())
        self.assertIsNotNone(start, self.log_text())
        root_pid, self.url, self.instance, revision = start.groups()
        self.root_pid = int(root_pid)
        self.assertEqual(revision, self.revision)
        self.assertEqual(self.log.stem, f"desktop-{self.instance}")
        self.assertEqual(urlsplit(self.url).hostname, "127.0.0.1")
        health = request(self.url + "api/health")
        self.assertEqual(health["service"], "monkeyhub-api")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["serverVersion"], "0.1.0")
        self.assertEqual(health["managedInstanceId"], self.instance)
        self.assertEqual(health["sourceRevision"], self.revision)
        self.assertEqual(health["processId"], self.root_pid)
        self.assertEqual(health["parentProcessId"], self.shell.pid)
        self.hub_pid = health["processId"]
        self.record("hub-ready", shellPid=self.shell.pid, hubPid=self.hub_pid, instance=self.instance)
        self.pids = {self.root_pid, self.hub_pid}
        for pid in self.pids:
            self.native.track(pid)
        self.ports = {urlsplit(self.url).port}
        wait_for(lambda: any(title == "MonkeyHub" for _, title in self.native.windows(self.shell.pid)),
                 "The ready EXE did not expose its native MonkeyHub window")
        wait_for(lambda: self.url in PAGE_LOADED.findall(self.log_text()),
                 lambda: f"The native WebView did not finish loading the verified Hub root page: {self.log_text()}")
        self.assertEqual(request(self.url, raw=True), (APPLICATION_ROOT / "apps/monkeyhub/web/dist/index.html").read_bytes())
        self.native.track_webviews(self.shell.pid)

    def app_ready(self, app_id):
        def check():
            rows = request(self.url + "api/apps?" + urlencode({"projectDir": str(self.project)}))
            app = next(row for row in rows if row["appId"] == app_id)
            self.assertNotIn(app["state"], ("error", "unavailable"), app)
            return app if app["state"] == "running" else None
        app = wait_for(check, lambda: f"{app_id} not ready: {self.log_text()}")
        health = request(app["apiUrl"] + "api/health")
        self.assertEqual(health["processId"], app["processId"])
        self.assertEqual(health["sourceRevision"], self.revision)
        self.assertTrue(health["managedInstanceId"])
        for pid in {health["processId"], health["parentProcessId"]} - {self.hub_pid}:
            self.native.track(pid)
            self.pids.add(pid)
        self.ports.add(urlsplit(app["apiUrl"]).port)
        return app

    def open_project(self):
        self.record("project-open-start")
        binding = {"projectId": self.fixture.PROJECT_ID, "projectDir": str(self.project)}
        opened = request(self.url + "api/runtime/projects/open", method="POST", payload=binding)
        request(self.url + "api/apps/monkeyarch/start?" + urlencode({"projectDir": str(self.project)}), method="POST")
        studio = self.app_ready("monkeyarch")
        self.assertNotEqual(studio["processId"], self.hub_pid)
        actual = request(studio["apiUrl"] + "api/project")
        self.assertEqual(actual["projectId"], self.fixture.PROJECT_ID)
        self.assertEqual(Path(actual["projectDir"]).resolve(), self.project.resolve())
        self.assertEqual(actual["referenceRun"]["runId"], self.fixture.REFERENCE_RUN_ID)
        # The real Hub UI prepares modeling when its selected project opens.
        # Join the same idempotent API before taking the retained-data baseline.
        prepared = request(self.url + "api/project/modeling?" + urlencode({"projectDir": str(self.project)}),
                           method="POST", payload={"projectId": self.fixture.PROJECT_ID}, timeout=40)
        self.assertEqual(prepared["projectId"], self.fixture.PROJECT_ID)
        wait_for(lambda: request(self.url + "api/runtime/projects/" + opened["runtimeId"])["projection"] == "ready",
                 "Studio runtime projection was not ready")
        # A measured Windows first static response takes about 6.6 s even after
        # modeling is ready; subsequent responses are under 0.1 s. Bound only
        # this cold asset read; health and ordinary API requests keep 5 s.
        self.assertEqual(request(studio["url"], raw=True, timeout=15),
                         (APPLICATION_ROOT / "apps/monkeyhub/web/dist/index.html").read_bytes())
        current = self.project_bytes()
        self.assertEqual({path: current.get(path) for path in self.before}, self.before)
        if self.opened_bytes is None:
            self.opened_bytes = current
        else:
            self.assertEqual(current, self.opened_bytes, "Reopening changed the prepared project")
        return studio

    def drained(self):
        wait_for(lambda: all(self.native.exited(pid) for pid in self.pids),
                 lambda: f"Owned processes survived: {[pid for pid in self.pids if not self.native.exited(pid)]}")
        self.record("owned-processes-drained", pids=sorted(self.pids))
        for port in self.ports:
            self.assertFalse(port_open(port), f"Owned listener {port} survived shutdown")
        self.assertEqual(self.project_bytes(), self.opened_bytes or self.before)
        self.assertEqual((self.runtime / "config/applications.json").read_bytes(), self.saved_settings)
        if INSTALLED:
            self.assertEqual(self.user_file.read_bytes(), b"User data must survive reinstall and reopen.\n")

    def chat_draft(self, value=None):
        """Use Windows' public UI Automation provider, with no product test hook."""
        windows = self.native.windows(self.shell.pid)
        self.assertEqual(len(windows), 1, windows)
        # The only interpolated text is a locally generated test draft.
        assignment = "" if value is None else """
$draft = '""" + value.replace("'", "''") + """'
[Console]::Error.WriteLine('UIA: focus input')
$inputElement.SetFocus()
[Console]::Error.WriteLine('UIA: set draft')
$pattern.SetValue($draft)
for ($attempt = 0; $attempt -lt 50 -and $pattern.Current.Value -ne $draft; $attempt++) {
    Start-Sleep -Milliseconds 100
}
"""
        script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName UIAutomationClient
[Console]::Error.WriteLine('UIA: resolve owned window')
$window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{windows[0][0]})
if ($window.Current.ProcessId -ne {self.shell.pid}) {{ throw 'Unexpected native window owner' }}
$condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'chat-input')
$inputElement = $null
[Console]::Error.WriteLine('UIA: find chat input')
for ($attempt = 0; $attempt -lt 50 -and $null -eq $inputElement; $attempt++) {{
    $inputElement = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    if ($null -eq $inputElement) {{ Start-Sleep -Milliseconds 100 }}
}}
if ($null -eq $inputElement) {{ throw 'The owned Hub chat input was not accessible' }}
if (-not $inputElement.Current.IsEnabled) {{ throw 'The owned Hub chat input was disabled' }}
[Console]::Error.WriteLine('UIA: get value pattern')
$pattern = $inputElement.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
{assignment}
[Console]::Error.WriteLine('UIA: read draft')
$pattern.Current.Value | ConvertTo-Json -Compress
"""
        try:
            # UI Automation runs in its own non-UI MTA, without an STA message pump.
            result = subprocess.run(
                ["powershell.exe", "-Mta", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                 base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as error:
            self.fail(f"Owned chat UI Automation timed out: {error.stderr!r}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip())

    def test_retained_chat_and_settings_are_visible_before_first_write(self):
        from monkeyhub_api.chat import ChatStore, _SavedChat
        from monkeyhub_api.models import ChatMessage

        timestamp = "2026-01-01T00:00:00+00:00"
        saved = _SavedChat(
            id=str(uuid4()), projectId=self.fixture.PROJECT_ID, projectDir=str(self.project),
            title="Synthetic retained chat", provider="codex", status="idle",
            createdAt=timestamp, updatedAt=timestamp,
            messages=[ChatMessage(id="retained-message", role="user",
                                  content="Synthetic retained state.", createdAt=timestamp)],
        )
        ChatStore(self.runtime, "http://127.0.0.1:1", commands={})._save(saved)
        chat_path = self.runtime / "chats" / f"{saved.id}.json"
        saved_chat = chat_path.read_bytes()

        self.launch()
        self.ready()
        # First state reads: no HTTP chat creation or settings save may precede them.
        sessions = request(self.url + "api/chat/sessions")
        settings = request(self.url + "api/settings/apps")
        self.assertEqual([row["id"] for row in sessions], [saved.id])
        self.assertEqual(settings["projectDir"], str(self.project))
        self.assertEqual(settings["workspaceDir"], str(self.root / "projects"))
        detail = request(self.url + f"api/chat/sessions/{saved.id}")
        self.assertEqual(detail["messages"], [message.model_dump() for message in saved.messages])
        self.assertEqual(chat_path.read_bytes(), saved_chat)
        self.assertEqual((self.runtime / "config/applications.json").read_bytes(), self.saved_settings)
        self.assertNotRegex(self.log_text(), r'"(?:POST|PUT|PATCH) /api/(?:chat|settings)')

        self.native.close_window(self.shell.pid)
        self.shell.wait(timeout=30)
        self.assertEqual(self.shell.returncode, 0)
        self.drained()
        self.assertEqual(chat_path.read_bytes(), saved_chat)

    def test_temporary_health_loss_preserves_unsubmitted_draft_without_navigation(self):
        self.launch()
        self.ready()
        self.open_project()
        self.app_ready("monkeymonitor")
        draft = "desktop-unsent-" + str(uuid4())
        self.assertEqual(self.chat_draft(draft), draft)
        sessions = request(self.url + "api/chat/sessions")
        runtime = request(self.url + "api/runtime")
        operations = {row["runtimeId"]: row["operations"] for row in runtime["projects"]}
        loaded_pages = PAGE_LOADED.findall(self.log_text())
        started = time.monotonic()
        with self.native.suspend(self.hub_pid):
            self.wait_state("recovering")
            self.assertGreaterEqual(time.monotonic() - started, 3)
            self.assertFalse(self.native.exited(self.hub_pid))
        wait_for(lambda: self.states()[-1] == "ready", lambda: self.log_text())
        recovered_draft = self.chat_draft()
        self.assertEqual((PAGE_LOADED.findall(self.log_text()), recovered_draft), (loaded_pages, draft),
                         "Transient health loss navigated away from the live page or lost the unsent draft")
        self.assertEqual(request(self.url + "api/chat/sessions"), sessions)
        recovered = request(self.url + "api/runtime")
        self.assertEqual({row["runtimeId"]: row["operations"] for row in recovered["projects"]}, operations)
        self.assertEqual(len(START.findall(self.log_text())), 1)
        self.native.close_window(self.shell.pid)
        self.assertEqual(self.shell.wait(timeout=40), 0)
        self.drained()

    def test_native_close_drains_accepted_work_and_reopen_preserves_project(self):
        self.launch()
        self.ready()
        first_instance = self.instance
        self.open_project()
        monitor = self.app_ready("monkeymonitor")
        body = json.dumps({"usage": {"input_tokens": 1, "output_tokens": 1},
                           "rate": {"provider": "fixture", "model": "fixture", "input": "1", "output": "1"}}).encode()
        with socket.create_connection(("127.0.0.1", urlsplit(monitor["url"]).port), timeout=10) as pending:
            pending.sendall((f"POST /api/quote HTTP/1.1\r\nHost: 127.0.0.1:{urlsplit(monitor['url']).port}\r\n"
                             f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode())
            self.assertEqual(request(monitor["url"] + "api/health")["name"], "MonkeyMonitor")
            self.assertFalse(select.select([pending], [], [], 0)[0], "Monitor rejected the request before its body was sent")
            self.native.close_window(self.shell.pid)
            self.wait_state("stopping")
            wait_for(lambda: not port_open(urlsplit(monitor["url"]).port), "Monitor continued accepting work during shutdown")
            self.assertIsNone(self.shell.poll(), "Shell exited before its accepted Monitor request drained")
            pending.sendall(body)
            with HTTPResponse(pending) as response:
                response.begin()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["currency"], "USD")
        self.assertEqual(self.shell.wait(timeout=40), 0)
        self.wait_state("stopped")
        self.assertEqual(self.states(), ["starting", "ready", "stopping", "stopped"])
        self.drained()

        if INSTALLED:
            # Run the actual package installer after project use and before
            # reopening. Existing user settings and P036 bytes must survive.
            installer = Path(os.environ["MONKEYHUB_PACKAGE_ROOT"]) / "apps/monkeyhub/installer/install.ps1"
            result = subprocess.run([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer),
                "-InstallDirectory", str(APPLICATION_ROOT),
            ], env=self.environment, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("already installed", result.stdout)
            self.drained()

        self.launch()
        self.ready()
        self.assertNotEqual(self.instance, first_instance)
        self.open_project()
        self.app_ready("monkeymonitor")
        self.native.close_window(self.shell.pid)
        self.assertEqual(self.shell.wait(timeout=40), 0)
        self.drained()

    def test_occupied_unrelated_port_is_never_attached_or_stopped(self):
        class UnrelatedService(BaseHTTPRequestHandler):
            def do_GET(self):
                data = b'{"service":"unrelated-test-service"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), UnrelatedService)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        unrelated = f"http://127.0.0.1:{server.server_port}/"
        self.launch(server.server_port)
        self.wait_state("failed")
        self.assertNotIn("ready", self.states())
        self.assertIn(str(server.server_port), self.log_text())
        # The root may fail its bind before the host reads the foreign health.
        # Both routes must produce a concrete diagnostic without loading it.
        failure_titles = {
            "MonkeyHub · 启动失败", "MonkeyHub · 运行时身份验证失败", "MonkeyHub · 运行时已退出",
        }
        wait_for(lambda: any(title in failure_titles for _, title in self.native.windows(self.shell.pid)),
                 "Occupied-port failure did not appear in the native window title")
        failure_details = "\n".join(match.group(2) for match in STATE.finditer(self.log_text())
                                    if match.group(1) == "failed")
        self.assertRegex(failure_details,
                         r"Invalid Hub health JSON|Hub (?:health/protocol|instance) mismatch|本地运行时意外退出|Cannot start Hub")
        self.assertEqual(request(unrelated)["service"], "unrelated-test-service")
        self.native.close_window(self.shell.pid)
        self.shell.wait(timeout=40)
        self.assertEqual(request(unrelated)["service"], "unrelated-test-service")
        self.assertEqual(self.project_bytes(), self.before)

    def test_owned_hub_crash_shows_failure_without_restarting_or_replaying(self):
        self.launch()
        self.ready()
        self.open_project()
        self.app_ready("monkeymonitor")
        self.native.kill(self.hub_pid)
        self.wait_state("failed")
        self.assertIsNone(self.shell.poll())
        wait_for(lambda: any("运行时已退出" in title for _, title in self.native.windows(self.shell.pid)),
                 "Hub crash did not appear in the native window title")
        self.drained()
        self.assertEqual(len(START.findall(self.log_text())), 1)
        self.native.close_window(self.shell.pid)
        self.shell.wait(timeout=40)

    def test_force_killing_own_shell_closes_stdin_and_drains_all_workers(self):
        self.launch()
        self.ready()
        self.open_project()
        self.app_ready("monkeymonitor")
        self.native.track_webviews(self.shell.pid)
        self.shell.kill()
        self.shell.wait(timeout=10)
        self.drained()


@unittest.skipUnless(os.name == "nt", "Exercises real Windows byte-range locks")
class ProjectSnapshotTests(unittest.TestCase):
    """Guard the retained-data assertion independently of a native UI race."""

    def setUp(self):
        from archflow.project.repository import FilesystemProjectRepository
        spec = importlib.util.spec_from_file_location("snapshot_fixture", ROOT / "apps/archflow-studio/api/tests/support.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        self.temporary = tempfile.TemporaryDirectory(prefix="monkeyhub-snapshot-")
        self.addCleanup(self.temporary.cleanup)
        fixture.make_project(Path(self.temporary.name))
        self.reader = DesktopRuntimeTests()
        self.reader._testMethodName = self._testMethodName
        self.reader.project = Path(self.temporary.name) / fixture.PROJECT_ID
        self.reader.project_locks = frozenset(FilesystemProjectRepository.open(self.reader.project).lock_paths())
        self.reader.events = []
        self.reader.native = WindowsProcesses()

    @contextmanager
    def held(self, paths):
        # The same P036 lock implementation runs in another real process; no
        # mocked PermissionError or permission override stands in for Windows.
        script = """import os, sys
from pathlib import Path
from contextlib import ExitStack
from archflow.project.repository import _HeadFileLock
with ExitStack() as stack:
    for path in sys.argv[1:]:
        stack.enter_context(_HeadFileLock(Path(path)))
    print(os.getpid(), flush=True)
    sys.stdin.readline()
"""
        child = subprocess.Popen([sys.executable, "-c", script, *map(str, paths)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            pid = int(child.stdout.readline())
            self.reader.native.track(pid)
            self.reader.record("test-locks-acquired", holderPid=pid, launcherPid=child.pid,
                               paths=[str(path.relative_to(self.reader.project)) for path in paths])
            yield
        finally:
            if child.poll() is None:
                child.stdin.write("release\n")
                child.stdin.flush()
            child.stdin.close()
            child.wait(timeout=10)
            stderr = child.stderr.read()
            child.stdout.close()
            child.stderr.close()
            self.reader.record("test-locks-released", exitCode=child.returncode)
            for handle in self.reader.native.handles.values():
                self.reader.native.kernel.CloseHandle(handle)
            self.reader.native.handles.clear()
            self.assertEqual(child.returncode, 0, stderr)

    def test_declared_locks_do_not_block_complete_content_comparison(self):
        before = self.reader.project_bytes()
        locks = sorted(self.reader.project_locks)
        with self.held(locks):
            for path in locks:
                with self.assertRaises(PermissionError) as raised:
                    path.read_bytes()  # The old scanner deterministically fails.
                native = windows_read_probe(path)
                self.assertEqual(raised.exception.errno, 13)
                self.assertEqual(native["winerror"], 33)  # ERROR_LOCK_VIOLATION
                self.reader.record("old-scan-reproduced", path=str(path.relative_to(self.reader.project)),
                                   errno=raised.exception.errno, nativeRead=native)
            self.assertEqual(self.reader.project_bytes(), before)
        self.assertEqual(self.reader.project_bytes(), before)
        self.assertIn("HEAD", before)
        self.assertIn("project.json", before)

    def test_other_lock_named_content_is_checked_and_read_errors_propagate(self):
        # A fixture file with a lock suffix is still data; this is not a glob.
        asset = self.reader.project / "user-asset.lock"
        asset.write_bytes(b"original retained bytes")
        before = self.reader.project_bytes()
        self.assertEqual(before["user-asset.lock"], b"original retained bytes")
        with self.held([asset]):
            with self.assertRaises(PermissionError):
                self.reader.project_bytes()
        self.assertEqual(self.reader.project_bytes(), before)
        asset.write_bytes(b"changed retained bytes")
        self.assertNotEqual(self.reader.project_bytes(), before)


if __name__ == "__main__":
    unittest.main()
