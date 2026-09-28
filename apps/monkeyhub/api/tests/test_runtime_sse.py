"""Real HTTP runtime attachment and SSE reconnection in an isolated Hub."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import signal
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID

import uvicorn

from test_monkeyhub_lifecycle import LocalHubCase, ROOT, http_json, wait_for
from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.main import HubServer, HubSettings, create_app
from monkeyhub_api.runtime import _WorkerEvents


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
            # The per-page Studio stream is gone (#366): the Hub's own stream carries the project's events.
            with self.assertRaises(HTTPError) as refused:
                opener.open(self.base_url + f"/api/runtime/projects/{opened['runtimeId']}/studio/api/events", timeout=10)
            self.assertEqual(refused.exception.code, 404)
            self.assertEqual(json.load(refused.exception)["code"], "STUDIO_EVENTS_RELAYED")
            with opener.open(self.base_url + "/api/runtime/events", timeout=10) as hub_stream:
                self.assertEqual(self.event(hub_stream)[1]["kind"], "runtime/snapshot")
                self.server.should_exit = True
                self.server_thread.join(timeout=8)
                self.assertFalse(self.server_thread.is_alive(), "Open SSE subscriptions blocked normal server shutdown")
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

    def test_relayed_frames_the_snapshot_does_not_hold_are_sent_and_the_kept_studio_events_open_the_stream(self):
        """#366: a hint queued while the snapshot is read is never dropped, and a new page opens with the Studio replay."""
        with self.serving() as app:
            runtimes = app.state.runtimes
            runtimes.studio_event("runtime-a", "stream-a", {"seq": 1, "type": "candidate.queued", "at": "t"})
            snapshot = runtimes.snapshot

            def racing():
                # Published after the stream subscribed and before the snapshot is read: its seq is at
                # or below the snapshot's, and the snapshot does not hold it.
                runtimes.index_hint("runtime-a", {"epoch": "e", "revision": 7, "domains": ["run"]})
                return snapshot()

            opener = build_opener(ProxyHandler({}))
            with mock.patch.object(runtimes, "snapshot", racing), \
                 opener.open(self.base_url + "/api/runtime/events", timeout=10) as stream:
                name, first = self.frame(stream)
                self.assertEqual((name, first["kind"]), ("runtime", "runtime/snapshot"))
                name, replayed = self.frame(stream)
                self.assertEqual((name, replayed["stream"], replayed["studio"]["seq"], replayed["replay"]),
                                 ("studio", "stream-a", 1, True))
                name, hint = self.frame(stream)
                self.assertEqual((name, hint["index"]["revision"]), ("index", 7))
                self.assertLessEqual(hint["sequence"], first["sequence"], "the hint was taken before the snapshot")
                runtimes.studio_event("runtime-a", "stream-b", {"seq": 1, "type": "candidate.running", "at": "t"})
                name, live = self.frame(stream)
                self.assertEqual((name, live["stream"], live["studio"]["seq"]), ("studio", "stream-b", 1))
                self.assertNotIn("replay", live)


class _ScriptedStudioStream(BaseHTTPRequestHandler):
    """A worker's ``GET /api/events``: each connection answers the next scripted frames, then ends."""

    def do_GET(self):
        self.server.attaches.append(self.headers.get("Last-Event-ID", ""))
        frames = self.server.script.pop(0) if self.server.script else []
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for frame in frames:
            self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    def log_message(self, *args):
        pass


def _frame(name, stream=None, seq=None, **body):
    head = "" if stream is None else f"id: {stream}:{seq}\n"
    return f"event: {name}\n{head}data: {json.dumps({'seq': seq or 0, 'at': 't', 'type': name, **body})}\n\n"


class WorkerEventsTests(unittest.TestCase):
    """The Hub's one attachment to a worker's stream (#366), against a scripted worker."""

    def follow(self, script):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedStudioStream)
        server.script, server.attaches = list(script), []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        manager = mock.Mock(spec=["index_hint", "studio_event"])
        follower = _WorkerEvents(manager, "runtime-a", f"http://127.0.0.1:{server.server_address[1]}")
        follower.start()
        self.addCleanup(follower.stop)
        return server, manager

    def test_a_restarted_worker_numbering_from_one_again_is_relayed_under_its_own_stream(self):
        old = [_frame(kind, "old", seq) for seq, kind in enumerate(("candidate.queued", "candidate.running", "candidate.succeeded"), 1)]
        new = [_frame("stream.reset", reason="restart")] + [
            _frame(kind, "new", seq) for seq, kind in enumerate(("candidate.queued", "candidate.running", "candidate.succeeded"), 1)]
        server, manager = self.follow([old, new])
        wait_for(lambda: manager.studio_event.call_count >= 6, "the six events were not relayed", timeout=10)
        relayed = [(call.args[1], call.args[2]["seq"], call.args[2]["type"]) for call in manager.studio_event.call_args_list]
        self.assertEqual(relayed[:6], [("old", 1, "candidate.queued"), ("old", 2, "candidate.running"), ("old", 3, "candidate.succeeded"),
                                       ("new", 1, "candidate.queued"), ("new", 2, "candidate.running"), ("new", 3, "candidate.succeeded")])
        self.assertEqual(server.attaches[:2], ["", "old:3"], "it resumes from the last event it was sent")
        # The first attachment and the reset may each have missed something; nothing else says so.
        self.assertEqual([call.args for call in manager.index_hint.call_args_list], [("runtime-a", None), ("runtime-a", None)])

    def test_a_stream_that_ends_at_once_is_attached_again_ever_later_and_hints_once(self):
        with mock.patch("monkeyhub_api.runtime._WORKER_EVENTS_RETRY_S", 0.05):
            server, manager = self.follow([])
            time.sleep(1.2)
        # 0.1, 0.2, 0.4, 0.8 s apart: a fixed 50 ms retry would have attached about 24 times.
        self.assertLessEqual(len(server.attaches), 5, server.attaches)
        self.assertGreaterEqual(len(server.attaches), 3, server.attaches)
        self.assertEqual(manager.index_hint.call_count, 1, "only the first attachment may have missed something")


if __name__ == "__main__":
    import unittest
    unittest.main()
