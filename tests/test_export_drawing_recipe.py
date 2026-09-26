"""tools/export_drawing_recipe.py: one project's confirmed drawing correction starts another's new drawings (#252).

Both projects are disposable P036 fixtures under their own project ids,
authored with the Studio test record. No provider or user CAD application is
used; the cut plans in the demonstration are real OCCT projections.
"""

from __future__ import annotations

import base64
import contextlib
from importlib import import_module
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/archflow-studio/api"))

from fastapi.testclient import TestClient

from archflow.adapters.occt_backend import occt_available
from archflow.project.repository import FilesystemProjectRepository, ProjectHeadLocked
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.decisions import recipe_export
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from tools.export_drawing_recipe import ATTRIBUTION, main

# Load API fixtures by their full package so they do not shadow kernel tests.
support = import_module("apps.archflow-studio.api.tests.support")
two_page_pdf = import_module("apps.archflow-studio.api.tests.test_documents").two_page_pdf
room_edit = import_module("apps.archflow-studio.api.tests.test_drawing_plans").room_edit

DEFAULTS = {"cutLineMm": 0.35, "visibleLineMm": 0.18, "hatchSpacingMm": 2.0}


def studio(test: unittest.TestCase, root: Path, project_id: str, *, cad_export: str = "off") -> TestClient:
    """A real project under its own id, with the Studio's test record authored, and a runtime bound to it."""

    FilesystemProjectRepository.initialize(
        root / project_id, project_id=project_id, initial_state={"project_id": project_id, "version": 0},
        authored_record={**support.RECORD_PAYLOAD, "project_id": project_id}, seat_pack=support.SEATS_PAYLOAD)
    client = TestClient(create_app(StudioSettings(project_dir=root / project_id, cad_export=cad_export)))
    test.addCleanup(client.close)
    return client


def uploaded_page(test: unittest.TestCase, client: TestClient, project_id: str) -> dict:
    response = client.post("/api/documents", json={
        "projectId": project_id, "fileName": "plan.pdf", "mimeType": "application/pdf",
        "contentBase64": base64.b64encode(two_page_pdf()).decode()})
    test.assertEqual(response.status_code, 201, response.text)
    document = response.json()
    return {"runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": 0}


def confirm_recipe(test: unittest.TestCase, client: TestClient, project_id: str, page: dict, graphics: dict) -> dict:
    """A person's confirmed project recipe, on the page they confirmed it on (as the Drawing saves one)."""

    response = client.post("/api/decisions", json={
        "projectId": project_id, "rawLanguage": "Save as project recipe: hatchSpacingMm 3 mm.",
        "disposition": "require", "strength": "strong_preference", "targetRef": "drawing:hatch",
        "scope": {"domain": "drawing", "extent": "project"}, "source": {"kind": "document", **page},
        "applicability": "scope", "sourceKind": "human", "typedBinding": {"kind": "recipe", "graphics": graphics}})
    test.assertEqual(response.status_code, 201, response.text)
    return response.json()


def decisions(test: unittest.TestCase, client: TestClient) -> list[dict]:
    response = client.get("/api/decisions")
    test.assertEqual(response.status_code, 200, response.text)
    return response.json()["decisions"]


