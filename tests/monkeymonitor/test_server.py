"""Exercise the actual local HTTP boundary and persisted diagnostic readback."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
from queue import Queue
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from monkeymonitor import server as monitor_server
from monkeymonitor.__main__ import main
from monkeymonitor.server import MonitorData, make_server
from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent


class MonitorServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "diagnostics"
        self.server = make_server(MonitorData(self.data_dir), 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(), headers=headers or {})
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def test_readback_deduplicates_and_marks_bad_line_without_exposing_content(self):
        self.assertFalse(self.data_dir.exists())
        event = UsageEvent("event1", "studio", "anthropic", "test-model", "intent", "question", "2026-09-09T00:00:00Z", TokenUsage(100, 20), duration_ms=35)
        store = UsageLog(self.data_dir)
        store.append(event)
        store.append(event)
        with store.path.open("a", encoding="utf-8") as stream:
            stream.write('{"secret":"must not appear"\n')
        result = self.request("/api/events")
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["tokens"]["input_tokens"], 100)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertNotIn("secret", json.dumps(result))

    def test_quote_http_and_invalid_counts(self):
        payload = {"usage": {"input_tokens":1000,"cached_input_tokens":400,"cache_write_input_tokens":0,"cache_write_1h_input_tokens":0,"output_tokens":100,"reasoning_output_tokens":20}, "rate":{"provider":"test","model":"test", "input":"2", "cached_input":"0.5", "output":"10"}}
        self.assertEqual(self.request("/api/quote", payload)["amount_usd"], "0.0024")
        payload["usage"]["input_tokens"] = -1
        with self.assertRaises(HTTPError) as error:
            self.request("/api/quote", payload)
        self.assertEqual(error.exception.code, 400)

    def test_multiple_explicit_sessions_without_metadata_do_not_collide(self):
        row = {"type":"event_msg", "timestamp":"2026-09-09T00:00:00Z", "payload":{
            "type":"token_count", "info":{"last_token_usage":{"input_tokens":100,"output_tokens":10}}}}
        paths = [Path(self.temp.name) / name for name in ("a.jsonl", "b.jsonl")]
        for path in paths:
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        result = MonitorData(codex_sessions=tuple(paths + paths)).snapshot()
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(len({event["event_id"] for event in result["events"]}), 2)
        self.assertTrue(result["warnings"])

    def test_assets_presets_and_other_origin_refused(self):
        health = self.request("/api/health")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["name"], "MonkeyMonitor")
        self.assertIsNone(health["managedInstanceId"])
        self.assertEqual(health["processId"], os.getpid())
        self.assertEqual(health["parentProcessId"], os.getppid())
        self.assertEqual(health["serverVersion"], "0.1.0")
        self.assertEqual(len(self.request("/api/rates")["rates"]), 4)
        with urlopen(self.url, timeout=3) as response:
            self.assertIn(b"MonkeyMonitor", response.read())
        with self.assertRaises(HTTPError) as error:
            self.request("/api/events", headers={"Origin":"https://example.invalid"})
        self.assertEqual(error.exception.code, 403)


class MonitorLifecycleTests(unittest.TestCase):
    def start_monitor(self, *, port=0, instance_id=None):
        # Standalone cleanup must own the interpreter itself, not a venv redirector.
        executable = sys.executable if instance_id is not None else getattr(sys, "_base_executable", sys.executable)
        command = [executable, "-u", "-m", "monkeymonitor", "serve", "--port", str(port)]
        if instance_id is not None:
            command.extend(["--managed-stdin", "--managed-instance-id", instance_id])
        process = subprocess.Popen(
            command, cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.addCleanup(self.close_process, process)
        startup = Queue()
        Thread(target=lambda: startup.put(process.stdout.readline()), daemon=True).start()
        line = startup.get(timeout=5)
        self.assertTrue(line.startswith("MonkeyMonitor: "), line)
        return process, line.partition(": ")[2].strip()

    @staticmethod
    def close_process(process):
        if not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # Only this test's own child may need forced cleanup after a failure.
            process.terminate()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()

    @staticmethod
    def health(url):
        with urlopen(url + "/api/health", timeout=3) as response:
            return json.loads(response.read())

    def test_managed_stop_eof_and_reopen_same_port(self):
        port = 0
        for stop in ("stop", "eof"):
            with self.subTest(stop=stop):
                instance_id = str(uuid4())
                process, url = self.start_monitor(port=port, instance_id=instance_id)
                port = urlsplit(url).port
                health = self.health(url)
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["name"], "MonkeyMonitor")
                self.assertEqual(health["managedInstanceId"], instance_id)
                self.assertIn(process.pid, (health["processId"], health["parentProcessId"]))
                self.assertEqual(health["sourceRevision"], monitor_server._source_revision())
                self.assertEqual(health["serverVersion"], "0.1.0")
                if stop == "stop":
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                else:
                    process.stdin.close()
                self.assertEqual(process.wait(timeout=5), 0, process.stderr.read())
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", port), timeout=1)

    def test_standalone_serve_ignores_stdin_eof(self):
        process, url = self.start_monitor()
        process.stdin.close()
        self.assertIsNone(self.health(url)["managedInstanceId"])
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
        self.assertEqual(self.health(url)["status"], "ok")

    def test_report_remains_read_only_without_managed_options(self):
        with TemporaryDirectory() as directory:
            data_dir = Path(directory) / "not-created"
            result = subprocess.run(
                [sys.executable, "-m", "monkeymonitor", "report", "--data-dir", str(data_dir)],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True, text=True, check=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertEqual(json.loads(result.stdout), {"events": [], "warnings": []})
            self.assertFalse(data_dir.exists())

    def test_managed_options_require_serve_and_an_instance_uuid(self):
        for arguments in (
            ["serve", "--managed-stdin"],
            ["serve", "--managed-instance-id", str(uuid4())],
            ["serve", "--managed-stdin", "--managed-instance-id", "invalid"],
            ["report", "--managed-stdin", "--managed-instance-id", str(uuid4())],
        ):
            with self.subTest(arguments=arguments), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(arguments)
                self.assertEqual(error.exception.code, 2)

    def test_managed_close_finishes_an_accepted_request(self):
        entered, release = Event(), Event()

        class BlockingData(MonitorData):
            def snapshot(self):
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("test did not release the accepted request")
                return {"events": [], "warnings": []}

        server = make_server(BlockingData(), 0, managed_instance_id=str(uuid4()))
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()

        def request():
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/events", timeout=4) as response:
                return json.loads(response.read())

        def close():
            server.shutdown()
            server.server_close()

        try:
            with ThreadPoolExecutor(max_workers=2) as workers:
                response = workers.submit(request)
                try:
                    self.assertTrue(entered.wait(timeout=3))
                    stopped = workers.submit(close)
                    serving.join(timeout=3)
                    self.assertFalse(serving.is_alive())
                    with self.assertRaises(FutureTimeoutError):
                        stopped.result(timeout=0.2)
                finally:
                    release.set()
                self.assertEqual(response.result(timeout=3), {"events": [], "warnings": []})
                stopped.result(timeout=3)
        finally:
            release.set()
            if serving.is_alive():
                server.shutdown()
            server.server_close()
            serving.join(timeout=3)


class MonitorSourceRevisionTests(unittest.TestCase):
    def test_packaged_revision_precedes_git(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source-version.txt").write_text("a" * 40 + "\n", encoding="ascii")
            with patch.object(monitor_server, "__file__", str(root / "monkeymonitor" / "server.py")):
                with patch.object(monitor_server.subprocess, "run") as git:
                    self.assertEqual(monitor_server._source_revision(), "a" * 40)
                    git.assert_not_called()

    def test_source_checkout_reads_its_own_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(monitor_server, "__file__", str(root / "monkeymonitor" / "server.py")):
                with patch.object(monitor_server.subprocess, "run") as git:
                    git.return_value.stdout = "b" * 40 + "\n"
                    self.assertEqual(monitor_server._source_revision(), "b" * 40)
                    self.assertEqual(git.call_args.args[0], ["git", "-C", str(root), "rev-parse", "HEAD"])
                    git.side_effect = FileNotFoundError("git is unavailable")
                    self.assertIsNone(monitor_server._source_revision())

    def test_invalid_packaged_revision_is_unknown(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source-version.txt").write_text("not a source revision\n", encoding="ascii")
            with patch.object(monitor_server, "__file__", str(root / "monkeymonitor" / "server.py")):
                with patch.object(monitor_server.subprocess, "run") as git:
                    self.assertIsNone(monitor_server._source_revision())
                    git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
