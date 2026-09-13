"""Opt-in Windows EXE lifecycle tests against real isolated Hub/Studio workers.

Build the production web clients and desktop EXE from this checkout, then set
MONKEYARCH_DESKTOP_EXE to its absolute path and run this file with unittest or
pytest. The Python interpreter running the tests supplies the Hub dependencies.
These checks observe a native window, identity-verified HTTP and process exit;
they do not claim WebView rendering or interactive modeling acceptance.
"""

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import site
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[4]
EXE = os.environ.get("MONKEYARCH_DESKTOP_EXE")
START = re.compile(r"event=start pid=(\d+) url=(\S+) instance=(\S+) source=([0-9a-f]{40})")
STATE = re.compile(r"event=state state=(\w+) detail=(.*)")


def wait_for(check, message, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(message() if callable(message) else message)


def request(url, *, method="GET", payload=None, raw=False):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    with build_opener(ProxyHandler({})).open(Request(
        url, method=method, data=body, headers={"Content-Type": "application/json"},
    ), timeout=5) as response:
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

    def windows(self, pid):
        found = []

        @self.callback_type
        def collect(hwnd, _):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and self.user.IsWindowVisible(hwnd):
                caption = ctypes.create_unicode_buffer(512)
                self.user.GetWindowTextW(hwnd, caption, len(caption))
                if caption.value.startswith("MonkeyArch"):
                    found.append((hwnd, caption.value))
            return True

        self.user.EnumWindows(collect, 0)
        return found

    def close_window(self, pid):
        for hwnd, _ in self.windows(pid):
            if not self.user.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
                raise ctypes.WinError(ctypes.get_last_error())

    def cleanup(self):
        for pid, handle in self.handles.items():
            try:
                self.kill(pid)
                self.kernel.WaitForSingleObject(handle, 5000)
            finally:
                self.kernel.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt" and EXE, "Set MONKEYARCH_DESKTOP_EXE to run the real Windows EXE tests")
class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path(EXE).is_absolute() and Path(EXE).is_file(), EXE)
        for client in ("monkeyhub", "archflow-studio"):
            self.assertTrue((ROOT / f"apps/{client}/web/dist/index.html").is_file(), f"Build {client} web first")
        for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
            if str(directory) not in sys.path:
                sys.path.insert(0, str(directory))
        from archflow_studio_api.settings import save_application_settings
        from archflow_studio_api.transport.settings import ApplicationSettingsDto
        from monkeyhub_api.applications import source_revision

        self.revision = source_revision(ROOT)
        self.assertIsNotNone(self.revision)
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyArch desktop 测试 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.native = WindowsProcesses()
        self.addCleanup(self.native.cleanup)
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
        if site.ENABLE_USER_SITE and user_site in sys.path:
            self.environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
                self.environment.get("PYTHONPATH"), user_site,
            )))
        spec = importlib.util.spec_from_file_location("desktop_project_fixture", ROOT / "apps/archflow-studio/api/tests/support.py")
        self.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixture)
        self.fixture.make_project(self.root / "projects")
        self.project = self.root / "projects" / self.fixture.PROJECT_ID
        self.before = self.project_bytes()
        save_application_settings(self.runtime, ApplicationSettingsDto(
            projectDir=str(self.project), referenceRun=self.fixture.REFERENCE_RUN_ID,
            cadExport="off", studioPort=self.studio_port, monitorPort=self.monitor_port,
        ))
        self.saved_settings = (self.runtime / "config/applications.json").read_bytes()

    def project_bytes(self):
        return {str(path.relative_to(self.project)): path.read_bytes()
                for path in self.project.rglob("*") if path.is_file()}

    def cleanup_shells(self):
        for shell in self.shells:
            if shell.poll() is None:
                self.native.close_window(shell.pid)
                try:
                    shell.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    shell.kill()  # Only the Popen object created by this test.
                    shell.wait(timeout=10)

    def launch(self, port=None):
        previous = set((self.runtime / "logs").glob("desktop-*.log"))
        command = [str(Path(EXE)), "--source-root", str(ROOT), "--python", sys.executable,
                   "--runtime-root", str(self.runtime), "--startup-timeout-seconds", "40"]
        if port is not None:
            command.extend(("--port", str(port)))
        self.shell = subprocess.Popen(
            command, cwd=self.root, env=self.environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.shells.append(self.shell)
        self.log = wait_for(lambda: next(iter(set((self.runtime / "logs").glob("desktop-*.log")) - previous), None),
                            lambda: f"No desktop log; EXE exit={self.shell.poll()}")
        return self.shell

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
        self.assertIn(self.root_pid, (health["processId"], health["parentProcessId"]))
        self.hub_pid = health["processId"]
        self.pids = {self.root_pid, self.hub_pid}
        for pid in self.pids:
            self.native.track(pid)
        self.ports = {urlsplit(self.url).port}
        wait_for(lambda: any(title == "MonkeyArch" for _, title in self.native.windows(self.shell.pid)),
                 "The ready EXE did not expose its native MonkeyArch window")
        self.assertEqual(request(self.url, raw=True), (ROOT / "apps/monkeyhub/web/dist/index.html").read_bytes())

    def app_ready(self, app_id):
        def check():
            rows = request(self.url + "api/apps?" + urlencode({"projectDir": str(self.project)}))
            app = next(row for row in rows if row["appId"] == app_id)
            self.assertNotIn(app["state"], ("error", "unavailable"), app)
            return app if app["state"] == "running" else None
        app = wait_for(check, lambda: f"{app_id} not ready: {self.log_text()}")
        health = request(app["url"] + "api/health")
        self.assertEqual(health["processId"], app["processId"])
        self.assertEqual(health["sourceRevision"], self.revision)
        self.assertTrue(health["managedInstanceId"])
        for pid in {health["processId"], health["parentProcessId"]} - {self.hub_pid}:
            self.native.track(pid)
            self.pids.add(pid)
        self.ports.add(urlsplit(app["url"]).port)
        return app

    def open_project(self):
        binding = {"projectId": self.fixture.PROJECT_ID, "projectDir": str(self.project)}
        opened = request(self.url + "api/runtime/projects/open", method="POST", payload=binding)
        request(self.url + "api/apps/monkeyarch/start?" + urlencode({"projectDir": str(self.project)}), method="POST")
        studio = self.app_ready("monkeyarch")
        self.assertNotEqual(studio["processId"], self.hub_pid)
        actual = request(studio["url"] + "api/project")
        self.assertEqual(actual["projectId"], self.fixture.PROJECT_ID)
        self.assertEqual(Path(actual["projectDir"]).resolve(), self.project.resolve())
        self.assertEqual(actual["referenceRun"]["runId"], self.fixture.REFERENCE_RUN_ID)
        wait_for(lambda: request(self.url + "api/runtime/projects/" + opened["runtimeId"])["projection"] == "ready",
                 "Studio runtime projection was not ready")
        self.assertEqual(request(studio["url"], raw=True), (ROOT / "apps/archflow-studio/web/dist/index.html").read_bytes())
        return studio

    def drained(self):
        wait_for(lambda: all(self.native.exited(pid) for pid in self.pids),
                 lambda: f"Owned processes survived: {[pid for pid in self.pids if not self.native.exited(pid)]}")
        for port in self.ports:
            self.assertFalse(port_open(port), f"Owned listener {port} survived shutdown")
        self.assertEqual(self.project_bytes(), self.before)
        self.assertEqual((self.runtime / "config/applications.json").read_bytes(), self.saved_settings)

    def test_native_close_drains_accepted_work_and_reopen_preserves_project(self):
        self.launch()
        self.ready()
        first_instance = self.instance
        self.open_project()
        monitor = self.app_ready("monkeymonitor")
        body = json.dumps({"usage": {"input_tokens": 1, "output_tokens": 1},
                           "rate": {"provider": "fixture", "model": "fixture", "input": "1", "output": "1"}}).encode()
        with socket.create_connection(("127.0.0.1", urlsplit(monitor["url"]).port), timeout=10) as pending:
            pending.sendall((f"POST /api/quote HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                             f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode())
            self.assertEqual(request(monitor["url"] + "api/health")["name"], "MonkeyMonitor")
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
        wait_for(lambda: any("启动失败" in title for _, title in self.native.windows(self.shell.pid)),
                 "Occupied-port failure did not appear in the native window title")
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
        self.shell.kill()
        self.shell.wait(timeout=10)
        self.drained()


if __name__ == "__main__":
    unittest.main()
