"""Hub behavior against its real managed children in private temporary directories.

The Studio fixture below verifies HTTP routing and shared process ownership. Its
minimal static page is not a browser or Studio Web end-to-end acceptance test.
"""

from contextlib import contextmanager, ExitStack
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import site
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient

from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.settings import StudioSettings, save_application_settings
from archflow_studio_api.transport.settings import ApplicationSettingsDto
from monkeyhub_api import chat as chat_tools
from monkeyhub_api.main import HubSettings, create_app


def project_fixture():
    """Reuse the complete Studio P036 fixture without importing a tests package."""

    name = "monkeyhub_studio_project_fixture"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "apps/archflow-studio/api/tests/support.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def free_ports(count):
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return [listener.getsockname()[1] for listener in sockets]


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def http_json(url, *, method="GET", payload=None):
    request = Request(
        url, method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
        return json.load(response)


def wait_for(check, message, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(message)


class LocalHubCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub 测试 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.appdata = self.root / "private roaming"
        self.hub_port, self.studio_port, self.monitor_port = free_ports(3)
        self.base_url = f"http://127.0.0.1:{self.hub_port}"
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("ARCHFLOW_STUDIO_") and key != "MONKEYMONITOR_DATA_DIR"
        }
        environment.update({
            "APPDATA": str(self.appdata), "LOCALAPPDATA": str(self.root / "private local"),
            "PYTHONUTF8": "1",
        })
        # APPDATA also locates Windows' Python user site. Keep dependencies
        # already available to this interpreter when isolating app settings.
        user_site = site.getusersitepackages()
        if site.ENABLE_USER_SITE and user_site in sys.path:
            environment["PYTHONPATH"] = os.pathsep.join(
                value for value in (environment.get("PYTHONPATH"), user_site) if value
            )
        environment_patch = patch.dict(os.environ, environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        # Hub prepares its shared Monitor during lifespan startup.
        save_application_settings(self.runtime, ApplicationSettingsDto(**self.configuration()))

    @contextmanager
    def hub(self, *, studio_web=None):
        app = create_app(HubSettings(
            runtime_root=self.runtime, port=self.hub_port, studio_web_dir=studio_web,
        ), source_root=ROOT)
        with TestClient(app, base_url=self.base_url) as client:
            yield client

    def configuration(self, **changes):
        return {
            "projectDir": None, "workspaceDir": None, "referenceRun": None, "cadExport": "off",
            "studioPort": self.studio_port, "monitorPort": self.monitor_port,
            **changes,
        }

    def configure(self, client, **changes):
        body = self.configuration(**changes)
        response = client.put("/api/settings/apps", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), body)
        return body

    def wait_state(self, client, app_id, state, *, project_dir=None):
        def read():
            response = client.get("/api/apps", params={"projectDir": str(project_dir)} if project_dir is not None else {})
            self.assertEqual(response.status_code, 200, response.text)
            item = next(row for row in response.json() if row["appId"] == app_id)
            if item["state"] == "error" and state != "error":
                self.fail(str(item))
            return item if item["state"] == state else None
        return wait_for(read, f"{app_id} never reached {state}")


class HubApiLifecycleTests(LocalHubCase):
    def test_projects_run_independent_studios_reuse_their_tools_and_shutdown_together(self):
        fixture = project_fixture()
        fixture.make_project(self.root / "projects")
        project_a = self.root / "projects" / fixture.PROJECT_ID
        project_b = self.root / "projects" / "parallel-project"
        FilesystemProjectRepository.initialize(
            project_b, project_id="parallel-project", initial_state={"project_id": "parallel-project", "version": 0},
        )
        web = self.root / "minimal Studio web"
        web.mkdir()
        (web / "index.html").write_text("<html>Parallel Studio fixture</html>", encoding="utf-8")
        processes, ports = [], set()
        with self.hub(studio_web=web) as client:
            configured = self.configure(client, projectDir=str(project_a), referenceRun=fixture.REFERENCE_RUN_ID)
            settings_file = self.runtime / "config/applications.json"
            saved_settings = settings_file.read_bytes()
            for project in (project_a, project_b):
                response = client.post("/api/apps/monkeyarch/start", params={"projectDir": str(project)})
                self.assertEqual(response.status_code, 202, response.text)
            a = self.wait_state(client, "monkeyarch", "running")
            b = self.wait_state(client, "monkeyarch", "running", project_dir=project_b)
            self.assertNotEqual(a["processId"], b["processId"])
            self.assertNotEqual(a["url"], b["url"])
            self.assertEqual(a["url"], f"http://127.0.0.1:{self.studio_port}/")
            for project, expected, project_id in ((project_a, a, fixture.PROJECT_ID), (project_b, b, "parallel-project")):
                binding = http_json(expected["url"] + "api/project")
                self.assertEqual(binding["projectId"], project_id)
                self.assertEqual(Path(binding["projectDir"]).resolve(), project.resolve())
                if project == project_b:
                    self.assertNotEqual(binding["referenceRun"]["runId"], fixture.REFERENCE_RUN_ID)
                alias = project.parent / "." / project.name / ".." / project.name
                for tool in ("monkeyarch", "monkeydiagram", "monkeyboard"):
                    response = client.post(f"/api/apps/{tool}/start", params={"projectDir": str(alias)})
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(response.json()["processId"], expected["processId"])
                    self.assertEqual(self.wait_state(client, tool, "running", project_dir=project)["processId"], expected["processId"])
            self.assertEqual(client.post("/api/apps/monkeymonitor/start", params={"projectDir": str(project_b)}).status_code, 202)
            monitor = self.wait_state(client, "monkeymonitor", "running")
            self.assertEqual(self.wait_state(client, "monkeymonitor", "running", project_dir=project_b)["processId"], monitor["processId"])
            initial_children = tuple(client.app.state.applications.supervisor._children.values())
            processes.extend(child.process for child in initial_children)
            ports.update(child.port for child in initial_children)

            stopped = client.post("/api/apps/monkeyboard/stop", params={"projectDir": str(project_b)})
            self.assertEqual(stopped.status_code, 202, stopped.text)
            for tool in ("monkeyarch", "monkeydiagram", "monkeyboard"):
                self.wait_state(client, tool, "stopped", project_dir=project_b)
            self.assertEqual(self.wait_state(client, "monkeyarch", "running")["processId"], a["processId"])
            self.assertEqual(http_json(a["url"] + "api/project")["projectId"], fixture.PROJECT_ID)
            self.assertEqual(client.post("/api/apps/monkeydiagram/start", params={"projectDir": str(project_b)}).status_code, 202)
            reopened = self.wait_state(client, "monkeyarch", "running", project_dir=project_b)
            self.assertNotEqual(reopened["processId"], b["processId"])
            self.assertEqual(http_json(reopened["url"] + "api/project")["projectId"], "parallel-project")
            changed = client.put("/api/settings/apps", json={**configured, "cadExport": "occt"})
            self.assertEqual(changed.status_code, 409, changed.text)
            self.assertEqual(changed.json()["code"], "APPS_RUNNING")
            self.assertEqual(client.get("/api/settings/apps").json(), configured)
            self.assertEqual(settings_file.read_bytes(), saved_settings)
            remaining_children = tuple(client.app.state.applications.supervisor._children.values())
            processes.extend(child.process for child in remaining_children)
            ports.update(child.port for child in remaining_children)
        for process in processes:
            self.assertEqual(process.poll(), 0)
        for port in ports:
            self.assertFalse(port_open(port), f"Owned service on {port} survived Hub shutdown")

    def test_no_project_hub_runs_monitor_reopens_and_deduplicates_start(self):
        with patch.object(StudioSettings, "__post_init__", side_effect=AssertionError(
            "A project-free Hub must not construct StudioSettings"
        )), self.hub() as client:
            self.assertEqual(client.get("/api/health").json()["service"], "monkeyhub-api")
            rows = {row["appId"]: row for row in client.get("/api/apps").json()}
            self.assertEqual(set(rows), {"monkeyarch", "monkeydiagram", "monkeymonitor", "monkeyboard", "monkeyfab"})
            self.assertEqual(rows["monkeyboard"]["state"], "stopped")
            self.assertEqual(rows["monkeyboard"]["serviceId"], "studio")
            self.assertIn(rows["monkeymonitor"]["state"], {"starting", "running"})
            self.assertIsNotNone(rows["monkeymonitor"]["processId"])
            self.assertIsNone(client.get("/api/settings/apps").json()["projectDir"])
            self.configure(client)
            for app_id in ("monkeyarch", "monkeydiagram", "monkeyboard"):
                response = client.post(f"/api/apps/{app_id}/start")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["code"], "PROJECT_REQUIRED")
            actual_popen = subprocess.Popen
            with patch("monkeyhub_api.applications.subprocess.Popen", wraps=actual_popen) as spawn:
                first = client.post("/api/apps/monkeymonitor/start")
                repeated = client.post("/api/apps/monkeymonitor/start")
                self.assertEqual(first.status_code, 202, first.text)
                self.assertEqual(repeated.status_code, 202, repeated.text)
                self.assertEqual(first.json()["processId"], repeated.json()["processId"])
                spawn.assert_not_called()
            running = self.wait_state(client, "monkeymonitor", "running")
            first_health = http_json(running["url"] + "api/health")
            self.assertEqual(first_health["name"], "MonkeyMonitor")
            self.assertEqual(running["processId"], first_health["processId"])
            self.assertEqual(client.post("/api/apps/monkeymonitor/stop").status_code, 202)
            self.wait_state(client, "monkeymonitor", "stopped")
            self.assertFalse(port_open(self.monitor_port))
            self.assertEqual(client.post("/api/apps/monkeymonitor/start").status_code, 202)
            reopened = self.wait_state(client, "monkeymonitor", "running")
            second_health = http_json(reopened["url"] + "api/health")
            self.assertNotEqual(first_health["managedInstanceId"], second_health["managedInstanceId"])

    def test_application_config_and_user_preferences_survive_independent_reopen(self):
        preferences = {"language": "zh-CN", "theme": "dark", "fontScale": 1.1, "intentProvider": "deterministic"}
        project_path = str(self.root / "future project")
        with self.hub() as client:
            response = client.put("/api/settings/user", json=preferences)
            self.assertEqual(response.status_code, 200, response.text)
            preference_file = self.appdata / "MonkeyArch/settings.json"
            original_preferences = preference_file.read_bytes()
            configured = self.configure(client, projectDir=project_path, referenceRun="selected-run")
            self.assertEqual(preference_file.read_bytes(), original_preferences)
            application_file = self.runtime / "config/applications.json"
            original_applications = application_file.read_bytes()
            changed_preferences = {**preferences, "theme": "light"}
            self.assertEqual(client.put("/api/settings/user", json=changed_preferences).status_code, 200)
            self.assertEqual(application_file.read_bytes(), original_applications)
            self.assertFalse((self.root / "future project").exists())
        with self.hub() as reopened:
            self.assertEqual(reopened.get("/api/settings/apps").json(), configured)
            self.assertEqual(reopened.get("/api/settings/user").json(), changed_preferences)

    def test_running_application_refuses_launch_configuration_changes(self):
        with self.hub() as client:
            configured = self.configure(client)
            self.assertEqual(client.post("/api/apps/monkeymonitor/start").status_code, 202)
            self.wait_state(client, "monkeymonitor", "running")
            response = client.put("/api/settings/apps", json={**configured, "monitorPort": free_ports(1)[0]})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "APPS_RUNNING")
            self.assertEqual(client.get("/api/settings/apps").json(), configured)
            changed = client.put("/api/settings/apps", json={**configured, "cadExport": "occt"})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(next(row for row in client.get("/api/apps").json() if row["appId"] == "monkeymonitor")["state"], "running")

    def test_a_foreign_listener_is_neither_claimed_nor_stopped(self):
        class ForeignHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"service":"foreign-fixture"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", self.monitor_port), ForeignHandler) as foreign:
            thread = threading.Thread(target=foreign.serve_forever, daemon=True)
            thread.start()
            try:
                with self.hub() as client:
                    self.configure(client)
                    response = client.post("/api/apps/monkeymonitor/start")
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(response.json()["code"], "PORT_IN_USE")
                    item = self.wait_state(client, "monkeymonitor", "stopped")
                    self.assertIsNone(item["processId"])
                    self.assertEqual(client.post("/api/apps/monkeymonitor/stop").status_code, 202)
                self.assertEqual(http_json(f"http://127.0.0.1:{self.monitor_port}/")["service"], "foreign-fixture")
            finally:
                foreign.shutdown()
                thread.join(5)

    def test_a_real_child_from_an_unexpected_source_is_refused_and_stopped(self):
        with patch("monkeyhub_api.applications.source_revision", return_value="f" * 40), self.hub() as client:
            self.configure(client)
            response = client.post("/api/apps/monkeymonitor/start")
            self.assertEqual(response.status_code, 202, response.text)
            failure = self.wait_state(client, "monkeymonitor", "error")
            self.assertEqual(failure["error"]["code"], "SERVICE_IDENTITY_MISMATCH")
            self.assertIn("Restart MonkeyHub", failure["error"]["detail"])

            def exited():
                rows = client.get("/api/apps").json()
                item = next(row for row in rows if row["appId"] == "monkeymonitor")
                return item["processId"] is None
            wait_for(exited, "The rejected owned child did not exit")
            self.assertFalse(port_open(self.monitor_port))

    def test_created_project_prepares_one_shared_studio_and_retries_without_recreating(self):
        web = self.root / "new Studio web"
        with self.hub(studio_web=web) as client:
            created = client.post("/api/chat/projects", json={"name": "prepared-project"})
            self.assertEqual(created.status_code, 201, created.text)
            project = created.json()
            root = Path(project["projectDir"])
            identity = (root / "project.json").read_bytes()
            head = (root / "HEAD").read_bytes()
            route = "/api/project/modeling"
            query, body = {"projectDir": str(root)}, {"projectId": project["projectId"]}
            refused = client.post(route, params=query, json=body)
            self.assertEqual(refused.status_code, 503, refused.text)
            self.assertEqual(refused.json()["code"], "STUDIO_WEB_MISSING")
            self.assertEqual((root / "project.json").read_bytes(), identity)
            web.mkdir()
            (web / "index.html").write_text("<html>Prepared Studio fixture</html>", encoding="utf-8")
            actual_request = chat_tools._request_json

            def request(base, path, method="GET", body=None, timeout=180):
                if base == self.base_url:
                    response = client.request(method, path, json=body)
                    response.raise_for_status()
                    return response.json()
                return actual_request(base, path, method, body, timeout)

            with patch.object(chat_tools, "_request_json", side_effect=request):
                prepared = client.post(route, params=query, json=body)
                self.assertEqual(prepared.status_code, 200, prepared.text)
                self.assertTrue(prepared.json()["initialized"])
                arch = self.wait_state(client, "monkeyarch", "running", project_dir=root)
                with patch("monkeyhub_api.applications.subprocess.Popen") as spawn:
                    repeated = client.post(route, params=query, json=body)
                    self.assertEqual(repeated.status_code, 200, repeated.text)
                    self.assertFalse(repeated.json()["initialized"])
                    spawn.assert_not_called()
            for tool in ("monkeydiagram", "monkeyboard"):
                self.assertEqual(self.wait_state(client, tool, "running", project_dir=root)["processId"], arch["processId"])
            self.assertEqual((root / "project.json").read_bytes(), identity)
            self.assertEqual((root / "HEAD").read_bytes(), head)
            self.assertEqual(client.get("/api/chat/workspace").json()["projects"], ["prepared-project"])

    def test_arch_diagram_and_board_share_one_studio_and_any_card_stops_it(self):
        fixture = project_fixture()
        fixture.make_project(self.root / "projects")
        web = self.root / "minimal Studio web"
        web.mkdir()
        page = b"<html>Isolated Studio static route fixture</html>"
        (web / "index.html").write_bytes(page)
        with self.hub(studio_web=web) as client:
            self.configure(
                client, projectDir=str(self.root / "projects" / fixture.PROJECT_ID),
                referenceRun=fixture.REFERENCE_RUN_ID,
            )
            self.assertEqual(client.post("/api/apps/monkeymonitor/start").status_code, 202)
            monitor = self.wait_state(client, "monkeymonitor", "running")
            for first_card, other_card, stop_card in (
                ("monkeyboard", "monkeyarch", "monkeydiagram"),
                ("monkeyarch", "monkeydiagram", "monkeyboard"),
                ("monkeydiagram", "monkeyboard", "monkeyarch"),
            ):
                with self.subTest(first_card=first_card):
                    first = client.post(f"/api/apps/{first_card}/start")
                    second = client.post(f"/api/apps/{other_card}/start")
                    self.assertEqual(first.status_code, 202, first.text)
                    self.assertEqual(second.status_code, 202, second.text)
                    self.assertEqual(first.json()["processId"], second.json()["processId"])
                    arch = self.wait_state(client, "monkeyarch", "running")
                    diagram = self.wait_state(client, "monkeydiagram", "running")
                    board = self.wait_state(client, "monkeyboard", "running")
                    self.assertEqual(arch["processId"], diagram["processId"])
                    self.assertEqual(arch["processId"], board["processId"])
                    self.assertEqual(board["serviceId"], "studio")
                    self.assertEqual(diagram["url"], arch["url"] + "?view=documents")
                    self.assertEqual(board["url"], arch["url"] + "?view=board")
                    self.assertTrue(http_json(arch["url"] + "api/health")["projectBound"])
                    for url in (arch["url"], diagram["url"], board["url"]):
                        with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
                            self.assertEqual(response.read(), page)
                    self.assertEqual(client.post(f"/api/apps/{stop_card}/stop").status_code, 202)
                    self.wait_state(client, "monkeyarch", "stopped")
                    self.wait_state(client, "monkeydiagram", "stopped")
                    self.wait_state(client, "monkeyboard", "stopped")
                    self.assertEqual(self.wait_state(client, "monkeymonitor", "running")["processId"], monitor["processId"])


