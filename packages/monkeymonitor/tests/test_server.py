"""Exercise the retained MonkeyMonitor API after its product UI moved into Hub."""
from contextlib import redirect_stdout
from http.client import HTTPConnection
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler, urlopen

from monkeymonitor.__main__ import main
from monkeymonitor.server import MonitorData, make_server
from monkeymonitor.store import BUSY_NOTICE, UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def event(event_id="evt-1", *, project_id="project-a", duration_ms=1200):
    return UsageEvent(
        event_id=event_id, source="studio", provider="openai", model="gpt-5.6-sol",
        phase="model_request", status="succeeded", started_at="2026-09-15T12:00:00+00:00",
        ended_at="2026-09-15T12:00:01.200000+00:00", duration_ms=duration_ms,
        timing_scope="model_call", model_call=True, project_id=project_id,
        tokens=TokenUsage(input_tokens=100, cached_input_tokens=40, output_tokens=20,
                          cache_write_input_tokens=0, cache_write_1h_input_tokens=0,
                          reasoning_output_tokens=5),
        details={"blocking": True, "billing_plan": "api-standard"},
    )


class MonitorServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "diagnostics"
        self.data = MonitorData(self.data_dir)
        self.server = make_server(self.data, 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def _close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def json(self, path, *, method="GET", body=None, headers=None):
        request = Request(
            self.url + path, method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            return response, json.load(response)

    def test_events_traces_rates_and_quote_use_the_same_retained_data(self):
        UsageLog(self.data_dir).append(event())
        _, snapshot = self.json("/api/events")
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertEqual(snapshot["events"][0]["project_id"], "project-a")
        _, traces = self.json("/api/traces")
        self.assertEqual(len(traces["traces"]), 1)
        trace = traces["traces"][0]
        self.assertEqual(trace["project_id"], "project-a")
        self.assertIn("coverage", trace)
        _, rates = self.json("/api/rates")
        self.assertGreaterEqual(len(rates["rates"]), 1)
        rate = rates["rates"][0]
        _, quoted = self.json("/api/quote", method="POST", body={
            "usage": TokenUsage(input_tokens=100, cached_input_tokens=40, output_tokens=20,
                                cache_write_input_tokens=0, cache_write_1h_input_tokens=0,
                                reasoning_output_tokens=5).to_dict(),
            "rate": rate,
        })
        self.assertEqual(quoted["currency"], "USD")
        self.assertIsNotNone(quoted["known_subtotal_usd"])

    def test_trace_export_keeps_one_trace_and_refuses_missing_identity(self):
        UsageLog(self.data_dir).append(event())
        _, traces = self.json("/api/traces")
        trace_id = traces["traces"][0]["trace_id"]
        response, exported = self.json(f"/api/traces/export?trace_id={trace_id}")
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertEqual(exported["trace_id"], trace_id)
        with self.assertRaises(HTTPError) as missing:
            urlopen(self.url + "/api/traces/export", timeout=3)
        self.assertEqual(missing.exception.code, 400)

    def test_explicit_codex_sources_are_mutable_without_discovery(self):
        source = Path(self.temp.name) / "rollout.jsonl"
        source.write_text("", encoding="utf-8")
        _, initial = self.json("/api/sources/codex")
        self.assertEqual(initial, {"paths": []})
        _, selected = self.json("/api/sources/codex", method="PUT", body={"paths": [str(source)]})
        self.assertEqual(selected["paths"], [source.resolve().as_posix()])
        with self.assertRaises(HTTPError) as bad:
            self.json("/api/sources/codex", method="PUT", body={"paths": ["relative.jsonl"]})
        self.assertEqual(bad.exception.code, 400)

    def test_busy_log_is_503_not_an_empty_snapshot(self):
        class Busy:
            def read(self):
                return [], [BUSY_NOTICE]
        self.data.store = Busy()
        with self.assertRaises(HTTPError) as busy:
            self.json("/api/events")
        self.assertEqual(busy.exception.code, 503)

    def test_server_has_no_standalone_web_assets(self):
        _, root = self.json("/")
        self.assertEqual(root["name"], "MonkeyMonitor")
        self.assertEqual(root["ui"], "MonkeyHub")
        for path in ("/app.js", "/style.css", "/shared/appearance.js"):
            with self.assertRaises(HTTPError) as missing:
                urlopen(self.url + path, timeout=3)
            self.assertEqual(missing.exception.code, 404)

    def test_only_the_owning_hub_origin_gets_cors_and_root_redirect(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        hub_port = 8790
        self.data = MonitorData(self.data_dir, codex_bindings_url=f"http://127.0.0.1:{hub_port}/api/chat/usage-sources")
        self.server = make_server(self.data, 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        request = Request(self.url + "/api/rates", headers={"Origin": f"http://localhost:{hub_port}"})
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], f"http://localhost:{hub_port}")
        with self.assertRaises(HTTPError) as denied:
            build_opener(ProxyHandler({})).open(Request(self.url + "/api/rates", headers={"Origin": "http://example.com"}), timeout=3)
        self.assertEqual(denied.exception.code, 403)

        no_redirect = build_opener(ProxyHandler({}), _NoRedirect())
        with self.assertRaises(HTTPError) as redirect:
            no_redirect.open(self.url + "/?lang=en&theme=light&fontScale=1.1&host=ignored", timeout=3)
        self.assertEqual(redirect.exception.code, 307)
        location = redirect.exception.headers["Location"]
        self.assertTrue(location.startswith(f"http://127.0.0.1:{hub_port}/?"))
        self.assertIn("view=monitor", location)
        self.assertIn("lang=en", location)
        self.assertNotIn("host=", location)

    def test_preflight_is_bounded_to_the_owning_hub(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        hub_port = 8790
        self.data = MonitorData(self.data_dir, codex_bindings_url=f"http://127.0.0.1:{hub_port}/api/chat/usage-sources")
        self.server = make_server(self.data, 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("OPTIONS", "/api/sources/codex", headers={"Origin": f"http://127.0.0.1:{hub_port}", "Host": f"127.0.0.1:{self.server.server_port}"})
        response = connection.getresponse()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), f"http://127.0.0.1:{hub_port}")
        self.assertIn("PUT", response.getheader("Access-Control-Allow-Methods"))
        connection.close()

    def test_report_cli_remains_available_without_a_browser_shell(self):
        UsageLog(self.data_dir).append(event())
        output = StringIO()
        with redirect_stdout(output):
            code = main(["report", "--data-dir", str(self.data_dir)])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["events"][0]["event_id"], "evt-1")

    def test_host_header_still_refuses_foreign_access(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("GET", "/api/health", headers={"Host": "example.com"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        connection.close()


if __name__ == "__main__":
    unittest.main()
