"""The health route is the API's own claim about what it is allowed to do."""

from __future__ import annotations

from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings


class HealthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = create_app(
            StudioSettings(project_dir=Path("unbound-placeholder"))
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_health_states_the_read_only_round_one_posture(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "StudioHealth@3")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "archflow-studio-api")
        self.assertIs(payload["readOnly"], True)
        self.assertIs(payload["canonicalWriteAuthority"], False)
        self.assertIs(payload["projectBound"], False)

    def test_unknown_api_route_answers_in_the_studio_error_shape(self) -> None:
        response = self.client.get("/api/nonsense")

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["schema"], "StudioError@1")
        self.assertEqual(payload["code"], "NOT_FOUND")
        self.assertIsInstance(payload["detail"], str)


if __name__ == "__main__":
    unittest.main()