class HubCliLifecycleTests(LocalHubCase):
    def assert_runtime_in_use(self):
        # Ordinary browser/dev CLI and the desktop-managed CLI both enter main.
        # A second port cannot bypass the runtime directory's real lifetime.
        other_port = free_ports(1)[0]
        result = subprocess.run(
            [sys.executable, str(ROOT / "apps/monkeyhub/run.py"),
             "--runtime-root", str(self.runtime), "--port", str(other_port), "--no-browser"],
            cwd=self.root, input=b"", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another Hub is using runtime directory", result.stdout.decode("utf-8", errors="replace"))
        self.assertFalse(port_open(other_port))

    def test_cli_stop_and_eof_wait_for_an_accepted_monitor_request(self):
        for stop_mode in ("stop", "eof"):
            with self.subTest(stop_mode=stop_mode):
                instance_id = str(uuid4())
                log_path = self.root / f"hub-{stop_mode}.log"
                with log_path.open("wb") as log:
                    child = subprocess.Popen(
                        [sys.executable, str(ROOT / "apps/monkeyhub/run.py"),
                         "--runtime-root", str(self.runtime), "--port", str(self.hub_port),
                         "--managed-stdin", "--managed-instance-id", instance_id, "--no-browser"],
                        cwd=self.root, env=os.environ.copy(), stdin=subprocess.PIPE,
                        stdout=log, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    pending = None
                    runtime_stream = None
                    try:
                        def ready():
                            self.assertIsNone(child.poll(), f"Hub exited early; see {log_path}")
                            try:
                                return http_json(self.base_url + "/api/health")
                            except OSError:
                                return None
                        health = wait_for(ready, "The isolated CLI Hub did not become ready")
                        self.assert_runtime_in_use()
                        self.assertEqual(health["managedInstanceId"], instance_id)
                        self.assertIn(child.pid, (health["processId"], health["parentProcessId"]))
                        runtime_stream = build_opener(ProxyHandler({})).open(self.base_url + "/api/runtime/events", timeout=5)
                        http_json(self.base_url + "/api/settings/apps", method="PUT", payload=self.configuration())
                        http_json(self.base_url + "/api/apps/monkeymonitor/start", method="POST")

                        def monitor_running():
                            rows = http_json(self.base_url + "/api/apps")
                            item = next(row for row in rows if row["appId"] == "monkeymonitor")
                            self.assertNotEqual(item["state"], "error", str(item))
                            return item if item["state"] == "running" else None
                        running = wait_for(monitor_running, "The CLI-owned Monitor did not become ready")
                        body = json.dumps({
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                            "rate": {"provider": "fixture", "model": "fixture", "input": "1", "output": "1"},
                        }).encode("utf-8")
                        pending = socket.create_connection(("127.0.0.1", self.monitor_port), timeout=5)
                        pending.sendall((
                            f"POST /api/quote HTTP/1.1\r\nHost: 127.0.0.1:{self.monitor_port}\r\n"
                            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
                        ).encode("ascii"))
                        # A subsequent health connection is served while the earlier
                        # accepted request is waiting for its body on its own thread.
                        self.assertEqual(http_json(running["url"] + "api/health")["name"], "MonkeyMonitor")
                        if stop_mode == "stop":
                            child.stdin.write(b"stop\n")
                            child.stdin.flush()
                        else:
                            child.stdin.close()
                        wait_for(lambda: not port_open(self.monitor_port), "Monitor kept accepting connections after Hub shutdown")
                        self.assertIsNone(child.poll(), "Hub exited before the accepted Monitor request finished")
                        self.assert_runtime_in_use()
                        pending.sendall(body)
                        with HTTPResponse(pending) as response:
                            response.begin()
                            self.assertEqual(response.status, 200)
                            self.assertEqual(json.loads(response.read())["currency"], "USD")
                        pending.close()
                        pending = None
                        self.assertEqual(child.wait(timeout=20), 0)
                        self.assertFalse(port_open(self.monitor_port))
                    finally:
                        if runtime_stream is not None:
                            runtime_stream.close()
                        if pending is not None:
                            pending.close()
                        if child.stdin is not None and not child.stdin.closed:
                            child.stdin.close()
                        if child.poll() is None:
                            child.wait(timeout=30)
                self.assertIn("Application shutdown complete.", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
