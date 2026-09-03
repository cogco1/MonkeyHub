"""Every refusal arrives as ``{code, detail}``; a human question adds its
fields; a bug leaks nothing."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import BlockedNeedsHuman, StudioError

from .support import PROJECT_ID, RUNNER_RECORD_PATH, make_project

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

    def test_a_wrong_method_arrives_in_the_same_body(self) -> None:
        """Starlette's own refusal is shaped like every other one."""

        response = self.client.post("/api/state")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["code"], "METHOD_NOT_ALLOWED")
        self.assertIn("detail", response.json())

    def test_an_unknown_route_arrives_in_the_same_body(self) -> None:
        response = self.client.get("/api/nothing-here")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "NOT_FOUND")


class RefusalDetailTests(unittest.TestCase):
    """No refusal publishes where this process keeps the project.

    ``projectDir`` is a declared field of ``GET /api/project``: an operator
    asking which project this process is bound to is entitled to the answer. A
    refusal's ``detail`` is a different thing — it is read by whoever ran into
    the refusal, and this service's own filesystem layout is not part of any
    answer about a design. One policy, and it is tested rather than asserted.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        # The exact string the API itself would publish for this binding.
        self.project_dir = self.client.get("/api/project").json()["projectDir"]

    def _refusals(self) -> list[tuple[str, dict]]:
        """One of every refusal a bound project can answer with."""

        record = self.repository.layout.resolve_relative(RUNNER_RECORD_PATH)
        answers = [
            ("run named by the request", self.client.get(
                "/api/state", params={"run": "run-nowhere"}
            )),
            ("candidate this process never ran", self.client.get(
                "/api/candidates/studio-cand-nope"
            )),
            ("validation of a candidate nobody ran", self.client.get(
                "/api/candidates/studio-cand-nope/validation"
            )),
            ("artifact no receipt claims", self.client.get(
                f"/api/artifacts/{'b' * 64}/bytes"
            )),
            ("proposal this process does not hold", self.client.get(
                "/api/proposals/studio-nope"
            )),
        ]
        record.write_text("{ not json", encoding="utf-8")
        answers.append(
            ("record that will not parse", self.client.get("/api/state"))
        )
        record.unlink()
        answers.append(
            ("record that is not there", self.client.get("/api/state"))
        )
        return [(name, response.json()) for name, response in answers]

    def test_no_refusal_carries_the_servers_project_directory(self) -> None:
        for name, body in self._refusals():
            with self.subTest(refusal=name):
                self.assertIn("code", body)
                self.assertNotIn(self.project_dir, body["detail"])
                self.assertNotIn(str(self.root), body["detail"])

    def test_an_unbindable_project_names_no_path_either(self) -> None:
        client = TestClient(
            create_app(
                StudioSettings(project_dir=self.root / "never-initialized")
            )
        )
        self.addCleanup(client.close)

        response = client.get("/api/project")

        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual(body["code"], "PROJECT_NOT_BOUND")
        self.assertNotIn(str(self.root), body["detail"])
        # It still says which knob names the project, which is the actionable
        # half of the answer.
        self.assertIn("ARCHFLOW_STUDIO_PROJECT_DIR", body["detail"])


if __name__ == "__main__":
    unittest.main()
