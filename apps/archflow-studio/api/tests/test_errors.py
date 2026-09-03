"""Every refusal the app returns arrives in the one StudioError@1 shape.

The routes here are attached to a throwaway app: the handlers under test are
the ones ``create_app`` registers, not stand-ins for them.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import BlockedNeedsHuman, NotBound


class ErrorShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(
            StudioSettings(project_dir=Path("unbound-placeholder"))
        )

        @self.app.get("/api/raises-not-bound")
        def raises_not_bound() -> None:
            raise NotBound("no project is bound to this process")

        @self.app.get("/api/raises-blocked")
        def raises_blocked() -> None:
            raise BlockedNeedsHuman(
                "the span cannot be resolved without a decision",
                question="Which structural depth applies to the long span?",
                accepted_forms=("400mm", "600mm"),
            )

        @self.app.get("/api/needs-a-number")
        def needs_a_number(count: int) -> dict[str, int]:
            return {"count": count}

        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_studio_error_keeps_its_status_and_code(self) -> None:
        response = self.client.get("/api/raises-not-bound")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "schema": "StudioError@1",
                "code": "PROJECT_NOT_BOUND",
                "detail": "no project is bound to this process",
            },
        )

    def test_blocked_needs_human_carries_the_question_it_needs(self) -> None:
        response = self.client.get("/api/raises-blocked")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {
                "schema": "StudioError@1",
                "code": "BLOCKED_NEEDS_HUMAN",
                "detail": "the span cannot be resolved without a decision",
                "question": (
                    "Which structural depth applies to the long span?"
                ),
                "acceptedForms": ["400mm", "600mm"],
            },
        )

    def test_request_validation_reads_as_a_studio_error(self) -> None:
        response = self.client.get("/api/needs-a-number?count=not-a-number")

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["schema"], "StudioError@1")
        self.assertEqual(payload["code"], "REQUEST_INVALID")
        self.assertIn("count", payload["detail"])


if __name__ == "__main__":
    unittest.main()
