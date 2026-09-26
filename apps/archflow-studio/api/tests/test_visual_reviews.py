"""POST /api/visual-reviews renders exact sources through their owners and looks once (GH-303 S1).

No test reaches a model: the Codex process is replaced at its subprocess
boundary, or a counting provider stands at the channel's provider seam.
"""

from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.adapters import occt_backend
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application import intent_agent
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.intent_agent import CodexCompiler
from archflow_studio_api.application.visual_observation import (
    Criterion, ReviewReason, SourceRef, TaskClass, VisualReviewBudget, VisualReviewRequest,
)
from archflow_studio_api.application.visual_reviews import PAGE_MAX_EDGE, review_sources
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID
from .test_candidate import CandidateTestCase
from .test_documents import two_page_pdf
from .test_visual_observation import CountingProvider


PAGE_ANSWER = {
    "observations": [{
        "type": "composition", "target_refs": ["criterion:hierarchy"],
        "description": "Two drawings of equal weight; neither leads the sheet.", "confidence": 0.8,
        "severity": "minor", "evidence_region": {"view_ref": "page-0", "x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.6},
    }],
    "unresolved_questions": [], "suggested_checks": ["Compare the two drawings' printed scales."],
}
FRESH = {"taskClass": "spatial_formal", "allowed": 2, "used": 0}


def model_answer(views):
    return {
        "observations": [{
            "type": "spatial", "target_refs": ["criterion:massing", "preserve:1"],
            "description": "The raised portico reads as one mass with the base.", "confidence": 0.7,
            "severity": "info", "evidence_region": {"view_ref": views[0], "x0": 0.2, "y0": 0.2, "x1": 0.8, "y1": 0.8},
        }],
        "unresolved_questions": ["Line projections carry no material."], "suggested_checks": [],
    }


@contextmanager
def codex_transport(answer):
    """The real Codex compiler with only its subprocess replaced: what it was sent is recorded."""

    calls = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(intent_agent, "_codex_version", return_value="visual-route-test-1"))

        def run(command, prompt, timeout_s):
            images = [Path(command[i + 1]).read_bytes() for i, token in enumerate(command) if token == "--image"]
            calls.append({"command": command, "prompt": prompt, "images": images})
            Path(command[command.index("-o") + 1]).write_text(json.dumps(answer), encoding="utf-8")
            event = json.dumps({"type": "turn.completed", "model": "test-reported-model", "usage": {
                "input_tokens": 900, "cached_input_tokens": 300, "output_tokens": 70, "reasoning_output_tokens": 20}})
            return subprocess.CompletedProcess(command, 0, event, "")

        stack.enter_context(patch.object(intent_agent, "_run_bounded", side_effect=run))
        yield CodexCompiler(model="test-only-model", timeout_s=5), calls


def files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


