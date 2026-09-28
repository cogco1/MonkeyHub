"""Real HTTP runtime attachment and SSE reconnection in an isolated Hub."""

from contextlib import contextmanager
import json
import signal
import threading
import unittest
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
        app = create_app(HubSettings(runtime_root=self.runtime, port=self.hub_port), source_root=ROOT)
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
                # The stream ended; all it carried was the project index's own announcements (#366).
                rest = studio_stream.read().decode("utf-8")
                self.assertEqual({line for line in rest.splitlines() if line.startswith("event:")} - {"event: index.committed"}, set())
                self.assertTrue(app.state.runtimes._closing.is_set())
                self.assertFalse(any(row.process_id for row in app.state.applications.worker_snapshots()))
            self.assertEqual(before, {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()})

    def frame(self, stream):
        """The next frame's event name and data."""
        name = None
        while True:
            line = stream.readline().decode("utf-8").strip()
            if line.startswith("event:"):
                name = line[6:].strip()
            if line.startswith("data:"):
                return name, json.loads(line[5:])

    def test_a_worker_index_commit_reaches_the_hub_stream_as_a_hint(self):
        """#366: the Hub relays the worker's index.committed on its one stream, and the write names that revision."""
        import base64
        import io
        from PIL import Image

        project = self.root / "index-project"
        FilesystemProjectRepository.initialize(project, project_id=project.name,
                                                initial_state={"project_id": project.name, "version": 0})
        with self.serving() as app:
            opened = http_json(self.base_url + "/api/runtime/projects/open", method="POST",
                               payload={"projectId": project.name, "projectDir": str(project)})
            runtime_id = opened["runtimeId"]
            http_json(self.base_url + "/api/apps/monkeyarch/start?" + urlencode({"projectDir": str(project)}), method="POST")
            wait_for(lambda: any(row.healthy for row in app.state.applications.worker_snapshots(project_dir=str(project))),
                     "managed Studio did not start")
            studio = self.base_url + f"/api/runtime/projects/{runtime_id}/studio"
            opener = build_opener(ProxyHandler({}))
            with opener.open(self.base_url + "/api/runtime/events", timeout=30) as stream:
                self.assertEqual(self.frame(stream)[0], "runtime")
                # The worker's first index load, or the Hub attaching to it, is announced.
                index = None
                while index is None or index.get("index") is None:
                    name, body = self.frame(stream)
                    if name == "index" and body["runtimeId"] == runtime_id:
                        index = body
                snapshot = http_json(studio + "/api/index")
                self.assertTrue(snapshot["reset"])
                picture = io.BytesIO()
                Image.new("RGB", (8, 8), "red").save(picture, format="PNG")
                with opener.open(Request(studio + "/api/documents", method="POST", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"projectId": project.name, "fileName": "plan.png", "mimeType": "image/png",
                                                          "contentBase64": base64.b64encode(picture.getvalue()).decode()}).encode()),
                                 timeout=30) as written:
                    epoch, revision = written.headers["X-Monkey-Index"].split(":")
                self.assertEqual(epoch, snapshot["epoch"])
                self.assertGreater(int(revision), snapshot["revision"])
                for _ in range(200):
                    name, body = self.frame(stream)
                    if name == "index" and (body["index"] or {}).get("revision", 0) >= int(revision):
                        break
                else:
                    self.fail("the write's index commit did not reach the Hub stream")
                self.assertEqual(body["runtimeId"], runtime_id)
                self.assertEqual(body["index"]["epoch"], epoch)
                self.assertIn("run", body["index"]["domains"])
                changes = http_json(studio + "/api/index?" + urlencode({"since": snapshot["revision"], "epoch": epoch}))
                self.assertFalse(changes["reset"])
                self.assertTrue(changes["upserts"])

    @unittest.skipUnless(hasattr(signal, "SIGKILL"), "kills the worker the POSIX way")
    def test_a_restarted_worker_is_followed_again_and_a_change_made_meanwhile_is_not_missed(self):
        """#366: kill the worker, change the project while it is down, recover: the hint comes, the changes hold it."""
        import os
        from archflow.project.ports import PersistenceArea, PersistenceDestination
        from archflow.project.record_kinds import STATE_RECORD

        project = self.root / "restart-project"
        repository = FilesystemProjectRepository.initialize(project, project_id=project.name,
                                                             initial_state={"project_id": project.name, "version": 0})
        with self.serving() as app:
            opened = http_json(self.base_url + "/api/runtime/projects/open", method="POST",
                               payload={"projectId": project.name, "projectDir": str(project)})
            runtime_id = opened["runtimeId"]
            http_json(self.base_url + "/api/apps/monkeyarch/start?" + urlencode({"projectDir": str(project)}), method="POST")
            workers = lambda: app.state.applications.worker_snapshots(project_dir=str(project))
            wait_for(lambda: any(row.healthy for row in workers()), "managed Studio did not start")
            studio = self.base_url + f"/api/runtime/projects/{runtime_id}/studio"
            before = wait_for(lambda: self.index(studio), "the index did not load")
            process_id = next(row.process_id for row in workers() if row.process_id)
            opener = build_opener(ProxyHandler({}))
            with opener.open(self.base_url + "/api/runtime/events", timeout=60) as stream:
                self.assertEqual(self.frame(stream)[0], "runtime")
                os.kill(process_id, signal.SIGKILL)
                wait_for(lambda: not any(row.healthy and row.process_id == process_id for row in workers()),
                         "the killed worker is still reported healthy")
                # Another client changes the project while no worker watches it.
                repository.create_run("run-while-down")
                repository.put_json(run=repository.load_run("run-while-down"),
                                    destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="run-while-down"),
                                    record_kind=STATE_RECORD, payload={"written": "meanwhile"})
                http_json(self.base_url + f"/api/runtime/projects/{runtime_id}/recover", method="POST",
                          payload={"projectId": project.name})
                wait_for(lambda: any(row.healthy and row.process_id != process_id for row in workers()),
                         "the worker did not restart", timeout=60)
                for _ in range(400):
                    name, body = self.frame(stream)
                    if name == "index" and body["runtimeId"] == runtime_id:
                        break
                else:
                    self.fail("the Hub did not say the restarted worker's index may have moved")
            after = wait_for(lambda: self.index(studio, since=before["revision"], epoch=before["epoch"]),
                             "the restarted worker's index did not answer")
            ids = {entity["id"] for entity in after["upserts"]}
            self.assertIn("run:run-while-down", ids, "caught up or reset, the change made meanwhile is there")
            if not after["reset"]:
                self.assertEqual(after["epoch"], before["epoch"])
                self.assertEqual(after["from"], before["revision"])

    def index(self, studio, **query):
        try:
            return http_json(studio + "/api/index" + (f"?{urlencode(query)}" if query else ""))
        except OSError:
            return None


if __name__ == "__main__":
    import unittest
    unittest.main()
