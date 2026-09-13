"""Real HTTP runtime attachment and SSE reconnection in an isolated Hub."""

from contextlib import contextmanager
import json
import threading
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID

import uvicorn

from test_monkeyhub_lifecycle import LocalHubCase, ROOT, http_json, wait_for
from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.main import HubServer, HubSettings, create_app


class RuntimeSseTests(LocalHubCase):
    @contextmanager
    def serving(self):
        web = self.root / "web"
        web.mkdir(exist_ok=True)
        (web / "index.html").write_text("<html>SSE shutdown fixture</html>", encoding="utf-8")
        app = create_app(HubSettings(runtime_root=self.runtime, port=self.hub_port, studio_web_dir=web), source_root=ROOT)
        server = HubServer(uvicorn.Config(app, host="127.0.0.1", port=self.hub_port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        self.server, self.server_thread = server, thread
        thread.start()
        try:
            wait_for(lambda: server.started, "isolated Hub did not start")
            yield app
        finally:
            server.should_exit = True
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), "SSE client prevented normal Hub shutdown")

    def event(self, stream):
        event_id = None
        while True:
            line = stream.readline().decode("utf-8").strip()
            if line.startswith("id:"):
                event_id = line[3:].strip()
            if line.startswith("data:"):
                return event_id, json.loads(line[5:])

    def test_reconnect_rebuilds_snapshot_even_with_old_or_foreign_cursor(self):
        root, other = self.root / "sse-a", self.root / "sse-b"
        for path in (root, other):
            FilesystemProjectRepository.initialize(path, project_id=path.name,
                                                    initial_state={"project_id": path.name, "version": 0})
        with self.serving() as app:
            first = http_json(self.base_url + "/api/runtime/projects/open", method="POST",
                              payload={"projectId": root.name, "projectDir": str(root)})
            runtime_id = first["runtimeId"]
            wait_for(lambda: http_json(self.base_url + f"/api/runtime/projects/{runtime_id}")["retained"],
                     "initial retained state was not reconstructed")
            url = self.base_url + "/api/runtime/events"
            opener = build_opener(ProxyHandler({}))
            with opener.open(Request(url, headers={"Last-Event-ID": "foreign:999999"}), timeout=10) as stream:
                cursor, event = self.event(stream)
                UUID(event["serverId"])
                self.assertEqual(cursor, f"{event['serverId']}:{event['sequence']}")
                self.assertEqual(event["kind"], "runtime/snapshot")
                snapshot = event["snapshot"]
                project = next(row for row in snapshot["projects"] if row["runtimeId"] == runtime_id)
                self.assertEqual(project["retained"]["published"]["version"], 0)
                self.assertEqual(project["workers"], [], "attachment must not start a Studio")
                second = http_json(self.base_url + "/api/runtime/projects/open", method="POST",
                                   payload={"projectId": other.name, "projectDir": str(other)})
                for _ in range(20):
                    next_cursor, update = self.event(stream)
                    if update["runtimeId"] == second["runtimeId"]:
                        break
                else:
                    self.fail("the attached client did not receive the second project event")
                self.assertGreater(update["sequence"], event["sequence"])
            with opener.open(Request(url, headers={"Last-Event-ID": cursor}), timeout=10) as stream:
                _, reattached = self.event(stream)
                self.assertEqual(reattached["kind"], "runtime/snapshot")
                self.assertEqual({row["runtimeId"] for row in reattached["snapshot"]["projects"]},
                                 {runtime_id, second["runtimeId"]})
                self.assertFalse(any(row.service_id == "studio" for row in app.state.applications.worker_snapshots()))

    def test_normal_shutdown_ends_open_hub_and_studio_subscriptions(self):
        project = self.root / "stream-project"
        FilesystemProjectRepository.initialize(project, project_id=project.name,
                                                initial_state={"project_id": project.name, "version": 0})
        with self.serving() as app:
            opened = http_json(self.base_url + "/api/runtime/projects/open", method="POST",
                               payload={"projectId": project.name, "projectDir": str(project)})
            http_json(self.base_url + "/api/apps/monkeyarch/start?" + urlencode({"projectDir": str(project)}), method="POST")
            wait_for(lambda: any(row.healthy for row in app.state.applications.worker_snapshots(project_dir=str(project))),
                     "managed Studio did not start")
            before = {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            opener = build_opener(ProxyHandler({}))
            with opener.open(self.base_url + "/api/runtime/events", timeout=10) as hub_stream, \
                 opener.open(self.base_url + f"/api/runtime/projects/{opened['runtimeId']}/studio/api/events", timeout=10) as studio_stream:
                self.assertEqual(self.event(hub_stream)[1]["kind"], "runtime/snapshot")
                wait_for(lambda: bool(app.state.studio_event_sockets), "Studio stream did not attach")
                self.server.should_exit = True
                self.server_thread.join(timeout=8)
                self.assertFalse(self.server_thread.is_alive(), "Open SSE subscriptions blocked normal server shutdown")
                self.assertEqual(studio_stream.read(), b"")
                self.assertTrue(app.state.runtimes._closing.is_set())
                self.assertFalse(any(row.process_id for row in app.state.applications.worker_snapshots()))
            self.assertEqual(before, {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()})


if __name__ == "__main__":
    import unittest
    unittest.main()
