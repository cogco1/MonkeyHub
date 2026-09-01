from __future__ import annotations

import http.client
import json
from pathlib import Path
from threading import Thread
import unittest

from backend.server import create_server


class StudioApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(
            host="127.0.0.1",
            port=0,
            static_dir=Path(__file__).parent / "missing-dist",
        )
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload

    def test_health_is_explicitly_read_only(self) -> None:
        status, payload = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], "StudioHealth@1")
        self.assertTrue(payload["kernelImportable"])
        self.assertFalse(payload["canonicalWriteAuthority"])

    def test_capabilities_keep_future_providers_reserved(self) -> None:
        status, payload = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200)
        by_id = {item["id"]: item for item in payload["capabilities"]}
        self.assertEqual(by_id["intent-provider"]["availability"], "reserved")
        self.assertEqual(by_id["retrieval-provider"]["availability"], "reserved")
        self.assertEqual(by_id["studio-canonical-write"]["availability"], "disabled")

    def test_session_does_not_claim_a_project_binding(self) -> None:
        status, payload = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertFalse(payload["projectBound"])
        self.assertIsNone(payload["stage"])

    def test_all_api_posts_fail_closed(self) -> None:
        status, payload = self.request("POST", "/api/session/advance")
        self.assertEqual(status, 501)
        self.assertEqual(payload["code"], "WRITE_PATH_NOT_IMPLEMENTED")
        self.assertFalse(payload["canonicalWriteAuthority"])


if __name__ == "__main__":
    unittest.main()

