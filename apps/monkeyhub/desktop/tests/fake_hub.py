"""Disposable root-process fixture for the Rust host's lifecycle tests."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument("--runtime-root", type=Path)
parser.add_argument("--port", type=int)
parser.add_argument("--managed-stdin", action="store_true")
parser.add_argument("--managed-instance-id")
parser.add_argument("--no-browser", action="store_true")
args = parser.parse_args()
options = json.loads((Path.cwd() / "fixture.json").read_text())
if options.get("crash"):
    print("before-tail " + "x" * 9000, file=sys.stderr)
    for line in range(40):
        print(f"earlier startup output {line}", file=sys.stderr)
    print("RuntimeError: fixture runtime directory is already in use", file=sys.stderr, flush=True)
    raise SystemExit(17)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "status": "ok", "service": "monkeyhub-api", "serverVersion": "0.1.0",
            "processId": os.getpid(), "parentProcessId": os.getppid(),
            "managedInstanceId": args.managed_instance_id,
            "sourceRevision": (Path.cwd() / "source-version.txt").read_text().strip(),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


server = HTTPServer(("127.0.0.1", args.port), Handler)


def stop():
    for line in sys.stdin:
        if line.strip() == "stop":
            break
    (args.runtime_root / "stop-observed").write_text("yes")
    time.sleep(options.get("drain_seconds", 0))
    (args.runtime_root / "operation-finished").write_text("retained")
    server.shutdown()


threading.Thread(target=stop, daemon=True).start()
server.serve_forever(poll_interval=0.05)
server.server_close()
