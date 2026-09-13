"""Faults and recovery use only disposable, explicitly owned worker processes."""

from dataclasses import replace
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import unittest

from test_monkeyhub_lifecycle import ROOT, LocalHubCase, free_ports, http_json, port_open, project_fixture, wait_for
from monkeyhub_api.applications import Applications
from monkeyhub_api.models import HubFailure
from monkeyhub_api.workers import WorkerLaunch, WorkerSupervisor


WORKER = r'''
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int)
parser.add_argument("--managed-stdin", action="store_true")
parser.add_argument("--managed-instance-id")
args = parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if Path(os.environ["OUTAGE_FILE"]).exists():
            self.send_error(503)
            return
        result = {
            "managedInstanceId": args.managed_instance_id,
            "sourceRevision": "a" * 40,
            "processId": os.getpid(), "parentProcessId": os.getppid(),
            "serverVersion": "0.1.0", "projectBound": True,
        } if self.path == "/api/health" else {
            "projectId": os.environ["PROJECT_ID"], "projectDir": os.environ["PROJECT_DIR"],
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
def stop():
    sys.stdin.readline()
    server.shutdown()
threading.Thread(target=stop, daemon=True).start()
server.serve_forever(poll_interval=0.05)
server.server_close()
'''


class WorkerSupervisorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-worker-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / "worker.py"
        self.script.write_text(WORKER, encoding="utf-8")
        self.supervisor = WorkerSupervisor()
        self.addCleanup(self.supervisor.shutdown)

    def launch(self, name, **changes):
        project = self.root / name
        project.mkdir()
        return replace(WorkerLaunch(
            worker_id=f"studio:{name}", service_id="studio", project_id=name, project_dir=str(project),
            source_revision="a" * 40, command=[sys.executable, str(self.script)],
            environment={**os.environ, "PROJECT_ID": name, "PROJECT_DIR": str(project), "OUTAGE_FILE": str(project / "outage")},
            cwd=self.root, port=free_ports(1)[0], logs_dir=self.root / "logs",
            health_fields={"projectBound": True},
        ), **changes)

    def wait_state(self, key, state):
        def check():
            value = self.supervisor.snapshot(key)
            return value if value and value.state == state else None
        return wait_for(check, f"Worker {key} did not reach {state}", timeout=10)

    def test_parallel_crash_explicit_recovery_same_port_and_normal_shutdown(self):
        a, b = self.launch("project-a"), self.launch("project-b")
        self.supervisor.start(a)
        self.supervisor.start(b)
        ready_a, ready_b = self.wait_state(a.worker_id, "ready"), self.wait_state(b.worker_id, "ready")
        self.assertNotEqual(ready_a.process_id, ready_b.process_id)
        self.assertEqual([row.project_id for row in self.supervisor.snapshots(project_dir=a.project_dir)], ["project-a"])
        self.supervisor.set_busy(a.worker_id, True)
        self.assertEqual(self.supervisor.snapshot(a.worker_id).state, "busy")
        self.assertEqual(self.supervisor.start(a).instance_id, ready_a.instance_id)
        child = self.supervisor._children[a.worker_id]
        child.process.kill()  # Deliberate fault injection, solely this test's process.
        child.process.wait(timeout=5)
        crashed = self.supervisor.snapshot(a.worker_id)
        self.assertEqual(crashed.state, "crashed")
        self.assertEqual(crashed.desired_state, "running")
        self.assertFalse(crashed.healthy)
        self.assertIsNone(crashed.process_id)
        self.assertEqual(self.supervisor.snapshot(b.worker_id).instance_id, ready_b.instance_id)
        self.assertEqual(http_json(ready_b.url + "api/project")["projectId"], "project-b")
        self.assertEqual(self.supervisor.snapshot(a.worker_id).instance_id, ready_a.instance_id)
        recovering = self.supervisor.recover(a.worker_id)
        self.assertEqual(recovering.state, "recovering")
        recovered = self.wait_state(a.worker_id, "ready")
        self.assertEqual(recovered.url, ready_a.url)
        self.assertNotEqual(recovered.instance_id, ready_a.instance_id)
        self.assertEqual(http_json(recovered.url + "api/project")["projectId"], "project-a")
        self.supervisor.stop(a.worker_id)
        stopped = self.wait_state(a.worker_id, "stopped")
        self.assertEqual(stopped.desired_state, "stopped")
        self.assertFalse(port_open(a.port))
        self.assertEqual(self.supervisor._children[a.worker_id].process.returncode, 0)
        self.assertEqual(self.supervisor.snapshot(b.worker_id).state, "ready")
        self.supervisor.start(a)
        self.wait_state(a.worker_id, "ready")
        processes = [value.process for value in self.supervisor._children.values()]
        self.supervisor.shutdown()
        self.assertTrue(all(process.returncode == 0 for process in processes))
        self.assertFalse(port_open(a.port))
        self.assertFalse(port_open(b.port))
        with self.assertRaises(HubFailure) as raised:
            self.supervisor.start(a)
        self.assertEqual(raised.exception.error.code, "HUB_STOPPING")

    def test_recovery_refuses_foreign_listener_and_does_not_adopt_it(self):
        launch = self.launch("project")
        self.supervisor.start(launch)
        original = self.wait_state(launch.worker_id, "ready")
        child = self.supervisor._children[launch.worker_id]
        os.kill(original.process_id, signal.SIGTERM)
        child.process.wait(timeout=5)
        wait_for(lambda: not port_open(launch.port), "The crashed fixture's listener did not close", timeout=5)
        with socket.socket() as foreign:
            # The old listener is confirmed closed. Like HTTPServer, this
            # replacement fixture permits reuse of completed TCP connections
            # on Windows too; it must reach listen() before testing refusal.
            foreign.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            foreign.bind(("127.0.0.1", launch.port))
            foreign.listen()
            with self.assertRaises(HubFailure) as raised:
                self.supervisor.recover(launch.worker_id)
            self.assertEqual(raised.exception.error.code, "PORT_IN_USE")
            refused = self.supervisor.snapshot(launch.worker_id)
            self.assertEqual(refused.state, "crashed")
            self.assertEqual(refused.instance_id, original.instance_id)
            self.assertTrue(port_open(launch.port))

    def test_exact_project_identity_and_path_are_verified(self):
        for mismatch in ("id", "path"):
            with self.subTest(mismatch=mismatch):
                launch = self.launch(mismatch)
                changed = {"PROJECT_ID": "other-project"} if mismatch == "id" else {"PROJECT_DIR": str(self.root / "other-path")}
                launch = replace(launch, environment={**launch.environment, **changed})
                self.supervisor.start(launch)
                rejected = self.wait_state(launch.worker_id, "unavailable")
                self.assertFalse(rejected.healthy)
                self.assertEqual(rejected.error.code, "SERVICE_IDENTITY_MISMATCH")
                self.assertEqual(rejected.desired_state, "stopped")
                self.assertEqual(self.supervisor._children[launch.worker_id].process.wait(timeout=5), 0)
                self.assertFalse(port_open(launch.port))
                with self.assertRaises(HubFailure) as raised:
                    self.supervisor.recover(launch.worker_id)
                self.assertEqual(raised.exception.error.code, "WORKER_NOT_CRASHED")

    def test_health_outage_does_not_claim_readiness_or_restart_a_live_worker(self):
        launch = self.launch("health")
        self.supervisor.start(launch)
        original = self.wait_state(launch.worker_id, "ready")
        outage = Path(launch.environment["OUTAGE_FILE"])
        outage.touch()
        unavailable = self.wait_state(launch.worker_id, "unavailable")
        self.assertEqual(unavailable.instance_id, original.instance_id)
        self.assertEqual(unavailable.process_id, original.process_id)
        self.assertFalse(unavailable.healthy)
        self.assertEqual(unavailable.error.code, "SERVICE_UNAVAILABLE")
        outage.unlink()
        ready = self.wait_state(launch.worker_id, "ready")
        self.assertEqual(ready.instance_id, original.instance_id)
        self.assertIsNone(ready.error)