class PageReviewTests(unittest.TestCase):
    """Board, drawing and render reviews read registered pages; an uploaded PDF stands in here."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-visual-review-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0})
        self.app = create_app(StudioSettings(project_dir=self.root, cad_export="off"))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        uploaded = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "fileName": "review-sheet.pdf", "mimeType": "application/pdf",
            "contentBase64": base64.b64encode(two_page_pdf()).decode("ascii"),
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.document = uploaded.json()
        self.assertIsNone(self.document["revisionRef"])
        self.page = {"kind": "page", "runId": self.document["runId"], "assetSha256": self.document["assetSha256"],
                     "revisionRef": None, "pageIndex": 0}

    def review_body(self, **overrides) -> dict:
        body = {
            "projectId": PROJECT_ID, "domain": "board", "sourceRefs": [self.page], "viewRecipe": ["page-0"],
            "task": "Check the sheet's hierarchy before it is sent.",
            "criteria": [{"criterionId": "hierarchy", "text": "One drawing leads the sheet."},
                         {"criterionId": "legible", "text": "Dimension text is legible at print size."}],
            "preserve": ["Do not change the drawn content."], "reason": "first_bundle", "budgetState": FRESH,
        }
        body.update(overrides)
        return body

    def review(self, compiler=None, **overrides):
        if compiler is not None:
            self.app.state.intent_compiler = compiler
        return self.client.post("/api/visual-reviews", json=self.review_body(**overrides))

    def exported_page(self, page_index: int = 0) -> bytes:
        """The page export owner's own answer for this page, read through its own route."""

        response = self.client.post("/api/board/export", json={
            "projectId": PROJECT_ID, "format": "png", "zip": False, "maxEdge": PAGE_MAX_EDGE,
            "pages": [{**{key: value for key, value in self.page.items() if key != "kind"}, "pageIndex": page_index}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.content

    def test_a_page_review_sends_the_page_exports_own_pixels_and_hands_back_the_allowance(self):
        expected = self.exported_page()
        before = files(self.root)
        fact = "Sheet is A3 landscape; both drawings are at 1:100."
        with codex_transport(PAGE_ANSWER) as (compiler, calls):
            response = self.review(compiler, knownFacts=[fact])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["images"], [expected], "the provider sees the owner's pixels and nothing else")
        self.assertIn("--ephemeral", calls[0]["command"])
        self.assertIn(fact, calls[0]["prompt"])
        observation = result["observation"]
        self.assertEqual((observation["domain"], observation["sourceRefs"], observation["viewRefs"]),
                         ("board", [self.page], ["page-0"]))
        self.assertEqual(observation["frameSha256"], [hashlib.sha256(expected).hexdigest()])
        self.assertEqual(observation["reviewIndex"], 1)
        self.assertEqual(observation["observations"][0]["findingId"], "f1")
        self.assertEqual(observation["observations"][0]["targetRefs"], ["criterion:hierarchy"])
        self.assertEqual(observation["observations"][0]["evidenceRegion"]["viewRef"], "page-0")
        self.assertEqual(observation["suggestedChecks"], PAGE_ANSWER["suggested_checks"])
        self.assertNotIn("verdict", observation)
        usage = result["usage"]
        self.assertEqual((usage["provider"], usage["model"], usage["providerCalls"], usage["imageInputs"]),
                         ("codex", "test-reported-model", 1, 1))
        self.assertEqual((usage["imageBytes"], usage["inputTokens"], usage["cachedInputTokens"]),
                         (len(expected), 900, 300))
        self.assertIsNotNone(usage["receiptId"])
        self.assertEqual(result["budgetState"],
                         {"taskClass": "spatial_formal", "allowed": 2, "used": 1, "lastFindingIds": ["f1"]})
        self.assertEqual(files(self.root), before, "a visual review writes nothing to the project")

    def test_a_follow_up_names_a_finding_of_the_last_review_and_then_the_loop_is_exhausted(self):
        with codex_transport(PAGE_ANSWER) as (compiler, calls):
            first = self.review(compiler).json()["budgetState"]
            for addressed in ([], ["f2"]):
                with self.subTest(addressed=addressed):
                    refused = self.review(reason="after_repair", budgetState=first, addressedFindingIds=addressed)
                    self.assertEqual(refused.status_code, 409, refused.text)
                    self.assertEqual(refused.json()["code"], "VISUAL_REVIEW_NOT_WARRANTED")
                    self.assertEqual(refused.json()["budgetState"], first, "a refusal spends nothing")
            follow_up = self.review(reason="after_repair", budgetState=first, addressedFindingIds=["f1"])
            self.assertEqual(follow_up.status_code, 200, follow_up.text)
            spent = follow_up.json()["budgetState"]
            self.assertEqual((spent["used"], follow_up.json()["observation"]["reviewIndex"]), (2, 2))
            for state in (spent, {**FRESH, "used": 3}):
                with self.subTest(state=state):
                    exhausted = self.review(budgetState=state, reason="first_bundle")
                    self.assertEqual(exhausted.status_code, 409, exhausted.text)
                    self.assertEqual(exhausted.json()["code"], "VISUAL_BUDGET_EXHAUSTED")
        self.assertEqual(len(calls), 2)

    def test_an_exhausted_or_unwarranted_allowance_refuses_before_anything_renders(self):
        cases = {
            "VISUAL_BUDGET_EXHAUSTED": {**FRESH, "used": 2},
            "VISUAL_REVIEW_NOT_WARRANTED": {"taskClass": "deterministic_edit", "allowed": 0, "used": 0},
        }
        with codex_transport(PAGE_ANSWER) as (compiler, calls), patch(
                "archflow_studio_api.application.visual_reviews.export_board_pages",
                side_effect=AssertionError("a refused review renders nothing")):
            for code, state in cases.items():
                with self.subTest(code=code):
                    response = self.review(compiler, budgetState=state)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(response.json()["code"], code)
                    self.assertEqual(response.json()["budgetState"], {**state, "lastFindingIds": []})
        self.assertEqual(calls, [])

    def test_a_runtime_without_a_vision_provider_refuses_before_rendering(self):
        self.assertIsInstance(self.app.state.intent_compiler, intent_agent.DeterministicCompiler)
        with patch("archflow_studio_api.application.visual_reviews.export_board_pages",
                   side_effect=AssertionError("no provider, nothing to render for")):
            response = self.review()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "VISUAL_PROVIDER_UNAVAILABLE")
        self.assertIn("deterministic", response.json()["detail"])

    def test_a_page_the_project_does_not_retain_exactly_is_a_source_mismatch(self):
        stale = {
            "an unregistered asset": ({**self.page, "assetSha256": "0" * 64}, "page-0"),
            "a revision the upload never had": ({**self.page, "revisionRef": "project://P/runs/r/records/x.json"}, "page-0"),
            "a page the document does not have": ({**self.page, "pageIndex": 5}, "page-5"),
            "a run the project does not have": ({**self.page, "runId": "no-such-run"}, "page-0"),
        }
        with codex_transport(PAGE_ANSWER) as (compiler, calls), patch(
                "archflow_studio_api.application.boards._page_raster",
                side_effect=AssertionError("a source that is not retained is not rasterized")):
            for name, (source, view) in stale.items():
                with self.subTest(name):
                    response = self.review(compiler, sourceRefs=[source], viewRecipe=[view])
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(response.json()["code"], "VISUAL_SOURCE_MISMATCH")
                    self.assertEqual(response.json()["sourceRef"], source)
        self.assertEqual(calls, [])

    def test_the_caller_has_no_channel_for_pixels(self):
        pixels = base64.b64encode(self.exported_page()).decode("ascii")
        smuggled = {
            "frames at the top": self.review_body(frames=[{"viewRef": "page-0", "data": pixels}]),
            "images at the top": self.review_body(images=[pixels]),
            "a PNG beside the source": self.review_body(sourceRefs=[{**self.page, "pngBase64": pixels}]),
            "data beside the source": self.review_body(sourceRefs=[{**self.page, "data": pixels}]),
            "a frame in the allowance": self.review_body(budgetState={**FRESH, "frame": pixels}),
        }
        with codex_transport(PAGE_ANSWER) as (compiler, calls), patch(
                "archflow_studio_api.application.visual_reviews.export_board_pages",
                side_effect=AssertionError("a smuggled frame is refused before rendering")):
            self.app.state.intent_compiler = compiler
            for name, body in smuggled.items():
                with self.subTest(name):
                    response = self.client.post("/api/visual-reviews", json=body)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["code"], "REQUEST_INVALID")
        self.assertEqual(calls, [])

    def test_a_malformed_review_is_refused_before_anything_renders(self):
        malformed = {
            "a model view of a page": dict(viewRecipe=["top"]),
            "a page view that is not the page": dict(viewRecipe=["page-1"]),
            "a modeling review of a page": dict(domain="modeling"),
            "a raised spatial allowance": dict(budgetState={**FRESH, "allowed": 3}),
            "findings before any review": dict(budgetState={**FRESH, "lastFindingIds": ["f1"]}),
            "a repair without a follow-up": dict(addressedFindingIds=["f1"]),
            "repeated criteria": dict(criteria=[{"criterionId": "legible", "text": "A."},
                                                {"criterionId": "legible", "text": "B."}]),
            "a blank task": dict(task="   "),
            "nine readback facts": dict(knownFacts=["Level 1 at 0.00."] * 9),
            "an inexact page source": dict(sourceRefs=[{key: value for key, value in self.page.items()
                                                        if key != "revisionRef"}]),
        }
        with codex_transport(PAGE_ANSWER) as (compiler, calls), patch(
                "archflow_studio_api.application.visual_reviews.export_board_pages",
                side_effect=AssertionError("a malformed review renders nothing")):
            for name, overrides in malformed.items():
                with self.subTest(name):
                    response = self.review(compiler, **overrides)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["code"], "REQUEST_INVALID")
            elsewhere = self.review(compiler, projectId="another-project")
            self.assertEqual(elsewhere.status_code, 403, elsewhere.text)
            self.assertEqual(elsewhere.json()["code"], "PROJECT_MISMATCH")
        self.assertEqual(calls, [])

    def test_a_failed_provider_call_is_spent_and_says_what_it_cost(self):
        foreign = {**PAGE_ANSWER, "observations": [{**PAGE_ANSWER["observations"][0], "target_refs": ["entity:roof"]}]}
        with codex_transport(foreign) as (compiler, calls):
            response = self.review(compiler)
        self.assertEqual(response.status_code, 502, response.text)
        failed = response.json()
        self.assertEqual(failed["code"], "VISUAL_PROVIDER_FAILED")
        self.assertEqual(failed["budgetState"],
                         {"taskClass": "spatial_formal", "allowed": 2, "used": 1, "lastFindingIds": []})
        self.assertEqual((failed["usage"]["inputTokens"], failed["usage"]["imageInputs"]), (900, 1))
        self.assertEqual(len(calls), 1)

    def test_a_counting_provider_sees_only_the_owners_frame(self):
        """At the channel's provider seam, the frame is the page export's answer, bound to the named page."""

        provider = CountingProvider(PAGE_ANSWER)
        source = SourceRef.page(self.page["runId"], self.page["assetSha256"], None, 0)
        request = VisualReviewRequest(
            domain="render", source_refs=(source,), view_recipe=("page-0",), task="Read the render's composition.",
            criteria=(Criterion("hierarchy", "One element leads the image."),), budget=2)
        budget = VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
        result = review_sources(bound_project(self.app.state), request, reason=ReviewReason.FIRST_BUNDLE,
                                budget=budget, provider=provider)
        (sent, frames), = provider.calls
        self.assertIs(sent, request)
        self.assertEqual([(frame.source, frame.view_ref, frame.png) for frame in frames],
                         [(source, "page-0", self.exported_page())])
        self.assertEqual(frames[0].representation, "registered-page-raster")
        self.assertEqual(result.observation.frame_sha256, (frames[0].sha256,))
        self.assertEqual(budget.to_dict(), {"taskClass": "spatial_formal", "allowed": 2, "used": 1,
                                            "lastFindingIds": ["f1"]})


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class ModelReviewTests(CandidateTestCase):
    """A modeling review draws each orthographic view of one exact retained model."""

    def setUp(self) -> None:
        super().setUp()
        self.client.close()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        accepted, job = self.run_candidate("set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        self.model = next(row for row in candidate["artifacts"] if row["format"] == "3dm")["modelSource"]

    def enable_monitor(self) -> None:
        self.client.close()
        self.app = create_app(replace(self.settings, monitor_dir=self.root / "diagnostics"))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def review(self, source: dict, views: list[str], compiler=None, **overrides):
        if compiler is not None:
            self.app.state.intent_compiler = compiler
        return self.client.post("/api/visual-reviews", json={
            "projectId": PROJECT_ID, "domain": "modeling", "sourceRefs": [{"kind": "model", **source}],
            "viewRecipe": views, "task": "Raise the portico base without breaking the massing.",
            "criteria": [{"criterionId": "massing", "text": "The portico still reads as one mass with the base."}],
            "preserve": ["Keep the column spacing."], "reason": "first_bundle", "budgetState": FRESH, **overrides,
        })

    def owner_view(self, view: str) -> bytes:
        response = self.client.get("/api/drawings/model-view", params={**self.model, "view": view})
        self.assertEqual(response.status_code, 200, response.text)
        return base64.b64decode(response.json()["data"], validate=True)

    def test_a_model_review_renders_each_view_of_the_exact_model(self):
        self.enable_monitor()
        # Three orthographic views and the axonometric one, each drawn by the model-view owner.
        views = ["top", "front", "right", "axon"]
        expected = [self.owner_view(view) for view in views]
        project = self.repository.layout.root
        before = files(project)
        with codex_transport(model_answer(views)) as (compiler, calls):
            response = self.review(self.model, views, compiler)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["images"], expected, "each frame is the model-view owner's own projection")
        observation = response.json()["observation"]
        self.assertEqual((observation["sourceRefs"], observation["viewRefs"]), ([{"kind": "model", **self.model}], views))
        self.assertEqual(observation["frameSha256"], [hashlib.sha256(png).hexdigest() for png in expected])
        self.assertEqual(response.json()["usage"]["imageInputs"], len(views))
        self.assertEqual(files(project), before, "a visual review writes nothing to the project")
        events, warnings = self.app.state.monitor.store.read()
        self.assertFalse(warnings)
        span, = [event for event in events if event.phase == "visual_observation" and event.status == "succeeded"]
        self.assertEqual((span.project_id, span.run_id), (PROJECT_ID, self.model["runId"]))
        self.assertEqual(span.details["comparison_refs"],
                         [f"frame:{view}:{hashlib.sha256(png).hexdigest()}" for view, png in zip(views, expected)])

    def test_a_stale_state_digest_is_refused_before_projection_and_provider(self):
        stale = {
            "another run's state": {**self.model, "stateDigest": self.state_digest},
            "a state no run projects to": {**self.model, "stateDigest": "0" * 64},
            "an asset the state never exported": {**self.model, "assetSha256": "0" * 64},
        }
        with codex_transport(model_answer(["top"])) as (compiler, calls), patch(
                "archflow_studio_api.application.drawings.project_model_axis_elevation",
                side_effect=AssertionError("a stale source is never projected")):
            for name, source in stale.items():
                with self.subTest(name):
                    response = self.review(source, ["top"], compiler)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(response.json()["code"], "VISUAL_SOURCE_MISMATCH")
                    self.assertEqual(response.json()["sourceRef"], {"kind": "model", **source})
        self.assertEqual(calls, [])

    def test_a_generated_drawing_is_reviewed_only_at_its_exact_revision(self):
        generated = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "modelSource": self.model, "view": "front", "drawingId": "review-elevation"})
        self.assertEqual(generated.status_code, 201, generated.text)
        drawing = generated.json()
        page = {"kind": "page", "runId": drawing["runId"], "assetSha256": drawing["assetSha256"],
                "revisionRef": drawing["revisionRef"], "pageIndex": 0}
        answer = {**PAGE_ANSWER, "observations": [{**PAGE_ANSWER["observations"][0],
                                                   "target_refs": ["criterion:massing"]}]}
        with codex_transport(answer) as (compiler, calls):
            unnamed = self.review({}, ["page-0"], compiler, domain="drawing",
                                  sourceRefs=[{**page, "revisionRef": None}])
            self.assertEqual(unnamed.status_code, 409, unnamed.text)
            self.assertEqual(unnamed.json()["code"], "VISUAL_SOURCE_MISMATCH")
            self.assertEqual(calls, [])
            exact = self.review({}, ["page-0"], compiler, domain="drawing", sourceRefs=[page])
        self.assertEqual(exact.status_code, 200, exact.text)
        exported = self.client.post("/api/board/export", json={
            "projectId": PROJECT_ID, "format": "png", "zip": False, "maxEdge": PAGE_MAX_EDGE,
            "pages": [{key: value for key, value in page.items() if key != "kind"}]})
        self.assertEqual(calls[0]["images"], [exported.content])
        self.assertEqual(exact.json()["observation"]["sourceRefs"], [page])


if __name__ == "__main__":
    unittest.main()
