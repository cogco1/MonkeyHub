"""Every refusal arrives as ``{code, detail}``; a human question adds its
fields; a bug leaks nothing."""

from __future__ import annotations

from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import BlockedNeedsHuman, StudioError

BUG_MARKER = "a-bug-nobody-anticipated"


class ErrorShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(StudioSettings(project_dir=Path("unbound-placeholder")))

        @self.app.get("/api/raises-not-bound")
        def raises_not_bound() -> None:
            raise StudioError(503, "PROJECT_NOT_BOUND", "no project is bound to this process")

        @self.app.get("/api/raises-a-bug")
        def raises_a_bug() -> None:
            raise RuntimeError(BUG_MARKER)

        @self.app.get("/api/raises-blocked")
        def raises_blocked() -> None:
            raise BlockedNeedsHuman(
                "the span cannot be resolved without a decision",
                question="Which structural depth applies to the long span?",
                accepted_forms=("400mm", "600mm"),
            )

        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_studio_error_keeps_its_status_and_code(self) -> None:
        response = self.client.get("/api/raises-not-bound")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"code": "PROJECT_NOT_BOUND", "detail": "no project is bound to this process"},
        )

    def test_blocked_needs_human_carries_the_question(self) -> None:
        response = self.client.get("/api/raises-blocked")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {
                "code": "BLOCKED_NEEDS_HUMAN",
                "detail": "the span cannot be resolved without a decision",
                "question": "Which structural depth applies to the long span?",
                "acceptedForms": ["400mm", "600mm"],
            },
        )

    def test_an_unhandled_bug_leaks_nothing(self) -> None:
        response = self.client.get("/api/raises-a-bug")
        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertEqual(payload["code"], "INTERNAL_ERROR")
        for leak in (BUG_MARKER, "RuntimeError", "Traceback"):
            self.assertNotIn(leak, payload["detail"])


if __name__ == "__main__":
    unittest.main()
