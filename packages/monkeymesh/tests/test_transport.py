"""Real Iroh stream tests, isolated from public relays and real credentials."""
import hashlib
import os
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import time
import unittest

from monkeymesh.transport import IrohTransport

BINARY = Path(os.environ.get("MONKEYMESH_TEST_BINARY") or (Path(__file__).resolve().parents[1] / "native/target/debug" / ("monkeymesh-tcp.exe" if os.name == "nt" else "monkeymesh-tcp")))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        remaining = int(self.headers["Content-Length"])
        digest = hashlib.sha256()
        while remaining:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                return
            digest.update(chunk)
            remaining -= len(chunk)
        result = digest.hexdigest().encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for number in range(100):
            try:
                self.wfile.write(f"data: {number}\n\n".encode())
                self.wfile.flush()
            except OSError:
                return
            time.sleep(.02)


@unittest.skipUnless(BINARY.exists(), "Build the locked native helper")
class StreamTests(unittest.TestCase):
    def test_large_body_sse_interruption_and_same_device_reconnect(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        peers = []
        listener = IrohTransport(BINARY, secret_key=secrets.token_hex(32), mode="listen", target=server.server_port,
                                 peer_changed=peers.append, test_loopback=True)
        self.addCleanup(listener.close)
        key = secrets.token_hex(32)
        caller = IrohTransport(BINARY, secret_key=key, mode="connect", ticket=listener.ticket, test_loopback=True)
        self.addCleanup(caller.close)
        listener.admission([caller.node_id], pairing=False)
        identity = caller.node_id
        connection = http.client.HTTPConnection("127.0.0.1", caller.port, timeout=30)
        connection.putrequest("POST", "/bytes")
        block = b"transport-fixture" * 4096
        count = 3200  # 200 MiB, streamed without constructing a whole-body buffer.
        connection.putheader("Content-Length", str(len(block) * count))
        connection.endheaders()
        digest = hashlib.sha256()
        for _ in range(count):
            digest.update(block)
            connection.send(block)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read().decode(), digest.hexdigest())
        connection.close()
        stream = http.client.HTTPConnection("127.0.0.1", caller.port, timeout=10)
        started = time.monotonic()
        stream.request("GET", "/events")
        response = stream.getresponse()
        self.assertEqual(response.readline(), b"data: 0\n")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn(b"data: 99", response.read())
        self.assertGreater(time.monotonic() - started, 1.8)
        stream.close()
        caller.close()
        reconnected = IrohTransport(BINARY, secret_key=key, mode="connect", ticket=listener.ticket, test_loopback=True)
        self.addCleanup(reconnected.close)
        self.assertEqual(reconnected.node_id, identity)
        connection = http.client.HTTPConnection("127.0.0.1", reconnected.port, timeout=10)
        connection.request("POST", "/bytes", b"after reconnect")
        self.assertEqual(connection.getresponse().read().decode(), hashlib.sha256(b"after reconnect").hexdigest())
        connection.close()
        self.assertTrue(any(event["event"] == "peer" and event["peer"] == identity for event in peers))
