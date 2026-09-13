"""Exercise the actual local HTTP boundary and persisted diagnostic readback."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import redirect_stderr
from io import StringIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from dataclasses import replace
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
from .test_core import counts, session_rows, token_row


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

    def request(self, path, data=None, headers=None, method=None):
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(), headers=headers or {}, method=method)
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

    def test_diagnostics_compare_inputs_without_inventing_avoidable_retries_or_mutable_poll_duplicates(self):
        store = UsageLog(self.data_dir)
        base = UsageEvent("model-1", "studio", "anthropic", "model", "model_request", "succeeded",
            "2026-09-09T00:00:00Z", TokenUsage(100, 2), project_id="example", operation_id="first",
            timing_scope="model_call", model_call=True,
            details={"input_identity": {"context_digest": "a" * 64, "prompt_sha256": "b" * 64, "provider_fingerprint": "c" * 64}})
        rows = [base, replace(base, event_id="model-2", operation_id="second", started_at="2026-09-09T00:01:00Z"),
            replace(base, event_id="model-changed", started_at="2026-09-09T00:02:00Z",
                    details={"input_identity": {**base.details["input_identity"], "prompt_sha256": "d" * 64}})]
        drawing = replace(base, event_id="drawing-1", provider="none", model="none", phase="drawing_generate", tokens=TokenUsage(),
            model_call=False, timing_scope="service", details={"input_identity": {"step_sha256": "e" * 64, "view_recipe": {"direction": [0, 0, 1]}},
                "cache_status": "miss", "execution_path": "full_projection"})
        rows += [drawing, replace(drawing, event_id="drawing-2", started_at="2026-09-09T00:02:00Z"),
                 replace(drawing, event_id="drawing-cached", started_at="2026-09-09T00:03:00Z",
                         details={**drawing.details, "cache_status": "hit", "execution_path": "retained_drawing"}),
                 replace(drawing, event_id="poll-1", phase="api_request", details={"request_kind": "GET /api/jobs/{job_id}"}),
                 replace(drawing, event_id="poll-2", phase="api_request", details={"request_kind": "GET /api/jobs/{job_id}"})]
        for row in rows:
            store.append(row)
        result = {row["event_id"]: row for row in self.request("/api/events")["events"]}
        self.assertEqual(result["model-2"]["details"]["duplicate_status"], "same_input_request")
        self.assertEqual(result["model-2"]["details"]["comparison_event_id"], "model-1")
        self.assertEqual(result["model-2"]["details"]["reuse_opportunity"], "provider_cache_policy_requires_verification")
        self.assertEqual(result["model-changed"]["details"]["duplicate_status"], "first_observed_input")
        self.assertEqual(result["model-changed"]["details"]["stable_input_parts"], ["context_digest", "provider_fingerprint"])
        self.assertEqual(result["drawing-2"]["details"]["duplicate_status"], "repeated_execution")
        self.assertEqual(result["drawing-cached"]["details"]["duplicate_status"], "reused_result")
        self.assertNotIn("duplicate_status", result["poll-2"]["details"])
        # Snapshot analysis is not a second persisted history or a token change.
        retained, warnings = store.read()
        self.assertFalse(warnings)
        self.assertTrue(all("duplicate_status" not in row.details for row in retained))
        self.assertEqual(result["model-2"]["tokens"], base.tokens.to_dict())

    def test_failed_retry_and_source_comparison_remain_distinct_from_history_matching(self):
        store = UsageLog(self.data_dir)
        first = UsageEvent("attempt-1", "studio", "test", "model", "model_request", "failed",
            "2026-09-09T00:00:00Z", TokenUsage(), model_call=True,
            details={"input_identity": {"context_digest": "a" * 64, "prompt_sha256": "b" * 64}})
        retry = replace(first, event_id="attempt-2", status="succeeded", started_at="2026-09-09T00:01:00Z")
        producer = replace(first, event_id="production-1", phase="element_production", status="succeeded", model_call=False,
            details={"input_identity": {"record_digest": "c" * 64}, "input_equivalent": False,
                     "cache_checks": {"wall.element_row": "changed"}})
        repeated = replace(producer, event_id="production-2", started_at="2026-09-09T00:02:00Z")
        for row in (first, retry, producer, repeated):
            store.append(row)
        rows = {row["event_id"]: row for row in self.request("/api/events")["events"]}
        self.assertEqual(rows["attempt-2"]["details"]["duplicate_status"], "same_input_request")
        self.assertEqual(rows["attempt-2"]["details"]["comparison_event_id"], "attempt-1")
        self.assertEqual(rows["attempt-2"]["details"]["reuse_opportunity"], "previous_attempt_has_no_verified_result")
        self.assertFalse(rows["production-2"]["details"]["input_equivalent"])
        self.assertEqual(rows["production-2"]["details"]["duplicate_status"], "repeated_execution")

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

    def test_codex_sources_select_only_explicit_files_and_clear(self):
        self.assertEqual(self.request("/api/sources/codex"), {"paths": []})
        source = Path(self.temp.name) / "selected.jsonl"
        source.write_text(json.dumps({
            "type": "event_msg", "timestamp": "2026-09-09T12:00:00Z",
            "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 3, "output_tokens": 1}}},
        }) + "\n", encoding="utf-8")
        (source.parent / "unselected.jsonl").write_text("unselected and not JSON\n", encoding="utf-8")
        self.assertEqual(self.request("/api/events")["events"], [])
        expected = {"paths": [source.resolve().as_posix()]}
        self.assertEqual(self.request("/api/sources/codex", {"paths": [str(source), str(source)]}, method="PUT"), expected)
        self.assertEqual(self.request("/api/sources/codex"), expected)
        self.assertEqual(len(self.request("/api/events")["events"]), 1)
        self.assertEqual(self.request("/api/sources/codex", {"paths": []}, method="PUT"), {"paths": []})
        self.assertEqual(self.request("/api/events")["events"], [])

    def test_conflicting_session_copies_leave_independent_healthy_usage_visible(self):
        def token(timestamp, count):
            usage = {"input_tokens": count, "output_tokens": 1}
            return {"type": "event_msg", "timestamp": timestamp, "payload": {
                "type": "token_count", "info": {"total_token_usage": usage, "last_token_usage": usage},
            }}

        private = "private-conflicting-session"
        metadata = {"type": "session_meta", "payload": {"id": private}}
        first = token("2026-09-09T12:00:00Z", 11)
        second = token("2026-09-09T12:00:01Z", 22)
        sources = (
            [metadata, first, second], [metadata, second, first],
            [{"type": "session_meta", "payload": {"id": "healthy"}}, token("2026-09-09T12:00:02Z", 7)],
        )
        paths = []
        for index, rows in enumerate(sources):
            path = Path(self.temp.name) / f"source-{index}.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            paths.append(str(path))
        self.request("/api/sources/codex", {"paths": paths}, method="PUT")
        result = self.request("/api/events")
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["session_id"], "healthy")
        self.assertEqual(result["events"][0]["tokens"]["input_tokens"], 7)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("顺序冲突", result["warnings"][0])
        self.assertNotIn(private, json.dumps(result))
        self.assertNotIn(self.temp.name, json.dumps(result))

    def test_codex_source_validation_is_atomic_and_local_only(self):
        source = Path(self.temp.name) / "selected.jsonl"
        source.write_text("", encoding="utf-8")
        expected = self.request("/api/sources/codex", {"paths": [str(source)]}, method="PUT")
        for body in (
            {"paths": "a.jsonl"}, {"paths": [1]}, {"paths": ["relative.jsonl"]},
            {"paths": [str(source), str(source.parent / "absent.jsonl")]},
            {"paths": [str(source.parent)]}, {"other": []}, [],
        ):
            with self.subTest(body=body):
                with self.assertRaises(HTTPError) as error:
                    self.request("/api/sources/codex", body, method="PUT")
                self.assertEqual(error.exception.code, 400)
                self.assertIn("error", json.loads(error.exception.read()))
                self.assertEqual(self.request("/api/sources/codex"), expected)
        for headers in ({"Host": "example.invalid"}, {"Origin": "https://example.invalid"}):
            with self.subTest(headers=headers):
                with self.assertRaises(HTTPError) as error:
                    self.request("/api/sources/codex", {"paths": []}, headers=headers, method="PUT")
                self.assertEqual(error.exception.code, 403)
                self.assertEqual(self.request("/api/sources/codex"), expected)

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

    def test_shared_assets_serve_exact_bytes_and_mime(self):
        shared = Path(self.temp.name) / "shared"
        shared.mkdir()
        assets = {
            "appearance.js": ("export const theme = '浅色';\n", "text/javascript"),
            "i18n.js": ("export const language = '中文';\n", "text/javascript"),
            "browserTranslator.js": ("export const translate = value => value;\n", "text/javascript"),
            "base.css": (":root { --font: sans-serif; }\n", "text/css"),
        }
        with patch.object(monitor_server, "SHARED_WEB", shared):
            for filename, (text, mime) in assets.items():
                body = text.encode("utf-8")
                (shared / filename).write_bytes(body)
                with self.subTest(filename=filename):
                    request = Request(self.url + "/shared/" + filename, headers={"Origin": self.url})
                    with urlopen(request, timeout=3) as response:
                        self.assertEqual(response.read(), body)
                        self.assertEqual(response.headers["Content-Type"], mime + "; charset=utf-8")

    def test_shared_assets_reject_unlisted_paths_and_traversal(self):
        shared = Path(self.temp.name) / "shared"
        shared.mkdir()
        (shared / "private.js").write_text("not public", encoding="utf-8")
        (shared.parent / "secret.js").write_text("outside shared", encoding="utf-8")
        with patch.object(monitor_server, "SHARED_WEB", shared):
            for path in (
                "/shared/", "/shared/private.js", "/shared/../secret.js",
                "/shared/%2e%2e/secret.js", "/shared/%2e%2e%2fsecret.js",
            ):
                with self.subTest(path=path):
                    with self.assertRaises(HTTPError) as error:
                        self.request(path)
                    self.assertEqual(error.exception.code, 404)

    def test_missing_shared_assets_are_404(self):
        with patch.object(monitor_server, "SHARED_WEB", Path(self.temp.name) / "not-present"):
            for filename in ("appearance.js", "i18n.js", "browserTranslator.js", "base.css"):
                with self.subTest(filename=filename):
                    with self.assertRaises(HTTPError) as error:
                        self.request("/shared/" + filename)
                    self.assertEqual(error.exception.code, 404)

    def test_shared_assets_retain_host_and_origin_checks(self):
        shared = Path(self.temp.name) / "shared"
        shared.mkdir()
        (shared / "appearance.js").write_text("export const theme = 'light';", encoding="utf-8")
        with patch.object(monitor_server, "SHARED_WEB", shared):
            for headers in ({"Host": "example.invalid"}, {"Origin": "https://example.invalid"}):
                with self.subTest(headers=headers):
                    with self.assertRaises(HTTPError) as error:
                        self.request("/shared/appearance.js", headers=headers)
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


class HubUsageBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "codex"
        self.home.mkdir()
        self.database = sqlite3.connect(self.home / "state_5.sqlite")
        self.addCleanup(self.database.close)
        self.database.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
        self.database.commit()
        self.bindings = []
        self.status = 200
        self.requests = []
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                case.requests.append(self.path)
                self.send_response(case.status)
                if case.status == 302:
                    self.send_header("Location", "/must-not-follow")
                self.end_headers()
                self.wfile.write(json.dumps(case.bindings).encode())

        self.hub = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.hub.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_hub)
        self.url = f"http://127.0.0.1:{self.hub.server_port}/api/chat/usage-sources"
        self.data_dir = Path(temporary.name) / "diagnostics"

    def close_hub(self):
        self.hub.shutdown()
        self.hub.server_close()
        self.thread.join()

    def source(self, session, project, *, archived=False):
        parent = self.home / ("archived_sessions" if archived else "sessions")
        parent.mkdir(exist_ok=True)
        path = parent / f"{session}.jsonl"
        rows = session_rows(session) + [
            {"type": "response_item", "payload": {"content": "PRIVATE BODY NEVER EXPORTED"}},
            token_row(counts(10, 2), counts(10, 2)),
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.database.execute("INSERT INTO threads VALUES (?, ?)", (session, str(path)))
        self.database.commit()
        self.bindings.append({"projectId": project, "sessionId": session})
        return path

    def data(self, manual=()):
        return MonitorData(self.data_dir, tuple(manual), codex_bindings_url=self.url, codex_home=self.home)

    def test_multiple_projects_archived_sources_manual_overlap_and_restart_keep_exact_usage(self):
        first = self.source("cli", "project-a")
        self.source("acp", "project-b", archived=True)
        self.bindings.append(self.bindings[0])
        data = self.data([first])
        snapshot = data.snapshot()
        self.assertFalse(snapshot["warnings"])
        self.assertEqual({row["session_id"]: row["project_id"] for row in snapshot["events"]},
                         {"cli": "project-a", "acp": "project-b"})
        self.assertEqual(sum(row["tokens"]["input_tokens"] for row in snapshot["events"]), 20)
        self.assertEqual(self.data([first]).snapshot(), snapshot)
        self.assertEqual(data.codex_sources(), {"paths": [first.as_posix()]}, "Automatic paths are not exposed by the manual-source endpoint")
        self.assertNotIn("PRIVATE BODY", json.dumps(snapshot))
        self.assertNotIn(str(self.home), json.dumps(snapshot))
        self.assertFalse(self.data_dir.exists())

    def test_log_append_and_new_hub_session_are_read_without_restart(self):
        path = self.source("cli", "project-a")
        data = self.data()
        self.assertEqual(len(data.snapshot()["events"]), 1)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(token_row(counts(15, 3), counts(5, 1), "2026-09-09T12:00:01Z")) + "\n")
        self.source("later", "project-b")
        events = data.snapshot()["events"]
        self.assertEqual(len(events), 3)
        self.assertEqual(sum(row["tokens"]["input_tokens"] for row in events), 25)
        self.assertTrue(all(row["duration_ms"] is None and row["model_call"] for row in events))

    def test_backfilled_diagnostics_are_not_doubled_or_unbound_on_hub_failure(self):
        from monkeymonitor.codex import iter_codex_events
        path = self.source("cli", "project-a")
        store = UsageLog(self.data_dir)
        original = replace(next(iter_codex_events([path])), project_id="project-a")
        store.append(original)
        data = self.data([path])
        self.assertEqual(data.snapshot()["events"], [original.to_dict()])
        self.status = 503
        failed = data.snapshot()
        self.assertEqual(failed["events"], [original.to_dict()])
        self.assertEqual(len(failed["warnings"]), 1)
        self.status = 200
        self.assertEqual(data.snapshot()["events"], [original.to_dict()])
        self.bindings = [{"projectId": "different-project", "sessionId": "cli"}]
        conflict = data.snapshot()
        self.assertEqual(conflict["events"], [original.to_dict()])
        self.assertIn("归属存在冲突", "".join(conflict["warnings"]))

    def test_conflicts_missing_index_and_wrong_native_identity_leave_manual_sources_available(self):
        path = self.source("cli", "project-a")
        manual = self.home / "manual.jsonl"
        manual.write_text("".join(json.dumps(row) + "\n" for row in session_rows("manual")
                                 + [token_row(counts(4, 1), counts(4, 1))]), encoding="utf-8")
        self.bindings.append({"projectId": "other-project", "sessionId": "cli"})
        data = self.data([manual])
        self.assertEqual([row["session_id"] for row in data.snapshot()["events"]], ["manual"])
        self.bindings = [{"projectId": "a", "sessionId": "missing"}]
        self.assertEqual([row["session_id"] for row in data.snapshot()["events"]], ["manual"])
        self.bindings = [{"projectId": "a", "sessionId": "cli"}]
        path.write_text("".join(json.dumps(row) + "\n" for row in session_rows("wrong")
                               + [token_row(counts(10, 2), counts(10, 2))]), encoding="utf-8")
        snapshot = data.snapshot()
        self.assertEqual([row["session_id"] for row in snapshot["events"]], ["manual"])
        self.assertIn("身份不符", "".join(snapshot["warnings"]))

    def test_loopback_fetch_ignores_proxy_and_never_follows_redirects(self):
        self.source("cli", "project-a")
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1",
                                     "NO_PROXY": "", "no_proxy": ""}):
            self.assertEqual(len(self.data().snapshot()["events"]), 1)
        self.requests.clear()
        self.status = 302
        self.assertEqual(self.data().snapshot()["events"], [])
        self.assertEqual(self.requests, ["/api/chat/usage-sources"])
        for url in ("https://127.0.0.1/api/chat/usage-sources", "http://example.com/api/chat/usage-sources",
                    "http://user@127.0.0.1/api/chat/usage-sources", self.url + "?other=1", self.url + "/other"):
            with self.assertRaises(ValueError):
                MonitorData(codex_bindings_url=url)

    def test_cli_report_uses_explicit_hub_url_and_environment_home_without_writes(self):
        self.source("cli", "project-a")
        before = {str(path.relative_to(self.home)): path.read_bytes() for path in self.home.rglob("*") if path.is_file()}
        output = StringIO()
        from contextlib import redirect_stdout
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home)}), redirect_stdout(output):
            self.assertEqual(main(["report", "--codex-bindings-url", self.url]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["events"][0]["project_id"], "project-a")
        self.assertEqual(before, {str(path.relative_to(self.home)): path.read_bytes() for path in self.home.rglob("*") if path.is_file()})
        self.assertFalse(self.data_dir.exists())


if __name__ == "__main__":
    unittest.main()