def run_tool(*args: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class ExportDrawingRecipeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.first = studio(self, self.root, "first-project")
        self.second = studio(self, self.root, "second-project")
        page = uploaded_page(self, self.first, "first-project")
        self.decision = confirm_recipe(self, self.first, "first-project", page, {"hatchSpacingMm": 3})
        self.file = self.root / "hatch-recipe.json"

    def export(self, out: Path | None = None, decision_id: str | None = None) -> tuple[int, str, str]:
        return run_tool("export", "--project", self.root / "first-project",
                        "--decision", decision_id or self.decision["decisionId"], "--out", out or self.file)

    def import_(self, file: Path | None = None, *flags: str) -> tuple[int, str, str]:
        return run_tool("import", "--project", self.root / "second-project", "--file", file or self.file, *flags)

    def test_an_export_is_written_once_and_imported_only_on_a_persons_confirmation(self) -> None:
        code, out, err = self.export()
        self.assertEqual(code, 0, err)
        written = self.file.read_text(encoding="utf-8")
        document = json.loads(written)
        self.assertEqual(document, recipe_export(bound_project(self.first.app.state), self.decision["decisionId"]))
        self.assertIn(document["sha256"], out)
        self.assertNotIn("first-project", written)
        # An export never replaces a file.
        code, _, err = self.export()
        self.assertEqual(code, 1)
        self.assertIn("FileExistsError", err)
        self.assertEqual(self.file.read_text(encoding="utf-8"), written)

        # Without --confirm it shows what it would retain and writes nothing.
        code, out, err = self.import_()
        self.assertEqual(code, 0, err)
        self.assertIn("Nothing was written", out)
        self.assertEqual(decisions(self, self.second), [])
        # With it: one decision, a person's preference for the whole project,
        # in the words they were shown, evidenced by the export.
        code, out, err = self.import_(None, "--confirm")
        self.assertEqual(code, 0, err)
        [imported] = decisions(self, self.second)
        self.assertEqual(
            (imported["projectId"], imported["disposition"], imported["strength"], imported["scope"]["extent"],
             imported["sourceKind"], imported["source"], imported["typedBinding"]["graphics"]["hatchSpacingMm"]),
            ("second-project", "require", "soft_preference", "project", "human",
             {"kind": "recipe-export", "exportSha256": document["sha256"]}, 3.0))
        self.assertEqual(imported["attribution"],
                         {"actorId": ATTRIBUTION.actor_id, "authenticated": False, "origin": ATTRIBUTION.origin})
        self.assertIn(imported["rawLanguage"], out)
        self.assertIn(imported["decisionId"], out)
        self.assertEqual(len(decisions(self, self.first)), 1)

        # The same export again is the recipe conflict, said plainly.
        code, _, err = self.import_(None, "--confirm")
        self.assertEqual(code, 1)
        self.assertIn("DECISION_RECIPE_CONFLICT", err)
        self.assertIn(imported["decisionId"], err)
        self.assertIn("Nothing was imported", err)
        self.assertEqual(decisions(self, self.second), [imported])

    def test_a_changed_file_a_decision_that_is_no_recipe_and_a_held_lock_are_refused(self) -> None:
        self.assertEqual(self.export()[0], 0)
        document = json.loads(self.file.read_text(encoding="utf-8"))
        document["recipe"]["graphics"]["hatchSpacingMm"] = 4.0
        changed = self.root / "changed-recipe.json"
        changed.write_text(json.dumps(document), encoding="utf-8")
        code, _, err = self.import_(changed, "--confirm")
        self.assertEqual(code, 1)
        self.assertIn("RECIPE_EXPORT_INVALID", err)
        self.assertEqual(decisions(self, self.second), [])

        missing = self.root / "none.json"
        code, _, err = self.export(missing, "no-such-decision")
        self.assertEqual(code, 1)
        self.assertIn("DECISION_NOT_FOUND", err)
        self.assertFalse(missing.exists())

        # A project whose lock a runtime holds past the repository's wait is
        # refused as such, with nothing written.
        with patch("tools.export_drawing_recipe.import_recipe",
                   side_effect=ProjectHeadLocked("another process holds the project head lock")):
            code, _, err = self.import_(None, "--confirm")
        self.assertEqual(code, 1)
        self.assertIn("ProjectHeadLocked", err)
        self.assertEqual(decisions(self, self.second), [])


def wait_for(test: unittest.TestCase, client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        test.assertEqual(response.status_code, 200, response.text)
        if response.json()["status"] in ("succeeded", "failed"):
            return response.json()
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished")


def drawing_project(test: unittest.TestCase, root: Path, project_id: str) -> tuple[TestClient, dict]:
    """A real project with one accepted Stage that a cut plan is drawn from."""

    client = studio(test, root, project_id, cad_export="occt")
    state = client.get("/api/state")
    test.assertEqual(state.status_code, 200, state.text)
    proposal = client.post("/api/proposals", json={
        "projectId": project_id, "stateDigest": state.json()["stateDigest"], "semanticEdit": room_edit()})
    test.assertEqual(proposal.status_code, 201, proposal.text)
    started = client.post(f"/api/proposals/{proposal.json()['proposalId']}/candidate")
    test.assertEqual(started.status_code, 202, started.text)
    test.assertEqual(wait_for(test, client, started.json()["jobId"])["status"], "succeeded")
    candidate = client.get(f"/api/candidates/{started.json()['candidateId']}").json()
    model = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
    stage = client.post("/api/design-stages/initialize", json={"projectId": project_id, "modelSource": model})
    test.assertEqual(stage.status_code, 201, stage.text)
    return client, stage.json()


def plan(test: unittest.TestCase, client: TestClient, project_id: str, stage: dict, **changes: object) -> dict:
    """A person's cut-plan request, as the Drawing makes it."""

    response = client.post("/api/drawings/plans", json={
        "projectId": project_id, "sourceStageRef": stage["stageRef"], "drawingId": "ground-plan",
        "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50, "sourceKind": "human", **changes})
    test.assertEqual(response.status_code, 201, response.text)
    return response.json()


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class RecipeTravelsBetweenProjectsTests(unittest.TestCase):
    def test_a_correction_confirmed_in_one_project_starts_the_next_projects_new_drawings(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        first, first_stage = drawing_project(self, root, "first-project")
        # In the first project a person corrects a drawing's hatch and confirms
        # it, on the corrected page, as the project's recipe.
        drawn = plan(self, first, "first-project", first_stage)
        corrected = plan(self, first, "first-project", first_stage, previousRevisionRef=drawn["revisionRef"],
                         hatchSpacingMm=3)
        self.assertEqual((drawn["viewRecipe"]["graphics"], corrected["viewRecipe"]["graphics"]),
                         (DEFAULTS, {**DEFAULTS, "hatchSpacingMm": 3.0}))
        page = {"runId": corrected["runId"], "assetSha256": corrected["assetSha256"],
                "revisionRef": corrected["revisionRef"], "pageIndex": 0}
        recipe = confirm_recipe(self, first, "first-project", page, {"hatchSpacingMm": 3})
        exported = root / "hatch-recipe.json"
        code, _, err = run_tool("export", "--project", root / "first-project", "--decision", recipe["decisionId"],
                                "--out", exported)
        self.assertEqual(code, 0, err)

        # The second project drew before the import, with the code default.
        second, second_stage = drawing_project(self, root, "second-project")
        before = plan(self, second, "second-project", second_stage)
        self.assertEqual(before["viewRecipe"]["graphics"], DEFAULTS)
        # A person imports the export while this runtime has the project open.
        code, _, err = run_tool("import", "--project", root / "second-project", "--file", exported, "--confirm")
        self.assertEqual(code, 0, err)
        [imported] = decisions(self, second)
        self.assertEqual((imported["strength"], imported["source"]["exportSha256"]),
                         ("soft_preference", json.loads(exported.read_text(encoding="utf-8"))["sha256"]))

        # Its next new drawing starts from the first project's correction.
        new = plan(self, second, "second-project", second_stage, drawingId="upper-plan")
        self.assertEqual(new["viewRecipe"]["graphics"], {**DEFAULTS, "hatchSpacingMm": 3.0})
        # The drawing made before keeps its own values, and an explicit value still wins.
        self.assertEqual(plan(self, second, "second-project", second_stage, previousRevisionRef=before["revisionRef"]),
                         before)
        explicit = plan(self, second, "second-project", second_stage, drawingId="section-plan", hatchSpacingMm=2.5)
        self.assertEqual(explicit["viewRecipe"]["graphics"]["hatchSpacingMm"], 2.5)


if __name__ == "__main__":
    unittest.main()
