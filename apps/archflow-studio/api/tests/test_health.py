"""The API answers health without a project and refuses unknown routes in
the error shape."""

from __future__ import annotations

from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings


class HealthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(StudioSettings(project_dir=Path("unbound-placeholder")))
        )
        self.addCleanup(self.client.close)

    def test_health_is_up_and_unbound(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "service": "archflow-studio-api", "projectBound": False},
        )

    def test_unknown_route_answers_in_the_error_shape(self) -> None:
        response = self.client.get("/api/nonsense")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
