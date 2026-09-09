"""Exercise the actual local HTTP boundary and persisted diagnostic readback."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
        self.assertEqual(self.request("/api/health")["status"], "ok")
        self.assertEqual(len(self.request("/api/rates")["rates"]), 4)
        with urlopen(self.url, timeout=3) as response:
            self.assertIn(b"MonkeyMonitor", response.read())
        with self.assertRaises(HTTPError) as error:
            self.request("/api/events", headers={"Origin":"https://example.invalid"})
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
