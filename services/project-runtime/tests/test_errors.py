"""Every refusal arrives as ``{code, detail}``; a human question adds its
fields; a bug leaks nothing."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.project.repository import ProjectWriterBusy
from monkeyarch.authoring.frame import FrameError
from monkeyarch.domain.massing_transforms import MassingTransformError
from monkeydiagram.documentation.sheet_layout import SheetLayoutError
from monkeydiagram.study import StudyEvidenceError
from project_runtime.main import OWNER_REFUSALS, create_app
from project_runtime.settings import StudioSettings
from project_runtime.errors import BlockedNeedsHuman, StudioError

from .support import (
    PROJECT_ID,
    RUNNER_RECORD_PATH,
    make_empty_project,
    make_project,
)

BUG_MARKER = "a-bug-nobody-anticipated"


class ErrorShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(StudioSettings(cad_export="off", project_dir=Path("unbound-placeholder")))

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
                # Every refusal a person answers is one of the four outcomes,
                # and says which. A refusal raised outside a clarification
                # chain carries no pendingIntent, because there is none.
                "outcome": "NEEDS_CLARIFICATION",
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
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        # The exact string the API itself would publish for this binding.
        self.project_dir = self.client.get("/api/project").json()["projectDir"]

    def _refusals(self) -> list[tuple[str, dict]]:
        """One of every refusal a bound project can answer with."""

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
        # Once a reference run exists, its immutable retained State Record is
        # authoritative and authored WIP drift is irrelevant. Exercise the two
        # authored-input refusals on a second project with no eligible run.
        no_run = make_empty_project(self.root / "no-run")
        no_run_client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=no_run.layout.root))
        )
        self.addCleanup(no_run_client.close)
        record = no_run.layout.resolve_relative(RUNNER_RECORD_PATH)
        record.write_text("{ not json", encoding="utf-8")
        answers.append(
            ("record that will not parse", no_run_client.get("/api/state"))
        )
        record.unlink()
        answers.append(
            ("record that is not there", no_run_client.get("/api/state"))
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
                StudioSettings(cad_export="off", project_dir=self.root / "never-initialized")
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


# One refusal of each kind an owner package raises, as its own code raises it.
OWNER_REFUSAL_EXAMPLES = (
    FrameError("UNKNOWN_REF", "the record carries no entity:level-grond."),
    MassingTransformError("LAST_FLOOR", "this massing has one floor."),
    SheetLayoutError("DRAWING_SECTION_MARK_OUTSIDE", "Section A (section-a) does not cross the plan's window."),
    StudyEvidenceError("Evidence 'void' polygon has no measurable area."),
    ProjectWriterBusy("Another process holds this project's writer lease (writer.lock)."),
)


class OwnerRefusalTests(unittest.TestCase):
    """An owner package's refusal answers as the Runtime's own ``StudioError`` did (#519).

    The owner names the code and says the sentence; ``OWNER_REFUSALS`` is the
    one place that gives each kind its status, and the body is the same
    ``{code, detail}``. The routes' own suites prove it for each real refusal.
    """

    def setUp(self) -> None:
        self.app = create_app(StudioSettings(cad_export="off", project_dir=Path("unbound-placeholder")))
        for index, refusal in enumerate(OWNER_REFUSAL_EXAMPLES):
            self.app.add_api_route(f"/api/raises-owner-{index}", self._raising(refusal))
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    @staticmethod
    def _raising(refusal: Exception):
        def raises() -> None:
            raise refusal
        return raises

    def test_every_kind_the_table_maps_is_exercised_here(self) -> None:
        self.assertEqual({type(refusal) for refusal in OWNER_REFUSAL_EXAMPLES}, set(OWNER_REFUSALS))
        self.assertEqual(dict(OWNER_REFUSALS), {FrameError: 422, MassingTransformError: 422, SheetLayoutError: 422,
                                                StudyEvidenceError: 422, ProjectWriterBusy: 409})

    def test_each_refusal_answers_its_status_with_its_own_code_and_sentence(self) -> None:
        for index, refusal in enumerate(OWNER_REFUSAL_EXAMPLES):
            with self.subTest(refusal=type(refusal).__name__):
                response = self.client.get(f"/api/raises-owner-{index}")
                self.assertEqual(response.status_code, OWNER_REFUSALS[type(refusal)])
                self.assertEqual(response.json(), {"code": refusal.code, "detail": str(refusal)})
                self.assertEqual(
                    response.json(),
                    StudioError(OWNER_REFUSALS[type(refusal)], refusal.code, str(refusal)).body(),
                )


if __name__ == "__main__":
    unittest.main()