class StudioWorkerRecoveryTests(LocalHubCase):
    def test_real_studio_crash_recovers_same_project_and_port_without_project_writes(self):
        fixture = project_fixture()
        fixture.make_project(self.root / "projects")
        project = self.root / "projects" / fixture.PROJECT_ID
        web = self.root / "web"
        web.mkdir()
        (web / "index.html").write_text("<html>Recovery fixture</html>", encoding="utf-8")
        applications = Applications(ROOT, self.runtime, web, self.hub_port)
        self.addCleanup(applications.shutdown)
        applications.start("monkeyarch", project_dir=str(project))

        def ready():
            value = applications.status("monkeyarch", project_dir=str(project))
            self.assertNotEqual(value.state, "error", str(value))
            return value if value.state == "running" else None

        original = wait_for(ready, "Studio did not become ready")
        before = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}
        worker = applications.worker_snapshots(project_dir=str(project))[0]
        process = applications.supervisor._children[worker.worker_id].process
        # The verified service PID may be a Windows venv launcher's Python child.
        os.kill(original.processId, signal.SIGTERM)
        process.wait(timeout=5)
        crashed = applications.status("monkeydiagram", project_dir=str(project))
        self.assertEqual(crashed.state, "error")
        self.assertIsNone(crashed.processId)
        applications.recover(project_dir=str(project))
        recovered = wait_for(ready, "Studio did not recover")
        self.assertEqual(recovered.url, original.url)
        self.assertNotEqual(recovered.processId, original.processId)
        binding = http_json(recovered.url + "api/project")
        self.assertEqual(binding["projectId"], fixture.PROJECT_ID)
        self.assertEqual(Path(binding["projectDir"]).resolve(), project.resolve())
        after = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
