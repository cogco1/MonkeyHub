"""Where the running Hub listens, and how a process the Hub did not start finds it (gap 44).

The desktop shell starts the Hub on a new free port at every start and holds the runtime lease exclusively, so an
external agent's MCP connection lost the Hub at each restart. The Hub writes runtime/hub.json once it listens and
takes it back when it quits; a reader trusts the file only when the Hub's own health answer agrees with it.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest

from monkeyhub_api.app import locator
from monkeyhub_api.app.main import _announce
from monkeyhub_api.chat import mcp_server
from monkeyhub_api.models import HubFailure


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the standard library names it
        body = json.dumps(self.server.answer).encode("utf-8")
        self.send_response(200 if self.path == "/api/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HubLocatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def hub(self, **answer) -> str:
        """A stand-in Hub answering /api/health; its URL."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
        server.answer = {"status": "ok", "service": "monkeyhub-api", "processId": os.getpid(),
                         "managedInstanceId": "instance-a", **answer}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def refused(self, code: str, **kwargs) -> str:
        with self.assertRaises(HubFailure) as caught:
            locator.locate(self.root, **kwargs)
        self.assertEqual(caught.exception.error.code, code)
        return caught.exception.error.detail

    def test_the_hub_says_where_it_listens_and_a_reader_checks_it_against_the_hub(self):
        url = self.hub()
        path = locator.write(self.root, url=url, managed_instance_id="instance-a")
        self.assertEqual(path, self.root / "runtime" / "hub.json")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((record["schema"], record["url"], record["processId"], record["managedInstanceId"]),
                         ("MonkeyHubLocator@1", url, os.getpid(), "instance-a"))
        self.assertTrue(record["startedAt"].endswith("+00:00"))
        self.assertEqual([item.name for item in path.parent.iterdir()], ["hub.json"], "written in one step")
        self.assertEqual(locator.locate(self.root), url)

    def test_no_file_a_stopped_hub_and_another_process_are_each_told_apart(self):
        self.assertIn("does not exist. Start MonkeyHub, or pass --hub-url", self.refused("HUB_NOT_RUNNING"))
        # A Hub that crashed left its file; nothing listens on that port now.
        locator.write(self.root, url=f"http://127.0.0.1:{free_port()}", managed_instance_id="instance-a")
        self.assertIn("the Hub that wrote it has stopped", self.refused("HUB_NOT_RUNNING", timeout=1))
        # A later Hub, or another program, answers on the port: not the Hub the file names.
        for answer in ({"processId": os.getpid() + 1}, {"managedInstanceId": "instance-b"}, {"service": "other"}):
            with self.subTest(answer=answer):
                locator.write(self.root, url=self.hub(**answer), managed_instance_id="instance-a")
                self.refused("HUB_NOT_RUNNING")
        for url in ("http://192.0.2.1:8790", "https://127.0.0.1:8790", "http://127.0.0.1"):
            with self.subTest(url=url):
                locator.write(self.root, url=url, managed_instance_id="instance-a")
                self.refused("HUB_NOT_FOUND")

    def test_a_quitting_hub_takes_back_only_its_own_file(self):
        path = locator.write(self.root, url="http://127.0.0.1:1", managed_instance_id=None)
        record = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**record, "processId": os.getpid() + 1}), encoding="utf-8")
        locator.remove(self.root)
        self.assertTrue(path.exists(), "a later Hub's file stays")
        locator.write(self.root, url="http://127.0.0.1:1", managed_instance_id=None)
        locator.remove(self.root)
        self.assertFalse(path.exists())
        locator.remove(self.root)  # nothing to take back is not an error

    def test_the_hub_writes_the_port_it_actually_bound(self):
        bound = SimpleNamespace(getsockname=lambda: ("127.0.0.1", 54321))
        server = SimpleNamespace(started=True, should_exit=False, servers=[SimpleNamespace(sockets=[bound])])
        _announce(server, SimpleNamespace(runtime_root=self.root, port=8790, managed_instance_id="instance-a"))
        record = json.loads((self.root / "runtime" / "hub.json").read_text(encoding="utf-8"))
        self.assertEqual((record["url"], record["managedInstanceId"]), ("http://127.0.0.1:54321", "instance-a"))
        # A server that stops before it listens says nothing.
        other = Path(tempfile.mkdtemp(dir=self.root))
        _announce(SimpleNamespace(started=False, should_exit=True),
                  SimpleNamespace(runtime_root=other, port=1, managed_instance_id=None))
        self.assertFalse((other / "runtime" / "hub.json").exists())

    def test_a_connection_without_a_url_finds_the_hub_again_after_it_restarts_on_another_port(self):
        self.assertEqual(mcp_server._hub_at("http://127.0.0.1:8790/", None), "http://127.0.0.1:8790")
        hub_at = mcp_server._hub_at(None, str(self.root))
        with self.assertRaises(HubFailure):
            hub_at()
        first = self.hub()
        locator.write(self.root, url=first, managed_instance_id="instance-a")
        self.assertEqual(hub_at(), first)
        second = self.hub(managedInstanceId="instance-b")
        locator.write(self.root, url=second, managed_instance_id="instance-b")
        self.assertEqual(hub_at(), second, "the same connection, the restarted Hub")


if __name__ == "__main__":
    unittest.main()
