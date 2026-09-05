"""The API answers health without a project and refuses unknown routes in
the error shape."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, make_project


class HealthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=Path("unbound-placeholder")))
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


class BoundHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_health_reports_the_binding_it_opened(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["projectBound"], True)


if __name__ == "__main__":
    unittest.main()
